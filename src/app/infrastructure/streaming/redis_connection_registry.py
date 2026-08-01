"""``WsConnectionRegistry`` over Redis — the cross-process home of the 07 §4
per-user WebSocket cap (P1-8, ``docs/p1-hardening-plan.md`` §3 step 11).

**Shape: one sorted set per user.** ``<prefix>:<user_id>`` holds one MEMBER
per live connection (its ``connection_id``), scored with the Redis-server
millisecond timestamp of its last renewal. That shape is chosen for two
reasons and not for taste:

* a plain ``INCR``/``DECR`` counter cannot express "this particular
  connection's slot", so it can neither be released precisely nor aged out
  individually — a lost ``DECR`` is unrecoverable drift;
* the score IS the age, so "evict entries older than ``ttl_s``" is one
  ``ZREMRANGEBYSCORE`` rather than a scan.

**Atomicity is bought back on the Redis side (the port's requirement 1).**
Before this step ``ConnectionHub.try_register`` was synchronous, and its own
docstring leaned on that: "no ``await`` between check and change ⇒ atomic
under cooperative scheduling". Reading the count over one round-trip and
adding over another would spend that guarantee for nothing — two sockets
arriving together would both see ``ZCARD < max`` and both be admitted, which
is the very overshoot the cap exists to prevent. Redis runs a Lua script
atomically from first command to last, so ``_ACQUIRE_LUA`` does the eviction,
the check and the add as ONE indivisible step: a concurrent caller either sees
the state before it entirely or after it entirely, never between.

**Time comes from ``redis.call('TIME')``, never from this process.** The
scores are compared across every sibling worker and every replica, so they
must be stamped by the one clock all of them share. Writing after a
non-deterministic command has been legal since Redis 5's effect-based script
replication (verified live against the test harness's Redis 7.0.15; the
deployed stack runs 7.4.9 — every command used here is 6.2-or-older, so the
gap between the two is not load-bearing).

**Entries age; the KEY's own expiry is only garbage collection.** ``PEXPIRE``
here exists so a user who stops connecting entirely leaves no permanent
residue in Redis — it is deliberately NOT the cap's safety mechanism, because a key
touched by every new connection is a key that never expires while the user is
active, and the dead entry inside it would then be counted forever (the port's
requirement 2 spells this out).

Error policy — translate, never fail open (the ``RedisCache`` precedent):
every driver failure becomes ``AppError('common.internal')`` so no ``redis``
exception type escapes this adapter. What the CALLER does with that failure is
caller policy, and ``ConnectionHub`` decides it explicitly (fail CLOSED on
admission, swallow on release) — see its docstring.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from redis import RedisError
from redis.asyncio import Redis

from app.framework.errors import AppError
from app.framework.types import Uuid

# The default key namespace. Tests pass their own uniquely-tagged prefix
# (`ws:test:<uuid7>`) so a live run never touches a production counter.
DEFAULT_KEY_PREFIX = "ws:conn"

# Redis's `TIME` returns [unix_seconds, microseconds]; every script needs the
# same millisecond stamp, so the derivation is written once and prepended.
_NOW_MS_LUA = """
local t = redis.call('TIME')
local now_ms = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
"""

# KEYS[1] = the user's set · ARGV = connection_id, ttl_ms, max_connections.
# Evict-then-check-then-add, indivisibly (module docstring's "Atomicity").
_ACQUIRE_LUA = (
    _NOW_MS_LUA
    + """
local key = KEYS[1]
local member = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
local max_connections = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - ttl_ms)
if redis.call('ZCARD', key) >= max_connections then
    return 0
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, ttl_ms)
return 1
"""
)

# KEYS[1] = the user's set · ARGV = ttl_ms, then one connection_id per entry.
# Evict-then-renew, in that order and never the reverse:
#   * the eviction is what makes the renewal pass ALSO the sweep -- a crashed
#     sibling's entry is reclaimed on the very next tick of any live hub's
#     renewal loop, instead of lingering until someone happens to acquire or
#     count. Nothing else in this system runs periodically over these keys.
#   * `XX` is then load-bearing twice over: it updates ONLY members that still
#     exist, so a renewal racing a release cannot resurrect the released slot,
#     AND a caller whose own entry just aged out (its process was unreachable
#     for longer than ttl) does not silently re-admit itself past the cap.
# `GT` keeps the score monotonic if two renewals interleave.
_RENEW_LUA = (
    _NOW_MS_LUA
    + """
