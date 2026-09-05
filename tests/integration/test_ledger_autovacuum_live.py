"""Live-Postgres proofs for capacity step 2.8 (``ح-16``) against real
``aizzak_test``.

Three things a hermetic stub structurally cannot prove, and each fails
differently:

1. **The reloptions were actually STORED.** ``tests/unit/test_ledger_
   autovacuum.py`` reads the migrations' source and proves the numbers are
   right; it cannot prove PostgreSQL accepted them. A misspelled storage
   parameter is not a silent no-op -- but a migration that was never applied to
   THIS database is, and that is the failure mode an operator meets.
2. **``retention_sweeper`` can reach ``usage.usage_records`` across tenants.**
   Every ``usage`` table is under FORCE ROW LEVEL SECURITY with a
   ``tenant_isolation`` policy that has no ``TO`` clause, so it applies to the
   sweep role too and confines an age-based cross-tenant sweep to whatever
   ``app.workspace_id`` its session set: nothing. The GRANT alone is not
   enough, and a sweep that silently deletes zero rows looks exactly like a
   sweep with nothing to do.
3. **The carve-out widened nobody else.** The whole risk of adding a permissive
   policy is that ``USING (true)`` is OR-combined with ``tenant_isolation``; if
   it were scoped wrongly, ``app_rw`` would see every tenant's metering rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.ops.retention import sweep_usage_records
from app.ops.table_growth import WATCHED, growth
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )


async def _seed_usage_record(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, *, created_at: datetime
) -> str:
    record_id = new_uuid7()
    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                "INSERT INTO usage.usage_records "
                "(id, workspace_id, agent_key, provider, tokens, cost_micros, "
                " operation_id, created_at) "
                "VALUES (:id, :ws, 'rag', 'ollama', 10, 0, :op, :created_at)"
            ),
            {"id": record_id, "ws": ctx.workspace_id, "op": new_uuid7(), "created_at": created_at},
        )
    return record_id


async def _owner_scalar(
    owner_dsn: str, sql: str, params: dict[str, object] | None = None
) -> object:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            return (await conn.execute(text(sql), params or {})).scalar_one()
    finally:
        await engine.dispose()


async def _visible_to_sweeper(engine: AsyncEngine, ids: list[str]) -> int:
    """Count seeded rows THROUGH the sweep role's own session.

    Reading these back as ``aizzak_owner`` is the trap this repository already
    measured once, in step 2.5: ``usage.usage_records`` is under FORCE ROW LEVEL
    SECURITY, so the owner is subject to ``tenant_isolation`` like anyone else
    and sees ZERO rows with no ``app.workspace_id`` set. An assertion that
    "nothing survived" written against that connection passes whether the sweep
    deleted everything or nothing at all -- a vacuous green. The sweep role is
    the one identity that can see across tenants here (its ``USING (true)``
    SELECT policy), which makes it both the correct reader and a second proof
    that the carve-out is real."""
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM usage.usage_records "
                        "WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": ids},
                )
            ).scalar_one()
        )


# ------------------------------------------- (1) the settings were stored --


@pytest.mark.anyio
async def test_every_watched_table_carries_an_absolute_vacuum_trigger(
    live_db: LiveDbDsns,
) -> None:
    """``ح-16`` is that the trigger scales with the table. Read back from
    ``pg_class.reloptions`` -- what the SERVER believes, not what the migration
    says -- the proportional term must be zero on every watched table."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.oid::regclass::text AS qualified, "
                        "  (SELECT option_value FROM pg_options_to_table(c.reloptions) "
                        "    WHERE option_name = 'autovacuum_vacuum_scale_factor') AS scale, "
                        "  (SELECT option_value FROM pg_options_to_table(c.reloptions) "
                        "    WHERE option_name = 'autovacuum_vacuum_threshold') AS threshold "
                        "FROM pg_class c WHERE c.oid = ANY(CAST(:tables AS regclass[]))"
                    ),
                    {"tables": list(WATCHED)},
                )
            ).all()
    finally:
        await engine.dispose()

    assert len(rows) == len(WATCHED)
    for row in rows:
        assert row.scale == "0", f"{row.qualified} still scales its trigger with its own size"
        assert int(row.threshold) > 0, f"{row.qualified} has no absolute floor"


@pytest.mark.anyio
async def test_the_rollups_reserve_page_space_and_the_ledgers_do_not(
    live_db: LiveDbDsns,
) -> None:
    """``fillfactor`` is only useful where a HOT update is possible at all --
    ``usage_rollups``'s upsert changes no indexed column; ``platform.outbox``'s
    relay UPDATE changes ``published_at``, which both partial indexes cover."""
    rollups = await _owner_scalar(
        live_db.owner,
        "SELECT option_value FROM pg_class c, pg_options_to_table(c.reloptions) "
        "WHERE c.oid = 'usage.usage_rollups'::regclass AND option_name = 'fillfactor'",
    )
    assert int(str(rollups)) < 100

    outbox_has_fillfactor = await _owner_scalar(
        live_db.owner,
        "SELECT count(*) FROM pg_class c, pg_options_to_table(c.reloptions) "
        "WHERE c.oid = 'platform.outbox'::regclass AND option_name = 'fillfactor'",
    )
    assert outbox_has_fillfactor == 0


