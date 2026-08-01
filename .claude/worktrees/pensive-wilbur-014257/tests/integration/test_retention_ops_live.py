"""Live-Postgres proof for the retention sweep (``app.ops.retention``, P1-5,
``docs/p1-hardening-plan.md`` §3 step 8) against real ``aizzak_test``.

The exit criterion this file exists for, per the plan's own wording: a test
that proves the sweep deletes what is PAST its table's cutoff AND does not
delete what is not -- the second half is what catches a greedy sweep, and a
hermetic stub cannot prove either half against real SQL comparison
semantics on real timestamptz columns. Argument/CLI-shape coverage that
needs no database lives in ``tests/unit/test_ops_retention.py``.

Rows are seeded with an EXPLICIT ``created_at``/``processed_at``/
``published_at`` (never relying on ``now()`` defaults) so "past cutoff" and
"not past cutoff" are exact, not timing-dependent.
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
from app.ops.retention import sweep_idempotency_keys, sweep_outbox, sweep_processed_events
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_ENDPOINT = "POST /files"
_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )


async def _seed_outbox_row(
    owner_dsn: str, *, created_at: datetime, published_at: datetime | None
) -> str:
    event_id = new_uuid7()
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO platform.outbox
                        (id, aggregate_type, aggregate_id, event_type, stream, payload,
                         created_at, published_at)
                    VALUES
                        (:id, 'test', :id, 'test.retention.v1', 'stream.test', '{}'::jsonb,
                         :created_at, :published_at)
                    """
                ),
                {"id": event_id, "created_at": created_at, "published_at": published_at},
            )
    finally:
        await engine.dispose()
    return event_id


async def _seed_processed_event(owner_dsn: str, *, processed_at: datetime) -> tuple[str, str]:
    consumer_group = f"cg.test.{new_uuid7()}"
    event_id = new_uuid7()
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO platform.processed_events "
                    "(consumer_group, event_id, processed_at) "
                    "VALUES (:cg, :eid, :processed_at)"
                ),
                {"cg": consumer_group, "eid": event_id, "processed_at": processed_at},
            )
    finally:
        await engine.dispose()
    return consumer_group, event_id


async def _seed_idempotency_key(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, *, created_at: datetime
) -> str:
    key = new_uuid7()
    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                "INSERT INTO platform.idempotency_keys "
                "(workspace_id, endpoint, idempotency_key, request_hash, created_at) "
                "VALUES (:ws, :endpoint, :key, 'hash', :created_at)"
            ),
            {"ws": ctx.workspace_id, "endpoint": _ENDPOINT, "key": key, "created_at": created_at},
        )
    return key


async def _count(owner_dsn: str, table: str) -> int:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            return (await conn.execute(text(f"SELECT count(*) FROM platform.{table}"))).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_sweep_outbox_deletes_old_published_rows_only(
    live_db: LiveDbDsns, retention_engine: AsyncEngine
) -> None:
    """Three rows: old+published (must go), new+published (must stay), and
    old+UNPUBLISHED (must stay regardless of age -- an unpublished row is
    never a candidate, module docstring)."""
    old_published = await _seed_outbox_row(
        live_db.owner,
        created_at=_NOW - timedelta(days=200),
        published_at=_NOW - timedelta(days=200),
    )
    new_published = await _seed_outbox_row(
        live_db.owner, created_at=_NOW - timedelta(days=1), published_at=_NOW - timedelta(days=1)
    )
    old_unpublished = await _seed_outbox_row(
        live_db.owner, created_at=_NOW - timedelta(days=200), published_at=None
    )

    async with retention_engine.begin() as conn:
        result = await sweep_outbox(conn, retention=timedelta(days=90), now=_NOW)

    assert result.table == "outbox"
    assert result.dry_run is False
    assert result.affected == 1

    remaining_engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with remaining_engine.begin() as conn:
            rows = (await conn.execute(text("SELECT id FROM platform.outbox"))).scalars().all()
    finally:
        await remaining_engine.dispose()
    remaining = {str(r) for r in rows}
    assert str(old_published) not in remaining
    assert str(new_published) in remaining
    assert str(old_unpublished) in remaining


@pytest.mark.anyio
async def test_sweep_outbox_dry_run_counts_but_deletes_nothing(
    live_db: LiveDbDsns, retention_engine: AsyncEngine
) -> None:
    await _seed_outbox_row(
        live_db.owner,
        created_at=_NOW - timedelta(days=200),
        published_at=_NOW - timedelta(days=200),
    )
    before = await _count(live_db.owner, "outbox")

    async with retention_engine.begin() as conn:
        result = await sweep_outbox(conn, retention=timedelta(days=90), dry_run=True, now=_NOW)

    assert result.dry_run is True
    assert result.affected == 1
    after = await _count(live_db.owner, "outbox")
    assert after == before, "dry_run must delete nothing"


