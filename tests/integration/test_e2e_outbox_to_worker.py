"""The AC-08 proof (5.1-ج, rewritten when indexing became manual): a file
goes from bytes-in-storage to a searchable index through the REAL path a
request takes -- ``IndexFileService`` (the producer write) → ``outbox_relay``
(5.1-ب) → the knowledge worker's REAL index handler -- against real Postgres
AND real Redis.

**What changed, and why the ``files.file.uploaded.v1`` hop is gone.** This
file used to begin with that event and the knowledge worker's register
handler, because a completed upload was what registered a document. It no
longer is: the worker does not subscribe to ``stream.files`` at all, and
``POST /knowledge/documents`` mints the document and publishes
``knowledge.document.registered.v1`` inside ONE request-scoped transaction.
So the first hop here is now that service, called exactly as the router calls
it, and the second hop -- relay → index handler → chunks + points -- is
untouched, because nothing downstream of the event knows which producer wrote
it.

Both ``live_db`` and ``live_redis`` (the seam between the two infrastructures
IS the point, ``test_outbox_relay_live.py``'s own module docstring
precedent). Everything targets a FRESH, UNIQUE ``stream.test.<uuid>``/
``cg.test.<uuid>`` pair (R6), so the handler closures are built directly
rather than through ``build_knowledge_worker``, which names production
streams.

``app_rw`` composes every dependency (``SqlDocumentRepository``/
``SqlEventOutbox``/``tenant_session``-as-``uow``) exactly as the Composition
Root would in production -- the design brief's "on app_rw" instruction. The
relay runs as the real ``outbox_relay`` role (5.1-ب's own least-privilege
split, unchanged here).
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
from app.framework.errors import ConflictError
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
from app.modules.files.application.use_cases import FilesQueryService
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.use_cases import IndexFile, IndexFileService
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.value_objects import IndexStatus
from app.workers.bootstrap import build_knowledge_index_handler
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
async def test_indexing_a_file_writes_its_document_and_its_event_in_one_transaction(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    """The producer half of the pipeline, live: ``IndexFileService`` (what
    ``POST /knowledge/documents`` calls) writes the ``pending`` document AND
    its ``knowledge.document.registered.v1`` outbox row through real Postgres,
    and the real relay publishes that row onto a stream.

    A document without its event would be a file the user asked to index that
    no worker is ever told about -- reporting itself ``pending`` forever,
    indistinguishable from one merely waiting its turn. That is what the
    single ``uow.begin`` block prevents, and only a live database can show
    both rows landing under it.
    """
    stream = f"stream.test.{new_uuid7()}"
    workspace_id = new_uuid7()
    file_id = new_uuid7()
    space_id = new_uuid7()
    ctx = _ctx(workspace_id)

    documents = SqlDocumentRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    service = IndexFileService(
        IndexFile(documents, FilesQueryService(files)), outbox, tenant_session
    )

    try:
        # 0) A real READY file row -- the state `IndexFile` demands, and the
        #    only one `FilesQueryService.get_readable` answers for.
        await files.add(
            ctx,
            _ready_file(
                workspace_id=workspace_id,
                file_id=file_id,
                name="notes.txt",
                storage_key=f"{workspace_id}/{file_id}/notes.txt",
                space_id=space_id,
            ),
        )

        document = await service.start(ctx, file_id=file_id)

        # (a) the knowledge.documents row exists (RLS-correct read-back),
        #     filed under the FILE's space -- read off the file, not supplied
        #     by the caller.
        rows = await _read_document_as_owner(
            live_db.owner, workspace_id=workspace_id, file_id=file_id
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert str(rows[0]["id"]) == document.id
        assert str(rows[0]["space_id"]) == space_id

        # Also provable through the real repository itself (app_rw, RLS on).
        stored = await documents.get(ctx, document.id)
        assert stored is not None
        assert stored.file_id == file_id

        # (b) the mapped event landed in the SAME transaction.
        follow_on = await _read_outbox_as_owner(
            live_db.owner,
            aggregate_id=document.id,
            event_type="knowledge.document.registered.v1",
        )
        assert len(follow_on) == 1
        assert str(follow_on[0]["workspace_id"]) == workspace_id

        # (c) the real relay publishes it -- the row is not merely written,
        #     it is deliverable.
        await _retarget_outbox_stream(live_db.owner, aggregate_id=document.id, stream=stream)
        relay = OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=10,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        )
        assert await relay.run_once() == 1
        assert await redis_client.xlen(stream) == 1
    finally:
        await redis_client.delete(stream)


@pytest.mark.anyio
async def test_indexing_the_same_file_twice_registers_exactly_one_document(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
) -> None:
    """Live proof of the guard that replaced a delivery guarantee.

    Registration is non-idempotent by design (INV-K3 -- a re-upload mints a
    brand-new document), and while an EVENT drove it, the DD-09 ledger claim
    was what stopped a redelivery from minting a second one. There is no
    delivery here any more: the caller is a person pressing a button, so the
    duplicate this must collapse is a second CALL, and what collapses it is
    `IndexFile`'s own read of `ids_for_files`. Against the real repository,
    because that read is a query -- a fake could agree with itself while the
    SQL filtered by something else.
    """
    workspace_id = new_uuid7()
    file_id = new_uuid7()
    ctx = _ctx(workspace_id)

    documents = SqlDocumentRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    service = IndexFileService(
        IndexFile(documents, FilesQueryService(files)),
        SqlEventOutbox(tenant_session),
        tenant_session,
    )
    await files.add(
        ctx,
        _ready_file(
            workspace_id=workspace_id,
            file_id=file_id,
            name="notes.txt",
            storage_key=f"{workspace_id}/{file_id}/notes.txt",
            space_id=new_uuid7(),
        ),
    )

    await service.start(ctx, file_id=file_id)
    with pytest.raises(ConflictError):
        await service.start(ctx, file_id=file_id)

    rows = await _read_document_as_owner(live_db.owner, workspace_id=workspace_id, file_id=file_id)
    assert len(rows) == 1


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


def _index_subscription(
    tenant_session: TenantSessionFactory,
    *,
    documents: SqlDocumentRepository,
    outbox: SqlEventOutbox,
    content_resolver: WorkerDocumentContentResolver,
    vectors: QdrantVectorStore,
    knowledge_stream: str,
    group: str,
) -> Subscription:
    """The real index handler closure, on this test's UNIQUE stream/group pair
    -- ``build_knowledge_worker``'s own (now only) subscription, named for a
    test (R6) instead of for production."""
    return Subscription(
        stream=knowledge_stream,
        group=group,
        handlers={
            "knowledge.document.registered.v1": build_knowledge_index_handler(
                documents,
                IndexDocument(_StubEmbeddings(), vectors),
                content_resolver,
                outbox,
                tenant_session,
                SqlProcessedEventLedger(tenant_session),
                consumer_group=group,
            )
        },
    )


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
async def test_a_real_txt_file_flows_from_requested_all_the_way_to_indexed(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    minio_storage: MinioStorage,
    qdrant_client: AsyncQdrantClient,
) -> None:
    """Step 16's headline proof, re-cut for manual indexing: ``someone asks
    -> registered -> indexed`` over live Postgres + MinIO + Qdrant, with the
    REAL ``WorkerDocumentContentResolver`` filling the seam that had no
    adapter at all until that step.

    The first hop is ``IndexFileService``, called exactly as the router calls
    it -- the whole feature in one line: the bytes have been in MinIO the
    entire time and NOTHING indexed them, because nothing indexes anything
    until this is called. The rest runs through the real relay and the real
    consumer engine, so what is exercised downstream is production wiring
    rather than a hand-called use-case: the follow-on outbox row is relayed,
    and the index handler -- whose ``content.resolve`` reaches all the way
    into MinIO and the parser dispatch -- finalizes the document as
    ``indexed``.
    """
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

    index = _index_subscription(
        tenant_session,
        documents=documents,
        outbox=outbox,
        content_resolver=WorkerDocumentContentResolver(
            files, minio_storage, DocumentContentExtractor(), _StubProviders()
        ),
        vectors=QdrantVectorStore(qdrant_client),
        knowledge_stream=knowledge_stream,
        group=group,
    )
    index_file = IndexFileService(
        IndexFile(documents, FilesQueryService(files)), outbox, tenant_session
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
        await consumer.setup([index])

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

        # 1) Nothing has been indexed by the upload itself -- the row and
        #    the bytes are both in place and the corpus is still empty. This
        #    is the assertion the feature exists for; everything below it is
        #    what the request then does.
        assert (
            await _read_document_as_owner(live_db.owner, workspace_id=workspace_id, file_id=file_id)
            == []
        )

        # 2) The request: document + `knowledge.document.registered.v1`, one
        #    transaction.
        document_id = (await index_file.start(ctx, file_id=file_id)).id

        rows = await _read_document_as_owner(
            live_db.owner, workspace_id=workspace_id, file_id=file_id
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        # Spaces plan step 8, live: the space is read off the FILE and lands
        # on the `knowledge.documents` row. This is the ONE hop the column
        # depends on, and nothing else in the system would notice if it were
        # dropped until row 8-b's `SET NOT NULL`.
        assert str(rows[0]["space_id"]) == space_id
        assert str(rows[0]["id"]) == document_id

        # 3) The registered event, relayed onto this test's stream.
        await _retarget_outbox_stream(
            live_db.owner, aggregate_id=document_id, stream=knowledge_stream
        )
        assert await relay.run_once() == 1

        # 4) The index handler: MinIO fetch + parse + embed + upsert.
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
            streams=(knowledge_stream,),
            group=group,
        )
