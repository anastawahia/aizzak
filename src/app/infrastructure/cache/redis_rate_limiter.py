"""``RateLimiter`` over Redis — capacity-plan step 1.2's enforcement engine.

**Shape: one sorted set per bucket, one member per admitted request.**
``<prefix>:<bucket key>`` holds a member per request admitted inside the
window, scored with the Redis-server millisecond timestamp of its arrival.
That is the ``RedisWsConnectionRegistry`` shape and it is chosen for the same
two reasons, plus one this port adds:

* a plain ``INCR`` with an expiry is a FIXED window, not a sliding one: it
  admits a full allowance at 11:00:59 and another at 11:01:00, so the
  criterion "the 121st request in a minute is refused" would be false for
  ordinary traffic that straddles a minute boundary;
* the score IS the arrival time, so "forget everything older than the window"
  is one ``ZREMRANGEBYSCORE`` rather than a scan;
* and the OLDEST surviving score is exactly when capacity returns, so
  ``Retry-After`` is read off the data rather than guessed.

**Atomicity, and why it is bought on the server (the port's requirement 1).**
Everything below happens inside one Lua script, which Redis runs from first
command to last with nothing interleaved. Two simultaneous requests therefore
cannot both observe ``ZCARD < limit`` and both be admitted. The alternative —
count, then add — overshoots exactly the ceiling this exists to hold, and it
overshoots WORST under precisely the burst the limit is for.

**All-or-nothing across buckets (the port's requirement 2), which is why there
are two passes.** The first pass evicts and checks every bucket and touches
nothing else; only if all of them have room does the second pass add. A single
pass that consumed as it went would charge a user's allowance for a request
the workspace ceiling then refused.

**Time comes from ``redis.call('TIME')``, never from this process.** The
scores are compared across every sibling worker and every replica, so they
must be stamped by the one clock all of them share — a limiter that trusted
each replica's own wall clock would enforce a window that is wider or narrower
than a minute by however far the hosts have drifted. Writing after a
non-deterministic command has been legal since Redis 5's effect-based script
replication (the registry's own note, verified there against the live
harness).

**The member is supplied by the caller-facing adapter, not invented in Lua.**
``ZADD`` overwrites a member that already exists, so two requests sharing a
member would count once; a fresh uuid7 per call makes every admitted request
its own entry, and keeps the script itself free of randomness.

**The KEY's expiry is garbage collection, not the mechanism.** ``PEXPIRE`` is
refreshed to one window on every admission so a user who stops calling leaves
no permanent residue; the eviction inside the script is what actually enforces
the window, because a key touched by every request is a key that never expires
while its owner is active.

Error policy — translate, never decide: every driver failure becomes
``AppError('common.internal')`` so no ``redis`` exception type escapes this
adapter, and what the CALLER does with it is caller policy. 1.2's is the
opposite of ``ConnectionHub``'s — see ``api/middleware/rate_limit.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from redis import RedisError
from redis.asyncio import Redis

from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.rate_limiter import ALLOWED, RateBucket, RateVerdict

# The key namespace, beside the denylist's `auth:revoked:` and the registry's
# `ws:conn`. Tests pass their own uniquely-tagged prefix so a live run never
# touches a real user's window.
DEFAULT_KEY_PREFIX = "rate"

# What the script returns: {allowed, bound_index, retry_after_ms}.
_VERDICT_FIELDS = 3

# KEYS = one per bucket, in the caller's order.
# ARGV[1] = the member for this request; then, per bucket i:
#   ARGV[2i] = limit, ARGV[2i+1] = window_ms.
#
# Returns {allowed, bound_index, retry_after_ms} -- the index rather than the
# scope name, so the script never carries a string it did not need.
_CONSUME_LUA = """
local t = redis.call('TIME')
local now_ms = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
local member = ARGV[1]

for i = 1, #KEYS do
    local key = KEYS[i]
    local limit = tonumber(ARGV[i * 2])
    local window_ms = tonumber(ARGV[i * 2 + 1])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
    if redis.call('ZCARD', key) >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_ms = window_ms
        if oldest[2] then
            retry_ms = (tonumber(oldest[2]) + window_ms) - now_ms
        end
        if retry_ms < 1 then
            retry_ms = 1
        end
        return {0, i, retry_ms}
    end