@pytest.mark.anyio
async def test_sweep_processed_events_deletes_old_but_not_new(
    live_db: LiveDbDsns, retention_engine: AsyncEngine
) -> None:
    old_group, old_event = await _seed_processed_event(
        live_db.owner, processed_at=_NOW - timedelta(days=60)
    )
    new_group, new_event = await _seed_processed_event(
        live_db.owner, processed_at=_NOW - timedelta(days=1)
    )

    async with retention_engine.begin() as conn:
        result = await sweep_processed_events(conn, retention=timedelta(days=30), now=_NOW)

    assert result.table == "processed_events"
    assert result.affected == 1

    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            old_still_there = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM platform.processed_events "
                        "WHERE consumer_group = :cg AND event_id = :eid"
                    ),
                    {"cg": old_group, "eid": old_event},
                )
            ).scalar_one()
            new_still_there = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM platform.processed_events "
                        "WHERE consumer_group = :cg AND event_id = :eid"
                    ),
                    {"cg": new_group, "eid": new_event},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert old_still_there == 0
    assert new_still_there == 1


@pytest.mark.anyio
async def test_sweep_idempotency_keys_deletes_old_across_every_workspace_but_not_new(
    tenant_session: TenantSessionFactory, retention_engine: AsyncEngine
) -> None:
    """Cross-tenant reach is the whole point of the role-scoped RLS carve-out
    (0003_retention_sweep.py): two DIFFERENT workspaces, an old key each, a
    new key each -- the sweep must reach BOTH old ones in one call, because
    it never sets any `app.workspace_id` at all."""
    ws_one, ws_two = _ctx(new_uuid7()), _ctx(new_uuid7())

    old_one = await _seed_idempotency_key(
        tenant_session, ws_one, created_at=_NOW - timedelta(days=10)
    )
    new_one = await _seed_idempotency_key(
        tenant_session, ws_one, created_at=_NOW - timedelta(hours=1)
    )
    old_two = await _seed_idempotency_key(
        tenant_session, ws_two, created_at=_NOW - timedelta(days=10)
    )
    new_two = await _seed_idempotency_key(
        tenant_session, ws_two, created_at=_NOW - timedelta(hours=1)
    )

    async with retention_engine.begin() as conn:
        result = await sweep_idempotency_keys(conn, retention=timedelta(days=2), now=_NOW)

    assert result.table == "idempotency_keys"
    assert result.affected == 2

    async with tenant_session(ws_one) as session:
        remaining_one = (
            (await session.execute(text("SELECT idempotency_key FROM platform.idempotency_keys")))
            .scalars()
            .all()
        )
    async with tenant_session(ws_two) as session:
        remaining_two = (
            (await session.execute(text("SELECT idempotency_key FROM platform.idempotency_keys")))
            .scalars()
            .all()
        )

    assert {str(k) for k in remaining_one} == {str(new_one)}
    assert str(old_one) not in {str(k) for k in remaining_one}
    assert {str(k) for k in remaining_two} == {str(new_two)}
    assert str(old_two) not in {str(k) for k in remaining_two}


@pytest.mark.anyio
async def test_retention_sweeper_cannot_insert_or_update_any_of_the_three_tables(
    retention_engine: AsyncEngine,
) -> None:
    """Least privilege, the denying direction (the ``outbox_relay``/``app_rw``
    proof precedent): ``retention_sweeper`` can shrink these tables but must
    never be able to forge or alter a row in any of them."""
    statements = (
        "INSERT INTO platform.outbox "
        "(id, aggregate_type, aggregate_id, event_type, stream, payload) "
        "VALUES (gen_random_uuid(), 't', gen_random_uuid(), 't.v1', 's', '{}'::jsonb)",
        "UPDATE platform.outbox SET published_at = now() WHERE false",
        "INSERT INTO platform.processed_events (consumer_group, event_id) "
        "VALUES ('cg', gen_random_uuid())",
        "INSERT INTO platform.idempotency_keys "
        "(workspace_id, endpoint, idempotency_key, request_hash) "
        "VALUES (gen_random_uuid(), 'e', gen_random_uuid(), 'h')",
        "UPDATE platform.idempotency_keys SET response_body = '{}'::jsonb WHERE false",
    )
    for statement in statements:
        with pytest.raises(DBAPIError) as exc_info:
            async with retention_engine.begin() as conn:
                await conn.execute(text(statement))
        assert getattr(exc_info.value.orig, "sqlstate", None) == "42501", statement
