"""The two-bucket policy — ``api/middleware/rate_limit.py`` (capacity-plan 1.2).

Hermetic, over ``InMemoryRateLimiter``: what is under test here is the POLICY,
not the Redis script. Which buckets exist, in which order, what a refusal says
to the client, and what happens when the store does not answer at all.

The engine's own guarantees — atomicity across processes, a sliding window a
real server evicts, an exact ``Retry-After`` — belong to
``tests/integration/test_rate_limiter_live.py``, where there is a real Redis to
break them.
"""

from __future__ import annotations

import pytest

from app.api.middleware.rate_limit import USER_SCOPE, WORKSPACE_SCOPE, ApiRateLimiter
from app.framework.errors import AppError, RateLimitedError, ValidationError
from tests.unit.support_rate_limit import InMemoryRateLimiter

_USER = "018f0000-0000-7000-8000-0000000000u1"
_OTHER_USER = "018f0000-0000-7000-8000-0000000000u2"
_WORKSPACE = "018f0000-0000-7000-8000-0000000000w1"
_OTHER_WORKSPACE = "018f0000-0000-7000-8000-0000000000w2"


def _limiter(
    fake: InMemoryRateLimiter, *, user_per_min: int = 120, workspace_per_min: int = 2400
) -> ApiRateLimiter:
    return ApiRateLimiter(fake, user_per_min=user_per_min, workspace_per_min=workspace_per_min)


async def _spend(limiter: ApiRateLimiter, times: int, *, user: str = _USER) -> None:
    for _ in range(times):
        await limiter.check(user_id=user, workspace_id=_WORKSPACE)


# --------------------------------------------------------------------------- #
# What the two buckets ARE                                                    #
# --------------------------------------------------------------------------- #
async def test_one_request_consumes_from_the_user_and_the_workspace_at_once() -> None:
    """Both ceilings in ONE call, which is what makes them all-or-nothing: two
    separate calls could not refuse the second without having spent the
    first."""
    fake = InMemoryRateLimiter()

    await _limiter(fake).check(user_id=_USER, workspace_id=_WORKSPACE)

    assert len(fake.calls) == 1
    assert [bucket.scope for bucket in fake.calls[0]] == [USER_SCOPE, WORKSPACE_SCOPE]


async def test_the_user_bucket_is_evaluated_first() -> None:
    """A single abusive client must be told it is over ITS limit. Reporting
    the tenant ceiling instead would point an operator at the workspace and at
    every innocent colleague inside it."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=2, workspace_per_min=2)

    await _spend(limiter, 2)

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)
    assert "user" in str(refusal.value)


async def test_the_buckets_carry_the_minute_window_07_declares() -> None:
    fake = InMemoryRateLimiter()

    await _limiter(fake).check(user_id=_USER, workspace_id=_WORKSPACE)

    assert [bucket.window_s for bucket in fake.calls[0]] == [60, 60]


async def test_the_configured_ceilings_are_what_reach_the_buckets() -> None:
    fake = InMemoryRateLimiter()

    await _limiter(fake, user_per_min=7, workspace_per_min=9).check(
        user_id=_USER, workspace_id=_WORKSPACE
    )

    assert [bucket.limit for bucket in fake.calls[0]] == [7, 9]


async def test_two_users_in_one_workspace_do_not_share_a_user_bucket() -> None:
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=1, workspace_per_min=100)

    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)
    await limiter.check(user_id=_OTHER_USER, workspace_id=_WORKSPACE)

    with pytest.raises(RateLimitedError):
        await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)


async def test_one_workspace_at_its_ceiling_does_not_refuse_another() -> None:
    """Tenant isolation stated as the property it is: the ceiling exists to
    stop a noisy tenant from spending the platform, not to couple tenants."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=100, workspace_per_min=1)

    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    with pytest.raises(RateLimitedError):
        await limiter.check(user_id=_OTHER_USER, workspace_id=_WORKSPACE)
    await limiter.check(user_id=_OTHER_USER, workspace_id=_OTHER_WORKSPACE)


