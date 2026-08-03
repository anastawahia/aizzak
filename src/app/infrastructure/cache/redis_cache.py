"""Redis adapter for the ``CacheProvider`` port (02-port-contracts §1.7).

Phase 2.3. Mirrors the ``persistence/database.py`` split: a small factory
builds the technology client from ``Settings`` (called only by the
Composition Root), and a thin adapter class implements the port over it
(structural Protocol match -- no inheritance, per this codebase's
Protocol-based ports).

Values are raw ``bytes`` end-to-end (``decode_responses=False``): the port
deliberately says nothing about encoding, so serialization is entirely the
caller's concern (``integrations``' OAuth-state binding stores packed JSON,
future response caches may store anything).

Error policy -- translate, never fail open: every Redis/driver failure is
mapped onto the shared framework hierarchy (``common.internal``, the R6
precedent from every SQL adapter) instead of being swallowed into a ``None``
cache miss. ``CacheProvider`` is not only an optimization surface: the
``integrations`` module binds single-use OAuth ``state`` through it
(CSRF fail-closed), where an outage disguised as a miss would be
indistinguishable from an attack and much harder to diagnose than an
explicit 500. Callers that WANT miss-on-error caching wrap the port
themselves (alpha's fail-open pattern is caller policy, not adapter policy).

``alpha`` reference (migration-from-alpha §2, ``services/cache.py``): the
presence-TTL and fixed-window patterns live in callers; nothing else was
worth porting -- the client itself is new ``redis.asyncio`` code.
"""

from __future__ import annotations

from typing import cast

from redis import RedisError
from redis.asyncio import Redis

from app.framework.errors import AppError
from app.framework.settings.settings import RedisSettings

# One place to keep the client's socket behaviour: fail fast instead of
# hanging a request-handling coroutine on a dead cache (07-nfr latency
# budgets are all sub-second). This is the READ timeout for the
# request/response path (``RedisCache`` above, the API's cache client) and
# the DEFAULT every caller gets unless it opts into a longer one via
# ``read_timeout_s`` below -- see ``blocking_read_timeout_s`` for the other
# profile: a consumer that blocks on ``XREADGROUP`` needs a read timeout
# LONGER than what it waits for, not this one (stream-topology-plan.md
# §1-أ, §3 "الخطوة 1").
_SOCKET_TIMEOUT_S = 2.0
# The CONNECT timeout, not the read timeout -- a different profile entirely.
# Never raised, for anybody, workers included: failing to *establish* a
# connection means Redis is absent, not that a caller is waiting on a quiet
# stream, and turning that into a multi-second freeze on every boot attempt
# buys nothing (stream-topology-plan.md §3, item 1's warning).
_CONNECT_TIMEOUT_S = 2.0

# The margin added on top of a consumer's own ``BLOCK`` window (see
# ``blocking_read_timeout_s``) so the socket timeout is comfortably longer
# than the wait it wraps, not merely equal to it -- equal values race under
# real scheduling/network jitter.
_BLOCKING_READ_MARGIN_S = 1.0


def create_redis_client(
    settings: RedisSettings, *, read_timeout_s: float = _SOCKET_TIMEOUT_S
) -> Redis:
    """Build a shared async Redis client (Composition Root only).

    ``decode_responses=False`` keeps the ``bytes`` port contract exact;
    pooling is redis-py's built-in connection pool (one client per process,
    shared across coroutines -- the library's intended usage).

    ``read_timeout_s`` is keyword-only and defaults to ``_SOCKET_TIMEOUT_S``
    (2.0) so every call site that does not pass it behaves byte-for-byte as
    before. A caller that performs a BLOCKING read (``XREADGROUP ...
    BLOCK <block_ms>``) must pass ``blocking_read_timeout_s(block_ms)``
    instead -- see that function's docstring for why a shorter socket
    timeout than the blocking window it wraps is a deterministic crash, not
    an edge case (stream-topology-plan.md §1-أ).
    """
    return Redis.from_url(
        settings.url,
        decode_responses=False,
        socket_timeout=read_timeout_s,
        socket_connect_timeout=_CONNECT_TIMEOUT_S,
    )


def blocking_read_timeout_s(block_ms: int) -> float:
    """Derive a client's read-socket timeout from a consumer's own ``BLOCK``
    window (``XREADGROUP ... BLOCK <block_ms>``).

    A consumer that blocks asks Redis to hold the socket open, silent, for
    up to ``block_ms`` while it waits for a new stream entry -- that is the
    intended, successful case on a quiet stream, not a hang. If the client's
    read-socket timeout is shorter than that window, a read behaving exactly
    as asked looks indistinguishable, from the socket's point of view, from
    a dead connection, and gets cut mid-wait (``redis.exceptions.
    TimeoutError``) every single cycle the stream stays quiet.

    Deriving the timeout from ``block_ms`` -- rather than hand-picking a
    second literal beside it -- is the point: a bare constant drifts the
    moment ``CONSUMER_BLOCK_MS`` changes in ``.env``, silently reopening the
    exact gap this function exists to close (stream-topology-plan.md §3,
    item 2).
    """
    return (block_ms / 1000) + _BLOCKING_READ_MARGIN_S


class RedisCache:
    """Redis-backed ``CacheProvider`` (02 §1.7, structural Protocol match)."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def get(self, key: str) -> bytes | None:
        try:
            value = await self._client.get(key)
        except RedisError as exc:
            raise _translate(exc) from exc
        return cast("bytes | None", value)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        # ``ex=None`` means "no expiry" to redis-py, matching the port's
        # ``ttl_s: int | None`` exactly -- no branching needed.
        try:
            await self._client.set(key, value, ex=ttl_s)
        except RedisError as exc:
            raise _translate(exc) from exc

    async def delete(self, key: str) -> None:
        # DEL of a missing key is a no-op returning 0 -- the port's delete
        # is intentionally idempotent (same silent-on-absent semantics as
        # ``access.remove``).
        try:
            await self._client.delete(key)
        except RedisError as exc:
            raise _translate(exc) from exc

    async def incr(self, key: str, amount: int = 1) -> int:
        # INCRBY initializes a missing key at 0 before adding -- so the
        # first call returns ``amount``, which is exactly the fixed-window
        # counter semantics rate-limit callers expect.
        try:
            return await self._client.incrby(key, amount)
        except RedisError as exc:
            raise _translate(exc) from exc

    async def expire(self, key: str, ttl_s: int) -> None:
        # EXPIRE on a missing key returns 0 (nothing to expire) -- silently
        # idempotent, consistent with ``delete``.
        try:
            await self._client.expire(key, ttl_s)
        except RedisError as exc:
            raise _translate(exc) from exc


def _translate(exc: RedisError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``redis``-package exception types never escape this
    adapter (the R6 precedent from every SQL adapter). There is no cache
    analogue of 23505/42501: every failure here (connection refused,
    timeout, WRONGTYPE, OOM, ...) is an infrastructure fault the caller
    cannot meaningfully branch on, so everything folds into the 500-class
    ``common.internal``."""
    return AppError("cache operation failed", code="common.internal")
