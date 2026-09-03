"""Live-Redis proof of the sliding-window limiter (capacity-plan 1.2).

The hermetic suite (``tests/unit/test_rate_limiter.py``,
``tests/unit/test_api_auth.py``) proves the POLICY — which buckets exist, what
a refusal says, what happens when the store does not answer. It cannot prove
anything about the engine, and for this port the engine is where the
guarantees live:

1. **Atomicity.** The whole reason ``try_consume`` is one Lua script is that
   "count, then add" over two round trips lets simultaneous requests both
   observe room and both be admitted. A limit that overshoots under
   concurrency is not a limit, and concurrency is precisely the condition it
   exists for — so the ceiling is asserted here against a real server with
   many requests genuinely in flight at once.
2. **All-or-nothing across buckets.** That a refused request consumed nothing
   is a claim about what is IN Redis afterwards, and only Redis can be asked.
3. **The window really slides**, on the server's own clock, and the key really
   expires. A window that never reopened would be a ban rather than a rate
   limit.
4. **One ceiling across processes.** Two limiter instances over two
   independent clients are the replica topology, minus the process boundary.
   If it did not hold, every ceiling in the hermetic suite would be true of
   one worker and false of the platform.

Every test keys under its own unique prefix and sweeps it (the
``test_auth_revocation_live.py`` discipline: the local Redis is shared and may
hold unrelated data).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.framework.identifiers import new_uuid7
from app.framework.ports.rate_limiter import RateBucket
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.cache.redis_rate_limiter import RedisRateLimiter

pytestmark = [pytest.mark.live_redis]


@pytest.fixture
async def prefix(redis_client: Redis) -> AsyncIterator[str]:
    """A unique key namespace, swept afterwards."""
    value = f"rate:aizzak-test:{new_uuid7()}"
    try:
        yield value
    finally:
        keys = [key async for key in redis_client.scan_iter(match=f"{value}:*")]
        if keys:
            await redis_client.delete(*keys)


def _bucket(scope: str = "user", *, limit: int = 3, window_s: int = 60) -> RateBucket:
    return RateBucket(scope=scope, key=scope, limit=limit, window_s=window_s)


# --------------------------------------------------------------------------- #
# The ceiling itself                                                          #
# --------------------------------------------------------------------------- #
async def test_the_bucket_admits_exactly_its_limit_and_then_refuses(
    redis_client: Redis, prefix: str
) -> None:
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=5)

    for _ in range(5):
        assert (await limiter.try_consume([bucket])).allowed

    verdict = await limiter.try_consume([bucket])
    assert not verdict.allowed
    assert verdict.scope == "user"


async def test_a_refusal_reports_the_bucket_that_bound_not_the_first_one(
    redis_client: Redis, prefix: str
) -> None:
    """The caller turns this into "your user limit" or "your workspace's" —
    two refusals that mean opposite things to the person reading them."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    user = _bucket("user", limit=10)
    workspace = _bucket("workspace", limit=2)

    for _ in range(2):
        assert (await limiter.try_consume([user, workspace])).allowed

    verdict = await limiter.try_consume([user, workspace])
    assert verdict.scope == "workspace"


