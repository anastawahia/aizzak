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
import json
import math
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.types import Json
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.application.use_cases import (
    IndexFile,
    IndexFileService,
    IndexRegisteredDocument,
    KnowledgeRetrievalService,
    RegisterDocumentFromFile,
)
from app.modules.knowledge.domain.collections import chunk_point_id
from app.modules.knowledge.domain.entities import Chunk, Document, ParentChunk
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

# Two spaces in ONE workspace (spaces plan step 8) -- the axis the tests
# below narrow along. Opaque strings, exactly as every layer under test
# treats them.
_SPACE_A = "space-research"
_SPACE_B = "space-drafts"


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
    space_id: str | None = None,
    file_id: str = "file-1",
    status: IndexStatus = IndexStatus.PENDING,
    chunk_count: int = 0,
    error: str | None = None,
) -> Document:
    now = utc_now()
    return Document(
        id=doc_id,
        workspace_id=workspace_id,
        space_id=space_id,
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
    # A LIST value is membership, not equality — mirroring the Qdrant adapter,
    # which renders a list as `MatchAny` and a scalar as `MatchValue`
    # (`infrastructure/vector/qdrant_store.py::_build_filter`). Without this
    # branch the fake would silently match nothing for BE-RAG-005's
    # `document_id` scope, and a scoping bug would read as an empty result.
    return all(
        payload.get(key) in value if isinstance(value, list) else payload.get(key) == value
        for key, value in flt.items()
    )


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

    # See `test_knowledge_pipeline.FakeHybridVectors` -- adapter-side concern,
    # present for the Protocol only.
    async def ensure_payload_index(
        self, collection: str, field: str, *, tenant: bool = False
    ) -> None: ...

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
    and flattens every ``add_chunks``/``add_parent_chunks`` call into
    ``self.chunks``/``self.parents``."""

    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}
        self.chunks: list[Chunk] = []
        self.parents: list[ParentChunk] = []
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

    async def add_parent_chunks(
        self, ctx: ExecutionContext, parents: Sequence[ParentChunk]
    ) -> None:
        self.parents.extend(parents)

    async def ids_for_files(self, ctx: ExecutionContext, file_ids: Sequence[str]) -> Sequence[str]:
        # The real predicate: same workspace, `file_id` in the caller's list.
        # A file with no document contributes nothing, which is what makes a
        # pin of an unindexed file narrow the scope rather than widen it.
        wanted = set(file_ids)
        return [
            doc.id
            for doc in self.docs.values()
            if doc.workspace_id == ctx.workspace_id and doc.file_id in wanted
        ]


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

    doc, events = await RegisterDocumentFromFile(documents).execute(
        ctx, file_id="file-1", space_id=None
    )

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
        await RegisterDocumentFromFile(_FakeDocumentRepository()).execute(
            _ctx(), file_id="   ", space_id=None
        )


async def test_register_document_from_file_allows_duplicate_registrations() -> None:
    """INV-K3: a re-registration for the same file is a brand-new,
    independent ``Document`` -- never an update to a prior one."""
    documents = _FakeDocumentRepository()
    ctx = _ctx()
    use_case = RegisterDocumentFromFile(documents)

    first, _ = await use_case.execute(ctx, file_id="file-1", space_id=None)
    second, _ = await use_case.execute(ctx, file_id="file-1", space_id=None)

    assert first.id != second.id
    assert len(documents.docs) == 2


# --------------------------------------------------------------------------- #
# IndexFile / IndexFileService -- the manual-indexing face                     #
# --------------------------------------------------------------------------- #
class _FakeReadableFile:
    def __init__(self, file_id: str, space_id: str | None) -> None:
        self.file_id = file_id
        self.space_id = space_id


class _FakeReadableFiles:
    """A structural ``ReadableFiles``. ``None`` for anything not seeded --
    the real seam collapses "unknown", "deleted", "quarantined" and "still
    uploading" into exactly that answer."""

    def __init__(self, files: dict[str, str | None] | None = None) -> None:
        self.files = files or {}
        self.calls: list[str] = []

    async def get_readable(self, ctx: ExecutionContext, file_id: str) -> _FakeReadableFile | None:
        self.calls.append(file_id)
        if file_id not in self.files:
            return None
        return _FakeReadableFile(file_id, self.files[file_id])


class _TrackingUnitOfWork:
    def __init__(self) -> None:
        self.active = False

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        self.active = True
        try:
            yield
        finally:
            self.active = False


async def test_index_file_mints_a_pending_document_under_the_files_space() -> None:
    """The space comes off the FILE, never off the caller: a document filed
    under a space its file does not belong to would answer, inside that space,
    from content the space cannot see."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    ctx = _ctx("ws1")

    doc, events = await IndexFile(documents, files).execute(ctx, file_id="file-1")

    assert doc.status is IndexStatus.PENDING
    assert doc.file_id == "file-1"
    assert doc.space_id == _SPACE_A
    assert doc.workspace_id == "ws1"
    assert documents.docs[doc.id] is doc
    assert [type(event) for event in events] == [DocumentRegistered]
    assert files.calls == ["file-1"]


async def test_index_file_accepts_a_file_that_belongs_to_no_space() -> None:
    """A spaceless file produces a spaceless document -- the same shape the
    pre-plan corpus already has. Refusing it here would make a file
    unindexable for a reason that has nothing to do with indexing."""
    documents = _FakeDocumentRepository()

    doc, _ = await IndexFile(documents, _FakeReadableFiles({"file-1": None})).execute(
        _ctx(), file_id="file-1"
    )

    assert doc.space_id is None


async def test_index_file_refuses_a_file_that_is_not_readable() -> None:
    """This is the whole "only after the upload completes" precondition: the
    seam answers ``None`` until the bytes are in storage, so a button pressed
    too early is a 404 rather than a document a worker cannot parse."""
    documents = _FakeDocumentRepository()

    with pytest.raises(NotFoundError):
        await IndexFile(documents, _FakeReadableFiles()).execute(_ctx(), file_id="file-1")

    assert documents.docs == {}


async def test_index_file_refuses_a_file_that_already_has_a_document() -> None:
    """The double-click guard. INV-K3 permits a second document over one file
    and the repository has no constraint against it, so this read is the only
    thing between two clicks and a corpus that answers from that file twice."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    ctx = _ctx("ws1")
    use_case = IndexFile(documents, files)
    await use_case.execute(ctx, file_id="file-1")

    with pytest.raises(ConflictError):
        await use_case.execute(ctx, file_id="file-1")

    assert len(documents.docs) == 1


async def test_index_file_guard_is_scoped_to_the_workspace() -> None:
    """``ids_for_files`` filters by workspace, so another tenant's document
    over the same file id must not make this one a 409."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    await IndexFile(documents, files).execute(_ctx("ws1"), file_id="file-1")

    doc, _ = await IndexFile(documents, files).execute(_ctx("ws2"), file_id="file-1")

    assert doc.workspace_id == "ws2"


async def test_index_file_empty_file_id_raises_validation_error() -> None:
    files = _FakeReadableFiles()

    with pytest.raises(ValidationError):
        await IndexFile(_FakeDocumentRepository(), files).execute(_ctx(), file_id="   ")

    assert files.calls == []  # refused before the seam was even asked


async def test_index_file_service_appends_the_event_inside_the_unit_of_work() -> None:
    """A document without its ``DocumentRegistered`` is a file the user asked
    to index, reporting itself ``pending``, that no worker was ever told
    about -- indistinguishable from one merely waiting its turn."""
    uow = _TrackingUnitOfWork()
    documents = _FakeDocumentRepository()

    class _SpyOutbox:
        def __init__(self) -> None:
            self.appended_while_active: bool | None = None
            self.event_types: list[str] = []

        async def append(self, ctx: ExecutionContext, records: Sequence[object]) -> None:
            self.appended_while_active = uow.active
            self.event_types.extend(record.event_type for record in records)

    spy = _SpyOutbox()
    service = IndexFileService(
        IndexFile(documents, _FakeReadableFiles({"file-1": _SPACE_A})), spy, uow
    )

    document = await service.start(_ctx(), file_id="file-1")

    assert document.status is IndexStatus.PENDING
    assert spy.appended_while_active is True
    assert spy.event_types == ["knowledge.document.registered.v1"]
    assert uow.active is False


async def test_index_file_service_outbox_failure_is_not_swallowed() -> None:
    class _ExplodingOutbox:
        async def append(self, ctx: ExecutionContext, records: Sequence[object]) -> None:
            raise RuntimeError("outbox is down")

    service = IndexFileService(
        IndexFile(_FakeDocumentRepository(), _FakeReadableFiles({"file-1": None})),
        _ExplodingOutbox(),
        _TrackingUnitOfWork(),
    )

    with pytest.raises(RuntimeError, match="outbox is down"):
        await service.start(_ctx(), file_id="file-1")


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


async def test_index_registered_document_wires_table_parent_id_end_to_end() -> None:
    """P-13 (plan §3.3) end to end: a small table's rows explode into their
    own ``Chunk`` rows, a SINGLE ``ParentChunk`` row is minted for the whole
    table, and every one of those ``Chunk`` rows' ``parent_id`` resolves to
    that same minted id -- never the table text itself (constraint 1,
    plan §3.2)."""
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc
    use_case = IndexRegisteredDocument(
        documents, IndexDocument(_FakeEmbeddings(dim=6), _FakeHybridVectors())
    )
    table_text = json.dumps(
        {
            "headers": ["Name", "Salary"],
            "rows": [{"Name": "Ahmad", "Salary": "5000"}, {"Name": "Sara", "Salary": "6000"}],
        }
    )
    table_chunk = ParsedChunk(text=table_text, order=0, kind=ParsedChunkKind.TABLE, metadata={})
    parsed = _parsed_document([table_chunk])

    result, _events = await use_case.execute(
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k"
    )

    assert result.status is IndexStatus.INDEXED
    assert len(documents.parents) == 1
    parent = documents.parents[0]
    assert parent.document_id == doc.id
    assert parent.workspace_id == "ws1"
    assert parent.text == "Name: Ahmad; Salary: 5000\nName: Sara; Salary: 6000"

    assert len(documents.chunks) == 2
    for chunk in documents.chunks:
        assert chunk.parent_id == parent.id
        assert chunk.text != parent.text  # the payload/row text, never the parent text


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
        space_id=None,
        parsed=_parsed_document([_parsed_chunk(text, order=0)]),
        model="embed-1",
        api_key="key-1",
    )

    resolver = _FakeEmbeddingResolver(model="embed-1", api_key="key-1")
    retrieval = RetrieveContext(embeddings, vectors)
    service = KnowledgeRetrievalService(retrieval, resolver, _FakeDocumentRepository())

    # Static-typing assertion: KnowledgeRetrievalService satisfies the
    # KnowledgeRetrieval inbound port -- mypy is the real assertion here.
    svc: KnowledgeRetrieval = service

    results = await svc.retrieve(ctx, text, 1, space_id=None)

    assert resolver.calls == [ctx]
    assert len(results) == 1
    assert results[0].document_id == "doc-1"
    assert results[0].text == text

    # k propagated all the way through to the underlying search calls:
    # search_k = k * _SEARCH_OVERFETCH (RetrieveContext's overfetch, 3.k3)
    # == 1 * 3 == 3.
    assert vectors.search_calls[-1][1] == 3
    assert vectors.search_sparse_calls[-1][1] == 3
    # Unscoped by default: no `document_id` narrowing reaches either leg, so
    # every caller that predates BE-RAG-005 still searches the whole corpus.
    assert "document_id" not in (vectors.search_calls[-1][2] or {})


async def _indexed_corpus(
    ctx: ExecutionContext, embeddings: _FakeEmbeddings, vectors: _FakeHybridVectors
) -> _FakeDocumentRepository:
    """Two files, two documents, one indexed chunk each — the smallest corpus
    in which a scope can be wrong in a visible way."""
    documents = _FakeDocumentRepository()
    for doc_id, file_id, text in (
        ("doc-north", "file-north", "quarterly revenue figures for the northern region"),
        ("doc-south", "file-south", "quarterly revenue figures for the southern region"),
    ):
        documents.docs[doc_id] = _document(
            doc_id=doc_id, file_id=file_id, status=IndexStatus.INDEXED, chunk_count=1
        )
        await IndexDocument(embeddings, vectors).execute(
            ctx,
            document_id=doc_id,
            space_id=None,
            parsed=_parsed_document([_parsed_chunk(text, order=0)]),
            model="embed-1",
            api_key="key-1",
        )
    return documents


async def _spaced_corpus(
    ctx: ExecutionContext, embeddings: _FakeEmbeddings, vectors: _FakeHybridVectors
) -> _FakeDocumentRepository:
    """The same two documents, in two DIFFERENT spaces — the smallest corpus
    in which a space filter can be wrong in a visible way (spaces plan step
    8). Indexed through the real pipeline, so the payload each point carries
    is the one production would have written."""
    documents = _FakeDocumentRepository()
    for doc_id, space_id, text in (
        ("doc-research", _SPACE_A, "quarterly revenue figures for the northern region"),
        ("doc-drafts", _SPACE_B, "quarterly revenue figures for the southern region"),
    ):
        documents.docs[doc_id] = _document(
            doc_id=doc_id,
            space_id=space_id,
            file_id=f"file-{doc_id}",
            status=IndexStatus.INDEXED,
            chunk_count=1,
        )
        await IndexDocument(embeddings, vectors).execute(
            ctx,
            document_id=doc_id,
            space_id=space_id,
            parsed=_parsed_document([_parsed_chunk(text, order=0)]),
            model="embed-1",
            api_key="key-1",
        )
    return documents


async def test_a_file_scope_narrows_retrieval_to_that_file_s_documents() -> None:
    """BE-RAG-005: pinned FILE ids are translated to document ids inside the
    module, and the translation is what reaches the vector filter."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _indexed_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    results = await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-north"], space_id=None
    )

    assert [chunk.document_id for chunk in results] == ["doc-north"]
    # FILE ids in, DOCUMENT ids out: the caller never has to know documents
    # exist, and the payload the filter runs against only knows document ids.
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws1", "document_id": ["doc-north"]}
    # The tenant filter is never REPLACED by the scope (DD-04).
    assert vectors.search_sparse_calls[-1][2]["workspace_id"] == "ws1"


