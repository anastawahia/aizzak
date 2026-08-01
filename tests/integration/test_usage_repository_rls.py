"""Live-Postgres tests for ``SqlUsageLedgerRepository`` + RLS
(09-testing-strategy §3).

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. The centrepiece is ``append``'s INV-U1/AC-16 contract: an
idempotent replay of the same ``operation_id`` must return ``False`` and
leave BOTH the ledger and every rollup bucket untouched (a duplicate capture
never double-counts). ``append`` has no forged-write vector by construction
(``UsageCharge`` carries no ``workspace_id``; the adapter writes
``ctx.workspace_id``), so the forged-write RLS case is exercised through
``replace_limits`` instead, whose INSERT writes each ``UsageLimit``'s OWN
``workspace_id`` (the media ``add()`` precedent).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.usage.adapters.sql_repository import SqlUsageLedgerRepository
from app.modules.usage.domain.entities import UsageLimit
from app.modules.usage.domain.periods import rollup_buckets
from app.modules.usage.domain.value_objects import LimitScope, Metric, Period
from app.modules.usage.ports.inbound import UsageCharge
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _charge(
    *,
    agent: str = "rag_agent",
    provider: str = "openai",
    tokens: int = 1_000,
    cost_micros: int = 2_500,
    operation_id: str | None = None,
    estimated: bool = False,
) -> UsageCharge:
    return UsageCharge(
        agent=agent,
        provider=provider,
        tokens=tokens,
        cost_micros=cost_micros,
        operation_id=operation_id or new_uuid7(),
        estimated=estimated,
    )


def _limit(
    *,
    workspace_id: str,
    scope: LimitScope = LimitScope.WORKSPACE,
    scope_key: str = "*",
    metric: Metric = Metric.TOKENS,
    period: Period = Period.MONTH,
    limit_value: int = 5_000_000,
    now: datetime | None = None,
) -> UsageLimit:
    created = now or utc_now()
    return UsageLimit(
        id=new_uuid7(),
        workspace_id=workspace_id,
        scope=scope,
        scope_key=scope_key,
        metric=metric,
        period=period,
        limit_value=limit_value,
        created_at=created,
        updated_at=created,
        version=1,
    )


async def _count_as_owner(owner_dsn: str, workspace_id: str, table: str) -> int:
    """Raw owner-side row count, independent of the repository. FORCE ROW
    LEVEL SECURITY binds the owner too, so the tenant GUC is set first on the
    same connection. ``table`` comes only from the literals in this file
    (DDL identifiers cannot be bound parameters)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(text(f"SELECT count(*) AS n FROM {table}"))
            row = result.mappings().first()
            assert row is not None
            return int(row["n"])
    finally:
        await engine.dispose()


