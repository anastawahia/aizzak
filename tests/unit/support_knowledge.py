"""In-memory knowledge wiring shared by the API router tests (6.1-و-3).

Not a ``test_*`` module, so pytest never collects it — the
``support_credentials`` precedent: every router test file constructs
``ApiServices``, and a per-file copy of these fakes is how copies drift.

``InMemoryDocumentRepository.list`` returns the caller's OWN rows only,
newest first — the tenant scoping and ordering the SQL adapter gets from
``WHERE workspace_id`` plus a descending UUIDv7 sort. Rows of every lifecycle
status are seedable and all of them are listed, which is what lets a test
prove ``pending``/``failed`` documents are not quietly filtered out.

``RecordingRetrieval`` is a structural ``KnowledgeRetrieval``: it records the
``(query, k)`` it was called with and returns a canned list. It exists
because the production wiring passes ``search=None`` (no embedding adapter
yet), so the ONLY way to exercise the search route's own logic — argument
pass-through and the envelope — is to hand the bundle a stub. Its absence is
tested too: ``build_knowledge(retrieval=None)`` is the production shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.ports.event_outbox import OutboxRecord
from app.modules.knowledge.adapters.summary_renderer import MarkdownSummaryRenderer
from app.modules.knowledge.application.use_cases import (
    CancelReindexJob,
    CancelReindexJobService,
    CancelSummaryJob,
    CancelSummaryJobService,
    DeleteSummary,
    ExportSummary,
    GetDocument,
    GetReindexJob,
    GetSummary,
    GetSummaryJob,
    KnowledgeUseCases,
    ListDocuments,
    ReindexDocuments,
    ReindexService,
    RequestSummary,
    RequestSummaryService,
)
from app.modules.knowledge.domain.entities import Document, ReindexJob, Summary, SummaryJob
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)
from app.modules.knowledge.ports.retrieval import RetrievedChunk

SEEDED_CREATED_AT = datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC)


@dataclass
class RecordingRetrieval:
    """A structural ``KnowledgeRetrieval`` that logs its arguments."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)
    # Every `space_id` the route handed down (spaces plan step 8) — a separate
    # log so a test can assert the route passes one WITHOUT every existing
    # `calls` assertion having to grow a third tuple element.
    spaces: list[str | None] = field(default_factory=list)

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str | None,
    ) -> list[RetrievedChunk]:
        self.calls.append((query, k))
        self.spaces.append(space_id)
        return list(self.chunks)


