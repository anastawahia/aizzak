"""SQL adapter for ``UsageLedgerRepository`` (02-port-contracts §2;
01-data-model §2.10; ``migrations/versions/usage/0001_usage.py``).

Declares its own local Core ``Table``\\ s against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below.

``append`` is the module's one binding contract (``ports/repository.py``,
INV-U1): one transaction (one ``tenant_session`` call) that inserts the
ledger row via ``ON CONFLICT (workspace_id, operation_id) DO NOTHING`` —
this adapter mints ``id``/``created_at`` itself (``UsageRecord``'s own
docstring: unlike every other aggregate, its identity is never minted by the
application layer) — and, ONLY if that insert actually happened (a
``RETURNING`` row came back), upserts ``domain.periods.rollup_buckets
(charge.agent, charge.provider)`` x every ``Period`` member ("day"/"month"
-- the port docstring's "each configured rollup period"), keyed by
``domain.periods.period_start``, via ``ON CONFLICT (...) DO UPDATE SET
tokens_sum = tokens_sum + EXCLUDED.tokens_sum, cost_micros_sum =
cost_micros_sum + EXCLUDED.cost_micros_sum`` — a replayed duplicate capture
never double-counts (AC-16). ``usage_records`` is append-only (DAT-07): no
update/delete method here, no ``version`` column.

``replace_limits`` writes each ``UsageLimit``'s OWN ``workspace_id`` (not
``ctx.workspace_id``) on the INSERT half of its delete-then-insert -- the
``add()`` forged-write-guard precedent (media/credentials/... et al.): a
caller-forged cross-tenant ``UsageLimit`` is rejected by the RLS ``WITH
CHECK`` clause, not silently persisted under ``ctx``'s tenant. The preceding
``DELETE`` stays strictly ``WHERE workspace_id = ctx.workspace_id`` (defence
in depth), so a forged batch can never delete another tenant's limits either.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    delete,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.modules.usage.domain.entities import UsageLimit
from app.modules.usage.domain.periods import period_start, rollup_buckets
from app.modules.usage.domain.read_models import UsageRollup
from app.modules.usage.domain.value_objects import LimitScope, Metric, Period
from app.modules.usage.ports.inbound import UsageCharge
from app.modules.usage.ports.repository import UsageTotals

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

usage_records = Table(
    "usage_records",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("agent_key", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("tokens", BigInteger, nullable=False),
    Column("cost_micros", BigInteger, nullable=False),
    Column("operation_id", _uuid_col, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    # 4.7-c-1: was `tokens` measured or estimated? See `UsageCharge.estimated`.
    Column("estimated", Boolean, nullable=False),
    schema="usage",
)

# Composite PK (no surrogate `id`, 01 §2.10) — every column that makes up the
# bucket's identity is `primary_key=True`.
usage_rollups = Table(
    "usage_rollups",
    _metadata,
    Column("workspace_id", _uuid_col, primary_key=True),
    Column("agent_key", Text, primary_key=True),
    Column("provider", Text, primary_key=True),
    Column("period", Text, primary_key=True),
    Column("period_start", Date, primary_key=True),
    Column("tokens_sum", BigInteger, nullable=False),
    Column("cost_micros_sum", BigInteger, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    schema="usage",
)

# DB table name is `usage.limits`; the Python identifier is `usage_limits`
# only to avoid shadowing the `limits` parameter name used throughout.
usage_limits = Table(
    "limits",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("scope", Text, nullable=False),
    Column("scope_key", Text, nullable=False),
    Column("metric", Text, nullable=False),
    Column("period", Text, nullable=False),
    Column("limit_value", BigInteger, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="usage",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]

_ROLLUP_INDEX_ELEMENTS = (
    usage_rollups.c.workspace_id,
    usage_rollups.c.agent_key,
    usage_rollups.c.provider,
    usage_rollups.c.period,
    usage_rollups.c.period_start,
)


class SqlUsageLedgerRepository:
    """SQL ``UsageLedgerRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Every method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — except ``append``, whose ledger INSERT and
    rollup upserts share ONE transaction (INV-U1), and ``replace_limits``,
    whose DELETE + INSERT share one transaction (whole-set replacement).
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def append(self, ctx: ExecutionContext, charge: UsageCharge) -> bool:
        now = utc_now()
        insert_record = (
            pg_insert(usage_records)
            .values(
                id=new_uuid7(),
                workspace_id=ctx.workspace_id,
                agent_key=charge.agent,
                provider=charge.provider,
                tokens=charge.tokens,
                cost_micros=charge.cost_micros,
                operation_id=charge.operation_id,
                created_at=now,
                estimated=charge.estimated,
            )
            .on_conflict_do_nothing(
                index_elements=[usage_records.c.workspace_id, usage_records.c.operation_id]
            )
            .returning(usage_records.c.id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                inserted = (await session.execute(insert_record)).first()
                if inserted is None:
                    return False  # duplicate operation_id (INV-U1): rollups untouched
                for period in Period:
                    bucket_start = period_start(period, now)
                    for agent_bucket, provider_bucket in rollup_buckets(
                        charge.agent, charge.provider
                    ):
                        await session.execute(
                            _rollup_upsert(
                                ctx.workspace_id,
                                agent_bucket,
                                provider_bucket,
                                period,
                                bucket_start,
                                charge,
                            )
                        )
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return True

    async def rollup(
        self, ctx: ExecutionContext, agent: str, provider: str, period: str
    ) -> UsageTotals:
        bucket_start = period_start(Period(period), utc_now())
        stmt = select(usage_rollups.c.tokens_sum, usage_rollups.c.cost_micros_sum).where(
            usage_rollups.c.workspace_id == ctx.workspace_id,
            usage_rollups.c.agent_key == agent,
            usage_rollups.c.provider == provider,
            usage_rollups.c.period == period,
            usage_rollups.c.period_start == bucket_start,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            return UsageTotals(0, 0)
        return UsageTotals(tokens=row.tokens_sum, cost_micros=row.cost_micros_sum)

    async def get_limits(self, ctx: ExecutionContext) -> list[UsageLimit]:
        stmt = select(usage_limits).where(usage_limits.c.workspace_id == ctx.workspace_id)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_limit(row) for row in rows]

    async def replace_limits(self, ctx: ExecutionContext, limits: Sequence[UsageLimit]) -> None:
        # Whole-set replacement (port docstring): DELETE this workspace's
        # entire configured set, then INSERT the replacement -- one
        # transaction, so a reader never observes a partially-replaced set.
        delete_stmt = delete(usage_limits).where(usage_limits.c.workspace_id == ctx.workspace_id)
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(delete_stmt)
                if limits:
                    await session.execute(
                        insert(usage_limits).values([_limit_row(x) for x in limits])
                    )
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def list_rollups(self, ctx: ExecutionContext, period: str) -> Sequence[UsageRollup]:
        stmt = select(usage_rollups).where(
            usage_rollups.c.workspace_id == ctx.workspace_id, usage_rollups.c.period == period
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_rollup(row) for row in rows]


def _rollup_upsert(
    workspace_id: str,
    agent_bucket: str,
    provider_bucket: str,
    period: Period,
    bucket_start: date,
    charge: UsageCharge,
) -> PgInsert:
    """One bucket's ``ON CONFLICT (...) DO UPDATE`` upsert (port docstring,
    verbatim): a fresh bucket starts at ``charge``'s own totals; an existing
    one accumulates them additively. ``updated_at`` is left for
    ``platform.touch_updated_at()`` (the migration's trigger unconditionally
    overwrites it on every UPDATE, including the update half of an upsert)."""
    stmt = pg_insert(usage_rollups).values(
        workspace_id=workspace_id,
        agent_key=agent_bucket,
        provider=provider_bucket,
        period=period.value,
        period_start=bucket_start,
        tokens_sum=charge.tokens,
        cost_micros_sum=charge.cost_micros,
    )
    return stmt.on_conflict_do_update(
        index_elements=_ROLLUP_INDEX_ELEMENTS,
        set_={
            "tokens_sum": usage_rollups.c.tokens_sum + stmt.excluded.tokens_sum,
            "cost_micros_sum": usage_rollups.c.cost_micros_sum + stmt.excluded.cost_micros_sum,
        },
    )


def _limit_row(limit: UsageLimit) -> dict[str, object]:
    # The aggregate's OWN workspace_id is written (not ctx.workspace_id): a
    # forged/mismatched limit.workspace_id is then rejected by the RLS WITH
    # CHECK clause rather than silently persisted under ctx's tenant (the
    # media `add()` precedent).
    return {
        "id": limit.id,
        "workspace_id": limit.workspace_id,
        "scope": limit.scope.value,
        "scope_key": limit.scope_key,
        "metric": limit.metric.value,
        "period": limit.period.value,
        "limit_value": limit.limit_value,
        "created_at": limit.created_at,
        "updated_at": limit.updated_at,
        "version": limit.version,
    }


def _hydrate_limit(row: RowMapping) -> UsageLimit:
    return UsageLimit(
        id=row["id"],
        workspace_id=row["workspace_id"],
        scope=LimitScope(row["scope"]),
        scope_key=row["scope_key"],
        metric=Metric(row["metric"]),
        period=Period(row["period"]),
        limit_value=row["limit_value"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _hydrate_rollup(row: RowMapping) -> UsageRollup:
    return UsageRollup(
        workspace_id=row["workspace_id"],
        agent_key=row["agent_key"],
        provider=row["provider"],
        period=Period(row["period"]),
        period_start=row["period_start"],
        tokens_sum=row["tokens_sum"],
        cost_micros_sum=row["cost_micros_sum"],
        updated_at=row["updated_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: ``uq_limit``
    (``workspace_id, scope, scope_key, metric, period``) under real
    concurrency between two ``replace_limits`` calls -- ``ConflictError``
    (409, ``common.conflict``); ``append``'s own INSERT never raises this
    (``ON CONFLICT ... DO NOTHING`` absorbs the ``uq_usage_op`` race, INV-U1).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected a write (e.g. a forged cross-tenant ``UsageLimit.workspace_id``
    on ``replace_limits``) -- an internal/500-class error
    (``common.internal``): a well-behaved caller can never trigger this, so
    it is not a normal 4xx. Anything else is an unexpected database failure,
    folded into the same 500-class error rather than leaking the driver
    exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("usage limit write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("usage write rejected by row-level security", code="common.internal")
    return AppError("unexpected database error while persisting usage data", code="common.internal")