async def test_an_unscoped_retrieval_still_sees_the_whole_corpus() -> None:
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _indexed_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    results = await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=None)

    assert {chunk.document_id for chunk in results} == {"doc-north", "doc-south"}


async def test_a_scope_of_unindexed_files_retrieves_nothing_rather_than_everything() -> None:
    """The load-bearing distinction: a pin that resolves to NO documents is a
    scope of nothing, not an absent scope.

    Widening back to the whole corpus here would make a thread pinned to a
    file that failed to index answer from every other document in the
    workspace — the one moment the pin most needs to hold.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _indexed_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )
    searches_before = len(vectors.search_calls)

    results = await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-never-indexed"], space_id=None
    )

    assert results == []
    # And it short-circuits: no vector round trip is made for a scope that
    # cannot match anything.
    assert len(vectors.search_calls) == searches_before


# --------------------------------------------------------------------------- #
# The space axis (spaces plan step 8): the column, the payload, the filter    #
# --------------------------------------------------------------------------- #
async def test_a_registered_document_is_filed_under_its_file_s_space() -> None:
    """The whole point of the column: the document the worker mints carries
    the space the FILE was in, unexamined and unaltered."""
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")

    doc, _ = await RegisterDocumentFromFile(documents).execute(
        ctx, file_id="file-1", space_id=_SPACE_A
    )

    assert doc.space_id == _SPACE_A
    assert documents.docs[doc.id].space_id == _SPACE_A


async def test_a_file_with_no_space_registers_a_document_with_none() -> None:
    """The state every document has today, and the one row 8-b will find:
    `None` is written through, never turned into a guess."""
    documents = _FakeDocumentRepository()

    doc, _ = await RegisterDocumentFromFile(documents).execute(
        _ctx("ws1"), file_id="file-1", space_id=None
    )

    assert doc.space_id is None


async def test_every_indexed_point_carries_its_document_s_space_in_the_payload() -> None:
    """§3.4's payload key, written by the REAL pipeline — this is what the
    retrieval filter matches on, and what step 9 will index."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()

    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-1",
        space_id=_SPACE_A,
        parsed=_parsed_document(
            [_parsed_chunk("first window of text", 0), _parsed_chunk("second window of text", 1)]
        ),
        model="embed-1",
        api_key="key-1",
    )

    points = list(vectors.points["kn-ws1"].values())
    assert len(points) == 2
    assert {point.payload["space"] for point in points} == {_SPACE_A}
    # The tenant key is not replaced by it (DD-04): a space is an axis INSIDE
    # the workspace.
    assert {point.payload["workspace_id"] for point in points} == {"ws1"}


