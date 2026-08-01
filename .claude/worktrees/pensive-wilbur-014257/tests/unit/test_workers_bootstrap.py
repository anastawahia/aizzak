"""Unit tests for the worker composition roots in ``workers/bootstrap.py``
(5.1-ج).

Hermetic: every handler closure (``build_knowledge_register_handler``/
``build_knowledge_index_handler``/``build_media_run_handler``/
``build_memory_index_handler``) is exercised over in-memory fakes/real
use-cases, the same way ``test_media_outbox_seam.py``'s
``MediaRequestService`` tests are -- these closures ARE the worker-scoped
equivalent of that request-scoped seam. ``build_<name>_worker_from_env``'s
"honest failure" (module docstring) is proven here too, for the two seams
still genuinely blocked (``DocumentContentResolver``/``MediaGenerator``): no
live Redis/Postgres needed -- ``create_engine``/``create_redis_client`` are
both lazy, no connection at construction time. 2.10 closed the THIRD seam
(``EmbeddingProvider``): ``build_memory_worker_from_env`` no longer raises,
and is proven here to build a real, working consumer instead -- equally
hermetic, since ``create_qdrant_client``/``create_embedding_http_client``
are lazy too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.ports.vector_store import VectorHit, VectorPoint
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.domain.entities import Chunk, Document
from app.modules.knowledge.domain.value_objects import IndexStatus
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind
from app.workers.bootstrap import (
    Disposable,
    build_knowledge_index_handler,
    build_knowledge_register_handler,
    build_knowledge_worker_from_env,
    build_media_run_handler,
    build_media_worker_from_env,
    build_memory_index_handler,
    build_memory_worker_from_env,
)


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
# knowledge: build_knowledge_register_handler                                 #
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
        self, ctx: ExecutionContext, doc_id: str, status: str, error: str | None = None
    ) -> None:
        for doc in self.added:
            if doc.id == doc_id:
                doc.status = IndexStatus(status)
                doc.error = error

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        self.chunks.extend(chunks)


def _register_envelope(ctx: ExecutionContext, *, file_id: str = "file-1") -> Json:
    return {
        "type": "files.file.uploaded.v1",
        "workspaceid": ctx.workspace_id,
        "id": new_uuid7(),
        "data": {"file_id": file_id, "content_type": "text/plain", "size_bytes": 10},
    }


async def test_register_handler_creates_a_pending_document_and_appends_its_mapped_event() -> None:
    documents = _FakeDocuments()
    outbox = _FakeOutbox()
    handler = build_knowledge_register_handler(documents, outbox, _FakeUnitOfWork(), _FakeLedger())
    ctx = _ctx()

    await handler(ctx, _register_envelope(ctx, file_id="file-77"))

    assert len(documents.added) == 1
    doc = documents.added[0]
    assert doc.file_id == "file-77"
    assert doc.status is IndexStatus.PENDING
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["knowledge.document.registered.v1"]
    assert records[0].aggregate_id == doc.id


async def test_register_handler_appends_the_outbox_record_inside_the_unit_of_work() -> None:
    """Mutation-battery target #5: moving the outbox append OUTSIDE
    ``uow.begin`` would make ``appended_while_active`` False here."""
    uow = _TrackingUnitOfWork()

    class _SpyOutbox:
        def __init__(self) -> None:
            self.appended_while_active: bool | None = None

        async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
            self.appended_while_active = uow.active

    outbox = _SpyOutbox()
    handler = build_knowledge_register_handler(_FakeDocuments(), outbox, uow, _FakeLedger())
    ctx = _ctx()

    await handler(ctx, _register_envelope(ctx))

    assert outbox.appended_while_active is True
    assert uow.active is False  # the block closed cleanly afterwards


async def test_register_handler_outbox_failure_is_not_swallowed() -> None:
    """Swallowing would leave a registered document with no event --
    invisible to this same worker's own index handler (the
    ``MediaRequestService``/``RememberInteractionService`` precedent)."""
    handler = build_knowledge_register_handler(
        _FakeDocuments(), _ExplodingOutbox(), _FakeUnitOfWork(), _FakeLedger()
    )
    ctx = _ctx()

    with pytest.raises(RuntimeError, match="outbox is down"):
        await handler(ctx, _register_envelope(ctx))


async def test_register_handler_duplicate_claim_registers_nothing_and_appends_nothing() -> None:
    """DD-09's «تكرار = تجاهُل»: registration is non-idempotent by design
    (INV-K3 -- a redelivered ``file.uploaded`` would mint a SECOND document),
    so the ``False`` claim is the only thing standing between at-least-once
    delivery and duplicate aggregates. The clean return here is what lets the
    engine XACK the duplicate."""
    documents = _FakeDocuments()
    outbox = _FakeOutbox()
    ledger = _FakeLedger(result=False)
    handler = build_knowledge_register_handler(documents, outbox, _FakeUnitOfWork(), ledger)
    ctx = _ctx()
    envelope = _register_envelope(ctx)

    await handler(ctx, envelope)

    assert documents.added == []
    assert outbox.calls == []
    assert ledger.calls == [("cg.knowledge", envelope["id"])]


async def test_register_handler_claims_inside_the_unit_of_work_and_before_the_effect() -> None:
    """Two mutation targets in one shape: a claim hoisted OUTSIDE
    ``uow.begin`` would break DD-09's «داخل معاملة الأثر» (crash ⇒ claim
    committed but effect lost ⇒ redelivery skips forever); a claim moved
    AFTER the effect could only discover the duplicate having already
    half-performed it."""
    uow = _TrackingUnitOfWork()
    documents = _FakeDocuments()

    class _SpyLedger:
        def __init__(self) -> None:
            self.claimed_while_active: bool | None = None
            self.documents_seen_at_claim: int | None = None

        async def claim(self, ctx: ExecutionContext, *, consumer_group: str, event_id: str) -> bool:
            self.claimed_while_active = uow.active
            self.documents_seen_at_claim = len(documents.added)
            return True

    ledger = _SpyLedger()
    handler = build_knowledge_register_handler(documents, _FakeOutbox(), uow, ledger)
    ctx = _ctx()

    await handler(ctx, _register_envelope(ctx))

    assert ledger.claimed_while_active is True
    assert ledger.documents_seen_at_claim == 0  # claim FIRST, effect second
    assert len(documents.added) == 1


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


class _FakeHybridVectors:
    """Only what ``IndexDocument`` touches: ``ensure_hybrid_collection`` +
    ``upsert``."""

    def __init__(self) -> None:
        self.upserted: list[tuple[str, list[VectorPoint]]] = []

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None: ...

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None: ...

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        self.upserted.append((collection, list(points)))

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        return []

    async def search_sparse(
        self, collection: str, sparse: object, k: int, flt: Json | None = None
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
    ) -> tuple[ParsedDocument, str, str]:
        self.calls.append(file_id)
        return self._parsed, self._model, self._api_key


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


def _pending_document(ctx: ExecutionContext) -> Document:
    now = utc_now()
    return Document(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
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
            self, ctx: ExecutionContext, doc_id: str, status: str, error: str | None = None
        ) -> None:
            if status in (IndexStatus.INDEXED.value, IndexStatus.FAILED.value):
                active_at["terminal_status"] = uow.active
            await super().set_status(ctx, doc_id, status, error)

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
# The honest-failure rule: knowledge/media still raise; memory no longer does #
# (2.10 closed the EmbeddingProvider gap -- module docstring).                #
# --------------------------------------------------------------------------- #
async def test_build_knowledge_worker_from_env_raises_naming_only_document_content_resolver() -> (
    None
):
    """2.10 builds the REAL ``IndexDocument`` pipeline (embeddings + vectors)
    here -- only ``DocumentContentResolver`` remains blocked, and the
    message must name ONLY that (never ``EmbeddingProvider`` again, the
    stale gap this used to name)."""
    with pytest.raises(AppError) as exc_info:
        build_knowledge_worker_from_env()
    assert exc_info.value.code == "common.internal"
    assert "DocumentContentResolver" in str(exc_info.value)
    assert "EmbeddingProvider" not in str(exc_info.value)


async def test_build_media_worker_from_env_raises_naming_the_gap() -> None:
    with pytest.raises(AppError) as exc_info:
        build_media_worker_from_env()
    assert exc_info.value.code == "common.internal"
    assert "MediaGenerator" in str(exc_info.value)


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
