"""Unit tests for the worker composition roots in ``workers/bootstrap.py``
(5.1-ج).

Hermetic: every handler closure (``build_knowledge_index_handler``/
``build_media_run_handler``/``build_memory_index_handler``) is exercised over in-memory fakes/real
use-cases, the same way ``test_media_outbox_seam.py``'s
``MediaRequestService`` tests are -- these closures ARE the worker-scoped
equivalent of that request-scoped seam.

**All THREE ``_from_env`` factories are now proven the same way, by
BUILDING.** This file used to prove one of them the opposite way -- by
asserting the "honest failure" ``build_media_worker_from_env`` raised while
``MediaGenerator`` had no adapter. That assertion is gone with the gap it
guarded: 2.10 unblocked ``build_memory_worker_from_env``
(``EmbeddingProvider``), steps 15-16 of ``deferred-adapters-plan.md``
unblocked ``build_knowledge_worker_from_env`` (``DocumentContentResolver``),
and steps 19-20 unblocked ``build_media_worker_from_env``, so each is
asserted here to return a real consumer with its real subscriptions.

Hermetic throughout: no live Redis/Postgres/Qdrant/MinIO is needed, because
every client factory involved (``create_engine``/``create_redis_client``/
``create_qdrant_client``/``create_embedding_http_client``/
``create_openai_image_http_client``) is lazy -- not one connection is opened
at construction time -- and ``build_vault``/``bind_minio``, the only pieces
that genuinely perform I/O eagerly, are monkeypatched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.storage_handle import StorageHandle
from app.framework.errors import (
    AppError,
    NotFoundError,
    UnsupportedTypeError,
    ValidationError,
)
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.ports.vector_store import VectorHit, VectorPoint
from app.framework.providers.resolver import SettingsProviderResolver
from app.framework.settings import MinioSettings, Settings
from app.framework.types import Json
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.use_cases import (
    SUMMARY_TRUNCATED_NOTICE_AR,
    SUMMARY_TRUNCATED_NOTICE_EN,
)
from app.modules.knowledge.domain.entities import Chunk, Document, Summary
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    SummaryKind,
    SummaryLanguage,
)
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind
from app.workers import bootstrap
from app.workers.bootstrap import (
    _FOREIGN_TO_KNOWLEDGE,
    _FOREIGN_TO_MEDIA,
    _KNOWLEDGE_BATCH_COUNT,
    Disposable,
    _routing_for,
    build_knowledge_index_handler,
    build_knowledge_summary_delivery_handler,
    build_knowledge_summary_handler,
    build_knowledge_worker_from_env,
    build_media_run_handler,
    build_media_worker_from_env,
    build_memory_index_handler,
    build_memory_worker_from_env,
    knowledge_stale_idle_ms,
)
from app.workers.media_generation import WorkerMediaGenerator


def _ctx(workspace_id: str = "ws-1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id="corr-1", roles=frozenset()
    )


class _FakeOutbox:
    """Minimal ``EventOutbox`` -- records every append call, in order (the
    ``test_media_outbox_seam.py``/``test_memory_use_cases.py`` precedent)."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, list[OutboxRecord]]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append((ctx, list(records)))


class _ExplodingOutbox:
    """An outbox whose append always fails (a dead database, a lost grant)."""

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        raise RuntimeError("outbox is down")


class _FakeUnitOfWork:
    """A no-op transaction boundary -- ``test_unit_of_work.py`` covers the
    real ``TenantSessionFactory.begin`` seam; this only stands in for it."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


class _TrackingUnitOfWork:
    """Reports whether it is currently "active" -- the
    ``test_media_outbox_seam.py``
    ``test_the_append_runs_inside_the_units_of_work_transaction`` precedent."""

    def __init__(self) -> None:
        self.active = False

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        self.active = True
        try:
            yield
        finally:
            self.active = False


class _FakeLedger:
    """Minimal ``ProcessedEventLedger`` (5.2-أ) -- records every claim, in
    order, and answers with a configurable outcome (``True`` = first
    delivery, ``False`` = duplicate)."""

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def claim(self, ctx: ExecutionContext, *, consumer_group: str, event_id: str) -> bool:
        self.calls.append((consumer_group, event_id))
        return self.result


# --------------------------------------------------------------------------- #
# knowledge: the document repository fake the index handler runs on           #
# --------------------------------------------------------------------------- #
class _FakeDocuments:
    """Minimal ``DocumentRepository`` -- records adds, applies status
    updates, and collects chunks (the ``_FakeMediaJobs``/``_FakeMemory``
    precedent)."""

    def __init__(self) -> None:
        self.added: list[Document] = []
        self.chunks: list[Chunk] = []

    async def get(self, ctx: ExecutionContext, doc_id: str) -> Document | None:
        return next((d for d in self.added if d.id == doc_id), None)

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        self.added.append(doc)

    async def set_status(
        self,
        ctx: ExecutionContext,
        doc_id: str,
        status: str,
        error: str | None = None,
        *,
        content_hash: str | None = None,
        pipeline_version: int | None = None,
        text_chunks: int = 0,
        table_chunks: int = 0,
        image_chunks: int = 0,
    ) -> None:
        for doc in self.added:
            if doc.id == doc_id:
                doc.status = IndexStatus(status)
                doc.error = error
                if content_hash is not None:
                    doc.content_hash = content_hash
                if pipeline_version is not None:
                    doc.pipeline_version = pipeline_version
                if status == IndexStatus.INDEXED.value:
                    doc.text_chunks = text_chunks
                    doc.table_chunks = table_chunks
                    doc.image_chunks = image_chunks

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        self.chunks.extend(chunks)


# --------------------------------------------------------------------------- #
# knowledge: build_knowledge_index_handler                                    #
# --------------------------------------------------------------------------- #
class _FakeEmbeddings:
    provider = "fake"

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[[0.1] * self.dim for _ in texts],
            model=model,
            dimensions=self.dim,
            tokens=len(texts),
        )

    def dimensions(self, model: str) -> int:
        return self.dim


class _UnusedKeyResolver:
    """A ``KeyResolver`` the routing tests must never reach: every provider
    they route to is keyless, so a call here means key resolution leaked into
    a path that only parses a table."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> object:
        raise AssertionError(f"key resolution is not part of parsing (asked for {provider!r})")


class _FakeHybridVectors:
    """Only what ``IndexDocument`` touches: ``ensure_hybrid_collection`` +
    ``upsert``."""

    def __init__(self) -> None:
        self.upserted: list[tuple[str, list[VectorPoint]]] = []

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None: ...

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None: ...

    # The real adapter drives this from `ensure_hybrid_collection` itself
    # (spaces plan step 9), so no use-case ever calls it -- it is here for the
    # same reason `search`/`delete` are: the Protocol, satisfied structurally.
    async def ensure_payload_index(
        self, collection: str, field: str, *, tenant: bool = False
    ) -> None: ...

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        self.upserted.append((collection, list(points)))

    async def search(
        self,
        collection: str,
        vector: list[float],
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
    ) -> list[VectorHit]:
        return []

    async def search_sparse(
        self,
        collection: str,
        sparse: object,
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
    ) -> list[VectorHit]:
        return []

    async def delete(self, collection: str, ids: Sequence[str]) -> None: ...


class _FakeContentResolver:
    """A ``DocumentContentResolver`` fake -- the honest-failure rule's own
    "tests inject a fake" seam."""

    def __init__(self, parsed: ParsedDocument, *, model: str = "m", api_key: str = "k") -> None:
        self._parsed = parsed
        self._model = model
        self._api_key = api_key
        self.calls: list[str] = []

    async def resolve(
        self, ctx: ExecutionContext, *, file_id: str
    ) -> tuple[ParsedDocument, str, str, str]:
        self.calls.append(file_id)
        return self._parsed, self._model, self._api_key, "hash-abc"


def _parsed_document() -> ParsedDocument:
    return ParsedDocument(
        source_ext="txt",
        content_type="text/plain",
        chunks=(
            ParsedChunk(
                text="a reasonably long piece of source text for one coarse chunk",
                order=0,
                kind=ParsedChunkKind.TEXT,
                metadata={},
            ),
        ),
        metadata={},
    )


