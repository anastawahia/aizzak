"""The AC-08 proof (5.1-ج): a real ``files.file.uploaded.v1`` outbox row
survives the WHOLE pipeline -- producer write → ``outbox_relay`` (5.1-ب) →
the knowledge worker's REAL register handler (5.1-ج) -- against real
Postgres AND real Redis.

Both ``live_db`` and ``live_redis`` (the seam between the two
infrastructures IS the point, ``test_outbox_relay_live.py``'s own module
docstring precedent). The seeded record targets a FRESH, UNIQUE
``stream.test.<uuid>``/``cg.test.<uuid>`` pair (R6) -- the knowledge
register HANDLER closure is built directly via ``build_knowledge_register_
handler`` (not the whole ``build_knowledge_worker``, which also needs the
still-blocked index handler's dependencies, ``workers/bootstrap.py``'s own
"Honest-failure rule") and wired into a ``Subscription`` naming the unique
test stream/group, so this test proves the register handler end to end
without waiting on gap 2.10.

``app_rw`` composes the register handler's own dependencies
(``SqlDocumentRepository``/``SqlEventOutbox``/``tenant_session``-as-``uow``)
exactly as ``build_knowledge_worker`` would in production -- the design
brief's "on app_rw" instruction. The relay runs as the real ``outbox_relay``
role (5.1-ب's own least-privilege split, unchanged here).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.workers.bootstrap import build_knowledge_register_handler
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]

_EVENT_TYPE = "files.file.uploaded.v1"


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


def _uploaded_record(*, stream: str, workspace_id: str, file_id: str) -> OutboxRecord:
    """A schema-valid ``files.file.uploaded.v1`` record on a UNIQUE test
    stream -- ``test_outbox_relay_live.py``'s own ``_record`` precedent,
    this event's shape instead of media's."""
    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type="file",
        aggregate_id=file_id,
        event_type=_EVENT_TYPE,
        stream=stream,
        payload=build_envelope(
            event_id=event_id,
            source="files",
            event_type=_EVENT_TYPE,
            subject=file_id,
            occurred_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            workspace_id=workspace_id,
            data={
                "file_id": file_id,
                "content_type": "text/plain",
                "size_bytes": 42,
                "storage_key": f"{workspace_id}/{file_id}",
            },
        ),
    )


async def _read_outbox_as_owner(
    owner_dsn: str, *, aggregate_id: str, event_type: str
) -> list[RowMapping]:
    """Owner read -- ``platform.outbox`` carries no RLS at all (``persistence/
    outbox.py``'s module docstring), so no GUC dance is needed HERE (unlike
    the ``knowledge.documents`` read below)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM platform.outbox WHERE aggregate_id = :id AND event_type = :et"),
                {"id": aggregate_id, "et": event_type},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