@pytest.mark.anyio
async def test_the_growth_report_reads_every_watched_table(live_db: LiveDbDsns) -> None:
    """The report's own SQL, against the real catalogue: a table renamed or a
    schema moved makes ``regclass`` raise rather than silently return fewer
    rows, and the per-table trigger arithmetic must resolve for each one."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = await growth(conn, WATCHED)
    finally:
        await engine.dispose()

    assert {row.table for row in rows} == set(WATCHED)
    for row in rows:
        assert row.has_own_settings, f"{row.table} is not managed by step 2.8's migrations"
        # scale_factor is 0 for all of them, so the trigger IS the flat
        # threshold -- it must not have picked up the table's size.
        assert row.vacuum_trigger_at > 0


# -------------------------------------------- (2) the sweep can reach it --


@pytest.mark.anyio
async def test_the_sweep_deletes_past_the_cutoff_across_two_workspaces(
    retention_engine: AsyncEngine, tenant_session: TenantSessionFactory
) -> None:
    """The half that a GRANT alone does not buy. Two DIFFERENT workspaces, one
    stale row each, swept in one statement by a session that has set no
    ``app.workspace_id`` at all -- which under ``tenant_isolation`` alone
    matches nothing and would delete zero rows while reporting success."""
    first, second = _ctx(new_uuid7()), _ctx(new_uuid7())
    stale = _NOW - timedelta(days=120)
    fresh = _NOW - timedelta(days=1)

    old_first = await _seed_usage_record(tenant_session, first, created_at=stale)
    old_second = await _seed_usage_record(tenant_session, second, created_at=stale)
    recent = await _seed_usage_record(tenant_session, first, created_at=fresh)

    # Asserted BEFORE the sweep, so "nothing survived" afterwards cannot be a
    # reader that could never see them in the first place.
    assert await _visible_to_sweeper(retention_engine, [old_first, old_second, recent]) == 3

    async with retention_engine.begin() as conn:
        result = await sweep_usage_records(conn, now=_NOW)

    assert result.affected >= 2
    assert await _visible_to_sweeper(retention_engine, [old_first, old_second]) == 0, (
        "a cross-tenant sweep left another workspace's stale rows behind"
    )
    assert await _visible_to_sweeper(retention_engine, [recent]) == 1, (
        "the sweep deleted a row inside its retention window"
    )


@pytest.mark.anyio
async def test_a_dry_run_counts_the_same_rows_and_deletes_none(
    retention_engine: AsyncEngine, tenant_session: TenantSessionFactory
) -> None:
    ctx = _ctx(new_uuid7())
    record = await _seed_usage_record(tenant_session, ctx, created_at=_NOW - timedelta(days=120))

    async with retention_engine.begin() as conn:
        result = await sweep_usage_records(conn, dry_run=True, now=_NOW)

    assert result.dry_run is True
    assert result.affected >= 1
    assert await _visible_to_sweeper(retention_engine, [record]) == 1


@pytest.mark.anyio
async def test_the_sweep_role_cannot_touch_the_rollups(retention_engine: AsyncEngine) -> None:
    """The balance, not the detail: deleting a rollup row would hand a
    workspace back headroom it already spent. Enforced by the absence of a
    GRANT, so it holds even for a future statement that names the table."""
    with pytest.raises(DBAPIError) as caught:
        async with retention_engine.begin() as conn:
            await conn.execute(text("DELETE FROM usage.usage_rollups"))

    assert "permission denied" in str(caught.value).lower()


# ------------------------------------------ (3) nobody else was widened --


@pytest.mark.anyio
async def test_the_carve_out_did_not_widen_app_rw(
    tenant_session: TenantSessionFactory,
) -> None:
    """A permissive policy is OR-combined with ``tenant_isolation``. Scoped to
    the wrong role -- or to none -- it would hand ``app_rw`` every tenant's
    metering rows, which is the one outcome worse than an unswept table."""
    mine, theirs = _ctx(new_uuid7()), _ctx(new_uuid7())
    await _seed_usage_record(tenant_session, theirs, created_at=_NOW)

    async with tenant_session(mine) as session:
        visible = (
            await session.execute(text("SELECT count(*) FROM usage.usage_records"))
        ).scalar_one()

    assert visible == 0, "app_rw can see another workspace's usage rows"