def _pending_document(ctx: ExecutionContext, *, space_id: str | None = None) -> Document:
    now = utc_now()
    return Document(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        space_id=space_id,
        file_id="file-1",
        status=IndexStatus.PENDING,
        chunk_count=0,
        error=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _index_envelope(ctx: ExecutionContext, doc: Document) -> Json:
    return {
        "type": "knowledge.document.registered.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": {"document_id": doc.id, "file_id": "file-1"},
    }


async def test_index_handler_indexes_a_registered_document_and_appends_its_event() -> None:
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    documents.added.append(doc)
    pipeline = IndexDocument(_FakeEmbeddings(), _FakeHybridVectors())
    content = _FakeContentResolver(_parsed_document())
    outbox = _FakeOutbox()
    handler = build_knowledge_index_handler(
        documents, pipeline, content, outbox, _FakeUnitOfWork(), _FakeLedger()
    )

    await handler(ctx, _index_envelope(ctx, doc))

    assert doc.status is IndexStatus.INDEXED
    assert content.calls == ["file-1"]
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["knowledge.document.indexed.v1"]


async def test_index_handler_appends_no_event_and_claims_nothing_on_the_noop_path() -> None:
    """An already-INDEXED document short-circuits in ``run`` -> no
    transaction, no DD-09 claim, no append (5.2-أ: a claim row for a no-op
    would spend a write to record nothing -- the aggregate's own terminal
    guard already made the second run free)."""
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    doc.status = IndexStatus.INDEXED
    documents.added.append(doc)
    pipeline = IndexDocument(_FakeEmbeddings(), _FakeHybridVectors())
    content = _FakeContentResolver(_parsed_document())
    outbox = _FakeOutbox()
    ledger = _FakeLedger()
    handler = build_knowledge_index_handler(
        documents, pipeline, content, outbox, _FakeUnitOfWork(), ledger
    )

    await handler(ctx, _index_envelope(ctx, doc))

    assert outbox.calls == []
    assert ledger.calls == []


async def test_index_handler_duplicate_claim_skips_finalize_and_append() -> None:
    """A ``False`` claim after ``run`` means another delivery already
    finalized this event: THIS delivery must write neither chunks nor a
    terminal status nor an outbox row -- the document object stays at the
    ``indexing`` state ``run`` left it in (the OTHER delivery's committed
    transaction owns the terminal truth)."""
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    documents.added.append(doc)
    pipeline = IndexDocument(_FakeEmbeddings(), _FakeHybridVectors())
    content = _FakeContentResolver(_parsed_document())
    outbox = _FakeOutbox()
    ledger = _FakeLedger(result=False)
    handler = build_knowledge_index_handler(
        documents, pipeline, content, outbox, _FakeUnitOfWork(), ledger
    )
    envelope = _index_envelope(ctx, doc)

    await handler(ctx, envelope)

    assert doc.status is IndexStatus.INDEXING  # run's claim, never finalized here
    assert documents.chunks == []
    assert outbox.calls == []
    assert ledger.calls == [("cg.knowledge", envelope["id"])]


async def test_index_handler_claim_finalize_and_append_share_the_unit_of_work() -> None:
    """The D5 terminal window, closed (5.2-أ): the DD-09 claim, the chunk
    write, the terminal status, and the outbox append must ALL happen while
    the ONE ``uow.begin`` block is active -- any of them drifting outside it
    reopens the crash window 5.1-ج documented."""
    uow = _TrackingUnitOfWork()
    ctx = _ctx()
    active_at: dict[str, bool] = {}

    class _SpyDocuments(_FakeDocuments):
        async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
            active_at["add_chunks"] = uow.active
            await super().add_chunks(ctx, chunks)

        async def set_status(
            self,
            ctx: ExecutionContext,
            doc_id: str,
            status: str,
            error: str | None = None,
            *,
            content_hash: str | None = None,
            pipeline_version: int | None = None,
            text_chunks: int = 0,
            table_chunks: int = 0,
            image_chunks: int = 0,
        ) -> None:
            if status in (IndexStatus.INDEXED.value, IndexStatus.FAILED.value):
                active_at["terminal_status"] = uow.active
            await super().set_status(
                ctx,
                doc_id,
                status,
                error,
                content_hash=content_hash,
                pipeline_version=pipeline_version,
                text_chunks=text_chunks,
                table_chunks=table_chunks,
                image_chunks=image_chunks,
            )

    class _SpyOutbox:
        async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
            active_at["append"] = uow.active

    class _SpyLedger:
        async def claim(self, ctx: ExecutionContext, *, consumer_group: str, event_id: str) -> bool:
            active_at["claim"] = uow.active
            return True

    documents = _SpyDocuments()
    doc = _pending_document(ctx)
    documents.added.append(doc)
    pipeline = IndexDocument(_FakeEmbeddings(), _FakeHybridVectors())
    handler = build_knowledge_index_handler(
        documents,
        pipeline,
        _FakeContentResolver(_parsed_document()),
        _SpyOutbox(),
        uow,
        _SpyLedger(),
    )

    await handler(ctx, _index_envelope(ctx, doc))

    assert active_at == {
        "claim": True,
        "add_chunks": True,
        "terminal_status": True,
        "append": True,
    }
    assert uow.active is False


# --------------------------------------------------------------------------- #
# knowledge: the poisoned-file gap (step 16 · deferred-adapters-plan.md §1-ج) #
# --------------------------------------------------------------------------- #
class _ExplodingContentResolver:
    """A ``DocumentContentResolver`` whose ``resolve`` always raises -- the
    real one does exactly this for a file no parser routes
    (``UnsupportedTypeError``) or one that fails to parse
    (``ValidationError``), and for infrastructure faults (anything else)."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[str] = []

    async def resolve(
        self, ctx: ExecutionContext, *, file_id: str
    ) -> tuple[ParsedDocument, str, str, str]:
        self.calls.append(file_id)
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        UnsupportedTypeError("unsupported file type: 'contract.docx'"),
        ValidationError("failed to parse 'broken.pdf': cannot open"),
    ],
    ids=["unsupported_type", "parse_failed"],
)
async def test_index_handler_lands_an_unparseable_file_in_failed_with_its_event(
    error: Exception,
) -> None:
    """The §1-ج gap, closed. BEFORE this step the exception raised here
    escaped ``_handle`` entirely -- it is thrown OUTSIDE the broad catch that
    lives inside ``IndexRegisteredDocument.run`` -- so the engine never
    ``XACK``\\ ed the message, redelivered it up to ``max_deliveries``, and
    dropped it in the DLQ. The document stayed ``pending`` FOREVER and not
    one event ever told the user why.

    The failure mechanism this asserts against is exactly that: the handler
    must RETURN, having produced a terminal document and its
    ``indexing_failed`` event. On the old code this test fails by the
    exception propagating out of ``await handler(...)``.
    """
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    documents.added.append(doc)
    outbox = _FakeOutbox()
    ledger = _FakeLedger()
    handler = build_knowledge_index_handler(
        documents,
        IndexDocument(_FakeEmbeddings(), _FakeHybridVectors()),
        _ExplodingContentResolver(error),
        outbox,
        _FakeUnitOfWork(),
        ledger,
    )
    envelope = _index_envelope(ctx, doc)

    await handler(ctx, envelope)

    assert doc.status is IndexStatus.FAILED
    assert doc.error == str(error)
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["knowledge.document.indexing_failed.v1"]
    # The terminal write is claimed like any other effect -- a redelivery
    # that races it must not produce a second failure event.
    assert ledger.calls == [("cg.knowledge", envelope["id"])]
    # Nothing was indexed: a document that could not be parsed has no chunks.
    assert documents.chunks == []


async def test_index_handler_still_lets_infrastructure_faults_escape_for_redelivery() -> None:
    """The other half of the §1-ج decision, and the reason the ``except``
    names two types instead of catching broadly: a MinIO/Vault/Postgres fault
    MAY succeed on the next delivery, so it must keep escaping to the engine
    and be retried. Marking it terminal would burn a user's document on a
    thirty-second outage."""
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    documents.added.append(doc)
    outbox = _FakeOutbox()
    handler = build_knowledge_index_handler(
        documents,
        IndexDocument(_FakeEmbeddings(), _FakeHybridVectors()),
        _ExplodingContentResolver(AppError("object storage is unreachable")),
        outbox,
        _FakeUnitOfWork(),
        _FakeLedger(),
    )

    with pytest.raises(AppError):
        await handler(ctx, _index_envelope(ctx, doc))

    assert doc.status is IndexStatus.PENDING
    assert outbox.calls == []


async def test_index_handler_does_not_re_fail_an_already_terminal_document() -> None:
    """The DD-09 redelivery guard reaches the new path too: a document that
    already failed must not be failed a second time, nor emit a second
    event."""
    ctx = _ctx()
    documents = _FakeDocuments()
    doc = _pending_document(ctx)
    doc.status = IndexStatus.FAILED
    doc.error = "the original reason"
    documents.added.append(doc)
    outbox = _FakeOutbox()
    ledger = _FakeLedger()
    handler = build_knowledge_index_handler(
        documents,
        IndexDocument(_FakeEmbeddings(), _FakeHybridVectors()),
        _ExplodingContentResolver(UnsupportedTypeError("unsupported file type: 'x.docx'")),
        outbox,
        _FakeUnitOfWork(),
        ledger,
    )

    await handler(ctx, _index_envelope(ctx, doc))

    assert doc.error == "the original reason"
    assert outbox.calls == []
    assert ledger.calls == []


# --------------------------------------------------------------------------- #
# media: build_media_run_handler                                              #
# --------------------------------------------------------------------------- #
class _FakeJobs:
    def __init__(self) -> None:
        self.added: list[MediaJob] = []

    async def get(self, ctx: ExecutionContext, job_id: str) -> MediaJob | None:
        return next((j for j in self.added if j.id == job_id), None)

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.added.append(job)

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None:
        for index, existing in enumerate(self.added):
            if existing.id == job.id:
                self.added[index] = job
                return
        self.added.append(job)


class _FakeGenerator:
    def __init__(self, result_file_id: str = "file-out") -> None:
        self._result_file_id = result_file_id

    async def generate(
        self, ctx: ExecutionContext, *, kind: MediaKind, prompt: str, params: GenParams
    ) -> str:
        return self._result_file_id


def _queued_job(ctx: ExecutionContext) -> MediaJob:
    now = utc_now()
    return MediaJob(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        agent_key=AgentKey("image-agent"),
        kind=MediaKind.IMAGE,
        prompt="a cat wearing sunglasses",
        params=GenParams.from_dict(MediaKind.IMAGE, {"width": 64, "height": 64}),
        status=JobStatus.QUEUED,
        result_file_id=None,
        error=None,
        created_by=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _media_envelope(ctx: ExecutionContext, job: MediaJob) -> Json:
    return {
        "type": "media.job.requested.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": {"job_id": job.id},
    }


async def test_media_run_handler_completes_a_job_and_appends_its_generated_event() -> None:
    ctx = _ctx()
    jobs = _FakeJobs()
    job = _queued_job(ctx)
    jobs.added.append(job)
    outbox = _FakeOutbox()
    handler = build_media_run_handler(
        jobs, _FakeGenerator("file-out-1"), outbox, _FakeUnitOfWork(), _FakeLedger()
    )

    await handler(ctx, _media_envelope(ctx, job))

    assert jobs.added[0].status is JobStatus.SUCCEEDED
    assert jobs.added[0].result_file_id == "file-out-1"
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["media.job.generated.v1"]


async def test_media_run_handler_appends_the_outbox_record_inside_the_unit_of_work() -> None:
    uow = _TrackingUnitOfWork()

    class _SpyOutbox:
        def __init__(self) -> None:
            self.appended_while_active: bool | None = None

        async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
            self.appended_while_active = uow.active

    ctx = _ctx()
    jobs = _FakeJobs()
    job = _queued_job(ctx)
    jobs.added.append(job)
    outbox = _SpyOutbox()
    handler = build_media_run_handler(jobs, _FakeGenerator(), outbox, uow, _FakeLedger())

    await handler(ctx, _media_envelope(ctx, job))

    assert outbox.appended_while_active is True
    assert uow.active is False


async def test_media_run_handler_duplicate_claim_skips_finalize_and_append() -> None:
    """The knowledge index handler's duplicate shape, on media: a ``False``
    claim after ``run`` leaves the job at ``running`` (the other delivery's
    committed transaction owns the terminal truth) and appends nothing."""
    ctx = _ctx()
    jobs = _FakeJobs()
    job = _queued_job(ctx)
    jobs.added.append(job)
    outbox = _FakeOutbox()
    ledger = _FakeLedger(result=False)
    handler = build_media_run_handler(
        jobs, _FakeGenerator("file-out-1"), outbox, _FakeUnitOfWork(), ledger
    )
    envelope = _media_envelope(ctx, job)

    await handler(ctx, envelope)

    assert jobs.added[0].status is JobStatus.RUNNING  # run's claim, never finalized here
    assert jobs.added[0].result_file_id is None
    assert outbox.calls == []
    assert ledger.calls == [("cg.media", envelope["id"])]


async def test_media_run_handler_claims_nothing_on_the_terminal_noop_path() -> None:
    """An already-terminal job short-circuits in ``run`` -- no transaction,
    no claim, no append (the knowledge noop shape, on media)."""
    ctx = _ctx()
    jobs = _FakeJobs()
    job = _queued_job(ctx)
    job.status = JobStatus.SUCCEEDED
    job.result_file_id = "file-done"
    jobs.added.append(job)
    outbox = _FakeOutbox()
    ledger = _FakeLedger()
    handler = build_media_run_handler(jobs, _FakeGenerator(), outbox, _FakeUnitOfWork(), ledger)

    await handler(ctx, _media_envelope(ctx, job))

    assert outbox.calls == []
    assert ledger.calls == []


# --------------------------------------------------------------------------- #
# knowledge: build_knowledge_summary_handler — `F-4` (plan §4 step 4, §3.5)   #
# --------------------------------------------------------------------------- #
class _FakeSummaryAttempt:
    """Stands in for ``SummaryAttempt`` -- the closure only ever passes it on."""

    def __init__(self, *, error: str | None = None) -> None:
        self.error = error


class _FakeBuildSummary:
    """A fake shaped like ``BuildSummary``'s claim/run/fail/finalize split.

    ``run`` sleeps for ``run_seconds`` so ``asyncio.timeout`` has something
    real to interrupt -- the point of `F-4` is what the handler does when a
    build does not come back, and a fake that returns instantly could not
    exercise it. It also records the ``on_heartbeat`` it was handed, which is
    the whole of what the handler owes the heartbeat: WHETHER a build gets
    one is this closure's decision, what a build then does with it is
    ``BuildSummary``'s (``test_knowledge_summaries.py`` proves that half)."""

    def __init__(self, *, run_seconds: float = 0.0) -> None:
        self.run_calls = 0
        self.on_heartbeat: object = "not-called"
        self.failed: list[str] = []
        self.finalized: list[object] = []
        self.threads: list[str | None] = []  # `F-7` — what `finalize` was told
        self._run_seconds = run_seconds

    async def claim(self, ctx: ExecutionContext, *, job_id: str) -> object:
        return object()  # a plan; the closure only ever hands it to `run`

    async def run(
        self, ctx: ExecutionContext, plan: object, *, on_heartbeat: object = None
    ) -> _FakeSummaryAttempt:
        self.run_calls += 1
        self.on_heartbeat = on_heartbeat
        if self._run_seconds:
            await asyncio.sleep(self._run_seconds)
        return _FakeSummaryAttempt()

    async def fail(self, ctx: ExecutionContext, *, job_id: str, reason: str) -> _FakeSummaryAttempt:
        self.failed.append(reason)
        return _FakeSummaryAttempt(error=reason)

    async def finalize(
        self, ctx: ExecutionContext, attempt: object, *, conversation_id: str | None = None
    ) -> tuple[object, tuple[object, ...]]:
        self.finalized.append(attempt)
        self.threads.append(conversation_id)
        return object(), ()


class _CountingHeartbeat:
    """A structural ``Heartbeat``. No ``read`` side, like the real one."""

    def __init__(self) -> None:
        self.beats = 0

    def beat(self) -> None:
        self.beats += 1


def _summary_envelope(ctx: ExecutionContext) -> Json:
    return {
        "type": "knowledge.summary.requested.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": {"job_id": "job-1"},
    }


async def test_summary_handler_ends_a_build_that_outruns_its_budget_in_failed_with_a_reason() -> (
    None
):
    """`F-4`: a build that will not finish becomes ONE failed job carrying a
    sentence, instead of an unhandled exception.

    What it replaces is not a slower success. A handler that never returns is
    redelivered (DD-09); ``SummaryJob.start`` is re-entrant from ``running``,
    so the build restarts from the first chunk and meets the same wall --
    five times, to the DLQ -- with the job holding ``uq_summary_job_active``
    the whole way, so the user cannot even ask again.

    The timeout is answered HERE and not inside ``run``: ``asyncio.timeout``
    cancels, and ``run``'s broad ``except Exception`` rightly does not catch
    a ``CancelledError``. It is routed through the SAME ``fail`` the
    unresolvable-route branch uses, so there stays one path to a failed job.
    """
    build = _FakeBuildSummary(run_seconds=30.0)
    outbox = _FakeOutbox()
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        outbox,
        _FakeUnitOfWork(),
        _FakeLedger(),
        max_duration_s=0.01,
    )
    ctx = _ctx()

    await handler(ctx, _summary_envelope(ctx))  # nothing escapes

    assert build.failed == ["building this summary exceeded the 0.01s limit and was stopped"]
    # And the failure still takes the terminal transaction every other
    # outcome takes -- a reason nobody records is a reason nobody reads.
    assert len(build.finalized) == 1


