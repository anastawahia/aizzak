"""Live-Redis tests for ``RedisCache`` (02-port-contracts §1.7, Phase 2.3).

Runs against a real, local Redis 7 (no Docker -- see
``tests/integration/conftest.py``); auto-skips via ``live_redis`` when
unreachable. Unlike the Postgres harness there is NO flush/rebuild: the
server may hold unrelated data, so every test keys under its own unique
``aizzak-test:<uuid7>:`` prefix and deletes what it wrote.

Behaviours pinned here beyond the plain round-trip: raw non-UTF-8 ``bytes``
survive verbatim (``decode_responses=False``); ``ttl_s`` maps to a real
server-side expiry (both the TTL value and the actual disappearance are
asserted -- the ``integrations`` OAuth-state binding depends on real
expiry); ``delete``/``expire`` are silently idempotent on missing keys;
``incr`` initializes at ``amount`` (fixed-window counter semantics) and
interoperates with an integer previously ``set`` as bytes; and every driver
failure surfaces as the framework's ``common.internal`` -- never a silent
miss and never a raw ``redis`` exception (the adapter's fail-closed policy;
OAuth state must not mistake an outage for a cache miss).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import RedisCache, create_redis_client

pytestmark = [pytest.mark.live_redis]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
@pytest.fixture
async def key_prefix(redis_client: Redis) -> AsyncIterator[str]:
    """A unique per-test namespace, swept after the test so the shared local
    Redis never accumulates suite leftovers."""
    prefix = f"aizzak-test:{new_uuid7()}:"
    try:
        yield prefix
    finally:
        async for key in redis_client.scan_iter(match=f"{prefix}*"):
            await redis_client.delete(key)


# --------------------------------------------------------------------------- #
# (1)-(3) get/set round-trip                                                  #
# --------------------------------------------------------------------------- #
async def test_set_then_get_round_trips_raw_bytes(redis_cache: RedisCache, key_prefix: str) -> None:
    key = key_prefix + "blob"
    payload = b"\x00\xff\xfe binary \xd8\x00 not-utf8"

    await redis_cache.set(key, payload)

    assert await redis_cache.get(key) == payload


async def test_get_missing_key_returns_none(redis_cache: RedisCache, key_prefix: str) -> None:
    assert await redis_cache.get(key_prefix + "never-written") is None


async def test_set_overwrites_existing_value(redis_cache: RedisCache, key_prefix: str) -> None:
    key = key_prefix + "v"
    await redis_cache.set(key, b"first")

    await redis_cache.set(key, b"second")

    assert await redis_cache.get(key) == b"second"


# --------------------------------------------------------------------------- #
# (4)-(6) TTL semantics                                                       #
# --------------------------------------------------------------------------- #
async def test_set_with_ttl_applies_a_server_side_expiry(
    redis_cache: RedisCache, redis_client: Redis, key_prefix: str
) -> None:
    key = key_prefix + "ttl"

    await redis_cache.set(key, b"x", ttl_s=600)

    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 600
    assert await redis_cache.get(key) == b"x"  # readable until expiry


async def test_set_without_ttl_persists_without_expiry(
    redis_cache: RedisCache, redis_client: Redis, key_prefix: str
) -> None:
    key = key_prefix + "forever"

    await redis_cache.set(key, b"x")

    assert await redis_client.ttl(key) == -1  # exists, no expiry


async def test_value_actually_disappears_after_expiry(
    redis_cache: RedisCache, key_prefix: str
) -> None:
    """The OAuth-state contract: a 1s-TTL value must be truly gone after
    ~1.2s -- real server-side expiry, not a client-side illusion."""
    key = key_prefix + "shortlived"
    await redis_cache.set(key, b"one-shot", ttl_s=1)
    assert await redis_cache.get(key) == b"one-shot"

    await asyncio.sleep(1.2)

    assert await redis_cache.get(key) is None


# --------------------------------------------------------------------------- #
# (7) delete                                                                  #
# --------------------------------------------------------------------------- #
async def test_delete_removes_and_is_idempotent(redis_cache: RedisCache, key_prefix: str) -> None:
    key = key_prefix + "gone"
    await redis_cache.set(key, b"x")

    await redis_cache.delete(key)
    assert await redis_cache.get(key) is None

    await redis_cache.delete(key)  # second delete: silent no-op, no error


# --------------------------------------------------------------------------- #
# (8)-(9) incr                                                                #
# --------------------------------------------------------------------------- #
async def test_incr_initializes_at_amount_and_accumulates(
    redis_cache: RedisCache, key_prefix: str
) -> None:
    key = key_prefix + "counter"

    assert await redis_cache.incr(key) == 1  # missing key starts at 0
    assert await redis_cache.incr(key) == 2
    assert await redis_cache.incr(key, amount=5) == 7


async def test_incr_interoperates_with_an_integer_set_as_bytes(
    redis_cache: RedisCache, key_prefix: str
) -> None:
    key = key_prefix + "seeded-counter"
    await redis_cache.set(key, b"10")

    assert await redis_cache.incr(key) == 11


# --------------------------------------------------------------------------- #
# (10)-(11) expire                                                            #
# --------------------------------------------------------------------------- #
async def test_expire_adds_a_ttl_to_a_persistent_key(
    redis_cache: RedisCache, redis_client: Redis, key_prefix: str
) -> None:
    key = key_prefix + "later"
    await redis_cache.set(key, b"x")  # no ttl
    assert await redis_client.ttl(key) == -1

    await redis_cache.expire(key, 600)

    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 600


async def test_expire_on_missing_key_is_a_silent_noop(
    redis_cache: RedisCache, key_prefix: str
) -> None:
    await redis_cache.expire(key_prefix + "never-written", 60)  # no error


# --------------------------------------------------------------------------- #
# (12) failure translation: never a raw driver error, never a silent miss    #
# --------------------------------------------------------------------------- #
async def test_connection_failure_surfaces_as_common_internal() -> None:
    # A closed local port refuses instantly (ECONNREFUSED) -- no timeout wait.
    dead = create_redis_client(RedisSettings(url="redis://127.0.0.1:6399/0"))
    cache = RedisCache(dead)
    try:
        with pytest.raises(AppError) as excinfo:
            await cache.get("any-key")
        assert excinfo.value.code == "common.internal"
        assert excinfo.value.status == 500
    finally:
        await dead.aclose()