async def _read_document_as_owner(
    owner_dsn: str, *, workspace_id: str, file_id: str
) -> list[RowMapping]:
    """``knowledge.documents`` is ``FORCE ROW LEVEL SECURITY`` (``migrations/
    versions/knowledge/0001_knowledge.py``, docs/log/3.44.md's own recorded
    FORCE-RLS lesson: a raw owner SELECT with no GUC set silently returns
    ZERO rows -- a passing-by-accident assertion, not a real proof). The GUC
    is set on the SAME connection/transaction as the SELECT, exactly like
    ``test_producer_atomicity.py``'s own ``_count_media_jobs`` helper.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT * FROM knowledge.documents WHERE workspace_id = :ws AND file_id = :fid"
                ),
                {"ws": workspace_id, "fid": file_id},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_real_uploaded_event_flows_through_the_relay_to_the_register_handler(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    workspace_id = new_uuid7()
    file_id = new_uuid7()
    ctx = _ctx(workspace_id)

    documents = SqlDocumentRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    handler = build_knowledge_register_handler(
        documents,
        outbox,
        tenant_session,
        SqlProcessedEventLedger(tenant_session),
        # The ledger key must be THE group that owns this subscription
        # (production builders feed both from one constant); this test's
        # group is unique per run, so it is passed explicitly.
        consumer_group=group,
    )
    subscription = Subscription(stream=stream, group=group, handlers={_EVENT_TYPE: handler})
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="e2e-test",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )

    try:
        # 0) setup() FIRST -- ensure_group's own `$` (tail-start) semantics
        #    (its docstring) mean a group created AFTER an entry already
        #    exists on the stream never sees that entry; a real worker's
        #    setup() always runs at process boot, before it consumes
        #    anything, so this ordering is the realistic one, not a
        #    test-only convenience.
        await consumer.setup([subscription])

        # 1) Seed a real outbox row through the real producer-write path
        #    (SqlEventOutbox, app_rw) -- 04 §3.1's first guarantee.
        record = _uploaded_record(stream=stream, workspace_id=workspace_id, file_id=file_id)
        await SqlEventOutbox(tenant_session).append(ctx, [record])

        # 2) The real relay (5.1-ب), as the real outbox_relay role.
        relay = OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=10,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        )
        published = await relay.run_once()
        assert published == 1

        # 3) The real knowledge register handler, on app_rw.
        handled = await consumer.run_once([subscription])
        assert handled == 1

        # -- Assertions --------------------------------------------------- #
        # (a) knowledge.documents row exists (RLS-correct read-back).
        rows = await _read_document_as_owner(
            live_db.owner, workspace_id=workspace_id, file_id=file_id
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        document_id = str(rows[0]["id"])

        # Also provable through the real repository itself (app_rw, RLS on).
        stored = await documents.get(ctx, document_id)
        assert stored is not None
        assert stored.file_id == file_id

        # (b) the mapped knowledge.document.registered.v1 follow-on landed.
        follow_on = await _read_outbox_as_owner(
            live_db.owner, aggregate_id=document_id, event_type="knowledge.document.registered.v1"
        )
        assert len(follow_on) == 1
        assert str(follow_on[0]["workspace_id"]) == workspace_id

        # (c) the entry is acked -- nothing left pending for this group.
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        # (d) a second pass finds nothing new to do.
        second = await consumer.run_once([subscription])
        assert second == 0
    finally:
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)


@pytest.mark.anyio
async def test_a_double_published_event_registers_exactly_one_document(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    redis_client: Redis,
) -> None:
    """At-least-once becomes effectively-once, live (5.2-أ · DD-09 · 04 §3).

    The SAME envelope lands on the stream as TWO entries -- exactly 04
    §3.2's documented relay behaviour («المُرحّل قد ينشر مرتين عند إعادة
    المحاولة»), simulated by publishing twice through the real
    ``RedisStreamsPublisher``. Registration is non-idempotent by design
    (INV-K3), so before 5.2-أ this scenario minted TWO documents -- the very
    hazard R3 froze production publishing over. The claim in
    ``platform.processed_events`` must collapse it to one document, one
    ledger row, and two acked entries (the duplicate is acked as a clean
    skip, not retried).
    """
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    workspace_id = new_uuid7()
    file_id = new_uuid7()

    documents = SqlDocumentRepository(tenant_session)
    handler = build_knowledge_register_handler(
        documents,
        SqlEventOutbox(tenant_session),
        tenant_session,
        SqlProcessedEventLedger(tenant_session),
        consumer_group=group,
    )
    subscription = Subscription(stream=stream, group=group, handlers={_EVENT_TYPE: handler})
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="e2e-dup",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )

    try:
        await consumer.setup([subscription])

        record = _uploaded_record(stream=stream, workspace_id=workspace_id, file_id=file_id)
        publisher = RedisStreamsPublisher(redis_client)
        first_entry = await publisher.publish(stream, record.payload)
        second_entry = await publisher.publish(stream, record.payload)
        assert first_entry != second_entry  # two distinct Streams entries

        # One read covers both entries (batch_count=10). BOTH dispatch
        # cleanly -- the engine cannot tell a first delivery from a
        # ledger-skipped duplicate, and that blindness is the design
        # (engine.py's own idempotency comment) -- so both count as handled.
        handled = await consumer.run_once([subscription])
        assert handled == 2

        # Exactly ONE document, despite registration being non-idempotent.
        rows = await _read_document_as_owner(
            live_db.owner, workspace_id=workspace_id, file_id=file_id
        )
        assert len(rows) == 1

        # Exactly ONE ledger row for (group, event_id).
        engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                claims = (
                    await conn.execute(
                        text(
                            "SELECT consumer_group FROM platform.processed_events "
                            "WHERE event_id = :eid"
                        ),
                        {"eid": record.event_id},
                    )
                ).scalars()
                assert list(claims) == [group]
        finally:
            await engine.dispose()

        # Both entries acked -- the duplicate leaves nothing pending behind.
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0
    finally:
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)