async def test_summary_handler_leaves_the_build_unbounded_when_no_budget_is_configured() -> None:
    """The default is off, the ``sweep_interval_s`` precedent: every direct
    caller of this builder -- the live integration tests included -- keeps
    exactly today's behaviour, and only ``_from_env`` turns the cap on.
    ``asyncio.timeout(None)`` is the documented no-op, so this costs no
    branch of its own."""
    build = _FakeBuildSummary(run_seconds=0.05)
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
    )
    ctx = _ctx()

    await handler(ctx, _summary_envelope(ctx))

    assert build.failed == []
    assert build.run_calls == 1
    assert len(build.finalized) == 1


async def test_summary_handler_hands_the_build_the_workers_own_heartbeat() -> None:
    """`F-4`: the SAME ``Heartbeat`` the consumer loop is given, not a second
    one -- so a long build beats into the file ``app.ops.healthcheck``
    actually reads. The engine beats between messages and cannot beat during
    one, and a summary build is the only handler here long enough for that
    gap to reach ``heartbeat_max_age_s``."""
    heartbeat = _CountingHeartbeat()
    build = _FakeBuildSummary()
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
        heartbeat=heartbeat,
    )
    ctx = _ctx()

    await handler(ctx, _summary_envelope(ctx))

    assert build.on_heartbeat == heartbeat.beat