async def test_a_spaceless_document_omits_the_payload_key_rather_than_writing_null() -> None:
    """`null` and "absent" are the same to a `MatchValue` filter but not to a
    reader: the absent key is what every pre-step-8 point already has, and the
    adapter cannot render a `None` filter value at all."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()

    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-1",
        space_id=None,
        parsed=_parsed_document([_parsed_chunk("a window of text", 0)]),
        model="embed-1",
        api_key="key-1",
    )

    (point,) = vectors.points["kn-ws1"].values()
    assert "space" not in point.payload


async def test_the_worker_takes_the_payload_space_from_the_document_row() -> None:
    """The link between the column and the payload, over the REAL pipeline.

    ``knowledge.document.registered.v1`` carries no space, so the aggregate
    ``run`` loads is the only thing that knows one. Pass ``None`` there and
    every point is indexed spaceless while the row says otherwise — the two
    disagree, and only a space-scoped search (returning nothing, quietly)
    would ever say so.
    """
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    doc = _document(space_id=_SPACE_B, status=IndexStatus.PENDING)
    documents.docs[doc.id] = doc
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    use_case = IndexRegisteredDocument(documents, IndexDocument(embeddings, vectors))

    await use_case.execute(
        ctx,
        document_id=doc.id,
        parsed=_parsed_document([_parsed_chunk("a window of indexed text", 0)]),
        model="embed-1",
        api_key="key-1",
    )

    (point,) = vectors.points["kn-ws1"].values()
    assert point.payload["space"] == _SPACE_B


async def test_a_space_scoped_retrieval_never_answers_from_another_space() -> None:
    """The filter, end to end over the real pipeline and the real fusion: two
    documents, two spaces, one query that matches both texts."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    results = await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A)

    assert [chunk.document_id for chunk in results] == ["doc-research"]
    # A single value ⇒ `MatchValue` at the adapter, ANDed onto the tenant key
    # on BOTH legs.
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws1", "space": _SPACE_A}
    assert vectors.search_sparse_calls[-1][2] == {"workspace_id": "ws1", "space": _SPACE_A}


