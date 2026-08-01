"""Unit tests for the knowledge module's 3.k4 lifecycle: value objects, the
``Document``/``Chunk`` domain, and the ``RegisterDocumentFromFile``/
``IndexRegisteredDocument``/``KnowledgeRetrievalService`` use-cases over
local fakes (``EmbeddingProvider``/``HybridVectorStore``/
``DocumentRepository``/``EmbeddingResolver``) -- none imported from other
test modules. ``IndexRegisteredDocument`` is exercised over the REAL 3.k3
``IndexDocument`` pipeline (and ``KnowledgeRetrievalService`` over the REAL
``RetrieveContext``) so the 3.k3 -> 3.k4 handoff is proven end-to-end, not
just mocked at the boundary.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.types import Json
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.application.use_cases import (
    IndexRegisteredDocument,
    KnowledgeRetrievalService,
    RegisterDocumentFromFile,
)
from app.modules.knowledge.domain.collections import chunk_point_id
from app.modules.knowledge.domain.entities import Chunk, Document
from app.modules.knowledge.domain.errors import DocumentStateError, InvalidKnowledgeInput
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
)
from app.modules.knowledge.domain.value_objects import IndexStatus, VectorRef
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)
from app.modules.knowledge.ports.inbound import KnowledgeRetrieval
from app.modules.knowledge.ports.retrieval import ResolvedEmbedding


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _document(
    *,
    doc_id: str = "doc-1",
    workspace_id: str = "ws1",
    file_id: str = "file-1",
    status: IndexStatus = IndexStatus.PENDING,
    chunk_count: int = 0,
    error: str | None = None,
) -> Document:
    now = utc_now()
    return Document(
        id=doc_id,
        workspace_id=workspace_id,
        file_id=file_id,
        status=status,
        chunk_count=chunk_count,
        error=error,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _parsed_chunk(text: str, order: int = 0) -> ParsedChunk:
    return ParsedChunk(text=text, order=order, kind=ParsedChunkKind.TEXT, metadata={})


def _parsed_document(chunks: Sequence[ParsedChunk]) -> ParsedDocument:
    return ParsedDocument(
        source_ext=".txt", content_type="text/plain", chunks=tuple(chunks), metadata={}
    )


def _seeded_vector(text: str, dim: int) -> list[float]:
    """A deterministic unit vector derived from ``text`` (``blake2b``, never
    the randomized builtin ``hash()``): equal texts always get equal
    vectors, distinct texts get (with overwhelming probability) distinct
    ones -- enough to exercise real cosine search without a real embedding
    model."""
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=dim * 2).digest()
    raw = [
        (int.from_bytes(digest[i : i + 2], "big") / 65535.0) * 2.0 - 1.0
        for i in range(0, len(digest), 2)
    ]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


def _payload_matches(payload: Json, flt: Json | None) -> bool:
    if not flt:
        return True
    return all(payload.get(key) == value for key, value in flt.items())


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _sparse_dot(query: SparseVector, doc: SparseVector) -> float:
    doc_values = dict(zip(doc.indices, doc.values, strict=True))
    return sum(
        value * doc_values[index]
        for index, value in zip(query.indices, query.values, strict=True)
        if index in doc_values
    )


# --------------------------------------------------------------------------- #
# Fakes (EmbeddingProvider / HybridVectorStore / DocumentRepository /         #
# EmbeddingResolver) -- local to this module on purpose.                     #
# --------------------------------------------------------------------------- #
class _FakeEmbeddings:
    """Deterministic ``EmbeddingProvider`` fake; ``fail=True`` makes
    ``embed`` raise, to exercise ``IndexRegisteredDocument``'s failure
    path."""

    provider = "fake"

    def __init__(self, *, dim: int = 6, fail: bool = False) -> None:
        self.dim = dim
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        if self.fail:
            raise RuntimeError("embedding provider unavailable")
        text_list = list(texts)
        self.calls.append(text_list)
        vectors = [_seeded_vector(text, self.dim) for text in text_list]
        return EmbeddingResult(
            vectors=vectors, model=model, dimensions=self.dim, tokens=len(text_list)
        )

    def dimensions(self, model: str) -> int:
        return self.dim


class _FakeHybridVectors:
    """In-memory ``HybridVectorStore`` fake: brute-force cosine (dense) /
    dot-product (sparse) search over whatever has been upserted, scoped to
    the collection and filtered by an exact-match ``flt``. Records every
    ``search``/``search_sparse`` call's ``(collection, k, flt)`` for
    k-propagation/tenant-isolation assertions."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, VectorPoint]] = {}
        self.ensured_hybrid: list[tuple[str, int, str]] = []
        self.search_calls: list[tuple[str, int, Json | None]] = []
        self.search_sparse_calls: list[tuple[str, int, Json | None]] = []

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None:
        self.points.setdefault(name, {})

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None:
        self.ensured_hybrid.append((name, dim, distance))
        self.points.setdefault(name, {})

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        bucket = self.points.setdefault(collection, {})
        for point in points:
            bucket[point.id] = point

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        self.search_calls.append((collection, k, flt))
        candidates = [
            p for p in self.points.get(collection, {}).values() if _payload_matches(p.payload, flt)
        ]
        scored = sorted(
            ((p, _cosine(vector, p.vector)) for p in candidates),
            key=lambda item: item[1],
            reverse=True,
        )
        return [VectorHit(id=p.id, score=score, payload=p.payload) for p, score in scored[:k]]

    async def search_sparse(
        self, collection: str, sparse: SparseVector, k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        self.search_sparse_calls.append((collection, k, flt))
        candidates = [
            p for p in self.points.get(collection, {}).values() if _payload_matches(p.payload, flt)
        ]
        scored = [(p, _sparse_dot(sparse, p.sparse)) for p in candidates if p.sparse is not None]
        scored = [(p, score) for p, score in scored if score > 0.0]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [VectorHit(id=p.id, score=score, payload=p.payload) for p, score in scored[:k]]

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        bucket = self.points.get(collection, {})
        for point_id in ids:
            bucket.pop(point_id, None)


class _FakeDocumentRepository:
    """In-memory ``DocumentRepository``; records every ``set_status`` call
    and flattens every ``add_chunks`` call into ``self.chunks``."""

    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}
        self.chunks: list[Chunk] = []
        self.status_calls: list[tuple[str, str, str | None]] = []

    async def get(self, ctx: ExecutionContext, doc_id: str) -> Document | None:
        doc = self.docs.get(doc_id)
        if doc is None or doc.workspace_id != ctx.workspace_id:
            return None
        return doc

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        self.docs[doc.id] = doc

    async def set_status(
        self, ctx: ExecutionContext, doc_id: str, status: str, error: str | None = None
    ) -> None:
        self.status_calls.append((doc_id, status, error))

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        self.chunks.extend(chunks)