async def test_summary_handler_passes_no_heartbeat_when_it_has_none() -> None:
    """``None`` travels as ``None`` rather than as a no-op closure: a build
    given nothing to beat must be able to tell, and ``NullHeartbeat`` already
    exists for the deployment that genuinely wants a silent one."""
    build = _FakeBuildSummary()
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
    )
    ctx = _ctx()

    await handler(ctx, _summary_envelope(ctx))

    assert build.on_heartbeat is None


# --------------------------------------------------------------------------- #
# memory: build_memory_index_handler                                          #
# --------------------------------------------------------------------------- #
class _FakeMemoryAttempt:
    """Stands in for ``MemoryIndexAttempt`` -- only what the closure reads."""

    def __init__(self, noop: bool) -> None:
        self.is_redelivery_noop = noop


class _FakeIndexMemoryItem:
    """A fake shaped like ``IndexMemoryItem``'s 5.2-أ split -- proves the
    closure forwards ``memory_id``/``model``/``api_key`` to ``run`` and hands
    ``run``'s own attempt to ``finalize``, without re-exercising the real
    use-case (``test_memory_use_cases.py`` already does that)."""

    def __init__(self, *, noop: bool = False) -> None:
        self.run_calls: list[dict[str, str]] = []
        self.finalized: list[object] = []
        self._noop = noop

    async def run(
        self, ctx: ExecutionContext, *, memory_id: str, model: str, api_key: str
    ) -> _FakeMemoryAttempt:
        self.run_calls.append({"memory_id": memory_id, "model": model, "api_key": api_key})
        return _FakeMemoryAttempt(self._noop)

    async def finalize(self, ctx: ExecutionContext, attempt: object) -> object:
        self.finalized.append(attempt)
        return object()


def _memory_envelope(ctx: ExecutionContext) -> Json:
    return {
        "type": "memory.item.stored.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": {"memory_id": "mem-1", "agent_key": "rag-agent"},
    }


async def test_memory_index_handler_forwards_args_to_run_and_finalizes_its_attempt() -> None:
    use_case = _FakeIndexMemoryItem()
    handler = build_memory_index_handler(
        use_case,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        model="text-embedding-3",
        api_key="secret-key",
    )
    ctx = _ctx()

    await handler(ctx, _memory_envelope(ctx))

    assert use_case.run_calls == [
        {"memory_id": "mem-1", "model": "text-embedding-3", "api_key": "secret-key"}
    ]
    assert len(use_case.finalized) == 1


async def test_memory_index_handler_duplicate_claim_skips_finalize() -> None:
    use_case = _FakeIndexMemoryItem()
    ledger = _FakeLedger(result=False)
    handler = build_memory_index_handler(
        use_case,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        ledger,
        model="m",
        api_key="k",
    )
    ctx = _ctx()
    envelope = _memory_envelope(ctx)

    await handler(ctx, envelope)

    assert use_case.finalized == []
    assert ledger.calls == [("cg.memory", envelope["id"])]


async def test_memory_index_handler_claims_nothing_on_the_r3_noop_path() -> None:
    """An already-indexed item short-circuits in ``run`` (R3) -- no
    transaction, no claim, no finalize."""
    use_case = _FakeIndexMemoryItem(noop=True)
    ledger = _FakeLedger()
    handler = build_memory_index_handler(
        use_case,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        ledger,
        model="m",
        api_key="k",
    )
    ctx = _ctx()

    await handler(ctx, _memory_envelope(ctx))

    assert use_case.finalized == []
    assert ledger.calls == []


async def test_memory_index_handler_claim_and_finalize_share_the_unit_of_work() -> None:
    """5.2-أ gave this handler its first ``uow.begin`` PURELY so the DD-09
    claim and the one terminal write commit together -- both must observe an
    active unit of work."""
    uow = _TrackingUnitOfWork()
    active_at: dict[str, bool] = {}

    class _SpyLedger:
        async def claim(self, ctx: ExecutionContext, *, consumer_group: str, event_id: str) -> bool:
            active_at["claim"] = uow.active
            return True

    class _SpyIndexMemoryItem(_FakeIndexMemoryItem):
        async def finalize(self, ctx: ExecutionContext, attempt: object) -> object:
            active_at["finalize"] = uow.active
            return await super().finalize(ctx, attempt)

    handler = build_memory_index_handler(
        _SpyIndexMemoryItem(),  # type: ignore[arg-type]
        uow,
        _SpyLedger(),
        model="m",
        api_key="k",
    )
    ctx = _ctx()

    await handler(ctx, _memory_envelope(ctx))

    assert active_at == {"claim": True, "finalize": True}
    assert uow.active is False


