"""Live-Redis proof of the ``auth:principal:<sub>`` cache (capacity-plan 1.1).

The hermetic suite (``tests/unit/test_principal_cache.py``,
``tests/unit/test_api_auth.py``) already proves everything about our own
logic — what is stored, what is refused, which routes invalidate. What it
cannot prove is the part of step 1.1's safety argument that belongs to the
SERVER, and that part is not decoration:

1. **The TTL is a real server-side expiry**, not a number we passed and
   forgot. It is the backstop for an invalidation that never happened, so an
   entry Redis silently kept forever would mean a revoked role surviving
   indefinitely — the exact failure the ceiling exists to bound. The
   ``SessionRevocationList`` live test makes the same claim for the same
   reason; this is a cache of authorization facts, so it needs it more.
2. **An invalidation written by one process is seen by another's cache.**
   This is the entire mechanism: the platform-admin route runs on whichever
   replica served the PATCH, and the demoted user's next request lands on any
   of them. Two independent ``PrincipalCache`` instances over two independent
   clients is that topology, minus the process boundary. If it did not hold,
   every "takes effect on the next request" test in the hermetic suite would
   be true of one replica and false of the platform.

Keys are swept per test (the ``test_auth_revocation_live.py`` discipline: the
local Redis is shared and may hold unrelated data), which is why the subjects
below carry a unique prefix instead of a plain uid.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.framework.auth.principal_cache import CachedPrincipal, PrincipalCache
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import RedisCache, create_redis_client

pytestmark = [pytest.mark.live_redis]

_PRINCIPAL = CachedPrincipal(
    workspace_id="018f0000-0000-7000-8000-0000000000w1",
    user_id="018f0000-0000-7000-8000-0000000000u1",
    roles=frozenset({"owner"}),
    active=True,
)


@pytest.fixture
async def subject(redis_client: Redis) -> AsyncIterator[str]:
    """A unique subject, with its cache key swept afterwards."""
    value = f"aizzak-test-{new_uuid7()}"
    try:
        yield value
    finally:
        await redis_client.delete(f"auth:principal:{value}")


async def test_the_entry_carries_a_real_server_side_expiry(
    redis_cache: RedisCache, redis_client: Redis, subject: str
) -> None:
    await PrincipalCache(redis_cache, ttl_s=60).put(subject, _PRINCIPAL)

    ttl = await redis_client.ttl(f"auth:principal:{subject}")
    assert 0 < ttl <= 60


async def test_the_entry_actually_disappears_when_its_ttl_elapses(
    redis_cache: RedisCache, subject: str
) -> None:
    """A one-second cache, so the expiry is observed rather than reasoned
    about: the backstop must actually fire, or an invalidation that failed
    would be wrong forever instead of for a bounded window."""
    principals = PrincipalCache(redis_cache, ttl_s=1)
    await principals.put(subject, _PRINCIPAL)
    assert await principals.get(subject) == _PRINCIPAL

    # Redis expiry is second-granular; poll rather than sleep a fixed margin.
    for _ in range(30):
        await asyncio.sleep(0.1)
        if await principals.get(subject) is None:
            break
    assert await principals.get(subject) is None


async def test_a_principal_survives_the_real_encode_decode_round_trip(
    redis_cache: RedisCache, subject: str
) -> None:
    """Through the actual adapter, whose ``get`` returns raw ``bytes`` from the
    wire rather than the object a dict handed back — the one place the JSON
    encoding could differ from what the unit tests exercise."""
    principals = PrincipalCache(redis_cache)
    await principals.put(subject, _PRINCIPAL)

    assert await principals.get(subject) == _PRINCIPAL


async def test_an_invalidation_by_one_client_is_seen_by_another(
    redis_cache: RedisCache, live_redis: str, subject: str
) -> None:
    """The real topology, and the whole of 1.1's security criterion at the
    platform level: one replica serves the role change, a different one serves
    the demoted user's next request, and they share nothing but the server."""
    reader = PrincipalCache(redis_cache)
    await reader.put(subject, _PRINCIPAL)

    writer_client = create_redis_client(RedisSettings(url=live_redis))
    try:
        await PrincipalCache(RedisCache(writer_client)).invalidate(subject)
    finally:
        await writer_client.aclose()

    assert await reader.get(subject) is None