class _FakeEmbeddingResolver:
    """Records every ``resolve_embedding`` call's ``ctx`` and returns a
    fixed ``ResolvedEmbedding``."""

    def __init__(self, *, model: str = "embed-1", api_key: str = "key-1") -> None:
        self.model = model
        self.api_key = api_key
        self.calls: list[ExecutionContext] = []

    async def resolve_embedding(self, ctx: ExecutionContext) -> ResolvedEmbedding:
        self.calls.append(ctx)
        return ResolvedEmbedding(model=self.model, api_key=self.api_key)


# --------------------------------------------------------------------------- #
# value_objects                                                               #
# --------------------------------------------------------------------------- #
def test_vector_ref_rejects_empty_collection() -> None:
    with pytest.raises(InvalidKnowledgeInput):
        VectorRef(collection="", point_id="p1")


def test_vector_ref_rejects_empty_point_id() -> None:
    with pytest.raises(InvalidKnowledgeInput):
        VectorRef(collection="kn-ws1", point_id="")


def test_vector_ref_accepts_valid_values() -> None:
    ref = VectorRef(collection="kn-ws1", point_id="p1")
    assert (ref.collection, ref.point_id) == ("kn-ws1", "p1")


def test_index_status_values() -> None:
    assert [status.value for status in IndexStatus] == ["pending", "indexing", "indexed", "failed"]


# --------------------------------------------------------------------------- #
# Document transitions (INV-K2/K3)                                            #
# --------------------------------------------------------------------------- #
def test_pending_to_indexing_to_indexed_round_trip() -> None:
    doc = _document(status=IndexStatus.PENDING)
    doc.start_indexing(utc_now())
    assert doc.status is IndexStatus.INDEXING
    doc.complete_indexing(2, utc_now())
    assert doc.status is IndexStatus.INDEXED
    assert doc.chunk_count == 2