# --------------------------------------------------------------------------- #
# The honest-failure rule: only `media` still raises. `memory` stopped in 2.10 #
# (EmbeddingProvider), `knowledge` in step 16 (DocumentContentResolver) --     #
# module docstring.                                                            #
# --------------------------------------------------------------------------- #
async def test_build_knowledge_worker_from_env_builds_a_real_consumer_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 16 closes the LAST blocked seam: this factory now returns a fully
    wired worker instead of raising. On the old code it raised an ``AppError``
    naming ``DocumentContentResolver`` -- the same failure mechanism, from the
    same call.

    Hermetic: ``build_vault``/``bind_minio`` are monkeypatched with fakes --
    the real ones perform an AppRole login and a live Vault/MinIO read, which
    this suite must never touch. Everything else is genuinely constructed:
    every client factory involved (``create_engine``/``create_qdrant_client``/
    ``create_embedding_http_client``/``create_redis_client``) is lazy, so not
    one connection is opened here.

    ``bind_minio`` being awaited with the ``StorageHandle`` the factory built
    is step 15's proof, kept: storage is wired from env, and it is that same
    handle the content resolver now fetches bytes through."""
    fake_secrets = object()
    bind_calls: list[tuple[object, object, object]] = []

    def _fake_build_vault(settings: object) -> tuple[object, object, object]:
        return object(), fake_secrets, object()

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        bind_calls.append((storage, secrets, settings))

    monkeypatch.setattr("app.workers.bootstrap.build_vault", _fake_build_vault)
    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)

    consumer, subscriptions, disposables = await build_knowledge_worker_from_env()

    assert isinstance(consumer, StreamConsumer)
    # 04 §4's binding table, minus the row manual indexing retired: ONE
    # `cg.knowledge` group over ONE stream. `stream.files` was the other, and
    # its disappearance IS the feature -- a worker that still subscribed to it
    # would still be registering a document for every completed upload.
    assert [(s.stream, s.group) for s in subscriptions] == [
        ("stream.knowledge", "cg.knowledge"),
    ]
    # THREE handlers on `stream.knowledge` since `F-7`, still under the one
    # `cg.knowledge` group: a summary build -- and the delivery of what it
    # produced -- is more work for the process that already owns this stream,
    # not a reason for a second consumer group. The third one consumes an
    # event this same worker publishes, exactly as the first consumes
    # `document.registered.v1` to publish `document.indexed.v1`.
    assert set(subscriptions[0].handlers) == {
        "knowledge.document.registered.v1",
        "knowledge.summary.requested.v1",
        "knowledge.summary.built.v1",
    }
    # engine.dispose, qdrant_client.close, embedding_http.aclose, the FOUR
    # LLM clients, redis_client.aclose, _close_vault -- `_close_vault` is
    # step 15's, written then against a function that could not yet reach it.
    # The LLM clients joined the list at `F-1`: the 60 s pair had been leaking
    # a connection pool per shutdown since BE-RAG-009 wired it, and `F-1`'s
    # 300 s pair would have made that two.
    assert len(disposables) == 9
    for dispose in disposables:
        disposable: Disposable = dispose
        assert callable(disposable)

    assert len(bind_calls) == 1
    storage, secrets, minio_settings = bind_calls[0]
    assert isinstance(storage, StorageHandle)
    assert secrets is fake_secrets
    assert isinstance(minio_settings, MinioSettings)


async def test_the_summarizer_resolves_through_its_own_longer_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`F-1` (rag-summarization-fix-plan.md §3.1): the map-reduce resolves its
    provider through a SECOND ``SettingsProviderResolver`` whose HTTP clients
    carry ``summarize_timeout_s`` (300 s), while everything else in this
    process keeps ``llm_timeout_s`` (60 s).

    The defect this pins is not a slow call, it is a call that could not
    finish: an httpx timeout is set on the CLIENT, the adapters call
    ``complete`` (``stream: false``) so a provider emits nothing until the
    whole generation is done, and 60 s therefore capped an entire map call
    over ~6,600 characters of document text. Every long document died there.

    Asserted at the two seams that can actually drift -- which factory got
    which number, and which resolver the summarizer was handed -- rather than
    by reading a timeout back off a private client attribute. Both spies
    delegate to the real constructors, so this is still the genuine wiring:
    two resolvers really are built, over the same routing table.
    """
    fake_secrets = object()

    def _fake_build_vault(settings: object) -> tuple[object, object, object]:
        return object(), fake_secrets, object()

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        return None

    monkeypatch.setattr("app.workers.bootstrap.build_vault", _fake_build_vault)
    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)

    ollama_budgets: list[float] = []
    openai_budgets: list[float] = []
    resolvers: list[SettingsProviderResolver] = []
    summarizer_got: list[object] = []

    real_ollama_client = bootstrap.create_ollama_http_client
    real_openai_client = bootstrap.create_openai_http_client
    real_resolver = bootstrap.SettingsProviderResolver
    real_summarizer = bootstrap._WorkerSummarizerResolver

    def _spy_ollama(settings: object, *, timeout_s: float, **kwargs: object) -> object:
        ollama_budgets.append(timeout_s)
        return real_ollama_client(settings, timeout_s=timeout_s, **kwargs)  # type: ignore[arg-type]

    def _spy_openai(*, timeout_s: float, **kwargs: object) -> object:
        openai_budgets.append(timeout_s)
        return real_openai_client(timeout_s=timeout_s, **kwargs)  # type: ignore[arg-type]

    def _spy_resolver(**kwargs: object) -> SettingsProviderResolver:
        resolver = real_resolver(**kwargs)  # type: ignore[arg-type]
        resolvers.append(resolver)
        return resolver

    def _spy_summarizer(providers: object) -> object:
        summarizer_got.append(providers)
        return real_summarizer(providers)  # type: ignore[arg-type]

    monkeypatch.setattr("app.workers.bootstrap.create_ollama_http_client", _spy_ollama)
    monkeypatch.setattr("app.workers.bootstrap.create_openai_http_client", _spy_openai)
    monkeypatch.setattr("app.workers.bootstrap.SettingsProviderResolver", _spy_resolver)
    monkeypatch.setattr("app.workers.bootstrap._WorkerSummarizerResolver", _spy_summarizer)

    settings = load_settings()
    await build_knowledge_worker_from_env()

    # One client per adapter per budget: the interactive pair first, the
    # summarisation pair second. Equal numbers would mean the second pair was
    # never built; a single 300 would mean it replaced the first rather than
    # joining it.
    assert ollama_budgets == [settings.limits.llm_timeout_s, settings.limits.summarize_timeout_s]
    assert openai_budgets == [settings.limits.llm_timeout_s, settings.limits.summarize_timeout_s]
    assert settings.limits.summarize_timeout_s > settings.limits.llm_timeout_s

    # TWO resolvers, and the summarizer holds the SECOND. If this ever reads
    # `resolvers[0]` again the whole step is undone silently -- every call
    # still works, just with the 60 s clients back underneath.
    assert len(resolvers) == 2
    assert summarizer_got == [resolvers[1]]


async def test_the_summary_handler_is_built_with_the_workers_own_heartbeat_and_duration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`F-4`: the wiring, not the behaviour -- the two tests above prove what
    the handler DOES with a heartbeat and a cap, and this one proves it is
    given them at all.

    Both halves would fail silently otherwise. Drop ``heartbeat=heartbeat``
    from the call in ``build_knowledge_worker`` and every summary still
    builds correctly; the only difference is a container declared unhealthy
    and restarted in the middle of the longest job it runs, which no unit
    test would have noticed. Drop ``max_duration_s`` and nothing changes
    until a build hangs, which is the case the whole step exists for.

    ``is`` and not ``==`` on the heartbeat: a SECOND ``FileHeartbeat`` over
    the same path would compare unequal and would also be a real defect --
    two objects, two ``_failing`` flags, so the "log the transition, not
    every repeat" policy would report a write failure twice and a recovery
    twice."""
    fake_secrets = object()

    def _fake_build_vault(settings: object) -> tuple[object, object, object]:
        return object(), fake_secrets, object()

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        return None

    monkeypatch.setattr("app.workers.bootstrap.build_vault", _fake_build_vault)
    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)

    heartbeats: list[object] = []
    captured: dict[str, object] = {}
    real_heartbeat = bootstrap.build_heartbeat
    real_handler = bootstrap.build_knowledge_summary_handler

    def _spy_heartbeat(directory: str, name: str) -> object:
        built = real_heartbeat(directory, name)
        heartbeats.append(built)
        return built

    def _spy_handler(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return real_handler(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.workers.bootstrap.build_heartbeat", _spy_heartbeat)
    monkeypatch.setattr("app.workers.bootstrap.build_knowledge_summary_handler", _spy_handler)

    await build_knowledge_worker_from_env()

    settings = load_settings()
    assert captured["max_duration_s"] == settings.limits.summarize_job_max_duration_s
    # ONE heartbeat is built for this process, and the summary handler holds
    # that very object -- the same file `app.ops.healthcheck knowledge` reads.
    assert len(heartbeats) == 1
    assert captured["heartbeat"] is heartbeats[0]


def test_routing_for_drops_the_foreign_namespaces_and_keeps_everything_else() -> None:
    """The knowledge worker wires no image adapter, so handing its resolver
    that namespace would refuse construction on a route it never reads.

    ``llm`` USED to be foreign here too, and BE-RAG-009 removed it: this
    process now runs the summarisation map-reduce, so it wires the LLM
    adapters and reads the ``summarize`` route. Keeping it foreign would have
    meant resolving a model through a table this worker declined to be judged
    on.

    Written as "drop the known-foreign names" so a MISSPELLED namespace still
    reaches the strict parse and is refused there, instead of being silently
    discarded by a keep-list."""
    embedding = {"default": {"provider": "embedding-local", "model": "minilm"}}
    llm = {"summarize": {"provider": "openai", "model": "gpt"}}

    assert _routing_for(
        {"llm": llm, "embedding": embedding, "image": {}},
        foreign=_FOREIGN_TO_KNOWLEDGE,
    ) == {"llm": llm, "embedding": embedding}
    assert _routing_for({}, foreign=_FOREIGN_TO_KNOWLEDGE) == {}
    assert _routing_for({"embeddings": embedding}, foreign=_FOREIGN_TO_KNOWLEDGE) == {
        "embeddings": embedding
    }


def test_the_two_workers_narrow_the_routing_table_to_their_own_capabilities() -> None:
    """The narrowing is per-worker, and neither may drop a namespace its own
    process actually reads: ``knowledge`` keeps ``embedding`` (it indexes) and
    ``llm`` (BE-RAG-009 — it summarises) while dropping ``image``; ``media``
    keeps only ``image``. A single shared constant (what this file had until
    step 20) cannot express that, and getting it backwards would not fail
    loudly — it would boot a worker whose resolver has no route for a call it
    makes.

    The halves stopped being DISJOINT at BE-RAG-009, which is why this test
    was renamed: ``llm`` is now foreign to ``media`` alone. Disjointness was
    never the property worth asserting — "each worker keeps exactly what it
    wires" is, and that survives a second worker needing the same namespace.
    """
    llm = {"summarize": {"provider": "openai", "model": "gpt"}}
    embedding = {"default": {"provider": "embedding-local", "model": "minilm"}}
    image = {"default": {"provider": "image:openai", "model": "gpt-image-1"}}
    routing: Json = {"llm": llm, "embedding": embedding, "image": image}

    assert _routing_for(routing, foreign=_FOREIGN_TO_KNOWLEDGE) == {
        "llm": llm,
        "embedding": embedding,
    }
    assert _routing_for(routing, foreign=_FOREIGN_TO_MEDIA) == {"image": image}
    # The media worker generates images and makes no LLM call; the knowledge
    # worker is the reverse on both counts.
    assert "llm" in _FOREIGN_TO_MEDIA
    assert "llm" not in _FOREIGN_TO_KNOWLEDGE
    assert "image" in _FOREIGN_TO_KNOWLEDGE


def test_an_image_route_does_not_block_the_knowledge_worker() -> None:
    """Step 18's collateral, caught before it shipped: the moment ``image``
    became a legal namespace, an operator's image route would have travelled
    into THIS worker's resolver -- which passes ``image_providers={}`` -- and
    refused to boot a process that generates no images. The namespace joins
    ``llm`` in the foreign set for exactly the reason ``llm`` is there.

    The assertion is the whole point of the narrowing, so it is made against
    the REAL parse, not just the dict shape: the same table that is fine here
    is deliberately fatal to a root that claims the capability."""
    routing: Json = {
        "embedding": {"default": {"provider": "fake", "model": "minilm"}},
        "image": {"default": {"provider": "openai", "model": "gpt-image"}},
    }
    assert "image" not in _routing_for(routing, foreign=_FOREIGN_TO_KNOWLEDGE)

    embeddings = {"fake": _FakeEmbeddings()}
    SettingsProviderResolver(
        routing=_routing_for(routing, foreign=_FOREIGN_TO_KNOWLEDGE),
        llm_providers={},
        embedding_providers=embeddings,
        image_providers={},
        key_resolver=_UnusedKeyResolver(),
        keyless_providers=frozenset({"fake"}),
    )

    with pytest.raises(ValidationError) as exc_info:
        SettingsProviderResolver(
            routing=routing,
            llm_providers={},
            embedding_providers=embeddings,
            image_providers={},
            key_resolver=_UnusedKeyResolver(),
            keyless_providers=frozenset({"fake"}),
        )
    assert "provider_routing.image['default']" in str(exc_info.value)


async def test_build_media_worker_from_env_builds_a_real_consumer_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 20 closes the LAST blocked builder in this module. On the old code
    this same call raised ``AppError``/``common.internal`` naming
    ``MediaGenerator`` -- the defect's own mechanism, from the same call site,
    which is why this test replaces that one rather than sitting beside it.

    Hermetic exactly as the knowledge equivalent is: ``build_vault``/
    ``bind_minio`` are monkeypatched (the real pair performs an AppRole login
    and a live Vault read), and every client factory involved
    (``create_engine``/``create_openai_image_http_client``/
    ``create_redis_client``) is lazy, so not one connection is opened."""
    fake_secrets = object()
    bind_calls: list[tuple[object, object, object]] = []

    def _fake_build_vault(settings: object) -> tuple[object, object, object]:
        return object(), fake_secrets, object()

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        bind_calls.append((storage, secrets, settings))

    monkeypatch.setattr("app.workers.bootstrap.build_vault", _fake_build_vault)
    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)

    consumer, subscriptions, disposables = await build_media_worker_from_env()

    assert isinstance(consumer, StreamConsumer)
    # 04 §4's binding table: ONE `cg.media` group over one stream.
    assert [(s.stream, s.group) for s in subscriptions] == [("stream.media", "cg.media")]
    assert set(subscriptions[0].handlers) == {"media.job.requested.v1"}
    # engine.dispose, image_http.aclose, redis_client.aclose, _close_vault.
    # No Qdrant and no embedding client: this worker indexes nothing.
    assert len(disposables) == 4
    for dispose in disposables:
        disposable: Disposable = dispose
        assert callable(disposable)

    # Storage is bound from env into the SAME handle the generator writes the
    # produced bytes through -- step 15's proof, on the second worker.
    assert len(bind_calls) == 1
    storage, secrets, minio_settings = bind_calls[0]
    assert isinstance(storage, StorageHandle)
    assert secrets is fake_secrets
    assert isinstance(minio_settings, MinioSettings)