end

for i = 1, #KEYS do
    local window_ms = tonumber(ARGV[i * 2 + 1])
    redis.call('ZADD', KEYS[i], now_ms, member)
    redis.call('PEXPIRE', KEYS[i], window_ms)
end
return {1, 0, 0}
"""


class RedisRateLimiter:
    """Redis-backed ``RateLimiter`` (structural Protocol match — the
    ``RedisCache``/``RedisWsConnectionRegistry`` precedent, no inheritance)."""

    def __init__(self, client: Redis, *, key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
        self._client = client
        self._key_prefix = key_prefix
        # `register_script` uses EVALSHA with a transparent EVAL fallback, so
        # the script body crosses the wire once per server rather than once
        # per request -- which on this path is once per request the platform
        # serves at all.
        self._consume = client.register_script(_CONSUME_LUA)

    def key_for(self, bucket_key: str) -> str:
        """The one place the key layout is decided — exposed because a live
        test must be able to inspect and clean up exactly what it created."""
        return f"{self._key_prefix}:{bucket_key}"

    async def try_consume(self, buckets: Sequence[RateBucket]) -> RateVerdict:
        if not buckets:
            # No ceilings configured is not an error and not a refusal: the
            # caller decides which buckets exist, and "none" is a legitimate
            # answer (`م-8`'s baseline run turns them off).
            return ALLOWED
        args: list[str | int] = [new_uuid7()]
        for bucket in buckets:
            _guard(bucket)
            args.append(bucket.limit)
            args.append(bucket.window_s * 1000)
        try:
            outcome = await self._consume(
                keys=[self.key_for(bucket.key) for bucket in buckets], args=args
            )
        except RedisError as exc:
            raise _translate(exc) from exc
        return _verdict(cast("Any", outcome), buckets)


def _guard(bucket: RateBucket) -> None:
    """Refuse a bucket the script would silently turn into "refuse always".

    A ``limit`` of zero or less makes ``ZCARD >= limit`` true on an empty key,
    so a mis-wired ceiling would answer 429 to every request its scope covers
    — the loudest possible outage delivered as a correct-looking refusal. The
    caller's own knobs express "off" by building NO bucket, never by passing a
    zero (``api/middleware/rate_limit.py``).
    """
    if bucket.limit <= 0:
        raise ValidationError(f"rate bucket '{bucket.scope}' limit must be positive")
    if bucket.window_s <= 0:
        raise ValidationError(f"rate bucket '{bucket.scope}' window_s must be positive")
    if not bucket.key.strip():
        raise ValidationError(f"rate bucket '{bucket.scope}' key must not be empty")


def _verdict(outcome: Any, buckets: Sequence[RateBucket]) -> RateVerdict:
    """``{allowed, index, retry_ms}`` from the script into the port's verdict.

    Defensive on shape rather than trusting it: this is the return of a script
    a server ran, and a verdict this code could not read must degrade to
    ALLOWED — the module's fail-open policy applied to its own parsing, since
    the alternative is refusing every request in the platform over a decoding
    bug.
    """
    if not isinstance(outcome, (list, tuple)) or len(outcome) < _VERDICT_FIELDS:
        return ALLOWED
    allowed, index, retry_ms = int(outcome[0]), int(outcome[1]), int(outcome[2])
    if allowed == 1 or not 1 <= index <= len(buckets):
        return ALLOWED
    return RateVerdict(
        allowed=False,
        scope=buckets[index - 1].scope,
        # Ceiling, never floor: a `Retry-After` that expires a millisecond
        # before capacity returns sends the client back into a second refusal,
        # which is how a limiter turns one rejected request into a retry loop.
        retry_after_s=max(1, -(-retry_ms // 1000)),
    )


def _translate(exc: RedisError) -> AppError:
    """Map a driver-level failure onto the shared framework hierarchy (03 §4)
    — identical policy and reasoning to ``RedisCache._translate``: connection
    refused, timeout, WRONGTYPE and OOM are all infrastructure faults a caller
    cannot branch on, so they fold into ``common.internal``. The CALLER turns
    that into "admit the request" (1.2's fail-open); this adapter's job is
    only to stop a ``redis`` type from leaking past the boundary."""
    return AppError("rate limiter operation failed", code="common.internal")