def test_pending_to_indexing_to_failed_round_trip() -> None:
    doc = _document(status=IndexStatus.PENDING)
    doc.start_indexing(utc_now())
    doc.fail_indexing("parser exploded", utc_now())
    assert doc.status is IndexStatus.FAILED
    assert doc.error == "parser exploded"


def test_start_indexing_is_reentrant_from_indexing() -> None:
    doc = _document(status=IndexStatus.INDEXING)
    before = doc.updated_at
    later = utc_now()
    doc.start_indexing(later)
    assert doc.status is IndexStatus.INDEXING
    assert doc.updated_at == later
    assert doc.updated_at >= before


@pytest.mark.parametrize("status", [IndexStatus.INDEXED, IndexStatus.FAILED])
def test_start_indexing_from_terminal_status_raises(status: IndexStatus) -> None:
    doc = _document(status=status)
    with pytest.raises(DocumentStateError):
        doc.start_indexing(utc_now())


def test_complete_indexing_from_indexing_sets_indexed_and_clears_error() -> None:
    doc = _document(status=IndexStatus.INDEXING)
    doc.error = "stale error from a previous attempt"
    doc.complete_indexing(4, utc_now())
    assert doc.status is IndexStatus.INDEXED
    assert doc.chunk_count == 4
    assert doc.error is None


@pytest.mark.parametrize("status", [IndexStatus.PENDING, IndexStatus.INDEXED, IndexStatus.FAILED])
def test_complete_indexing_from_non_indexing_status_raises(status: IndexStatus) -> None:
    doc = _document(status=status)
    with pytest.raises(DocumentStateError):
        doc.complete_indexing(1, utc_now())


def test_fail_indexing_from_indexing_sets_failed_and_records_reason() -> None:
    doc = _document(status=IndexStatus.INDEXING)
    doc.fail_indexing("boom", utc_now())
    assert doc.status is IndexStatus.FAILED
    assert doc.error == "boom"


@pytest.mark.parametrize("status", [IndexStatus.PENDING, IndexStatus.INDEXED, IndexStatus.FAILED])
def test_fail_indexing_from_non_indexing_status_raises(status: IndexStatus) -> None:
    doc = _document(status=status)
    with pytest.raises(DocumentStateError):
        doc.fail_indexing("boom", utc_now())


# --------------------------------------------------------------------------- #
# RegisterDocumentFromFile                                                    #
# --------------------------------------------------------------------------- #
async def test_register_document_from_file_mints_pending_document() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")

    doc, events = await RegisterDocumentFromFile(documents).execute(ctx, file_id="file-1")

    assert doc.status is IndexStatus.PENDING
    assert doc.chunk_count == 0
    assert doc.version == 1
    assert doc.file_id == "file-1"
    assert doc.workspace_id == "ws1"
    assert documents.docs[doc.id] is doc

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, DocumentRegistered)
    assert event.document_id == doc.id
    assert event.workspace_id == "ws1"
    assert event.file_id == "file-1"
    assert event.occurred_at == doc.created_at


async def test_register_document_from_file_empty_file_id_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        await RegisterDocumentFromFile(_FakeDocumentRepository()).execute(_ctx(), file_id="   ")


async def test_register_document_from_file_allows_duplicate_registrations() -> None:
    """INV-K3: a re-registration for the same file is a brand-new,
    independent ``Document`` -- never an update to a prior one."""
    documents = _FakeDocumentRepository()
    ctx = _ctx()
    use_case = RegisterDocumentFromFile(documents)

    first, _ = await use_case.execute(ctx, file_id="file-1")
    second, _ = await use_case.execute(ctx, file_id="file-1")

    assert first.id != second.id
    assert len(documents.docs) == 2