@dataclass
class InMemoryDocumentRepository:
    """A structural ``DocumentRepository`` over one dict (only the two read
    methods the API bundle uses are exercised; the ingestion writes are the
    worker's and are covered by that module's own tests)."""

    rows: dict[str, Document] = field(default_factory=dict)
    # Where each document's chunks live in the vector store, and every id
    # `purge` was called with — the two facts a re-index test has to see.
    refs: dict[str, list[VectorRef]] = field(default_factory=dict)
    purged: list[str] = field(default_factory=list)
    # A document's chunk text in reading order — what the summariser reads
    # (BE-RAG-009). Empty by default, which is also the real shape of a
    # document that parsed to nothing.
    texts: dict[str, list[str]] = field(default_factory=dict)

    async def get(self, ctx: ExecutionContext, doc_id: str) -> Document | None:
        row = self.rows.get(doc_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def list(
        self,
        ctx: ExecutionContext,
        *,
        space_id: str | None,
        limit: int,
        cursor: str | None = None,
    ) -> Page[Document]:
        # Newest-first keyset on `id` through the REAL codec (6.3-ب).
        items = sorted(
            (
                row
                for row in self.rows.values()
                if row.workspace_id == ctx.workspace_id
                # The step-8 narrowing, ADDED to the tenant condition and
                # never swapping it -- and `None` means every space, not "the
                # spaceless ones" (the SQL adapter's own rule).
                and (space_id is None or row.space_id == space_id)
            ),
            key=lambda row: row.id,
            reverse=True,
        )
        if cursor is not None:
            after = decode_id_cursor(cursor)
            items = [row for row in items if row.id < after]
        page, has_more = items[:limit], len(items) > limit
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        self.rows[doc.id] = doc

    async def set_status(
        self, ctx: ExecutionContext, doc_id: str, status: str, error: str | None = None
    ) -> None:
        # No longer forbidden: cancelling a re-index claims its own pending
        # documents by driving them terminal (BE-RAG-008). The bundle still
        # cannot start a pipeline — this only ever writes `failed`.
        row = self.rows.get(doc_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return
        self.rows[doc_id] = replace(row, status=IndexStatus(status), error=error)

    async def add_chunks(self, ctx: ExecutionContext, chunks: object) -> None:
        raise AssertionError("the API bundle must never persist chunks")

    async def vector_refs(self, ctx: ExecutionContext, doc_id: str) -> list[VectorRef]:
        return list(self.refs.get(doc_id, ()))

    async def chunk_texts(self, ctx: ExecutionContext, doc_id: str) -> list[str]:
        row = self.rows.get(doc_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return []
        return list(self.texts.get(doc_id, ()))

    async def purge(self, ctx: ExecutionContext, doc_id: str) -> None:
        row = self.rows.get(doc_id)
        if row is not None and row.workspace_id == ctx.workspace_id:
            del self.rows[doc_id]
            self.refs.pop(doc_id, None)
        self.purged.append(doc_id)


@dataclass
class InMemoryReindexJobRepository:
    """A structural ``ReindexJobRepository``.

    ``get`` re-reads each item's status out of the DOCUMENT repository, the
    way the SQL adapter joins it (INV-K5) — storing it on the item here would
    fake away the one property the design rests on, and every progress test
    would then be measuring the fake.
    """

    documents: InMemoryDocumentRepository
    rows: dict[str, ReindexJob] = field(default_factory=dict)

    async def add(self, ctx: ExecutionContext, job: ReindexJob) -> None:
        self.rows[job.id] = job

    async def get(self, ctx: ExecutionContext, job_id: str) -> ReindexJob | None:
        row = self.rows.get(job_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        items = tuple(
            replace(item, status=document.status)
            for item in row.items
            if (document := self.documents.rows.get(item.document_id)) is not None
        )
        return replace(row, items=items)

    async def mark_cancelled(self, ctx: ExecutionContext, job_id: str, at: datetime) -> None:
        row = self.rows[job_id]
        self.rows[job_id] = replace(row, cancelled_at=at)


@dataclass
class InMemorySummaryRepository:
    """A structural ``SummaryRepository`` keyed the way the table is —
    ``(document_id, kind, lang)`` — so a test that asks for the Arabic
    overview cannot be handed the English full text by a fake that was
    laxer than ``uq_summary_key``."""

    rows: dict[tuple[str, str, str], Summary] = field(default_factory=dict)

    async def get(
        self, ctx: ExecutionContext, document_id: str, kind: SummaryKind, lang: SummaryLanguage
    ) -> Summary | None:
        row = self.rows.get((document_id, kind.value, lang.value))
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def upsert(self, ctx: ExecutionContext, summary: Summary) -> None:
        self.rows[(summary.document_id, summary.kind.value, summary.lang.value)] = summary

    async def delete(
        self, ctx: ExecutionContext, document_id: str, kind: SummaryKind, lang: SummaryLanguage
    ) -> bool:
        key = (document_id, kind.value, lang.value)
        row = self.rows.get(key)
        if row is None or row.workspace_id != ctx.workspace_id:
            return False
        del self.rows[key]
        return True

    async def delete_for_document(self, ctx: ExecutionContext, document_id: str) -> None:
        for key in [k for k in self.rows if k[0] == document_id]:
            del self.rows[key]


@dataclass
class InMemorySummaryJobRepository:
    """A structural ``SummaryJobRepository``.

    ``record_progress`` honours the ``status == 'running'`` guard the SQL
    adapter puts in its ``WHERE`` clause. Faking that away would let the one
    test that matters — a cancelled job stays cancelled while its worker
    keeps reporting progress — pass against a fake that cannot fail it.
    """

    rows: dict[str, SummaryJob] = field(default_factory=dict)

    async def add(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        self.rows[job.id] = job

    async def get(self, ctx: ExecutionContext, job_id: str) -> SummaryJob | None:
        row = self.rows.get(job_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def active_for(
        self, ctx: ExecutionContext, document_id: str, kind: SummaryKind, lang: SummaryLanguage
    ) -> SummaryJob | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.workspace_id == ctx.workspace_id
                and row.document_id == document_id
                and row.kind is kind
                and row.lang is lang
                and row.status in (SummaryJobStatus.QUEUED, SummaryJobStatus.RUNNING)
            ),
            None,
        )

    async def save(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        self.rows[job.id] = job

    async def record_progress(self, ctx: ExecutionContext, job_id: str, done_chunks: int) -> None:
        row = self.rows.get(job_id)
        if row is None or row.status is not SummaryJobStatus.RUNNING:
            return
        row.done_chunks = done_chunks


@dataclass
class RecordingVectorStore:
    """The two ``HybridVectorStore`` methods a re-index touches, and only
    those: ``delete`` is what it calls, and the rest exist so the Protocol is
    satisfied structurally rather than by inheritance.

    ``deleted`` is what makes "the old points were removed BEFORE the rows"
    assertable instead of assumed."""

    deleted: list[tuple[str, list[str]]] = field(default_factory=list)

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        self.deleted.append((collection, list(ids)))

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None:
        raise AssertionError("a re-index never provisions a collection")

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None:
        raise AssertionError("a re-index never provisions a collection")

    async def ensure_payload_index(
        self, collection: str, field: str, *, tenant: bool = False
    ) -> None:
        raise AssertionError("a re-index never provisions a collection")

    async def upsert(self, collection: str, points: Sequence[object]) -> None:
        raise AssertionError("a re-index never writes points — the worker does")

    async def search(
        self, collection: str, vector: list[float], k: int, flt: object = None
    ) -> list[object]:
        raise AssertionError("a re-index never searches")

    async def search_sparse(
        self, collection: str, sparse: object, k: int, flt: object = None
    ) -> list[object]:
        raise AssertionError("a re-index never searches")


class RecordingOutbox:
    """Minimal ``EventOutbox`` — records every append call, in order."""

    def __init__(self) -> None:
        self.calls: list[list[OutboxRecord]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append(list(records))

    @property
    def event_types(self) -> list[str]:
        return [record.event_type for call in self.calls for record in call]


class NoopUnitOfWork:
    """A no-op transaction boundary — the seam tests own the real one."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


@dataclass(frozen=True, slots=True)
class KnowledgeStack:
    """The bundle plus the fakes a test asserts against."""

    knowledge: KnowledgeUseCases
    repository: InMemoryDocumentRepository
    retrieval: RecordingRetrieval | None
    jobs: InMemoryReindexJobRepository
    vectors: RecordingVectorStore
    outbox: RecordingOutbox
    summaries: InMemorySummaryRepository
    summary_jobs: InMemorySummaryJobRepository


def seed_document(
    *,
    document_id: str,
    workspace_id: str,
    space_id: str | None = None,
    file_id: str = "file-1",
    status: IndexStatus = IndexStatus.INDEXED,
    chunk_count: int = 3,
    error: str | None = None,
) -> Document:
    # `space_id` DOES default here, unlike every production signature (spaces
    # plan step 8): a seed helper is not a writer, and its callers are tests
    # about paging/status/summaries that have no opinion about spaces. The
    # tests that DO have one pass it.
    return Document(
        id=document_id,
        workspace_id=workspace_id,
        space_id=space_id,
        file_id=file_id,
        status=status,
        chunk_count=chunk_count,
        error=error,
        created_at=SEEDED_CREATED_AT,
        updated_at=SEEDED_CREATED_AT,
        version=1,
    )


def build_knowledge(*, retrieval: RecordingRetrieval | None = None) -> KnowledgeStack:
    """One repository behind both reads. ``retrieval`` defaults to ``None`` —
    the production shape, where the missing embedding adapter makes
    ``POST /search`` a 503."""
    repository = InMemoryDocumentRepository()
    jobs = InMemoryReindexJobRepository(repository)
    vectors = RecordingVectorStore()
    outbox = RecordingOutbox()
    uow = NoopUnitOfWork()
    summaries = InMemorySummaryRepository()
    summary_jobs = InMemorySummaryJobRepository()
    return KnowledgeStack(
        knowledge=KnowledgeUseCases(
            list_documents=ListDocuments(repository),
            get_document=GetDocument(repository),
            search=retrieval,
            reindex=ReindexService(ReindexDocuments(repository, jobs, vectors), outbox, uow),
            get_job=GetReindexJob(jobs),
            cancel_job=CancelReindexJobService(CancelReindexJob(repository, jobs), outbox, uow),
            # No `BuildSummary` — the API bundle cannot run the pipeline, the
            # same line that keeps ingestion out of it (BE-RAG-009).
            request_summary=RequestSummaryService(
                RequestSummary(repository, summary_jobs), outbox, uow
            ),
            get_summary=GetSummary(summaries),
            # The REAL renderer, not a stub. It needs nothing from the
            # environment — no network, no credential, no settings — and
            # stubbing it would leave the router tests unable to say whether
            # an export actually produces a PDF (BE-RAG-012).
            export_summary=ExportSummary(summaries, MarkdownSummaryRenderer()),
            delete_summary=DeleteSummary(summaries),
            get_summary_job=GetSummaryJob(summary_jobs),
            cancel_summary_job=CancelSummaryJobService(CancelSummaryJob(summary_jobs), outbox, uow),
        ),
        repository=repository,
        retrieval=retrieval,
        jobs=jobs,
        vectors=vectors,
        outbox=outbox,
        summaries=summaries,
        summary_jobs=summary_jobs,
    )