async def test_the_media_worker_is_built_over_the_real_generator_not_a_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the previous test cannot see: ``build_media_worker`` takes the
    generator as an opaque parameter, so a consumer with the right streams
    and the right disposables would look identical if this builder handed it
    a stub. The generator is therefore captured at the wiring site and
    asserted to be the production class -- the one property the honest-failure
    rule existed to protect ("fakes exist ONLY in tests")."""
    captured: dict[str, object] = {}
    real_build_media_worker = bootstrap.build_media_worker

    def _spy(**kwargs: object) -> object:
        captured["generator"] = kwargs["generator"]
        return real_build_media_worker(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "app.workers.bootstrap.build_vault", lambda settings: (object(), object(), object())
    )

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        return None

    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)
    monkeypatch.setattr("app.workers.bootstrap.build_media_worker", _spy)

    await build_media_worker_from_env()

    assert isinstance(captured["generator"], WorkerMediaGenerator)


def test_build_memory_worker_from_env_builds_a_real_consumer_without_raising() -> None:
    """2.10's own closed gap: every dependency ``build_memory_worker`` needs
    is now buildable synchronously from env -- with zero network I/O, since
    every client factory here (``create_engine``/``create_qdrant_client``/
    ``create_embedding_http_client``/``create_redis_client``) is lazy."""
    consumer, subscriptions, disposables = build_memory_worker_from_env()

    assert isinstance(consumer, StreamConsumer)
    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    assert isinstance(subscription, Subscription)
    assert subscription.stream == "stream.memory"
    assert subscription.group == "cg.memory"
    assert set(subscription.handlers) == {"memory.item.stored.v1"}
    # engine.dispose, qdrant_client.close, embedding_http.aclose, redis_client.aclose
    assert len(disposables) == 4
    for dispose in disposables:
        disposable: Disposable = dispose
        assert callable(disposable)


# --------------------------------------------------------------------------- #
# `F-4`/`F-1`: the two numbers this worker no longer shares with the others   #
# --------------------------------------------------------------------------- #
def _settings_with(base: Settings, *, cap_s: int, block_ms: int, idle_s: float) -> Settings:
    """``base`` with the three numbers the derivation reads replaced.

    ``model_copy`` twice, rather than constructing ``Settings(...)``: both
    models are frozen and ``extra="forbid"``, and rebuilding either from
    scratch would mean restating every unrelated field -- which is how a test
    starts asserting against a configuration no deployment has.
    """
    return base.model_copy(
        update={
            "limits": base.limits.model_copy(update={"summarize_job_max_duration_s": cap_s}),
            "events": base.events.model_copy(
                update={"consumer_block_ms": block_ms, "consumer_stale_idle_s": idle_s},
            ),
        }
    )


async def _worker_wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """What each ``build_*_worker_from_env`` actually hands its builder.

    All three are booted for real -- every client factory in them is lazy, and
    ``build_vault``/``bind_minio``, the only eager I/O in the module, are faked
    exactly as the three ``..._builds_a_real_consumer_without_raising`` tests
    above fake them. The builders themselves still run, so what is captured is
    the production wiring and not a stub's account of it.

    The three real builders are read out of the module BEFORE the loop
    replaces any of them -- the tuple below is fully evaluated first, so no
    spy ever wraps another spy.
    """
    monkeypatch.setattr(
        "app.workers.bootstrap.build_vault", lambda settings: (object(), object(), object())
    )

    async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
        return None

    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)

    captured: dict[str, dict[str, object]] = {}

    def _spy(name: str, real: Callable[..., object]) -> Callable[..., object]:
        def _wrapped(**kwargs: object) -> object:
            captured[name] = dict(kwargs)
            return real(**kwargs)

        return _wrapped

    builders = (
        ("knowledge", bootstrap.build_knowledge_worker),
        ("media", bootstrap.build_media_worker),
        ("memory", bootstrap.build_memory_worker),
    )
    for name, real in builders:
        monkeypatch.setattr(f"app.workers.bootstrap.build_{name}_worker", _spy(name, real))

    await build_knowledge_worker_from_env()
    await build_media_worker_from_env()
    build_memory_worker_from_env()
    return captured


def test_the_knowledge_death_threshold_exceeds_its_longest_legitimate_build() -> None:
    """`F-4`'s acceptance, stated as the invariant and not as today's number:
    there is no valid configuration in which this worker is declared dead
    while a build it is still allowed to be running is running.

    Asserted by CALCULATION over a spread of configurations, on the pattern of
    ``test_the_token_ceiling_cannot_bite_before_the_character_ceiling``. A
    single assertion on the shipped defaults would pass just as happily for a
    hand-picked constant that happens to clear 1,800 s today.

    The margin is a whole read cycle and not a token: the idle clock is reset
    by the engine's next ``read``, so a threshold merely EQUAL to the build cap
    leaves a live worker looking dead for the ``consumer_block_ms`` it then
    spends blocking on Redis -- which is the whole window the sweeper needs to
    hand its message to a sibling.
    """
    base = load_settings()
    configurations = (
        # What this deployment actually runs.
        (
            base.limits.summarize_job_max_duration_s,
            base.events.consumer_block_ms,
            base.events.consumer_stale_idle_s,
        ),
        (1, 1, 0.0),  # the smallest of everything, sweep disabled
        (60, 5_000, 900.0),  # a cap well under the shared threshold
        (7_200, 30_000, 900.0),  # a cap far over it, on a slow read cycle
        (1_800, 5_000, 86_400.0),  # an operator who already waits a day
    )
    for cap_s, block_ms, idle_s in configurations:
        settings = _settings_with(base, cap_s=cap_s, block_ms=block_ms, idle_s=idle_s)
        threshold_ms = knowledge_stale_idle_ms(settings)
        assert threshold_ms >= cap_s * 1000 + block_ms, (
            f"a build may legitimately run {cap_s}s, but a worker running one counts as a "
            f"corpse after {threshold_ms / 1000}s"
        )


def test_raising_the_build_cap_raises_the_death_threshold_with_it() -> None:
    """The guard the item was born for.

    ``summarize_job_max_duration_s`` is named as a candidate for raising
    whenever ``_MAX_MAP_CHUNKS`` is, by the text of its own comment -- and a
    hand-written threshold, 2,400 s or any other, would sit still while that
    number moved and reopen `F-4` in silence.

    The assertion is the RELATION and not the result: the threshold moves by
    exactly what the cap moved by. Every fixed number fails it.
    """
    base = load_settings()
    cap_s = base.limits.summarize_job_max_duration_s
    block_ms = base.events.consumer_block_ms
    idle_s = base.events.consumer_stale_idle_s

    settings = _settings_with(base, cap_s=cap_s, block_ms=block_ms, idle_s=idle_s)
    raised = _settings_with(base, cap_s=cap_s * 2, block_ms=block_ms, idle_s=idle_s)

    assert knowledge_stale_idle_ms(raised) - knowledge_stale_idle_ms(settings) == cap_s * 1000
    assert knowledge_stale_idle_ms(raised) > raised.limits.summarize_job_max_duration_s * 1000


def test_an_operator_raised_idle_threshold_is_not_lowered_by_the_derivation() -> None:
    """``max``, not replacement.

    A deployment that has already chosen to wait LONGER than the derivation
    keeps its own number -- the derivation states a floor, not a target. One
    that asks to wait less than the longest build it also permits is overruled
    instead, because that pair of settings has no consistent reading: it says
    both "this build is legitimate" and "a worker running it is dead".
    """
    base = load_settings()
    cap_s = base.limits.summarize_job_max_duration_s
    block_ms = base.events.consumer_block_ms
    derived_ms = knowledge_stale_idle_ms(base)

    generous = _settings_with(base, cap_s=cap_s, block_ms=block_ms, idle_s=derived_ms / 1000 * 2)
    assert knowledge_stale_idle_ms(generous) == derived_ms * 2

    tightened = _settings_with(base, cap_s=cap_s, block_ms=block_ms, idle_s=60.0)
    assert knowledge_stale_idle_ms(tightened) == derived_ms


async def test_the_media_and_memory_workers_keep_the_shared_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The containing guard: `F-4` raised ONE worker's threshold, and the other
    two still read ``consumer_stale_idle_s`` exactly as they did.

    Raising it for everyone would have been the smaller diff and the wrong
    change. ``media``'s longest handler is bounded by ``media_timeout_s``
    (300 s) and ``memory``'s is shorter still, so both already clear 900 s --
    the only thing a global raise buys them is a longer wait before a real
    corpse's registration is swept.

    The knowledge assertion is against the FUNCTION, not against a number:
    that is what catches the call site being reverted to the shared
    expression, in every configuration where the two differ (and where they do
    not, the revert is harmless by construction).
    """
    captured = await _worker_wiring(monkeypatch)
    settings = load_settings()
    shared_ms = int(settings.events.consumer_stale_idle_s * 1000)

    assert captured["media"]["stale_idle_ms"] == shared_ms
    assert captured["memory"]["stale_idle_ms"] == shared_ms
    assert captured["knowledge"]["stale_idle_ms"] == knowledge_stale_idle_ms(settings)


