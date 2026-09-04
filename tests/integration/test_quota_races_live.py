"""Live-Postgres proof of capacity step 2.7 — «مئةُ طلبٍ متزامنٍ على مساحةِ
عملٍ يتبقّى في حصّتها **واحد** يُنتج نجاحاً واحداً بالضبط و99 رفضاً».

WHAT THIS REPLACES, in the two numbers that named the bug. Both ceilings below
were a READ followed by a WRITE with nothing between them, so every request
that started before the first one committed saw a total that excluded it.
Measured on this stack, on a workspace with **one** unit of headroom left and
100 concurrent callers:

* the token quota admitted **46** and the ledger finished at 55 against a
  limit of 10;
* the workspace file cap admitted **30** and finished at 39 against a cap of
  10.

⚠️ And the second number is the one that says how bad it was: 30 was the size
of the connection pool, not a property of the cap. The same run with
``pool_size=5`` admitted exactly 5 and with ``pool_size=30`` admitted 29 —
**the ceilings were being enforced at the width of the pool**, which means
capacity-plan 2.2's planned pool increase (25 → 100) would have made every
one of them four times leakier without touching a line of quota code. That is
why this file sizes its own pool explicitly and asserts the ADMISSION count
rather than "fewer than a hundred".

WHY THESE TESTS CANNOT BE UNIT TESTS. Every claim here is a claim about
PostgreSQL under real concurrency:

1. ``pg_advisory_xact_lock`` actually serialises two transactions of the same
   workspace + ceiling, as ``app_rw`` — a role that is neither superuser nor
   ``BYPASSRLS``;
2. it releases on COMMIT **and on ROLLBACK**, so 99 rejections in a row do not
   wedge the hundredth caller;
3. it does NOT serialise two different ceilings, or two different workspaces,
   which is what keeps a file upload from waiting behind a summary build;
4. READ COMMITTED is what makes the naive version wrong in the first place —
   an in-memory fake has no snapshot and cannot reproduce the defect, so a
   unit test of the same sequence passes either way.

The unit files next door (``test_space_quota.py``, ``test_usage_module.py``)
own the SEQUENCE — that the lock is asked for, under the right ceiling name,
before the count. This file owns the only thing that makes the sequence worth
anything.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings, Limits, UsageSettings
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.quota_lock import AdvisoryQuotaLock
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import FileTransferService, RegisterUpload
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from app.modules.usage.adapters.sql_repository import SqlUsageLedgerRepository
from app.modules.usage.application.use_cases import (
    CaptureUsage,
    CommitReservation,
    EnforceLimit,
    LimitSpec,
    ReleaseReservation,
    ReserveQuota,
    SetLimits,
    UsageEnforcementService,
)
from app.modules.usage.domain.value_objects import LimitScope, Metric, Period
from app.modules.usage.ports.inbound import UsageCharge
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]

# The acceptance line's own number.
_CALLERS = 100
# Small ceilings, so "one unit of headroom" is exact rather than approximate.
_TOKEN_LIMIT = 10
_FILE_CAP = 10
# A pool WIDER than the ceiling and narrower than the caller count, which is
# the shape that made the old bug visible: with `pool_size >= _CALLERS` the
# overrun was total, and with `pool_size == 1` the pool itself did the
# serialising and every version of the code looked correct.
_POOL = {"pool_size": 10, "max_overflow": 20}
# Long enough that nothing here expires mid-test, and the same value the
# Composition Root passes (`Limits.stream_max_duration_s`).
_TTL_S = 600


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


@pytest.fixture
async def pooled_engine(live_db: LiveDbDsns) -> AsyncEngine:
    """The ``app_rw`` engine with a REAL pool, unlike the session-wide
    ``app_engine`` fixture's ``NullPool``.

    Two reasons, and both are about this file specifically. ``NullPool`` opens
    one connection per checkout, so a hundred concurrent callers would open a
    hundred backends against a cluster whose measured ``max_connections`` is
    100 — the test would fail on connection exhaustion long before it could
    say anything about quotas. And the pool's width is the variable the
    original defect scaled with, so pinning it is what makes the "exactly one"
    assertion below a statement about the fix rather than about the harness.
    """
    engine = create_engine(DatabaseSettings(url=live_db.app, **_POOL))  # type: ignore[arg-type]
    try:
        yield engine
    finally:
        await engine.dispose()


def _usage(
    engine: AsyncEngine, *, ttl_s: int = _TTL_S
) -> tuple[UsageEnforcementService, SqlUsageLedgerRepository, ExecutionContext]:
    """The quota seam exactly as ``composition_root._usage_enforcement``
    builds it — one ``TenantSessionFactory`` playing session factory, unit of
    work and lock transport, which is what puts the lock in the same
    transaction as the totals it protects."""
    tenant_session = TenantSessionFactory(create_sessionmaker(engine))
    ledger = SqlUsageLedgerRepository(tenant_session)
    enforce = EnforceLimit(ledger, UsageSettings())
    service = UsageEnforcementService(
        enforce,
        ReserveQuota(enforce, ledger, tenant_session, AdvisoryQuotaLock(tenant_session), ttl_s),
        CommitReservation(ledger, CaptureUsage(ledger), tenant_session),
        ReleaseReservation(ledger),
    )
    return service, ledger, _ctx(new_uuid7())


async def _seed_quota(
    ledger: SqlUsageLedgerRepository, ctx: ExecutionContext, *, spent: int
) -> None:
    """A workspace whose token quota is ``_TOKEN_LIMIT`` and which has already
    spent ``spent`` of it. The cost budget is set out of the way deliberately:
    what is under test is the TOKENS ceiling, and a cost denial would be a
    passing test for the wrong reason."""
    await SetLimits(ledger).execute(
        ctx,
        limits=[
            LimitSpec(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, _TOKEN_LIMIT),
            LimitSpec(LimitScope.WORKSPACE, "*", Metric.COST_MICROS, Period.MONTH, 10**9),
        ],
    )
    if spent:
        await ledger.append(
            ctx,
            UsageCharge(
                agent="a", provider="p", tokens=spent, cost_micros=0, operation_id=new_uuid7()
            ),
        )


# --------------------------------------------------------------------------- #
# (1) ⭐ the acceptance line, on the token quota                               #
# --------------------------------------------------------------------------- #
async def test_a_hundred_callers_on_one_token_of_headroom_produce_exactly_one_admission(
    pooled_engine: AsyncEngine,
) -> None:
    """⭐ «نجاحٌ واحدٌ بالضبط و99 رفضاً». Measured before the fix: 46 and 54."""
    service, ledger, ctx = _usage(pooled_engine)
    await _seed_quota(ledger, ctx, spent=_TOKEN_LIMIT - 1)

    decisions = await asyncio.gather(*(service.reserve(ctx, "a", "p") for _ in range(_CALLERS)))

    admitted = [d for d in decisions if d.allowed]
    assert len(admitted) == 1, f"{len(admitted)} of {_CALLERS} admitted on one token of headroom"
    # An admission is not merely a "yes" -- it is a slot the caller now owes
    # back, and a decision without an id would leave nothing to commit against.
    assert admitted[0].reservation_id is not None
    denials = [d for d in decisions if not d.allowed]
    assert {d.reason for d in denials} == {"quota_exceeded"}
    # `Retry-After` survives the new path (3.79): the denial still knows which
    # period bound it.
    assert all(d.retry_after_s is not None and d.retry_after_s > 0 for d in denials)


async def test_the_ninety_nine_denials_wrote_nothing_at_all(
    pooled_engine: AsyncEngine,
) -> None:
    """«ولا حصّةً سالبة» — the other half of the acceptance line. A denial must
    leave no reservation and no ledger row, or the workspace would be charged
    for the requests it refused."""
    service, ledger, ctx = _usage(pooled_engine)
    await _seed_quota(ledger, ctx, spent=_TOKEN_LIMIT - 1)

    decisions = await asyncio.gather(*(service.reserve(ctx, "a", "p") for _ in range(_CALLERS)))
    winner = next(d for d in decisions if d.allowed)
    assert winner.reservation_id is not None

    # Exactly one token of headroom is held, by exactly one caller.
    assert (await ledger.reserved(ctx, "*", "*")).tokens == 1
    # And the ledger has not moved: reserving is not spending.
    assert (await ledger.rollup(ctx, "*", "*", Period.MONTH.value)).tokens == _TOKEN_LIMIT - 1

    # The winner spends what it actually spent, and the slot goes with it.
    await service.commit(
        ctx,
        winner.reservation_id,
        UsageCharge(agent="a", provider="p", tokens=1, cost_micros=0, operation_id=new_uuid7()),
    )
    assert (await ledger.reserved(ctx, "*", "*")).tokens == 0
    assert (await ledger.rollup(ctx, "*", "*", Period.MONTH.value)).tokens == _TOKEN_LIMIT


# --------------------------------------------------------------------------- #
# (2) the slot's life: held, committed, released, expired                     #
# --------------------------------------------------------------------------- #
async def test_a_held_slot_is_counted_by_the_next_caller_and_a_released_one_is_not(
    pooled_engine: AsyncEngine,
) -> None:
    """The whole mechanism in four calls: the reservation is counted because
    ``EnforceLimit`` adds it into ``current``, and nothing else. No counter, no
    decrement, no number that can drift from the rows it summarises."""
    service, ledger, ctx = _usage(pooled_engine)
    await _seed_quota(ledger, ctx, spent=_TOKEN_LIMIT - 2)  # two tokens of headroom

    first = await service.reserve(ctx, "a", "p")
    second = await service.reserve(ctx, "a", "p")
    third = await service.reserve(ctx, "a", "p")
    assert (first.allowed, second.allowed, third.allowed) == (True, True, False)

    assert first.reservation_id is not None
    await service.release(ctx, first.reservation_id)
    # The freed slot is available immediately -- not at the next period, and
    # not when something sweeps.
    assert (await service.reserve(ctx, "a", "p")).allowed is True


async def test_an_abandoned_slot_stops_costing_headroom_when_it_expires(
    pooled_engine: AsyncEngine,
) -> None:
    """The backstop for the request that never comes back. ``expires_at`` is a
    FILTER, not a sweep: the row stops counting the moment it expires, whether
    or not anything has deleted it yet — so a killed worker costs a workspace
    one slot for one deadline, never forever."""
    service, ledger, ctx = _usage(pooled_engine, ttl_s=1)
    await _seed_quota(ledger, ctx, spent=_TOKEN_LIMIT - 1)

    assert (await service.reserve(ctx, "a", "p")).allowed is True
    assert (await service.reserve(ctx, "a", "p")).allowed is False
    assert (await ledger.reserved(ctx, "*", "*")).tokens == 1

    await asyncio.sleep(1.2)
    assert (await ledger.reserved(ctx, "*", "*")).tokens == 0
    assert (await service.reserve(ctx, "a", "p")).allowed is True


# --------------------------------------------------------------------------- #
# (3) ⭐ the acceptance line again, on the workspace file cap                  #
# --------------------------------------------------------------------------- #
class _StubPresigner:
    """Signs and nothing else — the ``test_space_quota_live.py`` precedent.
    Pulling in a bucket would make a concurrency test fail for object-storage
    reasons."""

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        return f"https://put/{key}"


def _space(workspace_id: str, name: str) -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


async def test_a_hundred_registrations_on_one_free_slot_produce_exactly_one_file(
    pooled_engine: AsyncEngine,
) -> None:
    """⭐ The same acceptance line on the ceiling that is not the step's own
    subject — and the one whose overrun was measured to equal the connection
    pool exactly (30 of 100, with 39 files against a cap of 10).

    ⚠️ **A HUNDRED DISTINCT SPACES**, which is the whole point of the test.
    ``SpaceQuotaService`` already held the SPACE's row still, so registrations
    into ONE space were serialised before 2.7 and would pass this test
    unchanged. The ceiling that was unguarded is the WORKSPACE's, and a row
    lock on one space cannot serialise two registrations into two others.
    """
    tenant_session = TenantSessionFactory(create_sessionmaker(pooled_engine))
    limits = Limits(max_files_per_workspace=_FILE_CAP)
    files = SqlFileRepository(tenant_session)
    spaces = SqlSpaceRepository(tenant_session)
    service = SpaceQuotaService(
        FileTransferService(
            RegisterUpload(files, limits, SpacesQueryService(spaces)),
            files,
            _StubPresigner(),
            put_ttl_s=60,
            get_ttl_s=60,
        ),
        spaces,
        files,
        tenant_session,
        limits,
        AdvisoryQuotaLock(tenant_session),
    )

    ws = new_uuid7()
    ctx = _ctx(ws)
    rooms = [_space(ws, f"s{i}") for i in range(_CALLERS)]
    for room in rooms:
        await spaces.add(ctx, room)
    for i in range(_FILE_CAP - 1):
        await service.register(
            ctx,
            space_id=rooms[0].id,
            name=f"seed{i}.pdf",
            content_type="application/pdf",
            size_bytes=1,
        )
    assert await files.count(ctx) == _FILE_CAP - 1

    async def _register(index: int) -> bool:
        try:
            await service.register(
                ctx,
                space_id=rooms[index].id,
                name=f"race{index}.pdf",
                content_type="application/pdf",
                size_bytes=1,
            )
        except ConflictError:
            return False
        return True

    outcomes = await asyncio.gather(*(_register(i) for i in range(_CALLERS)))

    assert sum(outcomes) == 1, f"{sum(outcomes)} of {_CALLERS} admitted on one free slot"
    assert await files.count(ctx) == _FILE_CAP


# --------------------------------------------------------------------------- #
# (4) the lock itself: what it refuses, and what it deliberately does not      #
# --------------------------------------------------------------------------- #
async def test_the_lock_refuses_to_be_taken_outside_a_unit_of_work(
    pooled_engine: AsyncEngine,
) -> None:
    """A transaction-scoped lock taken in a transaction of its own is acquired
    and released in the same round trip. It reads exactly like a guarded
    ceiling in the source and guards nothing, and the only symptom is an
    over-quota tenant nobody can explain — so the adapter raises instead."""
    tenant_session = TenantSessionFactory(create_sessionmaker(pooled_engine))
    lock = AdvisoryQuotaLock(tenant_session)
    ctx = _ctx(new_uuid7())

    with pytest.raises(AppError) as caught:
        await lock.hold(ctx, "files.max_files_per_workspace")
    assert "unit of work" in str(caught.value)

    # And inside one it is fine, on the same objects.
    async with tenant_session.begin(ctx):
        await lock.hold(ctx, "files.max_files_per_workspace")


async def test_the_lock_serialises_one_ceiling_and_leaves_the_others_alone(
    pooled_engine: AsyncEngine,
) -> None:
    """Contention is scoped to ``(workspace, ceiling)``. Two tenants never wait
    for each other, and a file registration never waits behind a summary build
    — which is why the ceiling NAME is part of the key and why every caller
    passes a constant rather than a literal.

    ``asyncio.timeout`` is what makes the negative claims falsifiable: a lock
    that blocked when it should not would hang this test rather than fail it.
    """
    tenant_session = TenantSessionFactory(create_sessionmaker(pooled_engine))
    lock = AdvisoryQuotaLock(tenant_session)
    held = _ctx(new_uuid7())
    other_workspace = _ctx(new_uuid7())

    async def _take(ctx: ExecutionContext, ceiling: str) -> None:
        async with asyncio.timeout(5), tenant_session.begin(ctx):
            await lock.hold(ctx, ceiling)

    async with tenant_session.begin(held):
        await lock.hold(held, "ceiling.a")
        # A different ceiling in the SAME workspace.
        await _take(held, "ceiling.b")
        # The SAME ceiling in a different workspace.
        await _take(other_workspace, "ceiling.a")


async def test_the_same_ceiling_and_workspace_do_wait_for_each_other(
    pooled_engine: AsyncEngine,
) -> None:
    """The positive claim the three negatives above are worth nothing without:
    a second contender for the same ``(workspace, ceiling)`` BLOCKS until the
    first transaction ends. ``lock_timeout`` turns the wait into a named
    database error instead of a hang, which is what lets the test assert on it
    rather than time out."""
    tenant_session = TenantSessionFactory(create_sessionmaker(pooled_engine))
    lock = AdvisoryQuotaLock(tenant_session)
    ctx = _ctx(new_uuid7())
    blocked = asyncio.Event()

    async def _contend() -> str:
        async with tenant_session.begin(ctx):
            # A short `lock_timeout` (2.6 left `lock_timeout` itself to 2.9;
            # this one is local to the test transaction and bounds only it).
            async with tenant_session(ctx) as session:
                await session.execute(text("SET LOCAL lock_timeout = '400ms'"))
            blocked.set()
            try:
                await lock.hold(ctx, "ceiling.contended")
            except Exception as exc:
                return type(exc).__name__
            return "acquired"

    async with tenant_session.begin(ctx):
        await lock.hold(ctx, "ceiling.contended")
        # A SEPARATE task, and therefore a separate `ContextVar` scope: the
        # unit-of-work session is task-local, so this really is a second
        # transaction and not a re-entry into the first.
        contender = asyncio.create_task(_contend())
        await blocked.wait()
        outcome = await contender

    assert outcome != "acquired", "the second contender was not blocked at all"


async def test_ninety_nine_rejections_in_a_row_do_not_wedge_the_hundredth_caller(
    pooled_engine: AsyncEngine,
) -> None:
    """The lock is released by ROLLBACK as well as by COMMIT, and every one of
    the 99 denials above leaves through a rollback. Without that property the
    first over-quota request would have held the workspace's admissions
    forever — a far worse failure than the overrun this step exists to fix."""
    service, ledger, ctx = _usage(pooled_engine)
    await _seed_quota(ledger, ctx, spent=_TOKEN_LIMIT)  # no headroom at all

    denials = await asyncio.gather(*(service.reserve(ctx, "a", "p") for _ in range(_CALLERS)))
    assert not any(d.allowed for d in denials)

    # And the ceiling still answers -- immediately, and correctly -- afterwards.
    async with asyncio.timeout(5):
        assert (await service.check(ctx, "a", "p")).allowed is False