local key = KEYS[1]
local ttl_ms = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - ttl_ms)
for i = 2, #ARGV do
    redis.call('ZADD', key, 'XX', 'GT', 'CH', now_ms, ARGV[i])
end
redis.call('PEXPIRE', key, ttl_ms)
return redis.call('ZCARD', key)
"""
)

# KEYS[1] = the user's set · ARGV = ttl_ms. Counting EVICTS first rather than
# merely filtering, so the shared state self-heals on a read too -- a user who
# is only ever counted (never admitted) still sheds a crashed process's entry.
_COUNT_LUA = (
    _NOW_MS_LUA
    + """
local key = KEYS[1]
local ttl_ms = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - ttl_ms)
return redis.call('ZCARD', key)
"""
)


class RedisWsConnectionRegistry:
    """Redis-backed ``WsConnectionRegistry`` (structural Protocol match -- the
    ``RedisCache``/``SqlRedisMetricsSource`` precedent, no inheritance)."""

    def __init__(self, client: Redis, *, key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
        self._client = client
        self._key_prefix = key_prefix
        # `register_script` uses EVALSHA with a transparent EVAL fallback, so
        # the script body crosses the wire once per server rather than once
        # per connection attempt.
        self._acquire = client.register_script(_ACQUIRE_LUA)
        self._renew = client.register_script(_RENEW_LUA)
        self._count = client.register_script(_COUNT_LUA)

    def key_for(self, user_id: Uuid) -> str:
        """The one place the key layout is decided -- exposed because a live
        test must be able to inspect and clean up exactly what it created."""
        return f"{self._key_prefix}:{user_id}"

    async def try_acquire(
        self, *, user_id: Uuid, connection_id: Uuid, max_connections: int, ttl_s: int
    ) -> bool:
        try:
            admitted = await self._acquire(
                keys=[self.key_for(user_id)],
                args=[connection_id, _ms(ttl_s), max_connections],
            )
        except RedisError as exc:
            raise _translate(exc) from exc
        return int(cast("Any", admitted)) == 1

    async def renew(self, *, user_id: Uuid, connection_ids: Sequence[Uuid], ttl_s: int) -> None:
        if not connection_ids:
            # No entries to keep young: a round-trip that would touch nothing
            # but would still PEXPIRE a key this process no longer holds.
            return
        try:
            await self._renew(keys=[self.key_for(user_id)], args=[_ms(ttl_s), *connection_ids])
        except RedisError as exc:
            raise _translate(exc) from exc

    async def release(self, *, user_id: Uuid, connection_id: Uuid) -> None:
        # `ZREM` of an absent member returns 0 -- the port's idempotent
        # release needs no existence check (the `RedisCache.delete`
        # precedent).
        try:
            await self._client.zrem(self.key_for(user_id), connection_id)
        except RedisError as exc:
            raise _translate(exc) from exc

    async def count(self, *, user_id: Uuid, ttl_s: int) -> int:
        try:
            held = await self._count(keys=[self.key_for(user_id)], args=[_ms(ttl_s)])
        except RedisError as exc:
            raise _translate(exc) from exc
        return int(cast("Any", held))


def _ms(seconds: int) -> int:
    return seconds * 1000


def _translate(exc: RedisError) -> AppError:
    """Map a driver-level failure onto the shared framework hierarchy (03 §4)
    -- identical policy and identical reasoning to ``RedisCache._translate``:
    connection refused, timeout, WRONGTYPE and OOM are all infrastructure
    faults a caller cannot branch on, so they fold into ``common.internal``.
    A ``NOSCRIPT``/script error surfaces the same way rather than being
    retried here: ``register_script`` already re-sends the body on
    ``NOSCRIPT``, so anything left is a genuine fault."""
    return AppError("ws connection registry operation failed", code="common.internal")