async def test_the_knowledge_worker_reads_one_message_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`F-1`, the half of it this wave buys.

    One ``XREADGROUP`` used to reserve sixteen messages in this consumer's
    pending list, and the engine then walked them one at a time -- so a
    half-hour summary build sat on fifteen others, most of them indexing
    requests, which no sibling could take and which a crash would strand until
    the ghost sweep.

    ``1`` is asserted literally as well as through the constant: the constant
    is the wiring, the literal is the decision. Renaming or reusing the
    constant must not be able to quietly change what it holds.
    """
    captured = await _worker_wiring(monkeypatch)

    assert _KNOWLEDGE_BATCH_COUNT == 1
    assert captured["knowledge"]["batch_count"] == _KNOWLEDGE_BATCH_COUNT


async def test_the_media_and_memory_workers_keep_the_configured_batch_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The containing guard for `F-1`'s half: the drop to one is this worker's
    alone, and ``EventSettings.consumer_batch_count`` still means what it says
    for the other two. Neither has a handler that runs for minutes, so a batch
    of sixteen there is one round trip saved and nothing held hostage.
    """
    captured = await _worker_wiring(monkeypatch)
    settings = load_settings()

    assert captured["media"]["batch_count"] == settings.events.consumer_batch_count
    assert captured["memory"]["batch_count"] == settings.events.consumer_batch_count


async def test_the_summarisation_pipeline_is_given_the_configured_call_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ب-6 (scenarios plan §5): the per-call cap the streaming pipeline now
    holds is READ from `Limits.summarize_timeout_s` here, the same number the
    two summarisation HTTP clients are built with -- not left on the
    application layer's own default.

    Until ب-6 the httpx client timeout capped a whole call by itself, because
    ``complete()`` (``stream: false``) returns no byte before the end. Now
    that the pipeline streams, that same timeout is between-chunk only and
    this one is what bounds the call end to end -- so the two have to be one
    number, and this is where they are joined.

    The setting is moved to something the application default is NOT, which
    is the whole method: with both sitting at 300 the assertion would pass
    just as happily for a call that had dropped the argument entirely.
    """
    base = load_settings()
    raised = base.model_copy(
        update={"limits": base.limits.model_copy(update={"summarize_timeout_s": 777})}
    )
    monkeypatch.setattr("app.workers.bootstrap.load_settings", lambda: raised)

    captured = await _worker_wiring(monkeypatch)

    builder = captured["knowledge"]["summary_builder"]
    pipeline = builder._pipeline  # type: ignore[attr-defined]
    assert raised.limits.summarize_timeout_s != base.limits.summarize_timeout_s
    assert pipeline._timeout_s == raised.limits.summarize_timeout_s


# --------------------------------------------------------------------------- #
# `F-7`: knowledge.summary.built.v1 -> an assistant message in the thread     #
# --------------------------------------------------------------------------- #
_DELIVERY_DOC = "doc-77"
_DELIVERY_TEXT = "The retrieval policy, in eight paragraphs."
# ب-7ج -- the file the delivered summary is about. Distinctive
# enough that a test asserting the header cannot pass on a
# substring of the body.
_DELIVERY_FILE = "retrieval-policy.pdf"
_ARABIC_DELIVERY_TEXT = "سياسة الاسترجاع، في ثماني فقرات."


class _FakeSummaries:
    """Minimal ``SummaryRepository`` -- only the exact-key ``get`` the
    delivery handler calls, keyed the way the real table is."""

    def __init__(self, summary: Summary | None = None) -> None:
        self.summary = summary
        self.reads: list[tuple[str, SummaryKind, SummaryLanguage]] = []

    async def get(
        self,
        ctx: ExecutionContext,
        document_id: str,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary | None:
        self.reads.append((document_id, kind, lang))
        return self.summary


class _FakeAppendMessage:
    """Minimal ``AppendMessage`` -- records the turn it was asked to write,
    or raises whatever the test wants the conversations module to raise."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.appended: list[tuple[str, str, str]] = []

    async def execute(
        self,
        ctx: ExecutionContext,
        conversation_id: str,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
    ) -> object:
        if self.error is not None:
            raise self.error
        self.appended.append((conversation_id, role, text))
        return object()


