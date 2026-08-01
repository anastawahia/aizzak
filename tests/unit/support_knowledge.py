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

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.modules.knowledge.application.use_cases import (
    GetDocument,
    KnowledgeUseCases,
    ListDocuments,
)
from app.modules.knowledge.domain.entities import Document
from app.modules.knowledge.domain.value_objects import IndexStatus
from app.modules.knowledge.ports.retrieval import RetrievedChunk

SEEDED_CREATED_AT = datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC)


@dataclass
class RecordingRetrieval:
    """A structural ``KnowledgeRetrieval`` that logs its arguments."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def retrieve(self, ctx: ExecutionContext, query: str, k: int) -> list[RetrievedChunk]:
        self.calls.append((query, k))
        return list(self.chunks)


@dataclass
class InMemoryDocumentRepository:
    """A structural ``DocumentRepository`` over one dict (only the two read
    methods the API bundle uses are exercised; the ingestion writes are the
    worker's and are covered by that module's own tests)."""

    rows: dict[str, Document] = field(default_factory=dict)

    async def get(self, ctx: ExecutionContext, doc_id: str) -> Document | None:
        row = self.rows.get(doc_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Document]:
        # Newest-first keyset on `id` through the REAL codec (6.3-ب).
        items = sorted(
            (row for row in self.rows.values() if row.workspace_id == ctx.workspace_id),
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
        raise AssertionError("the API bundle must never drive a status transition")

    async def add_chunks(self, ctx: ExecutionContext, chunks: object) -> None:
        raise AssertionError("the API bundle must never persist chunks")


@dataclass(frozen=True, slots=True)
class KnowledgeStack:
    """The bundle plus the fakes a test asserts against."""

    knowledge: KnowledgeUseCases
    repository: InMemoryDocumentRepository
    retrieval: RecordingRetrieval | None


def seed_document(
    *,
    document_id: str,
    workspace_id: str,
    file_id: str = "file-1",
    status: IndexStatus = IndexStatus.INDEXED,
    chunk_count: int = 3,
    error: str | None = None,
) -> Document:
    return Document(
        id=document_id,
        workspace_id=workspace_id,
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
    return KnowledgeStack(
        knowledge=KnowledgeUseCases(
            list_documents=ListDocuments(repository),
            get_document=GetDocument(repository),
            search=retrieval,
        ),
        repository=repository,
        retrieval=retrieval,
    )