# --------------------------------------------------------------------------- #
# IndexRegisteredDocument (over the REAL 3.k3 IndexDocument pipeline)         #
# --------------------------------------------------------------------------- #
async def test_index_registered_document_happy_path() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc

    embeddings = _FakeEmbeddings(dim=6)
    vectors = _FakeHybridVectors()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, vectors))
    parsed = _parsed_document(
        [
            _parsed_chunk("first paragraph about quarterly revenue", order=0),
            _parsed_chunk("second paragraph about headcount changes", order=1),
        ]
    )

    result, events = await use_case.execute(
        ctx, document_id=doc.id, parsed=parsed, model="embed-1", api_key="key-1"
    )

    assert result is doc
    assert doc.status is IndexStatus.INDEXED
    assert doc.chunk_count == 2
    assert [call[1] for call in documents.status_calls] == ["indexing", "indexed"]
    assert [call[2] for call in documents.status_calls] == [None, None]

    assert len(documents.chunks) == 2
    seen_ids: set[str] = set()
    for chunk in documents.chunks:
        assert chunk.document_id == doc.id
        assert chunk.workspace_id == "ws1"
        assert chunk.text
        assert chunk.vector_ref is not None
        assert chunk.vector_ref.collection == "kn-ws1"
        expected_point_id = chunk_point_id(doc.id, chunk.seq)
        assert chunk.vector_ref.point_id == expected_point_id
        # 3.k3 -> 3.k4 handoff invariant: the row id is a FRESH UUIDv7,
        # distinct from the deterministic vector-store point id.
        assert chunk.id != expected_point_id
        seen_ids.add(chunk.id)
    assert len(seen_ids) == 2  # both chunk ids are distinct from each other too

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, DocumentIndexed)
    assert event.document_id == doc.id
    assert event.workspace_id == "ws1"
    assert event.file_id == "file-1"
    assert event.chunk_count == 2
    assert event.collection == "kn-ws1"


async def test_index_registered_document_pipeline_failure_marks_document_failed() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc

    embeddings = _FakeEmbeddings(fail=True)
    vectors = _FakeHybridVectors()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, vectors))
    parsed = _parsed_document([_parsed_chunk("some content to embed", order=0)])

    result, events = await use_case.execute(
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k"
    )

    assert result is doc
    assert doc.status is IndexStatus.FAILED
    assert doc.error is not None
    assert "unavailable" in doc.error
    assert [call[1] for call in documents.status_calls] == ["indexing", "failed"]
    assert documents.status_calls[-1][2] == doc.error
    assert documents.chunks == []

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, DocumentIndexingFailed)
    assert event.document_id == doc.id
    assert event.workspace_id == "ws1"
    assert event.reason == doc.error


async def test_index_registered_document_already_indexed_is_a_noop() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", status=IndexStatus.INDEXED, chunk_count=3)
    documents.docs[doc.id] = doc
    embeddings = _FakeEmbeddings()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, _FakeHybridVectors()))

    result, events = await use_case.execute(
        ctx, document_id=doc.id, parsed=_parsed_document([]), model="m", api_key="k"
    )

    assert result is doc
    assert result.status is IndexStatus.INDEXED
    assert events == ()
    assert documents.status_calls == []
    assert documents.chunks == []
    assert embeddings.calls == []


async def test_index_registered_document_already_failed_is_a_noop() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", status=IndexStatus.FAILED, error="earlier failure")
    documents.docs[doc.id] = doc
    embeddings = _FakeEmbeddings()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, _FakeHybridVectors()))

    result, events = await use_case.execute(
        ctx, document_id=doc.id, parsed=_parsed_document([]), model="m", api_key="k"
    )

    assert result is doc
    assert result.status is IndexStatus.FAILED
    assert events == ()
    assert documents.status_calls == []
    assert embeddings.calls == []


async def test_index_registered_document_missing_document_raises_not_found() -> None:
    documents = _FakeDocumentRepository()
    use_case = IndexRegisteredDocument(
        documents, IndexDocument(_FakeEmbeddings(), _FakeHybridVectors())
    )
    with pytest.raises(NotFoundError):
        await use_case.execute(
            _ctx(), document_id="missing", parsed=_parsed_document([]), model="m", api_key="k"
        )


