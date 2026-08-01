"""Live-Redis proof of the ``auth:revoked:<sub>`` denylist (3.79).

Two claims the hermetic suite cannot make, both about the SERVER rather than
about our logic:

1. **The TTL is a real server-side expiry**, not a number we passed and
   forgot. This control's entire safety argument is that an entry outlives
   every token it could deny and then goes away on its own — an entry that
   never expired would lock a legitimate re-login out permanently, and one
   that Redis silently ignored would leave the denial in place for the life of
   the key.
2. **A revocation is visible to a DIFFERENT process's list instance.** The
   writer is ``python -m app.ops.revoke`` and the reader is the API replica;
   they never share an object, only a Redis. Two independent
   ``SessionRevocationList`` instances over two independent clients is that
   topology, minus the process boundary.

Keys are swept per test (the ``test_redis_cache.py`` discipline: the local
Redis is shared and may hold unrelated data), which is why the subjects below
carry a unique prefix instead of a plain uid.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.framework.auth.revocation import MAX_REVOCATION_TTL_S, SessionRevocationList
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import RedisCache, create_redis_client

pytestmark = [pytest.mark.live_redis]


@pytest.fixture
async def subject(redis_client: Redis) -> AsyncIterator[str]:
    """A unique subject, with its denylist key swept afterwards."""
    value = f"aizzak-test-{new_uuid7()}"
    try:
        yield value
    finally:
        await redis_client.delete(f"auth:revoked:{value}")


async def test_the_entry_carries_a_real_server_side_expiry(
    redis_cache: RedisCache, redis_client: Redis, subject: str
) -> None:
    await SessionRevocationList(redis_cache).revoke(subject)

    ttl = await redis_client.ttl(f"auth:revoked:{subject}")
    assert 0 < ttl <= MAX_REVOCATION_TTL_S


async def test_the_entry_actually_disappears_when_its_ttl_elapses(
    redis_cache: RedisCache, subject: str
) -> None:
    """A one-second list, so the expiry is observed rather than reasoned
    about: the denial must lift on its own, or a revoked user could never log
    in again."""
    revocations = SessionRevocationList(redis_cache, ttl_s=1)
    await revocations.revoke(subject)
    assert await revocations.is_revoked(subject) is True

    # Redis expiry is second-granular; poll rather than sleep a fixed margin.
    for _ in range(30):
        await asyncio.sleep(0.1)
        if not await revocations.is_revoked(subject):
            break
    assert await revocations.is_revoked(subject) is False


async def test_a_revocation_written_by_one_client_is_seen_by_another(
    redis_cache: RedisCache, live_redis: str, subject: str
) -> None:
    """The real topology: the ops command writes, an API replica reads, and
    they share nothing but the server."""
    writer_client = create_redis_client(RedisSettings(url=live_redis))
    try:
        await SessionRevocationList(RedisCache(writer_client)).revoke(subject)
    finally:
        await writer_client.aclose()

    assert await SessionRevocationList(redis_cache).is_revoked(subject) is True