async def _rollup_rows_as_owner(owner_dsn: str, workspace_id: str) -> list[RowMapping]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT agent_key, provider, period, tokens_sum, cost_micros_sum"
                    " FROM usage.usage_rollups ORDER BY period, agent_key, provider"
                )
            )
            return list(result.mappings().all())
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1)-(3) append: insert + buckets + THE idempotency contract (AC-16)         #
# --------------------------------------------------------------------------- #
async def test_append_inserts_record_and_creates_all_rollup_buckets(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    charge = _charge(tokens=1_000, cost_micros=2_500)

    assert await repo_usage.append(ctx, charge) is True

    assert await _count_as_owner(live_db.owner, ws, "usage.usage_records") == 1
    # domain-defined bucket fan-out x every Period member -- no hardcoding.
    expected_rows = len(rollup_buckets(charge.agent, charge.provider)) * len(Period)
    rows = await _rollup_rows_as_owner(live_db.owner, ws)
    assert len(rows) == expected_rows
    assert all(r["tokens_sum"] == 1_000 and r["cost_micros_sum"] == 2_500 for r in rows)
    # The workspace-wide bucket reads back through the port too.
    totals = await repo_usage.rollup(ctx, "*", "*", Period.MONTH.value)
    assert (totals.tokens, totals.cost_micros) == (1_000, 2_500)


async def test_append_same_operation_id_is_idempotent_and_never_double_counts(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    """AC-16: the replayed capture returns False and changes NOTHING."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    op = new_uuid7()
    charge = _charge(tokens=700, cost_micros=90, operation_id=op)

    assert await repo_usage.append(ctx, charge) is True
    assert await repo_usage.append(ctx, charge) is False  # replay

    assert await _count_as_owner(live_db.owner, ws, "usage.usage_records") == 1
    totals = await repo_usage.rollup(ctx, "*", "*", Period.MONTH.value)
    assert (totals.tokens, totals.cost_micros) == (700, 90)
    day_totals = await repo_usage.rollup(ctx, "*", "*", Period.DAY.value)
    assert (day_totals.tokens, day_totals.cost_micros) == (700, 90)


async def test_append_distinct_operations_accumulate_additively(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)

    assert await repo_usage.append(ctx, _charge(agent="rag_agent", tokens=100, cost_micros=10))
    assert await repo_usage.append(ctx, _charge(agent="image_agent", tokens=40, cost_micros=4))

    workspace_wide = await repo_usage.rollup(ctx, "*", "*", Period.MONTH.value)
    assert (workspace_wide.tokens, workspace_wide.cost_micros) == (140, 14)
    per_agent = await repo_usage.rollup(ctx, "rag_agent", "*", Period.MONTH.value)
    assert (per_agent.tokens, per_agent.cost_micros) == (100, 10)
    other_agent = await repo_usage.rollup(ctx, "image_agent", "*", Period.MONTH.value)
    assert (other_agent.tokens, other_agent.cost_micros) == (40, 4)


async def test_rollup_returns_zeros_for_a_bucket_that_never_recorded(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ctx = _ctx(new_uuid7())
    totals = await repo_usage.rollup(ctx, "nonexistent_agent", "*", Period.DAY.value)
    assert (totals.tokens, totals.cost_micros) == (0, 0)


# --------------------------------------------------------------------------- #
# (4)-(6) limits: round-trip + whole-set replacement semantics                #
# --------------------------------------------------------------------------- #
async def test_replace_limits_then_get_limits_round_trips(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    tokens_limit = _limit(workspace_id=ws, metric=Metric.TOKENS, limit_value=5_000_000)
    budget_limit = _limit(workspace_id=ws, metric=Metric.COST_MICROS, limit_value=50_000_000)

    await repo_usage.replace_limits(ctx, [tokens_limit, budget_limit])
    stored = await repo_usage.get_limits(ctx)

    assert {x.id for x in stored} == {tokens_limit.id, budget_limit.id}
    by_id = {x.id: x for x in stored}
    round_tripped = by_id[tokens_limit.id]
    assert round_tripped.scope is LimitScope.WORKSPACE
    assert round_tripped.scope_key == "*"
    assert round_tripped.metric is Metric.TOKENS
    assert round_tripped.period is Period.MONTH
    assert round_tripped.limit_value == 5_000_000
    assert round_tripped.version == 1


async def test_replace_limits_replaces_the_whole_set(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    await repo_usage.replace_limits(
        ctx,
        [
            _limit(workspace_id=ws, metric=Metric.TOKENS),
            _limit(workspace_id=ws, metric=Metric.COST_MICROS),
        ],
    )
    replacement = _limit(
        workspace_id=ws, scope=LimitScope.AGENT, scope_key="rag_agent", period=Period.DAY
    )

    await repo_usage.replace_limits(ctx, [replacement])
    stored = await repo_usage.get_limits(ctx)

    assert [x.id for x in stored] == [replacement.id]
    assert stored[0].scope is LimitScope.AGENT
    assert stored[0].scope_key == "rag_agent"


async def test_replace_limits_with_empty_set_clears_configuration(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    await repo_usage.replace_limits(ctx, [_limit(workspace_id=ws)])

    await repo_usage.replace_limits(ctx, [])

    assert await repo_usage.get_limits(ctx) == []


# --------------------------------------------------------------------------- #
# (7) list_rollups                                                            #
# --------------------------------------------------------------------------- #
async def test_list_rollups_returns_every_bucket_for_the_period(
    repo_usage: SqlUsageLedgerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    charge = _charge(agent="rag_agent", provider="openai", tokens=55, cost_micros=5)
    await repo_usage.append(ctx, charge)

    rollups = await repo_usage.list_rollups(ctx, Period.MONTH.value)

    assert {(r.agent_key, r.provider) for r in rollups} == set(
        rollup_buckets(charge.agent, charge.provider)
    )
    assert all(r.workspace_id == ws and r.period is Period.MONTH for r in rollups)
    assert all((r.tokens_sum, r.cost_micros_sum) == (55, 5) for r in rollups)


# --------------------------------------------------------------------------- #
# (8)-(10) RLS: no context / empty-string GUC / tenant isolation              #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_sees_zero_rows(
    repo_usage: SqlUsageLedgerRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _ctx(new_uuid7())
    await repo_usage.append(ctx, _charge())
    await repo_usage.replace_limits(ctx, [_limit(workspace_id=ctx.workspace_id)])

    async with sessionmaker_app() as session:  # no GUC ever set
        for table in ("usage.usage_records", "usage.usage_rollups", "usage.limits"):
            result = await session.execute(text(f"SELECT count(*) AS n FROM {table}"))
            assert result.scalar_one() == 0


async def test_empty_string_guc_sees_zero_rows_without_error(
    repo_usage: SqlUsageLedgerRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """The NULLIF-hardened policy degrades '' to no-context (0 rows), never
    to a 22P02 cast error -- the pooled-connection failure mode."""
    ctx = _ctx(new_uuid7())
    await repo_usage.append(ctx, _charge())

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        result = await session.execute(text("SELECT count(*) AS n FROM usage.usage_records"))
        assert result.scalar_one() == 0


async def test_two_tenant_isolation_across_records_rollups_and_limits(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a, ctx_b = _ctx(ws_a), _ctx(ws_b)
    await repo_usage.append(ctx_a, _charge(tokens=111, cost_micros=11))
    await repo_usage.append(ctx_b, _charge(tokens=222, cost_micros=22))
    await repo_usage.replace_limits(ctx_a, [_limit(workspace_id=ws_a)])

    a_totals = await repo_usage.rollup(ctx_a, "*", "*", Period.MONTH.value)
    b_totals = await repo_usage.rollup(ctx_b, "*", "*", Period.MONTH.value)
    assert (a_totals.tokens, b_totals.tokens) == (111, 222)
    assert await repo_usage.get_limits(ctx_b) == []
    assert {r.workspace_id for r in await repo_usage.list_rollups(ctx_a, Period.MONTH.value)} == {
        ws_a
    }
    assert await _count_as_owner(live_db.owner, ws_a, "usage.usage_records") == 1
    assert await _count_as_owner(live_db.owner, ws_b, "usage.usage_records") == 1


# --------------------------------------------------------------------------- #
# (11) forged cross-tenant limit -> RLS WITH CHECK rejects                    #
# --------------------------------------------------------------------------- #
async def test_forged_cross_tenant_limit_is_rejected_by_rls_with_check(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    forged = _limit(workspace_id=ws_victim)  # claims the victim's tenant

    with pytest.raises(AppError) as excinfo:
        await repo_usage.replace_limits(_ctx(ws_attacker), [forged])

    assert not isinstance(excinfo.value, ConflictError)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500
    assert await _count_as_owner(live_db.owner, ws_victim, "usage.limits") == 0
    assert await _count_as_owner(live_db.owner, ws_attacker, "usage.limits") == 0


# --------------------------------------------------------------------------- #
# 4.7-c-1 -- the `estimated` marker survives the round trip to the ledger      #
# --------------------------------------------------------------------------- #
async def _estimated_flags_as_owner(owner_dsn: str, workspace_id: str) -> list[bool]:
    """Read the raw ``estimated`` column, owner-side, independent of the
    repository — the column is what the audit trail actually rests on, so a
    test that only round-tripped through the adapter's own mapping could not
    tell a persisted value from a returned one."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT estimated FROM usage.usage_records ORDER BY created_at")
            )
            return [bool(r["estimated"]) for r in result.mappings().all()]
    finally:
        await engine.dispose()