async def test_run_persists_no_terminal_outcome_until_finalize() -> None:
    """The 5.2-أ split's load-bearing property (the closed D5 window): after
    ``run``, the ONLY persisted status is ``indexing`` -- chunks, terminal
    status, and the follow-on event all wait for ``finalize``, which the
    worker handler wraps in its own single transaction. A ``run`` that
    quietly persisted the terminal outcome again would put the terminal
    write back outside that transaction -- the exact crash window 5.2-أ
    closed."""
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc
    use_case = IndexRegisteredDocument(
        documents, IndexDocument(_FakeEmbeddings(dim=6), _FakeHybridVectors())
    )
    parsed = _parsed_document([_parsed_chunk("a paragraph worth chunking", order=0)])

    attempt = await use_case.run(
        ctx, document_id=doc.id, parsed=parsed, model="embed-1", api_key="key-1"
    )

    assert attempt.outcome is not None
    assert attempt.error is None
    assert not attempt.is_redelivery_noop
    assert [call[1] for call in documents.status_calls] == ["indexing"]  # nothing terminal
    assert documents.chunks == []  # chunks are finalize's to write

    result, events = await use_case.finalize(ctx, attempt)

    assert result is doc
    assert doc.status is IndexStatus.INDEXED
    assert [call[1] for call in documents.status_calls] == ["indexing", "indexed"]
    assert len(documents.chunks) == 1
    assert [type(event).__name__ for event in events] == ["DocumentIndexed"]


async def test_run_carries_a_pipeline_failure_as_data_for_finalize() -> None:
    """Same property on the failure path: ``run`` catches the pipeline
    failure but persists nothing terminal; ``finalize`` is what lands
    ``failed`` + the ``DocumentIndexingFailed`` event."""
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc
    use_case = IndexRegisteredDocument(
        documents, IndexDocument(_FakeEmbeddings(fail=True), _FakeHybridVectors())
    )
    parsed = _parsed_document([_parsed_chunk("content that will fail to embed", order=0)])

    attempt = await use_case.run(ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k")

    assert attempt.outcome is None
    assert attempt.error is not None
    assert "unavailable" in attempt.error
    assert not attempt.is_redelivery_noop
    assert [call[1] for call in documents.status_calls] == ["indexing"]  # not failed YET

    result, events = await use_case.finalize(ctx, attempt)

    assert result is doc
    assert doc.status is IndexStatus.FAILED
    assert documents.status_calls[-1][1] == "failed"
    assert [type(event).__name__ for event in events] == ["DocumentIndexingFailed"]


async def test_run_short_circuits_terminal_documents_and_finalize_passes_through() -> None:
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", status=IndexStatus.INDEXED, chunk_count=3)
    documents.docs[doc.id] = doc
    embeddings = _FakeEmbeddings()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, _FakeHybridVectors()))

    attempt = await use_case.run(
        ctx, document_id=doc.id, parsed=_parsed_document([]), model="m", api_key="k"
    )

    assert attempt.is_redelivery_noop
    assert embeddings.calls == []

    result, events = await use_case.finalize(ctx, attempt)

    assert result is doc
    assert events == ()
    assert documents.status_calls == []


# --------------------------------------------------------------------------- #
# KnowledgeRetrievalService (over the REAL 3.k3 RetrieveContext use-case)     #
# --------------------------------------------------------------------------- #
async def test_knowledge_retrieval_service_resolves_embedding_and_delegates() -> None:
    ctx = _ctx("ws1")
    embeddings = _FakeEmbeddings(dim=6)
    vectors = _FakeHybridVectors()
    text = "quarterly revenue figures for the northern region"
    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-1",
        parsed=_parsed_document([_parsed_chunk(text, order=0)]),
        model="embed-1",
        api_key="key-1",
    )

    resolver = _FakeEmbeddingResolver(model="embed-1", api_key="key-1")
    retrieval = RetrieveContext(embeddings, vectors)
    service = KnowledgeRetrievalService(retrieval, resolver)

    # Static-typing assertion: KnowledgeRetrievalService satisfies the
    # KnowledgeRetrieval inbound port -- mypy is the real assertion here.
    svc: KnowledgeRetrieval = service

    results = await svc.retrieve(ctx, text, 1)

    assert resolver.calls == [ctx]
    assert len(results) == 1
    assert results[0].document_id == "doc-1"
    assert results[0].text == text

    # k propagated all the way through to the underlying search calls:
    # search_k = k * _SEARCH_OVERFETCH (RetrieveContext's overfetch, 3.k3)
    # == 1 * 3 == 3.
    assert vectors.search_calls[-1][1] == 3
    assert vectors.search_sparse_calls[-1][1] == 3