# --------------------------------------------------------------------------- #
# The refusal a client actually receives                                      #
# --------------------------------------------------------------------------- #
async def test_the_user_ceiling_refuses_with_a_retry_after_and_the_catalog_code() -> None:
    """`common.rate_limited`/429 is what `api/main.py` renders as RFC 9457,
    and the hint is what it renders as the RFC 9110 header — the producer 03
    §4 said had none."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=1)
    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)
    fake.now_ms = 20_000

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    assert refusal.value.code == "common.rate_limited"
    assert refusal.value.status == 429
    # 60s window, 20s spent: the oldest entry leaves in 40.
    assert refusal.value.retry_after_s == 40


async def test_the_workspace_ceiling_says_workspace_and_not_user() -> None:
    """The scope is named because the two refusals mean opposite things to the
    person reading them: "slow down" versus "your tenant is at its ceiling"."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=100, workspace_per_min=1)
    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_OTHER_USER, workspace_id=_WORKSPACE)

    assert "workspace" in str(refusal.value)


async def test_a_refusal_names_no_identifier_and_no_ceiling() -> None:
    """A refused caller learns which of its own limits it hit and nothing
    else: not the tenant it shares, not how much room the platform has left."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=1)
    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    detail = str(refusal.value)
    assert _USER not in detail
    assert _WORKSPACE not in detail
    assert "1" not in detail


async def test_a_window_that_has_rolled_admits_again() -> None:
    """A ceiling that never reopened would be a ban, and the sliding window is
    what makes the difference observable rather than argued."""
    fake = InMemoryRateLimiter()
    limiter = _limiter(fake, user_per_min=1)
    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)

    fake.now_ms = 60_001

    await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)


# --------------------------------------------------------------------------- #
# ⚠️ Fail OPEN — the policy that is the opposite of the denylist's           #
# --------------------------------------------------------------------------- #
async def test_a_store_outage_admits_the_request_rather_than_refusing_it() -> None:
    """1.2 in as many words. A rate limiter protects capacity, never
    correctness, so failing closed would turn a degraded Redis into a total
    outage of a platform that was otherwise able to serve. nginx's per-IP
    ceiling stays underneath, and it needs no Redis at all."""
    fake = InMemoryRateLimiter()
    fake.failure = AppError("redis is unreachable", code="common.internal")

    await _limiter(fake).check(user_id=_USER, workspace_id=_WORKSPACE)


async def test_the_outage_does_not_leak_the_stores_error_to_the_caller() -> None:
    """Not merely "does not refuse": it must not raise AT ALL, or the request
    would fail with a 500 instead — the same outage wearing a different
    status."""
    fake = InMemoryRateLimiter()
    fake.failure = AppError("boom", code="common.internal")
    limiter = _limiter(fake)

    for _ in range(3):
        await limiter.check(user_id=_USER, workspace_id=_WORKSPACE)


# --------------------------------------------------------------------------- #
# Construction                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("per_min", [0, -1])
def test_a_non_positive_user_ceiling_is_refused_at_construction(per_min: int) -> None:
    """Zero would not mean "off" — it would refuse every request in the
    platform, with a perfectly well-formed 429. Off is a WIRING decision
    (`API_RATE_LIMIT_ENABLED=false` builds no limiter at all)."""
    with pytest.raises(ValidationError):
        ApiRateLimiter(InMemoryRateLimiter(), user_per_min=per_min, workspace_per_min=2400)


@pytest.mark.parametrize("per_min", [0, -1])
def test_a_non_positive_workspace_ceiling_is_refused_at_construction(per_min: int) -> None:
    with pytest.raises(ValidationError):
        ApiRateLimiter(InMemoryRateLimiter(), user_per_min=120, workspace_per_min=per_min)


def test_the_ceilings_are_readable_back() -> None:
    limiter = ApiRateLimiter(InMemoryRateLimiter(), user_per_min=120, workspace_per_min=2400)

    assert (limiter.user_per_min, limiter.workspace_per_min) == (120, 2400)
