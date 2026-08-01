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
# budgets are all sub-second).
_SOCKET_TIMEOUT_S = 2.0
_CONNECT_TIMEOUT_S = 2.0


def create_redis_client(settings: RedisSettings) -> Redis:
    """Build the shared async Redis client (Composition Root only).

    ``decode_responses=False`` keeps the ``bytes`` port contract exact;
    pooling is redis-py's built-in connection pool (one client per process,
    shared across coroutines -- the library's intended usage).
    """
    return Redis.from_url(
        settings.url,
        decode_responses=False,
        socket_timeout=_SOCKET_TIMEOUT_S,
        socket_connect_timeout=_CONNECT_TIMEOUT_S,
    )


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
