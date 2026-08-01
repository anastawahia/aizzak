"""Live-Postgres + live-Redis tests for ``OutboxRelay`` (5.1-ب).

Both ``live_db`` and ``live_redis`` marks -- the whole point of this file is
the seam BETWEEN the two infrastructures, so it auto-skips (rather than
erroring) whenever either is unreachable, the ``live_db``/``live_redis``
precedent every other live test file already follows.

Seeded rows are built as plain ``OutboxRecord``s (via ``SqlEventOutbox`` as
``app_rw``, exactly the real producer path) rather than through any module's
``event_mapping.py``: 04 §1's envelope shape is shared platform
infrastructure, and which MODULE happens to own a given ``event_type`` is
irrelevant to what the relay itself does with a row once it is in
``platform.outbox``. Every seeded record targets a FRESH, UNIQUE
``stream.test.<uuid>`` name (R6 precedent: ``qdrant_collection``'s
unique-collection-per-test approach, applied here to Redis Streams) because
``live_redis``'s own docstring already documents the constraint this exists
for: "the server may hold unrelated data" -- a shared, long-lived Redis
instance means a fixed stream name could read another test's leftovers on
``XRANGE``, where a fresh name per test sidesteps that entirely instead of
needing to filter by content.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings, RedisSettings
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.rls import TenantSessionFactory
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]

_TEST_EVENT_TYPE = "media.job.requested.v1"
# A closed port nothing listens on (<1024, unprivileged binds cannot use it)
# -- deterministic, fast "connection refused" with no real outage needed.
_UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _record(*, stream: str) -> OutboxRecord:
    """A synthetic-but-schema-valid record targeting a caller-supplied
    (unique per test) stream -- see the module docstring for why."""
    event_id = new_uuid7()
    job_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type="media_job",
        aggregate_id=job_id,
        event_type=_TEST_EVENT_TYPE,
        stream=stream,
        payload=build_envelope(
            event_id=event_id,
            source="media",
            event_type=_TEST_EVENT_TYPE,
            subject=job_id,
            occurred_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            workspace_id="018f0000-0000-7000-8000-000000000001",
            data={"job_id": job_id, "kind": "image", "prompt": "outbox relay live test"},
        ),
    )


async def _read_outbox_as_owner(owner_dsn: str, event_ids: list[str]) -> list[RowMapping]:
    """Read back as the OWNER, independent of whatever the relay's own
    ``outbox_relay`` grant can see.

    Unlike ``test_media_repository_rls.py``'s ``_fetch_row_as_owner``, there
    is no ``set_config('app.workspace_id', ...)`` dance needed here:
    ``platform.outbox`` carries no RLS policy at all (``persistence/
    outbox.py``'s module docstring -- ``FORCE ROW LEVEL SECURITY`` was never
    applied to this table), so a plain owner ``SELECT`` already sees every
    row regardless of tenant. The FORCE-RLS GUC lesson §3.44 recorded does
    not apply to this table.
    """
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


# --------------------------------------------------------------------------- #
# (1) full cycle: seed 3 rows -> run_once -> XRANGE + published_at + 2nd = 0   #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_run_once_publishes_the_seeded_rows_in_order_and_a_second_run_finds_nothing(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    try:
        ctx = _ctx(new_uuid7())
        records = [_record(stream=stream) for _ in range(3)]
        await SqlEventOutbox(tenant_session).append(ctx, records)

        store = SqlOutboxRelayStore(relay_sessionmaker)
        publisher = RedisStreamsPublisher(redis_client)
        relay = OutboxRelay(
            store, publisher, batch_size=10, poll_interval_ms=100, max_backoff_ms=1000
        )

        count = await relay.run_once()
        assert count == 3

        # XRANGE returns entries oldest-first by construction (stream entry
        # ids are themselves time-ordered) -- exactly `created_at` order,
        # since `fetch_unpublished` orders by it and `run_once` publishes
        # in that same order.
        entries = await redis_client.xrange(stream)
        assert len(entries) == 3
        published_ids = [json.loads(fields[b"ce"])["id"] for _entry_id, fields in entries]
        assert published_ids == [r.event_id for r in records]
        # Verbatim -- the stored envelope round-trips through XADD unchanged.
        for (_entry_id, fields), record in zip(entries, records, strict=True):
            assert json.loads(fields[b"ce"]) == record.payload

        rows = await _read_outbox_as_owner(live_db.owner, [r.event_id for r in records])
        assert len(rows) == 3
        assert all(row["published_at"] is not None for row in rows)
        assert all(row["attempts"] == 0 for row in rows)

        second_count = await relay.run_once()
        assert second_count == 0
    finally:
        await redis_client.delete(stream)


# --------------------------------------------------------------------------- #
# (2) attempts path: an unreachable Redis leaves the row unpublished          #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_a_publish_failure_leaves_the_row_unpublished_with_attempts_bumped(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Mutation-battery target #5, live half."""
    stream = f"stream.test.{new_uuid7()}"
    ctx = _ctx(new_uuid7())
    record = _record(stream=stream)
    await SqlEventOutbox(tenant_session).append(ctx, [record])

    dead_client = create_redis_client(RedisSettings(url=_UNREACHABLE_REDIS_URL))
    try:
        store = SqlOutboxRelayStore(relay_sessionmaker)
        publisher = RedisStreamsPublisher(dead_client)
        relay = OutboxRelay(
            store, publisher, batch_size=10, poll_interval_ms=100, max_backoff_ms=1000
        )

        with pytest.raises(AppError):
            await relay.run_once()
    finally:
        await dead_client.aclose()

    rows = await _read_outbox_as_owner(live_db.owner, [record.event_id])
    assert len(rows) == 1
    assert rows[0]["published_at"] is None
    assert rows[0]["attempts"] == 1