async def test_a_space_and_a_pin_narrow_together_rather_than_replacing_each_other() -> None:
    """Both conditions reach the filter and are ANDed (§3.4): a pin does not
    widen the space, and a space does not drop the pin."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-doc-research"], space_id=_SPACE_A
    )

    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-research"],
        "space": _SPACE_A,
    }


async def test_an_unspaced_retrieval_still_sees_every_space() -> None:
    """`None` is "all spaces", never "the spaceless ones" — the key is left
    out of the filter entirely (which the Qdrant adapter also requires: a
    `None` filter value is a hard error there, not a no-op)."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    results = await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=None)

    assert {chunk.document_id for chunk in results} == {"doc-research", "doc-drafts"}
    assert "space" not in (vectors.search_calls[-1][2] or {})


async def test_content_indexed_before_spaces_falls_out_of_a_space_scoped_search() -> None:
    """§5-أ, made visible instead of discovered in production.

    A point written without the `space` key matches no space filter — no
    error, no warning, just nothing. This test exists so the mandatory
    re-index is a documented consequence rather than a surprise.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = _FakeDocumentRepository()
    documents.docs["doc-old"] = _document(
        doc_id="doc-old", space_id=_SPACE_A, status=IndexStatus.INDEXED, chunk_count=1
    )
    # Indexed as it was BEFORE this step: the row now says which space it is
    # in, but the point in the store predates the payload key.
    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-old",
        space_id=None,
        parsed=_parsed_document([_parsed_chunk("quarterly revenue figures", 0)]),
        model="embed-1",
        api_key="key-1",
    )
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
    )

    assert await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A) == []
    # And it is genuinely there, for anyone not asking about a space.
    assert await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=None) != []
