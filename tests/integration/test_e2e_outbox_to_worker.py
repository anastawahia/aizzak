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
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.storage.minio_storage import MinioStorage
from app.infrastructure.vector.qdrant_store import QdrantVectorStore
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.value_objects import IndexStatus
from app.workers.bootstrap import (
    build_knowledge_index_handler,
    build_knowledge_register_handler,
)
from app.workers.content_resolver import WorkerDocumentContentResolver
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


def _uploaded_record(
    *, stream: str, workspace_id: str, file_id: str, space_id: str | None = None
) -> OutboxRecord:
    """A schema-valid ``files.file.uploaded.v1`` record on a UNIQUE test
    stream -- ``test_outbox_relay_live.py``'s own ``_record`` precedent,
    this event's shape instead of media's.

    ``space_id`` is OMITTED when there is none (spaces plan step 8), exactly
    as ``files/application/event_mapping.py`` writes it and as every envelope
    published before that step already looks.
    """
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
            data=(
                {
                    "file_id": file_id,
                    "content_type": "text/plain",
                    "size_bytes": 42,
                    "storage_key": f"{workspace_id}/{file_id}",
                }
                if space_id is None
                else {
                    "file_id": file_id,
                    "content_type": "text/plain",
                    "size_bytes": 42,
                    "storage_key": f"{workspace_id}/{file_id}",
                    "space_id": space_id,
                }
            ),
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


# --------------------------------------------------------------------------- #
# The WHOLE path, live (deferred-adapters-plan.md step 16)                    #
# --------------------------------------------------------------------------- #
_TXT_BODY = (
    b"The knowledge worker fetches this file from object storage, routes it "
    b"through the parser dispatch table by its extension, chunks what comes "
    b"back, embeds every chunk, and upserts one hybrid point per chunk."
)


class _StubEmbeddings:
    """A deterministic ``EmbeddingProvider``.

    The ONE stub in this test, deliberately: the central embedding service is
    a separate deployable this harness does not run (``live_embedding`` skips
    without it), and it is not what step 16 built. Everything the step DID
    build is real here -- the MinIO fetch, the 3.k1 parser dispatch, the
    Postgres chunk write, the Qdrant upsert.
    """

    provider = "stub-embedding"
    _DIM = 8

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[[0.1] * self._DIM for _ in texts],
            model=model,
            dimensions=self._DIM,
            tokens=len(texts),
        )

    def dimensions(self, model: str) -> int:
        return self._DIM


class _StubProviders:
    """A ``ProviderResolver`` answering the embedding route only. The real
    ``SettingsProviderResolver`` needs a credential repository and a
    Vault-backed ``ResolveCredential``; the wiring that builds it is proven
    in ``tests/unit/test_workers_bootstrap.py``, and it is not what this
    test is about."""

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[object, ResolvedProvider]:
        resolved = ResolvedProvider(provider="stub-embedding", model="stub-model", api_key="")
        return object(), resolved


def _ready_file(
    *, workspace_id: str, file_id: str, name: str, storage_key: str, space_id: str | None = None
) -> File:
    """The row a completed upload leaves behind (``CompleteUpload``) -- which
    is exactly the state ``files.file.uploaded.v1`` announces."""
    now = datetime.now(UTC)
    return File(
        id=file_id,
        workspace_id=workspace_id,
        # The file's space (spaces plan step 8). The pipeline below does not
        # READ it off this row -- it reads it off the event, which is where
        # `CompleteUpload` puts it -- but a file row disagreeing with the
        # event it announced would make the assertions downstream ambiguous.
        space_id=space_id,
        name=FileName(name),
        content_type=ContentType("text/plain"),
        size_bytes=len(_TXT_BODY),
        storage_key=StorageKey(storage_key),
        checksum=None,
        status=FileStatus.READY,
        uploaded_by=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )


async def _retarget_outbox_stream(owner_dsn: str, *, aggregate_id: str, stream: str) -> None:
    """Point the follow-on ``registered`` row at this test's UNIQUE stream.

    The event mapper hardcodes the production ``stream.knowledge`` (04 §4's
    binding table), and R6 forbids a test touching a production stream on a
    shared Redis. Rewriting the row's target BEFORE the relay reads it keeps
    the relay itself real -- it publishes and marks the row dispatched
    exactly as it would in production -- while nothing ever lands on
    ``stream.knowledge``.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE platform.outbox SET stream = :stream WHERE aggregate_id = :id "
                    "AND event_type = 'knowledge.document.registered.v1'"
                ),
                {"stream": stream, "id": aggregate_id},
            )
    finally:
        await engine.dispose()


async def _read_chunks_as_owner(owner_dsn: str, *, workspace_id: str, doc_id: str) -> list[str]:
    """``knowledge.chunks`` is FORCE-RLS like ``documents`` -- same GUC dance
    (``_read_document_as_owner``'s own recorded lesson)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT text FROM knowledge.chunks WHERE document_id = :id ORDER BY seq"),
                {"id": doc_id},
            )
            return [str(value) for value in result.scalars()]
    finally:
        await engine.dispose()


def _knowledge_subscriptions(
    tenant_session: TenantSessionFactory,
    *,
    documents: SqlDocumentRepository,
    outbox: SqlEventOutbox,
    content_resolver: WorkerDocumentContentResolver,
    vectors: QdrantVectorStore,
    files_stream: str,
    knowledge_stream: str,
    group: str,
) -> tuple[Subscription, Subscription]:
    """Both real handler closures, on this test's UNIQUE stream/group pair --
    ``build_knowledge_worker``'s own two subscriptions, named for a test
    (R6) instead of for production."""
    ledger = SqlProcessedEventLedger(tenant_session)
    register = Subscription(
        stream=files_stream,
        group=group,
        handlers={
            _EVENT_TYPE: build_knowledge_register_handler(
                documents, outbox, tenant_session, ledger, consumer_group=group
            )
        },
    )
    index = Subscription(
        stream=knowledge_stream,
        group=group,
        handlers={
            "knowledge.document.registered.v1": build_knowledge_index_handler(
                documents,
                IndexDocument(_StubEmbeddings(), vectors),
                content_resolver,
                outbox,
                tenant_session,
                ledger,
                consumer_group=group,
            )
        },
    )
    return register, index


async def _indexed_point_spaces(
    client: AsyncQdrantClient, collection: str
) -> tuple[int, set[str | None]]:
    """How many points a collection holds, and the distinct ``space`` payload
    keys across them (spaces plan §3.4).

    The count travels back with the spaces on purpose: an EMPTY collection
    would make a set-equality assertion pass against ``set()`` for the wrong
    reason, and the two facts are only meaningful together.
    """
    info = await client.get_collection(collection)
    points, _ = await client.scroll(collection, limit=100, with_payload=True)
    return info.points_count or 0, {(point.payload or {}).get("space") for point in points}


async def _cleanup_index_run(
    qdrant_client: AsyncQdrantClient,
    minio_storage: MinioStorage,
    redis_client: Redis,
    *,
    collection: str,
    storage_key: str,
    streams: tuple[str, ...],
    group: str,
) -> None:
    """Leave the three shared local services exactly as they were found --
    the ``qdrant_collection`` fixture's own "never accumulate suite
    leftovers" rule, extended to the object and the streams this test also
    creates."""
    with contextlib.suppress(Exception):
        await qdrant_client.delete_collection(collection)
    with contextlib.suppress(Exception):
        await minio_storage.delete(storage_key)
    for stream in streams:
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)