class _FakeDocumentNames:
    """Minimal ``GetDocumentFileName`` (ب-7ج) -- hands back one name,
    or ``None`` for the document whose file can no longer be read,
    or raises whatever the test wants the files seam to raise."""

    def __init__(self, name: str | None = _DELIVERY_FILE, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.asked: list[str] = []

    async def execute(self, ctx: ExecutionContext, *, document_id: str) -> str | None:
        self.asked.append(document_id)
        if self.error is not None:
            raise self.error
        return self.name


def _summary(
    text: str = _DELIVERY_TEXT,
    *,
    lang: SummaryLanguage = SummaryLanguage.AUTO,
    truncated: bool = False,
) -> Summary:
    return Summary(
        id=new_uuid7(),
        workspace_id="ws-1",
        document_id=_DELIVERY_DOC,
        kind=SummaryKind.OVERVIEW,
        lang=lang,
        text=text,
        model="m",
        source_chunks=3,
        truncated=truncated,
        built_at=utc_now(),
    )


def _built_envelope(ctx: ExecutionContext, *, conversation_id: str | None = "conv-7") -> Json:
    data: Json = {
        "job_id": "job-1",
        "document_id": _DELIVERY_DOC,
        "kind": "overview",
        "lang": "auto",
    }
    if conversation_id is not None:
        data["conversation_id"] = conversation_id
    return {
        "type": "knowledge.summary.built.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": data,
    }


async def test_a_finished_summary_reaches_the_thread_that_asked_for_it() -> None:
    """`F-7`, the missing half of the chat summarisation route: the build's
    text arrives in the conversation as one assistant turn.

    Before this handler the event was minted, published, and consumed by
    nothing in the platform -- the thread kept the receipt it was given when
    the build was queued and never learned that the build had finished."""
    summaries = _FakeSummaries(_summary())
    append = _FakeAppendMessage()
    ledger = _FakeLedger()
    names = _FakeDocumentNames()
    handler = build_knowledge_summary_delivery_handler(
        summaries,  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        ledger,
        names,  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    # ب-7ج -- and it says WHICH file it summarises. The receipt the
    # thread already holds names the same document; without this
    # line the two were joined only by the reader's assumption,
    # across however many unrelated messages sat between them.
    assert append.appended == [
        ("conv-7", "assistant", f'Summary of "{_DELIVERY_FILE}":\n\n{_DELIVERY_TEXT}')
    ]
    assert names.asked == [_DELIVERY_DOC]
    # Read back under the event's own key, never guessed: the triple on the
    # message is what the build wrote the row under.
    assert summaries.reads == [(_DELIVERY_DOC, SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
    # And the DD-09 claim is taken under the group this worker already holds.
    assert [group for group, _ in ledger.calls] == ["cg.knowledge"]


async def test_a_summary_with_no_thread_is_delivered_nowhere_and_is_not_an_error() -> None:
    """The ordinary case for every build ``POST /documents/{id}/summary``
    starts: there is no thread, the result is read back through ``GET``, and
    this handler has nothing to do. Not even a ledger claim -- an event that
    causes no effect has no effect to make idempotent."""
    append = _FakeAppendMessage()
    ledger = _FakeLedger()
    summaries = _FakeSummaries(_summary())
    handler = build_knowledge_summary_delivery_handler(
        summaries,  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        ledger,
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx, conversation_id=None))

    assert append.appended == []
    assert summaries.reads == []
    assert ledger.calls == []


async def test_a_summary_deleted_before_its_delivery_leaves_the_thread_alone() -> None:
    """Deleted, or its document re-indexed away, between the build committing
    and this delivery. The thread is better off with the receipt it already
    has than with a message about a summary that no longer exists."""
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(None),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    assert append.appended == []


async def test_a_deleted_thread_ends_the_delivery_instead_of_redelivering_it_to_the_dlq() -> None:
    """A thread that is gone is a TERMINAL fact, and a handler that let it
    escape would be redelivered five times and dead-lettered for a condition
    no retry can fix. A Postgres or Redis outage is not an ``AppError`` and
    still escapes -- the rule ``build_knowledge_summary_handler`` states for
    its own broad branch, applied here."""
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary()),  # type: ignore[arg-type]
        _FakeAppendMessage(NotFoundError("conversation not found")),  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))  # nothing escapes


async def test_a_duplicate_delivery_writes_the_summary_into_the_thread_only_once() -> None:
    """DD-09: at-least-once redelivery must not append the same summary
    twice. The claim is the first statement inside the transaction the append
    runs in, so a second delivery returns before writing anything."""
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary()),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(result=False),  # already processed
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    assert append.appended == []


async def test_a_truncated_summary_reaches_the_thread_with_the_cut_declared() -> None:
    """`F-9` (plan §3.10): a build that stopped at the map ceiling summarises
    the document's BEGINNING. ``SummaryOut.truncated`` tells every REST
    reader that; a thread message has no field to tell anyone anything, so
    until this the chat surface presented a prefix as the whole document --
    the last silent cut in a module that raised «صدق البتر» to a rule."""
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary(lang=SummaryLanguage.EN, truncated=True)),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    delivered = append.appended[0][2]
    # Header above, notice below, body between -- the two additions
    # are composed by one function and neither displaces the other.
    assert delivered == (
        f'Summary of "{_DELIVERY_FILE}":\n\n'
        + _DELIVERY_TEXT
        + "\n\n"
        + SUMMARY_TRUNCATED_NOTICE_EN
    )


async def test_the_delivered_notice_speaks_the_summarys_own_language() -> None:
    """Not the thread's language and not the platform's: the notice sits
    under the summary and is read with it, so it follows the same ``_is_rtl``
    rule the export path uses -- here through ``auto``, where the only honest
    evidence is the body the model actually wrote."""
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary(_ARABIC_DELIVERY_TEXT, truncated=True)),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    assert append.appended[0][2].endswith(SUMMARY_TRUNCATED_NOTICE_AR)


async def test_a_failing_name_lookup_still_delivers_the_summary() -> None:
    """ب-7ج: a header is cosmetic; a summary is what was asked for.

    The same rule ب-2 states for the RAG agent's corpus header, on
    the one other read allowed to fail quietly in this codebase: a
    build that took minutes of provider calls must not be dropped
    because a name lookup failed. The message arrives untitled --
    which is exactly what it looked like before ب-7ج -- and the
    failure is logged rather than raised.
    """
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary()),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(error=RuntimeError("files seam down")),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))  # nothing escapes

    assert append.appended == [("conv-7", "assistant", _DELIVERY_TEXT)]


async def test_an_unnameable_document_delivers_the_summary_untitled() -> None:
    """`None` is an ordinary answer, not a failure: a summary is
    built from chunks stored at index time and stays deliverable
    long after the file it was built from is deleted or
    quarantined.

    A blank name is the same case for a different reason -- a
    message headed `Summary of "":` shows a user that their file
    is called nothing -- and both deliver the body alone.
    """
    for name in (None, "   "):
        append = _FakeAppendMessage()
        handler = build_knowledge_summary_delivery_handler(
            _FakeSummaries(_summary()),  # type: ignore[arg-type]
            append,  # type: ignore[arg-type]
            _FakeUnitOfWork(),
            _FakeLedger(),
            _FakeDocumentNames(name),  # type: ignore[arg-type]
        )
        ctx = _ctx()

        await handler(ctx, _built_envelope(ctx))

        assert append.appended == [("conv-7", "assistant", _DELIVERY_TEXT)]


async def test_the_delivery_header_speaks_the_summarys_own_language() -> None:
    """The header follows the body, exactly as the truncation
    notice does and for the same reason: both are read WITH the
    summary, so both take their language from it -- here through
    `auto`, where the only honest evidence is what the model
    actually wrote."""
    append = _FakeAppendMessage()
    handler = build_knowledge_summary_delivery_handler(
        _FakeSummaries(_summary(_ARABIC_DELIVERY_TEXT)),  # type: ignore[arg-type]
        append,  # type: ignore[arg-type]
        _FakeUnitOfWork(),
        _FakeLedger(),
        _FakeDocumentNames(),  # type: ignore[arg-type]
    )
    ctx = _ctx()

    await handler(ctx, _built_envelope(ctx))

    assert append.appended[0][2].startswith(f"ملخّص «{_DELIVERY_FILE}»:")


async def test_the_build_handler_hands_the_message_s_thread_to_finalize() -> None:
    """`F-7`: the id is read off the envelope being answered, not off the job
    row -- which does not have it. That is what puts it on
    ``knowledge.summary.built.v1`` for the delivery handler above."""
    build = _FakeBuildSummary()
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
    )
    ctx = _ctx()
    envelope = _summary_envelope(ctx)
    envelope["data"]["conversation_id"] = "conv-7"

    await handler(ctx, envelope)

    assert build.threads == ["conv-7"]


async def test_a_request_published_before_f7_still_finalizes_with_no_thread() -> None:
    """An envelope already sitting in the stream when this deploys carries no
    ``conversation_id`` key at all. ``.get`` rather than ``[...]`` is what
    keeps that message a normal build instead of a poisoned one."""
    build = _FakeBuildSummary()
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
    )
    ctx = _ctx()

    await handler(ctx, _summary_envelope(ctx))

    assert build.threads == [None]