async def test_append_persists_an_estimated_charge_as_estimated(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    """The marker 4.7-a's billing decision turns on: a turn whose provider
    reported no counters is billed on an ESTIMATE, and the ledger must say so
    — otherwise a measured row and a guessed row are byte-identical and no
    operator auditing a bill can tell them apart."""
    ws = new_uuid7()
    ctx = _ctx(ws)

    assert await repo_usage.append(ctx, _charge(tokens=1_000, estimated=True)) is True

    assert await _estimated_flags_as_owner(live_db.owner, ws) == [True]


async def test_append_defaults_to_measured_not_estimated(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    """``estimated`` defaults to ``False`` — the honest value, since a caller
    that says nothing is reporting a measured count. Defaulting the other way
    would quietly relabel every exact charge as a guess."""
    ws = new_uuid7()
    ctx = _ctx(ws)

    assert await repo_usage.append(ctx, _charge(tokens=1_000)) is True

    assert await _estimated_flags_as_owner(live_db.owner, ws) == [False]


async def test_measured_and_estimated_rows_coexist_distinguishably(
    repo_usage: SqlUsageLedgerRepository, live_db: LiveDbDsns
) -> None:
    """The whole point of the column, on one workspace's ledger: the two kinds
    of row remain separable after the fact."""
    ws = new_uuid7()
    ctx = _ctx(ws)

    await repo_usage.append(ctx, _charge(tokens=100, estimated=False))
    await repo_usage.append(ctx, _charge(tokens=200, estimated=True))

    assert sorted(await _estimated_flags_as_owner(live_db.owner, ws)) == [False, True]
    # Both still count toward the quota -- an estimated charge is NOT a free
    # one (see `UsageCharge.estimated`): enforcement must not branch on it.
    totals = await repo_usage.rollup(ctx, "*", "*", Period.MONTH.value)
    assert totals.tokens == 300
