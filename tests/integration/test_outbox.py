"""Live-Postgres tests for ``SqlEventOutbox`` (09-testing-strategy §3).

Runs against a real, local PostgreSQL 16; auto-skips via ``live_db`` when
unreachable. Two things are worth proving here and cannot be proven hermetically:

1. **Least privilege actually holds.** ``app_rw`` is granted INSERT on
   ``platform.outbox`` and nothing else, so every assertion reads the row back
   through the OWNER connection. If the adapter ever needed SELECT/UPDATE, that
   would surface as a hard failure here rather than as a quiet extra grant in
   production — a producer that could ``UPDATE published_at`` could make an
   event vanish unpublished.
2. **The database owns the columns the adapter refuses to write.**
   ``created_at`` defaults to server ``now()`` (the relay pulls unpublished rows
   in that order), ``published_at`` starts NULL, ``attempts`` starts 0.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import TenantSessionFactory
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


def _ctx(workspace_id: str, *, correlation_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=correlation_id or new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _record(*, event_id: str | None = None, job_id: str | None = None) -> OutboxRecord:
    job = job_id or new_uuid7()
    event = event_id or new_uuid7()
    return OutboxRecord(
        event_id=event,
        aggregate_type="media_job",
        aggregate_id=job,
        event_type="media.job.requested.v1",
        stream="stream.media",
        payload=build_envelope(
            event_id=event,
            source="media",
            event_type="media.job.requested.v1",
            subject=job,
            occurred_at=datetime(2026, 7, 18, 10, 15, tzinfo=UTC),
            workspace_id="018f0000-0000-7000-8000-000000000001",
            data={"job_id": job, "kind": "image", "prompt": "a cat"},
        ),
    )


async def _read_rows(owner_dsn: str, event_ids: list[str]) -> list[RowMapping]:
    """Read back as the OWNER -- ``app_rw`` has INSERT only (see module docstring)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM platform.outbox WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": event_ids},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_append_writes_every_column_the_relay_needs(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    workspace_id = new_uuid7()
    correlation_id = new_uuid7()
    record = _record()

    await SqlEventOutbox(tenant_session).append(
        _ctx(workspace_id, correlation_id=correlation_id), [record]
    )

    # The read-back goes through raw SQL (no typed Table), so asyncpg hands
    # back `uuid.UUID` objects rather than the adapter's `as_uuid=False` text.
    (row,) = await _read_rows(live_db.owner, [record.event_id])
    assert str(row["id"]) == record.event_id
    assert str(row["workspace_id"]) == workspace_id
    assert str(row["correlation_id"]) == correlation_id
    assert row["aggregate_type"] == "media_job"
    assert str(row["aggregate_id"]) == record.aggregate_id
    assert row["event_type"] == "media.job.requested.v1"
    assert row["stream"] == "stream.media"
    assert row["causation_id"] is None


@pytest.mark.anyio
async def test_payload_round_trips_verbatim(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """04 §1: the envelope is stored whole and republished as-is."""
    record = _record()

    await SqlEventOutbox(tenant_session).append(_ctx(new_uuid7()), [record])

    (row,) = await _read_rows(live_db.owner, [record.event_id])
    assert row["payload"] == record.payload


@pytest.mark.anyio
async def test_relay_owned_columns_are_left_to_the_database(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    record = _record()
    before = datetime.now(UTC)

    await SqlEventOutbox(tenant_session).append(_ctx(new_uuid7()), [record])

    (row,) = await _read_rows(live_db.owner, [record.event_id])
    assert row["published_at"] is None, "a producer must never pre-publish an event"
    assert row["attempts"] == 0
    assert row["created_at"] >= before, "created_at must come from the server clock"


@pytest.mark.anyio
async def test_a_multi_event_append_lands_as_one_statement(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """04 §3.1: several events from one use-case land together."""
    records = [_record(), _record(), _record()]

    await SqlEventOutbox(tenant_session).append(_ctx(new_uuid7()), records)

    rows = await _read_rows(live_db.owner, [r.event_id for r in records])
    assert len(rows) == 3


@pytest.mark.anyio
async def test_empty_sequence_writes_nothing(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """Event-free use-case paths must not need a guard of their own -- and an
    empty VALUES list would be a SQL error, not a no-op."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            before = (await conn.execute(text("SELECT count(*) FROM platform.outbox"))).scalar_one()
    finally:
        await engine.dispose()

    await SqlEventOutbox(tenant_session).append(_ctx(new_uuid7()), [])

    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            after = (await conn.execute(text("SELECT count(*) FROM platform.outbox"))).scalar_one()
    finally:
        await engine.dispose()
    assert after == before


@pytest.mark.anyio
async def test_duplicate_event_id_surfaces_as_a_conflict_not_a_driver_error(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """R6: no ``sqlalchemy``/``asyncpg`` type escapes the adapter."""
    outbox = SqlEventOutbox(tenant_session)
    ctx = _ctx(new_uuid7())
    record = _record()
    await outbox.append(ctx, [record])

    with pytest.raises(ConflictError):
        await outbox.append(ctx, [_record(event_id=record.event_id)])