@pytest.mark.anyio
@pytest.mark.live_minio
@pytest.mark.live_qdrant
async def test_a_real_txt_file_flows_from_uploaded_all_the_way_to_indexed(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    minio_storage: MinioStorage,
    qdrant_client: AsyncQdrantClient,
) -> None:
    """Step 16's headline proof: ``file.uploaded -> registered -> indexed``
    over live Postgres + MinIO + Qdrant, with the REAL
    ``WorkerDocumentContentResolver`` filling the seam that had no adapter at
    all until this step.

    Both hops run through the real relay and the real consumer engine, so
    what is exercised is the production wiring, not a hand-called use-case:
    the register handler mints the ``pending`` document, the follow-on outbox
    row is relayed, and the index handler -- whose ``content.resolve`` now
    reaches all the way into MinIO and the parser dispatch -- finalizes the
    document as ``indexed``.
    """
    files_stream = f"stream.test.{new_uuid7()}"
    knowledge_stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    workspace_id = new_uuid7()
    file_id = new_uuid7()
    space_id = new_uuid7()
    storage_key = f"{workspace_id}/{file_id}/notes.txt"
    ctx = _ctx(workspace_id)
    collection = knowledge_collection(workspace_id)

    documents = SqlDocumentRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)

    register, index = _knowledge_subscriptions(
        tenant_session,
        documents=documents,
        outbox=outbox,
        content_resolver=WorkerDocumentContentResolver(
            files, minio_storage, DocumentContentExtractor(), _StubProviders()
        ),
        vectors=QdrantVectorStore(qdrant_client),
        files_stream=files_stream,
        knowledge_stream=knowledge_stream,
        group=group,
    )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="e2e-index",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )
    relay = OutboxRelay(
        SqlOutboxRelayStore(relay_sessionmaker),
        RedisStreamsPublisher(redis_client),
        batch_size=10,
        poll_interval_ms=100,
        max_backoff_ms=1000,
    )

    try:
        await consumer.setup([register, index])

        # 0) A real READY file row + its real bytes in MinIO.
        await files.add(
            ctx,
            _ready_file(
                workspace_id=workspace_id,
                file_id=file_id,
                name="notes.txt",
                storage_key=storage_key,
                space_id=space_id,
            ),
        )
        await minio_storage.put(storage_key, _TXT_BODY, "text/plain")

        # 1) files.file.uploaded.v1 -> relay -> register handler.
        await outbox.append(
            ctx,
            [
                _uploaded_record(
                    stream=files_stream,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    space_id=space_id,
                )
            ],
        )
        assert await relay.run_once() == 1
        assert await consumer.run_once([register]) == 1

        rows = await _read_document_as_owner(
            live_db.owner, workspace_id=workspace_id, file_id=file_id
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        # Spaces plan step 8, live: the space rode the event out of `files`
        # and landed on the `knowledge.documents` row. This is the ONE hop
        # the column depends on, and nothing else in the system would notice
        # if it were dropped until row 8-b's `SET NOT NULL`.
        assert str(rows[0]["space_id"]) == space_id
        document_id = str(rows[0]["id"])

        # 2) The follow-on registered event, relayed onto this test's stream.
        await _retarget_outbox_stream(
            live_db.owner, aggregate_id=document_id, stream=knowledge_stream
        )
        assert await relay.run_once() == 1

        # 3) The index handler: MinIO fetch + parse + embed + upsert.
        assert await consumer.run_once([index]) == 1

        # -- Assertions --------------------------------------------------- #
        indexed = await documents.get(ctx, document_id)
        assert indexed is not None
        assert indexed.status is IndexStatus.INDEXED
        assert indexed.error is None
        assert indexed.chunk_count >= 1

        # The chunks really landed in Postgres carrying the PARSED text --
        # the bytes made the whole trip out of MinIO and through the parser,
        # rather than an empty shell being written.
        chunk_texts = await _read_chunks_as_owner(
            live_db.owner, workspace_id=workspace_id, doc_id=document_id
        )
        assert len(chunk_texts) == indexed.chunk_count
        assert all(chunk_texts)
        assert "parser dispatch table" in " ".join(chunk_texts)

        # One hybrid point per chunk really landed in Qdrant, each carrying
        # the `space` payload key (§3.4) — read back out of REAL Qdrant, so
        # what is asserted is what a search would filter on. The chain proven
        # end to end is: file event ⇒ document row ⇒ point payload. Break any
        # link and a space-scoped search silently returns nothing (§5-أ),
        # which no error anywhere would report.
        count, spaces = await _indexed_point_spaces(qdrant_client, collection)
        assert (count, spaces) == (indexed.chunk_count, {space_id})

        # The terminal event was emitted in the finalizing transaction.
        done = await _read_outbox_as_owner(
            live_db.owner, aggregate_id=document_id, event_type="knowledge.document.indexed.v1"
        )
        assert len(done) == 1

        pending = await redis_client.xpending(knowledge_stream, group)
        assert pending["pending"] == 0
    finally:
        await _cleanup_index_run(
            qdrant_client,
            minio_storage,
            redis_client,
            collection=collection,
            storage_key=storage_key,
            streams=(files_stream, knowledge_stream),
            group=group,
        )