async def test_a_request_refused_by_the_second_bucket_spends_nothing_from_the_first(
    redis_client: Redis, prefix: str
) -> None:
    """All-or-nothing, asked of the only thing that can answer it. Without
    this, a tenant sitting at its ceiling would silently burn down every one of
    its users' allowances — and when the tenant window reopened, its users
    would still be refused."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    user = _bucket("user", limit=10)
    workspace = _bucket("workspace", limit=1)
    await limiter.try_consume([user, workspace])

    for _ in range(5):
        assert not (await limiter.try_consume([user, workspace])).allowed

    # One admitted request, one entry -- the five refusals cost the user
    # nothing.
    assert await redis_client.zcard(limiter.key_for(user.key)) == 1


async def test_two_keys_are_two_independent_ceilings(redis_client: Redis, prefix: str) -> None:
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    mine = RateBucket(scope="user", key="user:mine", limit=1, window_s=60)
    theirs = RateBucket(scope="user", key="user:theirs", limit=1, window_s=60)

    assert (await limiter.try_consume([mine])).allowed
    assert not (await limiter.try_consume([mine])).allowed
    assert (await limiter.try_consume([theirs])).allowed


# --------------------------------------------------------------------------- #
# Atomicity — the reason this is a script and not three round trips           #
# --------------------------------------------------------------------------- #
async def test_a_burst_of_concurrent_requests_does_not_overshoot_the_ceiling(
    redis_client: Redis, prefix: str
) -> None:
    """Fifty requests genuinely in flight at once against a ceiling of ten.
    "Read the count, then add" would admit more than ten here — every caller
    that read before any of them wrote would see room. Redis runs the script
    end to end with nothing interleaved, so exactly ten get through."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=10)

    verdicts = await asyncio.gather(*(limiter.try_consume([bucket]) for _ in range(50)))

    assert sum(1 for verdict in verdicts if verdict.allowed) == 10
    assert await redis_client.zcard(limiter.key_for(bucket.key)) == 10


async def test_every_admitted_request_is_its_own_entry(redis_client: Redis, prefix: str) -> None:
    """A shared member would make ``ZADD`` overwrite rather than add, and the
    ceiling would then be one request rather than its limit. The member is a
    fresh uuid7 per call for exactly this reason."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=4)

    for _ in range(4):
        await limiter.try_consume([bucket])

    assert await redis_client.zcard(limiter.key_for(bucket.key)) == 4


# --------------------------------------------------------------------------- #
# The window slides, and the key does not outlive it                          #
# --------------------------------------------------------------------------- #
async def test_the_window_reopens_once_its_oldest_entry_ages_out(
    redis_client: Redis, prefix: str
) -> None:
    """A one-second window, so the slide is observed rather than reasoned
    about. This is the difference between a rate limit and a ban."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=2, window_s=1)
    assert (await limiter.try_consume([bucket])).allowed
    assert (await limiter.try_consume([bucket])).allowed
    assert not (await limiter.try_consume([bucket])).allowed

    for _ in range(30):
        await asyncio.sleep(0.1)
        if (await limiter.try_consume([bucket])).allowed:
            break
    else:
        pytest.fail("the window never reopened")


async def test_the_retry_after_points_at_when_capacity_actually_returns(
    redis_client: Redis, prefix: str
) -> None:
    """Read off the oldest surviving score rather than guessed: a hint that
    expired before capacity returned would send the client straight into a
    second refusal, which is how a limiter turns one rejected request into a
    retry loop."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=1, window_s=60)
    await limiter.try_consume([bucket])

    verdict = await limiter.try_consume([bucket])

    assert not verdict.allowed
    # Whole seconds, rounded UP, and the window has only just started.
    assert verdict.retry_after_s == 60


async def test_the_key_carries_a_real_server_side_expiry(redis_client: Redis, prefix: str) -> None:
    """Garbage collection, not the mechanism: a user who stops calling must
    leave no permanent residue in a store every replica shares."""
    limiter = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=3, window_s=60)
    await limiter.try_consume([bucket])

    ttl = await redis_client.ttl(limiter.key_for(bucket.key))
    assert 0 < ttl <= 60


# --------------------------------------------------------------------------- #
# One ceiling across processes — the actual topology                          #
# --------------------------------------------------------------------------- #
async def test_two_limiters_over_two_clients_share_one_ceiling(
    redis_client: Redis, live_redis: str, prefix: str
) -> None:
    """Two replicas, minus the process boundary. This is the whole reason the
    counter is not a dict on one process's heap: gunicorn defaults to two
    workers, so an in-process window would announce 120/min and permit 120 per
    worker per replica."""
    first = RedisRateLimiter(redis_client, key_prefix=prefix)
    bucket = _bucket(limit=2)
    assert (await first.try_consume([bucket])).allowed
    assert (await first.try_consume([bucket])).allowed

    other_client = create_redis_client(RedisSettings(url=live_redis))
    try:
        second = RedisRateLimiter(other_client, key_prefix=prefix)
        assert not (await second.try_consume([bucket])).allowed
    finally:
        await other_client.aclose()