# --------------------------------------------------------------------------- #
# (3) least-privilege negatives                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_app_rw_cannot_select_or_update_the_outbox(
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """``app_rw`` is INSERT-only on ``platform.outbox`` (``persistence/
    outbox.py``'s module docstring) -- SELECT and UPDATE must both raise
    42501. No ``set_config`` dance needed: this table has no RLS policy at
    all, so a bare table-privilege check is the only thing in play.

    ``pytest.raises`` wraps the WHOLE ``async with ... session.begin():``
    block (not just the failing ``execute``): once a statement fails,
    Postgres marks that transaction aborted, so the exception must propagate
    all the way out through ``session.begin()``'s own ``__aexit__`` (a real
    ROLLBACK) rather than being caught while the block is still "clean" --
    catching it INSIDE would leave ``__aexit__`` trying to COMMIT an already
    -aborted transaction instead.
    """
    with pytest.raises(DBAPIError) as select_exc:
        async with sessionmaker_app() as session, session.begin():
            await session.execute(text("SELECT * FROM platform.outbox LIMIT 1"))
    assert getattr(select_exc.value.orig, "sqlstate", None) == "42501"

    with pytest.raises(DBAPIError) as update_exc:
        async with sessionmaker_app() as session, session.begin():
            await session.execute(
                text("UPDATE platform.outbox SET published_at = now() WHERE false")
            )
    assert getattr(update_exc.value.orig, "sqlstate", None) == "42501"


@pytest.mark.anyio
async def test_outbox_relay_cannot_insert_into_the_outbox(
    relay_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The relay only ever FORWARDS what a producer already wrote -- it must
    never be able to forge a new event by inserting its own row. Same
    whole-block ``pytest.raises`` shape as the ``app_rw`` test above, for the
    same aborted-transaction reason."""
    with pytest.raises(DBAPIError) as exc_info:
        async with relay_sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO platform.outbox
                        (id, aggregate_type, aggregate_id, event_type, stream, payload)
                    VALUES
                        (:id, 'test', :id, 'media.job.requested.v1', 'stream.test', '{}'::jsonb)
                    """
                ),
                {"id": new_uuid7()},
            )
    assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"
