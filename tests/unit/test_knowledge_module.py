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
import inspect
import json
import math
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.settings.settings import RetrievalSettings
from app.framework.types import Json
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.retrieval import (
    _DEFAULT_TUNING as _SHIPPED_TUNING,
)
from app.modules.knowledge.application.retrieval import (
    RetrievalResult,
    RetrieveContext,
)
from app.modules.knowledge.application.routing import RouteQuestion
from app.modules.knowledge.application.use_cases import (
    _DEFAULT_MAX_CORPUS_NAMES,
    _LIST_PAGE_SIZE,
    IndexFile,
    IndexFileService,
    IndexRegisteredDocument,
    KnowledgeRetrievalService,
    ListDocumentNames,
    ListFileCandidates,
    RegisterDocumentFromFile,
)
from app.modules.knowledge.domain.collections import chunk_point_id
from app.modules.knowledge.domain.entities import Chunk, Document, ParentChunk, SummaryJob
from app.modules.knowledge.domain.errors import DocumentStateError, InvalidKnowledgeInput
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
)
from app.modules.knowledge.domain.file_resolution import FileCandidate
from app.modules.knowledge.domain.intent import Intent
from app.modules.knowledge.domain.pipeline import PIPELINE_VERSION, content_pipeline_unchanged
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    ParentChunkText,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)
from app.modules.knowledge.ports.inbound import DocumentNames, KnowledgeRetrieval, RoutedAnswer
from app.modules.knowledge.ports.retrieval import ResolvedEmbedding

# The shipped tuning with both per-leg floors off (س-22's numbers were
# calibrated 2026-08-27 on `P-38`'s evaluation set: 0.45 on the dense leg's
# cosine scale, 25.0 on the sparse leg's IDF-weighted dot product). Every
# retrieval in this file is about SCOPE -- which documents, which space, which
# file a question named -- and the fakes score with synthetic vectors whose
# magnitudes bear no relation to the scales those two numbers were measured
# on. Gating them here would assert the fake's arithmetic against a real
# corpus's calibration; the floors carry their own tests in
# `test_knowledge_pipeline.py`.
_UNGATED = replace(_SHIPPED_TUNING, min_dense_score=0.0, min_bm25_score=0.0)

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
    # س-32 — every corpus walk in this module filters on a space now (the
    # header's included), so the DEFAULT document has to live in one or half
    # these fixtures would describe a corpus nothing can reach. `_SPACE_A` is
    # that default; a test about the axis itself still names both, and a test
    # about a spaceless row still passes `space_id=None` explicitly.
    space_id: str | None = _SPACE_A,
    file_id: str = "file-1",
    status: IndexStatus = IndexStatus.PENDING,
    chunk_count: int = 0,
    error: str | None = None,
    content_hash: str | None = None,
    pipeline_version: int | None = None,
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
        content_hash=content_hash,
        pipeline_version=pipeline_version,
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
        self,
        collection: str,
        vector: list[float],
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
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
        self,
        collection: str,
        sparse: SparseVector,
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
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
        # Plan step 15 (§3.6): every `set_status(..., content_hash=..., pipeline_version=...)`
        # call, so a test can assert the fingerprint was persisted alongside
        # `'indexed'` without reaching into the SQL adapter.
        self.fingerprint_calls: list[tuple[str, str | None, int | None]] = []
        # Plan step 16 (`P-05`): the same, for the per-kind breakdown.
        self.stats_calls: list[tuple[str, int, int, int]] = []
        # Plan step 9 (`P-34`): a test seeds this directly to make
        # `parent_texts_for_chunk_ids` resolve specific (leaf) chunk ids to
        # a `ParentChunkText` -- see that method's own docstring.
        self.chunk_parent_texts: dict[str, ParentChunkText] = {}
        # ب-2: how many PAGES the corpus walk actually asked for, and how many
        # times it took the `count` short-circuit instead of paging on. The
        # whole point of that change is round trips, so a test that does not
        # count them cannot see it.
        self.list_calls = 0
        self.count_calls = 0

    async def get(self, ctx: ExecutionContext, doc_id: str) -> Document | None:
        doc = self.docs.get(doc_id)
        if doc is None or doc.workspace_id != ctx.workspace_id:
            return None
        return doc

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        self.docs[doc.id] = doc

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
        self.status_calls.append((doc_id, status, error))
        self.fingerprint_calls.append((doc_id, content_hash, pipeline_version))
        self.stats_calls.append((doc_id, text_chunks, table_chunks, image_chunks))

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        self.chunks.extend(chunks)

    async def add_parent_chunks(
        self, ctx: ExecutionContext, parents: Sequence[ParentChunk]
    ) -> None:
        self.parents.extend(parents)

    async def parent_texts_for_chunk_ids(
        self, ctx: ExecutionContext, chunk_ids: Sequence[str]
    ) -> dict[str, ParentChunkText]:
        """Plan step 9 (``P-34``): resolves each ``chunk_ids`` entry against
        ``self.chunk_parent_texts`` (empty by default, so every EXISTING test
        in this module degrades to leaf text unchanged) rather than actually
        joining ``self.chunks``/``self.parents`` -- a test that needs a real
        chunk-to-parent relationship sets this dict directly, mirroring how
        every other fake in this module trades a real join for a
        directly-seeded lookup."""
        wanted = set(chunk_ids)
        return {
            chunk_id: text
            for chunk_id, text in self.chunk_parent_texts.items()
            if chunk_id in wanted
        }

    async def count(self, ctx: ExecutionContext, *, space_id: str | None) -> int:
        # `list`'s predicate without its keyset -- the corpus walk reads THIS
        # once its cap is full instead of paging to the end of the corpus
        # (ب-2), and `None` still means "this workspace's", not "the
        # spaceless ones".
        self.count_calls += 1
        return sum(
            1
            for doc in self.docs.values()
            if doc.workspace_id == ctx.workspace_id
            and (space_id is None or doc.space_id == space_id)
        )

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

    async def list(
        self,
        ctx: ExecutionContext,
        *,
        space_id: str | None,
        limit: int,
        cursor: str | None = None,
    ) -> Page[Document]:
        # Retrieval plan §3.6, step 6 (`P-36`) -- `ListDocumentNames`' own
        # walk needs THIS, not `ids_for_files`; newest-first keyset on `id`
        # through the real codec, the `InMemoryDocumentRepository` precedent
        # (`tests/unit/support_knowledge.py`).
        self.list_calls += 1
        items = sorted(
            (
                doc
                for doc in self.docs.values()
                if doc.workspace_id == ctx.workspace_id
                and (space_id is None or doc.space_id == space_id)
            ),
            key=lambda doc: doc.id,
            reverse=True,
        )
        if cursor is not None:
            after = decode_id_cursor(cursor)
            items = [doc for doc in items if doc.id < after]
        page, has_more = items[:limit], len(items) > limit
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)


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


def test_complete_indexing_stamps_the_content_fingerprint_pair() -> None:
    """Plan step 15 (§3.6): the fingerprint is written in the SAME transition
    as completion, never independently of it."""
    doc = _document(status=IndexStatus.INDEXING)
    doc.complete_indexing(2, utc_now(), content_hash="hash-abc", pipeline_version=7)
    assert doc.content_hash == "hash-abc"
    assert doc.pipeline_version == 7


def test_complete_indexing_defaults_the_fingerprint_to_none() -> None:
    """A caller that only cares about the state machine (every OTHER test in
    this section) gets `None`/`None` rather than being forced to invent a
    fingerprint it has no opinion about."""
    doc = _document(status=IndexStatus.INDEXING)
    doc.complete_indexing(2, utc_now())
    assert doc.content_hash is None
    assert doc.pipeline_version is None


# --------------------------------------------------------------------------- #
# domain/pipeline.py -- the content fingerprint pair (§3.6, decision س-14)    #
# --------------------------------------------------------------------------- #
def test_content_pipeline_unchanged_is_true_when_both_halves_match() -> None:
    assert content_pipeline_unchanged(
        stored_content_hash="hash-abc",
        current_content_hash="hash-abc",
        stored_pipeline_version=PIPELINE_VERSION,
        current_pipeline_version=PIPELINE_VERSION,
    )


def test_content_pipeline_unchanged_is_false_when_only_the_content_hash_differs() -> None:
    """Half 1 of the pair: a genuinely different file (a hypothetical content
    replacement, or two documents compared against each other) never counts
    as unchanged, even under today's pipeline version."""
    assert not content_pipeline_unchanged(
        stored_content_hash="hash-abc",
        current_content_hash="hash-xyz",
        stored_pipeline_version=PIPELINE_VERSION,
        current_pipeline_version=PIPELINE_VERSION,
    )


def test_content_pipeline_unchanged_is_false_when_only_the_pipeline_version_differs() -> None:
    """Half 2 of the pair, and §6 risk 4's whole point: identical bytes under
    an OLDER pipeline version must never look unchanged, or a parser upgrade
    would be silently invisible to every re-index."""
    assert not content_pipeline_unchanged(
        stored_content_hash="hash-abc",
        current_content_hash="hash-abc",
        stored_pipeline_version=PIPELINE_VERSION - 1,
        current_pipeline_version=PIPELINE_VERSION,
    )


def test_content_pipeline_unchanged_is_false_for_a_never_indexed_document() -> None:
    """`stored_content_hash=None`/`stored_pipeline_version=None` (a document
    that never completed indexing) never equals a real hash/version."""
    assert not content_pipeline_unchanged(
        stored_content_hash=None,
        current_content_hash="hash-abc",
        stored_pipeline_version=None,
        current_pipeline_version=PIPELINE_VERSION,
    )


def test_a_documents_content_hash_is_stamped_once_and_can_never_change() -> None:
    """The tripwire for plan §3.6's summary-invalidation clause (step 15),
    named in ``domain/pipeline.py``'s own docstring.

    That clause ("a changed ``content_hash`` deletes the document's
    ``summaries``") has no code path because a stored hash cannot change:
    ``complete_indexing`` is the column's only writer, it refuses any status
    but ``indexing``, and INV-K3 forbids a document returning there. So the
    value goes ``None -> hash`` once, and a summary can never outlive the
    text it was written from.

    If this test ever fails, that reasoning has been broken — an in-place
    re-index now exists — and the summary invalidation the plan describes
    has to be built for real before the change ships.
    """
    doc = _document(status=IndexStatus.INDEXING)
    doc.complete_indexing(2, utc_now(), content_hash="hash-abc", pipeline_version=PIPELINE_VERSION)
    assert doc.content_hash == "hash-abc"

    with pytest.raises(DocumentStateError):
        doc.complete_indexing(
            2, utc_now(), content_hash="hash-xyz", pipeline_version=PIPELINE_VERSION
        )
    assert doc.content_hash == "hash-abc"


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
    def __init__(self, file_id: str, space_id: str | None, name: str = "") -> None:
        self.file_id = file_id
        self.space_id = space_id
        # Retrieval plan §3.6, step 6 (`P-36`) -- `IndexFile` never reads
        # this, only `ListDocumentNames` does; defaults to the empty string
        # so every pre-existing `_FakeReadableFiles({"file-1": _SPACE_A})`
        # call (name-blind) keeps compiling and passing unchanged.
        self.name = name


class _FakeReadableFiles:
    """A structural ``ReadableFiles``. ``None`` (or absence from the mapping,
    on the bulk read) for anything not seeded -- the real seam collapses
    "unknown", "deleted", "quarantined" and "still uploading" into exactly
    that answer.

    **Two counters, and the difference between them is the point** (branch
    review §2): `calls` is every file id this seam was ASKED about, whichever
    method asked, and `reads` is one entry per ROUND TRIP. Before the bulk
    read the two were the same list; now a page of 200 ids is 200 in `calls`
    and 1 in `reads`, which is exactly the cost that changed.
    """

    def __init__(
        self,
        files: dict[str, str | None] | None = None,
        *,
        names: dict[str, str] | None = None,
    ) -> None:
        self.files = files or {}
        # Retrieval plan §3.6, step 6 (`P-36`) -- a SEPARATE mapping so every
        # existing `files=` call site (keyed on space, not name) is untouched;
        # a file seeded in `files` but absent here just answers `""`.
        self.names = names or {}
        self.calls: list[str] = []
        self.reads: list[tuple[str, ...]] = []

    async def get_readable(self, ctx: ExecutionContext, file_id: str) -> _FakeReadableFile | None:
        self.calls.append(file_id)
        self.reads.append((file_id,))
        if file_id not in self.files:
            return None
        return _FakeReadableFile(file_id, self.files[file_id], self.names.get(file_id, ""))

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[str]
    ) -> Mapping[str, str]:
        # Branch review §2 -- ONE read for many ids, and it answers with the
        # same rule `get_readable` does: an unseeded file is ABSENT (never
        # `""`), a seeded one with no name in `names` answers `""`. The real
        # `SqlFileRepository.ready_names` states that rule as
        # `status = 'ready' AND deleted_at IS NULL`.
        self.calls.extend(file_ids)
        self.reads.append(tuple(file_ids))
        return {
            file_id: self.names.get(file_id, "") for file_id in file_ids if file_id in self.files
        }


class _FakeSummaryStarter:
    """A structural ``SummaryStarting`` (retrieval plan §3.4/§4 row 11,
    `P-21`): records what a routed summarisation asked for and hands back a
    queued job, the way the real ``RequestSummaryService`` does once its unit
    of work commits."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, SummaryKind, SummaryLanguage]] = []

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: str,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob:
        self.calls.append((document_id, kind, lang))
        return SummaryJob(
            id=f"job-{len(self.calls)}",
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            kind=kind,
            lang=lang,
            status=SummaryJobStatus.QUEUED,
            total_chunks=0,
            done_chunks=0,
            error=None,
            cancelled_at=None,
            finished_at=None,
            created_at=utc_now(),
        )


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


async def test_index_file_returns_the_existing_document_when_the_fingerprint_is_unchanged() -> None:
    """Decision س-14 = أ (plan §3.6/step 15): re-indexing a document whose
    fingerprint is unchanged returns immediately -- the existing INDEXED
    document, not a 409 and not a second one."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    ctx = _ctx("ws1")
    existing = _document(
        doc_id="doc-1",
        workspace_id="ws1",
        space_id=_SPACE_A,
        file_id="file-1",
        status=IndexStatus.INDEXED,
        chunk_count=3,
        content_hash="hash-abc",
        pipeline_version=PIPELINE_VERSION,
    )
    documents.docs[existing.id] = existing

    doc, events = await IndexFile(documents, files).execute(ctx, file_id="file-1")

    assert doc is existing
    assert events == ()
    assert len(documents.docs) == 1  # no second document was minted


async def test_index_file_still_conflicts_when_the_existing_document_predates_the_pipeline() -> (
    None
):
    """The other half of the pair: an INDEXED document fingerprinted under an
    OLDER `pipeline_version` is never "unchanged", even though its
    `content_hash` is set -- decision س-14's whole point (§6 risk 4)."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    ctx = _ctx("ws1")
    stale = _document(
        doc_id="doc-1",
        workspace_id="ws1",
        space_id=_SPACE_A,
        file_id="file-1",
        status=IndexStatus.INDEXED,
        chunk_count=3,
        content_hash="hash-abc",
        pipeline_version=PIPELINE_VERSION - 1,
    )
    documents.docs[stale.id] = stale

    with pytest.raises(ConflictError):
        await IndexFile(documents, files).execute(ctx, file_id="file-1")

    assert len(documents.docs) == 1


async def test_index_file_still_conflicts_when_the_existing_document_was_never_fingerprinted() -> (
    None
):
    """A document with no recorded `content_hash` (`pending`/`indexing`, or
    minted before this plan step) never satisfies the skip -- unindexed is
    never "unchanged"."""
    documents = _FakeDocumentRepository()
    files = _FakeReadableFiles({"file-1": _SPACE_A})
    ctx = _ctx("ws1")
    never_indexed = _document(
        doc_id="doc-1", workspace_id="ws1", space_id=_SPACE_A, file_id="file-1"
    )
    documents.docs[never_indexed.id] = never_indexed

    with pytest.raises(ConflictError):
        await IndexFile(documents, files).execute(ctx, file_id="file-1")


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
        ctx,
        document_id=doc.id,
        parsed=parsed,
        model="embed-1",
        api_key="key-1",
        content_hash="hash-abc",
    )

    assert result is doc
    assert doc.status is IndexStatus.INDEXED
    assert doc.chunk_count == 2
    assert [call[1] for call in documents.status_calls] == ["indexing", "indexed"]
    assert [call[2] for call in documents.status_calls] == [None, None]
    # Plan step 15 (§3.6): the fingerprint pair is stamped on the SAME
    # `'indexed'` transition, never on `indexing`.
    assert documents.fingerprint_calls[-1] == (doc.id, "hash-abc", PIPELINE_VERSION)
    assert documents.fingerprint_calls[0] == (doc.id, None, None)
    assert doc.content_hash == "hash-abc"
    assert doc.pipeline_version == PIPELINE_VERSION
    # Plan step 16 (`P-05`): two plain-text paragraphs, no table, no image --
    # and the three sum to `chunk_count` (`IndexOutcome`'s own docstring).
    assert (doc.text_chunks, doc.table_chunks, doc.image_chunks) == (2, 0, 0)
    assert documents.stats_calls[-1] == (doc.id, 2, 0, 0)

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
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k", content_hash="hash-abc"
    )

    assert result is doc
    assert doc.status is IndexStatus.FAILED
    assert doc.error is not None
    assert "unavailable" in doc.error
    assert [call[1] for call in documents.status_calls] == ["indexing", "failed"]
    assert documents.status_calls[-1][2] == doc.error
    assert documents.chunks == []
    # A failed attempt never produces output worth fingerprinting (plan
    # step 15's own `IndexAttempt` docstring).
    assert doc.content_hash is None
    assert doc.pipeline_version is None

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
        ctx,
        document_id=doc.id,
        parsed=_parsed_document([]),
        model="m",
        api_key="k",
        content_hash="hash-abc",
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
        ctx,
        document_id=doc.id,
        parsed=_parsed_document([]),
        model="m",
        api_key="k",
        content_hash="hash-abc",
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
            _ctx(),
            document_id="missing",
            parsed=_parsed_document([]),
            model="m",
            api_key="k",
            content_hash="hash-abc",
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
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k", content_hash="hash-abc"
    )

    assert result.status is IndexStatus.INDEXED
    assert len(documents.parents) == 1
    parent = documents.parents[0]
    assert parent.document_id == doc.id
    assert parent.workspace_id == "ws1"
    assert parent.text == "Name: Ahmad; Salary: 5000\nName: Sara; Salary: 6000"
    assert parent.is_complete is True

    assert len(documents.chunks) == 2
    for chunk in documents.chunks:
        assert chunk.parent_id == parent.id
        assert chunk.text != parent.text  # the payload/row text, never the parent text
    # Plan step 16 (`P-05`): both exploded rows keep their `TABLE` kind
    # (`_table_to_segments`'s own `kind=str(chunk.kind)`), so they land in
    # `table_chunks`, not `text_chunks`.
    assert (result.text_chunks, result.table_chunks, result.image_chunks) == (0, 2, 0)


async def test_index_registered_document_marks_a_header_only_parent_incomplete() -> None:
    """The same wiring for P-13's OTHER parent shape: a table past
    ``TABLE_PARENT_MAX_ROWS`` mints a parent holding the header line alone,
    and ``finalize`` must persist that fact -- it is the only thing standing
    between P-42's summariser input and a data file summarised as its column
    names (``domain/tables.py::collapse_parent_runs``)."""
    documents = _FakeDocumentRepository()
    ctx = _ctx("ws1")
    doc = _document(doc_id="doc-1", workspace_id="ws1", file_id="file-1")
    documents.docs[doc.id] = doc
    use_case = IndexRegisteredDocument(
        documents, IndexDocument(_FakeEmbeddings(dim=6), _FakeHybridVectors())
    )
    table_text = json.dumps(
        {
            "headers": ["Name", "Dept"],
            "rows": [{"Name": f"person-{i}", "Dept": "engineering"} for i in range(21)],
        }
    )
    table_chunk = ParsedChunk(text=table_text, order=0, kind=ParsedChunkKind.TABLE, metadata={})
    parsed = _parsed_document([table_chunk])

    result, _events = await use_case.execute(
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k", content_hash="hash-abc"
    )

    assert result.status is IndexStatus.INDEXED
    assert len(documents.parents) == 1
    assert documents.parents[0].text == "Name; Dept"
    assert documents.parents[0].is_complete is False
    assert len(documents.chunks) == 21


async def test_index_outcome_counts_chunks_by_kind_across_a_mixed_document() -> None:
    """The full breakdown, all three kinds in one document -- text, a
    two-row table, and an OCR chunk -- and the three numbers summing to
    `len(chunks)` (`IndexOutcome`'s own docstring)."""
    ctx = _ctx("ws1")
    table_text = json.dumps(
        {
            "headers": ["Name", "Salary"],
            "rows": [{"Name": "Ahmad", "Salary": "5000"}, {"Name": "Sara", "Salary": "6000"}],
        }
    )
    parsed = _parsed_document(
        [
            _parsed_chunk("a plain paragraph of prose", order=0),
            ParsedChunk(text=table_text, order=1, kind=ParsedChunkKind.TABLE, metadata={}),
            ParsedChunk(
                text="OCR text lifted from an embedded figure",
                order=2,
                kind=ParsedChunkKind.OCR,
                metadata={},
            ),
        ]
    )
    pipeline = IndexDocument(_FakeEmbeddings(dim=6), _FakeHybridVectors())

    outcome = await pipeline.execute(
        ctx, document_id="doc-1", space_id=_SPACE_A, parsed=parsed, model="m", api_key="k"
    )

    assert (outcome.text_chunks, outcome.table_chunks, outcome.image_chunks) == (1, 2, 1)
    assert outcome.text_chunks + outcome.table_chunks + outcome.image_chunks == len(outcome.chunks)


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
        ctx,
        document_id=doc.id,
        parsed=parsed,
        model="embed-1",
        api_key="key-1",
        content_hash="hash-abc",
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
    assert doc.content_hash == "hash-abc"
    assert doc.pipeline_version == PIPELINE_VERSION


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

    attempt = await use_case.run(
        ctx, document_id=doc.id, parsed=parsed, model="m", api_key="k", content_hash="hash-abc"
    )

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
        ctx,
        document_id=doc.id,
        parsed=_parsed_document([]),
        model="m",
        api_key="k",
        content_hash="hash-abc",
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
        space_id=_SPACE_A,
        parsed=_parsed_document([_parsed_chunk(text, order=0)]),
        model="embed-1",
        api_key="key-1",
    )

    resolver = _FakeEmbeddingResolver(model="embed-1", api_key="key-1")
    documents = _FakeDocumentRepository()
    retrieval = RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED)
    service = KnowledgeRetrievalService(
        retrieval, resolver, documents, _FakeReadableFiles(), _FakeSummaryStarter()
    )

    # Static-typing assertion: KnowledgeRetrievalService satisfies the
    # KnowledgeRetrieval inbound port -- mypy is the real assertion here.
    svc: KnowledgeRetrieval = service

    results = await svc.retrieve(ctx, text, 1, space_id=_SPACE_A)

    assert resolver.calls == [ctx]
    assert len(results) == 1
    assert results[0].document_id == "doc-1"
    assert results[0].text == text

    # k propagated all the way through to the underlying search calls:
    # search_k = k * the widened overfetch (plan row 20's
    # `max(search_overfetch, mmr_overfetch)`, which MMR needs a surplus from)
    # == 1 * 6 == 6.
    assert vectors.search_calls[-1][1] == 6
    assert vectors.search_sparse_calls[-1][1] == 6
    # Unscoped by default: no `document_id` narrowing reaches either leg, so
    # every caller that predates BE-RAG-005 still searches the whole corpus.
    assert "document_id" not in (vectors.search_calls[-1][2] or {})


# --------------------------------------------------------------------------- #
# ListDocumentNames / KnowledgeRetrievalService.list_document_names          #
# (retrieval plan §3.6/§4 row 6 — P-36, س-23 = ج)                            #
# --------------------------------------------------------------------------- #
def _seed_three_documents(documents: _FakeDocumentRepository) -> None:
    """Three documents, newest-first by id: doc-3, doc-2, doc-1."""
    for doc_id, file_id, status in (
        ("doc-1", "file-1", IndexStatus.INDEXED),
        ("doc-2", "file-2", IndexStatus.PENDING),
        ("doc-3", "file-3", IndexStatus.FAILED),
    ):
        documents.docs[doc_id] = _document(doc_id=doc_id, file_id=file_id, status=status)


async def test_list_document_names_resolves_up_to_the_cap_newest_first() -> None:
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=2)

    # Newest id first (doc-3 -> doc-2 -> doc-1), the same order `ListDocuments`
    # returns; capped at `limit=2` even though the workspace has three.
    assert result == DocumentNames(names=("c.pdf", "b.pdf"), total=3)


async def test_list_document_names_counts_every_lifecycle_status() -> None:
    """The `ListDocuments` rule, reused: a `pending`/`failed` document still
    names a file the user genuinely uploaded, so `total` must not undercount
    the corpus by excluding them."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=50)

    assert result.total == 3
    assert set(result.names) == {"a.pdf", "b.pdf", "c.pdf"}


async def test_list_document_names_skips_a_document_whose_file_is_unreadable() -> None:
    """A file deleted/quarantined since it was indexed degrades to being
    SKIPPED from `names` (never a bare id) — `total` still counts it, so
    the header's "N more" tail never silently drops a real document."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    documents.docs["doc-1"] = _document(doc_id="doc-1", file_id="file-1")
    documents.docs["doc-2"] = _document(doc_id="doc-2", file_id="file-gone")
    # `file-gone` is not seeded at all -- `get_readable` answers `None`.
    files = _FakeReadableFiles({"file-1": None}, names={"file-1": "a.pdf"})

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=50)

    assert result == DocumentNames(names=("a.pdf",), total=2)


async def test_list_document_names_names_only_the_space_it_was_asked_about() -> None:
    """س-32 (owner decision 2026-08-26) — and this test used to assert the
    reverse.

    ``test_list_document_names_spans_every_space_regardless_of_scope`` pinned
    ``space_id=None`` on the internal ``documents.list`` call, on decision
    س-23 = ج's argument that a corpus-aware header describes the WHOLE
    workspace. The decision isolates spaces completely — files, index and rows
    — so the corpus a thread HAS is its space's: naming (ب)'s files in a thread
    that can never retrieve from them told a user about documents no question
    of theirs could be answered from.

    ``total`` narrows with the names, which is the half that matters most: it
    is what the "N more files" tail is computed from, so a header that counted
    three and could reach one would have been a lie with a number on it.
    """
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    documents.docs["doc-a"] = _document(doc_id="doc-a", file_id="file-a", space_id=_SPACE_A)
    documents.docs["doc-b"] = _document(doc_id="doc-b", file_id="file-b", space_id=_SPACE_B)
    documents.docs["doc-c"] = _document(doc_id="doc-c", file_id="file-c", space_id=None)
    files = _FakeReadableFiles(
        {"file-a": _SPACE_A, "file-b": _SPACE_B, "file-c": None},
        names={"file-a": "a.pdf", "file-b": "b.pdf", "file-c": "c.pdf"},
    )

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=50)

    assert result == DocumentNames(names=("a.pdf",), total=1)
    # And the spaceless row is not "everyone's" either: a document no space
    # owns belongs to no space's header, exactly as it matches no space's
    # search (§5-أ).
    assert "c.pdf" not in result.names


async def test_list_document_names_on_an_empty_workspace() -> None:
    ctx = _ctx("ws1")
    result = await ListDocumentNames(_FakeDocumentRepository(), _FakeReadableFiles()).execute(
        ctx, space_id=_SPACE_A, limit=50
    )

    assert result == DocumentNames(names=(), total=0)


async def test_knowledge_retrieval_service_delegates_list_document_names() -> None:
    """`KnowledgeRetrievalService.list_document_names` -- the port's second
    face, over the SAME `documents`/`files` seams `retrieve` already holds."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        files,
        _FakeSummaryStarter(),
    )

    svc: KnowledgeRetrieval = service
    result = await svc.list_document_names(ctx, space_id=_SPACE_A, limit=2)

    assert result == DocumentNames(names=("c.pdf", "b.pdf"), total=3)


async def test_list_document_names_without_a_limit_uses_the_deployment_cap() -> None:
    """Review §8, in `RetrieveContext`'s `k = None` shape (plan row 18,
    `P-40`): a caller that names no `limit` gets the DEPLOYMENT's display cap
    — `Settings.retrieval.max_corpus_names`, injected here as
    `max_corpus_names` — which is what let the RAG agent drop its own
    `_MAX_CORPUS_NAMES = 50`.

    Proven by MOVING it, exactly as `default_k` is: an assertion that the
    shipped value is 50 would pass just as well against a hard-coded literal.
    `total` is the FULL corpus either way — the cap bounds the names shown,
    never the count."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )

    one = await ListDocumentNames(documents, files, max_corpus_names=1).execute(
        ctx, space_id=_SPACE_A
    )
    two = await ListDocumentNames(documents, files, max_corpus_names=2).execute(
        ctx, space_id=_SPACE_A
    )

    assert one == DocumentNames(names=("c.pdf",), total=3)
    assert two == DocumentNames(names=("c.pdf", "b.pdf"), total=3)


async def test_an_explicit_limit_still_overrides_the_deployment_cap() -> None:
    """Naming a `limit` stays allowed and still means what it did — a caller
    asking for a result-set SIZE, not overriding a deployment knob (the
    `POST /knowledge/search` `k` rule, س-24). So an explicit `limit` wins over
    the configured cap in BOTH directions, narrower and wider."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )
    names = ListDocumentNames(documents, files, max_corpus_names=2)

    assert (await names.execute(ctx, space_id=_SPACE_A, limit=1)).names == ("c.pdf",)
    assert (await names.execute(ctx, space_id=_SPACE_A, limit=50)).names == (
        "c.pdf",
        "b.pdf",
        "a.pdf",
    )


def test_the_corpus_name_cap_default_mirrors_its_settings_home() -> None:
    """`_DEFAULT_MAX_CORPUS_NAMES` is declared to mirror
    `RetrievalSettings.max_corpus_names` byte for byte — the `RetrievalTuning`
    rule, so a direct construction (a test, a script) gets the SHIPPED number
    rather than an accidental second configuration. A mirror nobody checks is
    just a copy, so this is the check; 50 is §3.6's own "سقف عرض 50 اسمًا".

    The optional `limit` is asserted here too, because the mirror is only
    reachable through it: a `limit` that went back to being required would
    strand the setting with no caller."""
    assert _DEFAULT_MAX_CORPUS_NAMES == RetrievalSettings().max_corpus_names == 50
    assert inspect.signature(ListDocumentNames.execute).parameters["limit"].default is None
    assert (
        inspect.signature(KnowledgeRetrievalService.list_document_names).parameters["limit"].default
        is None
    )


async def test_the_service_hands_the_configured_cap_down_to_the_use_case() -> None:
    """The Composition Root maps `Settings.retrieval.max_corpus_names` onto
    this constructor argument (the `tuning` precedent), and the service passes
    it to the `ListDocumentNames` it composes. Without that hop the setting
    would be wired to nothing and the port's default would silently be the
    module constant instead of the deployment's number."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    svc: KnowledgeRetrieval = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        files,
        _FakeSummaryStarter(),
        max_corpus_names=1,
    )

    assert await svc.list_document_names(ctx, space_id=_SPACE_A) == DocumentNames(
        names=("c.pdf",), total=3
    )


# --------------------------------------------------------------------------- #
# ListFileCandidates — the corpus `resolve_file` matches against              #
# (retrieval plan §3.5/§4 rows ١٣-١٤ — P-04)                                  #
# --------------------------------------------------------------------------- #
async def test_file_candidates_pair_every_document_with_its_file_name() -> None:
    """Newest-first, the `ListDocuments` order, and `document_id` is the
    DOCUMENT's — the id a summary is keyed on, not the file's."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    _seed_three_documents(documents)
    files = _FakeReadableFiles(
        {"file-1": None, "file-2": None, "file-3": None},
        names={"file-1": "a.pdf", "file-2": "b.pdf", "file-3": "c.pdf"},
    )

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert candidates == (
        FileCandidate(document_id="doc-3", file_name="c.pdf"),
        FileCandidate(document_id="doc-2", file_name="b.pdf"),
        FileCandidate(document_id="doc-1", file_name="a.pdf"),
    )


async def test_file_candidates_are_never_capped_at_a_display_limit() -> None:
    """`ListDocumentNames` takes a `limit` because a header shows at most
    that many; this takes none, and the difference is the whole point. A cap
    would let a question resolve CONFIDENTLY against the newest N files while
    the file the user meant sat at N+1, unseen — a guess wearing the costume
    of a performance guard (§3.5)."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    names = {f"file-{n}": f"{n}.pdf" for n in range(60)}
    for n in range(60):
        documents.docs[f"doc-{n:02d}"] = _document(doc_id=f"doc-{n:02d}", file_id=f"file-{n}")
    files = _FakeReadableFiles(dict.fromkeys(names), names=names)

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert len(candidates) == 60


async def test_file_candidates_include_a_document_that_is_not_indexed_yet() -> None:
    """Every lifecycle status, the `ListDocumentNames` rule. A `pending`
    document still NAMES a real file: hiding it would let a question about
    that file resolve to a DIFFERENT one, where offering it means the caller
    gets `RequestSummary`'s honest "not indexed yet" refusal instead."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    documents.docs["doc-1"] = _document(
        doc_id="doc-1", file_id="file-1", status=IndexStatus.PENDING
    )
    files = _FakeReadableFiles({"file-1": None}, names={"file-1": "fresh.pdf"})

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert candidates == (FileCandidate(document_id="doc-1", file_name="fresh.pdf"),)


async def test_file_candidates_drop_documents_with_no_readable_name() -> None:
    """A file deleted since it was indexed (`get_readable` -> `None`) and one
    whose name came back empty are both dropped: neither can be matched
    against, and an empty name would reach a user as a blank line in the
    clarification question."""
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    documents.docs["doc-1"] = _document(doc_id="doc-1", file_id="file-1")
    documents.docs["doc-2"] = _document(doc_id="doc-2", file_id="file-gone")
    documents.docs["doc-3"] = _document(doc_id="doc-3", file_id="file-nameless")
    files = _FakeReadableFiles({"file-1": None, "file-nameless": None}, names={"file-1": "a.pdf"})

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert candidates == (FileCandidate(document_id="doc-1", file_name="a.pdf"),)


def _corpus_across_two_spaces() -> tuple[_FakeDocumentRepository, _FakeReadableFiles]:
    """Three documents — one in (أ), one in (ب), one in no space at all — and
    the names they would be resolved by. The smallest corpus in which "every
    space" and "the space being searched" are different answers.
    """
    documents = _FakeDocumentRepository()
    documents.docs["doc-a"] = _document(doc_id="doc-a", file_id="file-a", space_id=_SPACE_A)
    documents.docs["doc-b"] = _document(doc_id="doc-b", file_id="file-b", space_id=_SPACE_B)
    documents.docs["doc-c"] = _document(doc_id="doc-c", file_id="file-c", space_id=None)
    files = _FakeReadableFiles(
        {"file-a": _SPACE_A, "file-b": _SPACE_B, "file-c": None},
        names={"file-a": "a.pdf", "file-b": "b.pdf", "file-c": "c.pdf"},
    )
    return documents, files


async def test_file_candidates_are_narrowed_to_the_space_being_searched() -> None:
    """Branch review §7. Unlike the corpus header, this walk DOES carry a
    space — because what it produces is matched against a question whose
    ANSWER will be retrieved under a `space` filter. A name resolved outside
    that space becomes `document_ids` from one space ANDed with `space` from
    another: zero chunks, about a file the workspace really does hold.
    """
    ctx = _ctx("ws1")
    documents, files = _corpus_across_two_spaces()

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    # Only (أ)'s file can be named here. `doc-c` is spaceless, which is not
    # "in every space" -- it is in none, exactly as the search's own `space`
    # condition reads it (see the payload tests below).
    assert candidates == (FileCandidate(document_id="doc-a", file_name="a.pdf"),)


async def test_file_candidates_never_span_a_second_space() -> None:
    """س-32 — and this test used to assert the reverse too.

    ``test_file_candidates_span_every_space_when_the_search_does`` pinned
    ``None`` as "the whole workspace on this axis, exactly as on the search's,
    so the two agree by being absent together". They agree on a REAL space now,
    which is the same agreement with the absence removed: a question may only
    name files that live where it was asked.
    """
    ctx = _ctx("ws1")
    documents, files = _corpus_across_two_spaces()

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert {candidate.document_id for candidate in candidates} == {"doc-a"}


async def test_the_two_walks_now_describe_the_same_corpus() -> None:
    """The one place the two walks used to part (review §7's table), over ONE
    corpus — and they part no longer.

    ``test_the_corpus_header_spans_every_space_even_where_candidates_do_not``
    asserted the split: the header took no ``space_id`` at all and named every
    file in the workspace, while the candidate list took one and honoured it.
    س-32 gives the header the same space, so what a user is TOLD they have and
    what a question may be answered FROM are the same set. A header that named
    more than the search could reach was the leak in its politest form.

    The two use-cases still differ — the display cap is the header's, the
    empty-name drop is the candidates' — which is why they are still two.
    """
    ctx = _ctx("ws1")
    documents, files = _corpus_across_two_spaces()

    header = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=50)
    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert header.total == 1
    assert set(header.names) == {"a.pdf"}
    assert [candidate.file_name for candidate in candidates] == ["a.pdf"]


# --------------------------------------------------------------------------- #
# The two corpus walks and what they cost (branch review §2)                  #
# --------------------------------------------------------------------------- #
def _paged_doc_id(n: int) -> str:
    """A REAL UUID for the nth document, ordered by `n`.

    The one-page tests above name documents `doc-1`; a walk that crosses a
    page boundary cannot, because the keyset cursor between pages is a
    base64-wrapped UUID and `decode_id_cursor` rejects anything else. Fixed
    width, so lexical order (what the fake and the SQL adapter both sort on)
    is numeric order.
    """
    return f"018f0000-0000-7000-8000-{n:012d}"


def _big_corpus(pages: int) -> tuple[_FakeDocumentRepository, _FakeReadableFiles, int]:
    """A corpus of `pages` FULL pages of `documents.list` plus one document
    over, so every test below crosses the page boundary the walk pages on
    rather than one a fake happens to choose."""
    size = pages * _LIST_PAGE_SIZE + 1
    documents = _FakeDocumentRepository()
    names: dict[str, str] = {}
    for n in range(size):
        doc_id = _paged_doc_id(n)
        documents.docs[doc_id] = _document(doc_id=doc_id, file_id=f"file-{n:05d}")
        names[f"file-{n:05d}"] = f"{n}.pdf"
    return documents, _FakeReadableFiles(dict.fromkeys(names), names=names), size


async def test_file_candidates_read_names_in_bulk_not_once_per_document() -> None:
    """Branch review §2 — the N+1 the plan's §7 recorded, pinned by counting.

    `ListFileCandidates` used to spend one `get_readable` per document, so a
    `D`-document repository paid `D` sequential round trips on every content
    question that was not already pinned to one file. Names now arrive one
    bulk read per PAGE: the walk costs two round trips per page whatever `D`
    is, and every id is still asked about exactly once.
    """
    ctx = _ctx("ws1")
    documents, files, size = _big_corpus(pages=2)

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert len(candidates) == size
    # THE assertion of this whole change: round trips, not ids. Three pages
    # (two full + the one document over), one name read each.
    assert len(files.reads) == 3
    # Every document still got its name, asked about exactly once -- fewer
    # reads, not fewer answers.
    assert len(files.calls) == size
    assert sorted(files.calls) == sorted(files.names)


async def test_the_corpus_header_reads_names_once_and_stops_at_its_cap() -> None:
    """The header's walk pays for the names it SHOWS and nothing more.

    Its cap fills inside the FIRST page, so the walk stops there: one page,
    one name read, and one `count` for `total` — for a corpus of any size,
    against `D`-capped-at-50 name reads before the bulk read and three full
    pages before ب-2.
    """
    ctx = _ctx("ws1")
    documents, files, size = _big_corpus(pages=2)

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=5)

    assert len(result.names) == 5
    # `total` is the FULL corpus, not the capped list -- the header's "N more"
    # tail is computed from it.
    assert result.total == size
    assert len(files.reads) == 1
    # THE ب-2 assertion: the two pages after the cap filled were never asked
    # for. `total` came from one `count`, not from hydrating them.
    assert documents.list_calls == 1
    assert documents.count_calls == 1


async def test_a_cap_that_fills_on_the_last_page_costs_no_count_at_all() -> None:
    """ب-2's short-circuit runs only when pages REMAIN.

    The corpus is one page, and the cap fills inside it. There is nothing left
    to page, so `walked` is already the exact total and asking the database
    for it again would be a round trip that buys a number we hold.
    """
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    names: dict[str, str] = {}
    for n in range(_LIST_PAGE_SIZE):  # EXACTLY one page: `next_cursor` is None
        doc_id = _paged_doc_id(n)
        documents.docs[doc_id] = _document(doc_id=doc_id, file_id=f"file-{n:05d}")
        names[f"file-{n:05d}"] = f"{n}.pdf"
    files = _FakeReadableFiles(dict.fromkeys(names), names=names)

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=5)

    assert len(result.names) == 5
    assert result.total == _LIST_PAGE_SIZE
    assert documents.list_calls == 1
    assert documents.count_calls == 0


async def test_an_uncapped_walk_never_short_circuits_to_count() -> None:
    """`cap=None` is the resolver's walk: it needs EVERY name, so there is no
    point at which paging could stop early. `total` stays the number of rows
    the walk itself saw, and `count` is never called — the short-circuit is
    guarded on the cap and not merely on "pages remain".
    """
    ctx = _ctx("ws1")
    documents, files, size = _big_corpus(pages=2)

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)

    assert len(candidates) == size
    assert documents.list_calls == 3
    assert documents.count_calls == 0


async def test_the_corpus_header_keeps_reading_until_its_cap_is_actually_full() -> None:
    """Skipping is not filling. A page whose files have all been deleted
    since they were indexed yields no names, so the walk must go on asking on
    the NEXT page — the `get_readable` loop's "keep going while `len(names) <
    cap`" rule, preserved through the bulk read.
    """
    ctx = _ctx("ws1")
    documents = _FakeDocumentRepository()
    for n in range(_LIST_PAGE_SIZE + 2):
        documents.docs[_paged_doc_id(n)] = _document(
            doc_id=_paged_doc_id(n), file_id=f"file-{n:05d}"
        )
    # Only the OLDEST two files survive, and newest-first paging puts them on
    # the second page: the first page resolves nothing at all.
    survivors = {"file-00000": "a.pdf", "file-00001": "b.pdf"}
    files = _FakeReadableFiles(dict.fromkeys(survivors), names=survivors)

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=5)

    assert set(result.names) == {"a.pdf", "b.pdf"}
    assert result.total == _LIST_PAGE_SIZE + 2
    # Two pages walked, two name reads: the second one was NOT skipped as
    # "cap already full", because it was not.
    assert len(files.reads) == 2
    # And for the same reason ب-2's short-circuit never fired: a cap that
    # never fills is a walk that always runs to the end, where `walked` is
    # already exact.
    assert documents.count_calls == 0


async def _names_the_old_way(
    ctx: ExecutionContext,
    documents: _FakeDocumentRepository,
    files: _FakeReadableFiles,
    *,
    space_id: str | None,
    cap: int | None,
) -> tuple[list[tuple[str, str]], int]:
    """The walk as it was written before the bulk read: one `get_readable`
    per document, newest first, cap counted on NAMES.

    Kept here as the reference the two use-cases are compared against —
    "fewer round trips, identical answers" is only a claim until something
    computes the old answer and asserts it out loud.
    """
    named: list[tuple[str, str]] = []
    total = 0
    cursor: str | None = None
    while True:
        page = await documents.list(ctx, space_id=space_id, limit=_LIST_PAGE_SIZE, cursor=cursor)
        for document in page.data:
            total += 1
            if cap is None or len(named) < cap:
                file = await files.get_readable(ctx, document.file_id)
                if file is not None:
                    named.append((document.id, file.name))
        cursor = page.next_cursor
        if cursor is None:
            break
    return named, total


def _mixed_corpus() -> tuple[_FakeDocumentRepository, _FakeReadableFiles]:
    """One corpus holding every case the two walks treat differently: two
    spaces and a spaceless document, a file deleted since it was indexed, a
    readable file whose name is empty, and two documents built from ONE file.
    """
    documents = _FakeDocumentRepository()
    documents.docs["doc-a"] = _document(doc_id="doc-a", file_id="file-a", space_id=_SPACE_A)
    documents.docs["doc-b"] = _document(doc_id="doc-b", file_id="file-b", space_id=_SPACE_B)
    documents.docs["doc-c"] = _document(doc_id="doc-c", file_id="file-c", space_id=None)
    documents.docs["doc-d"] = _document(doc_id="doc-d", file_id="file-gone", space_id=_SPACE_A)
    documents.docs["doc-e"] = _document(doc_id="doc-e", file_id="file-blank", space_id=_SPACE_A)
    documents.docs["doc-f"] = _document(doc_id="doc-f", file_id="file-a", space_id=_SPACE_A)
    files = _FakeReadableFiles(
        {"file-a": _SPACE_A, "file-b": _SPACE_B, "file-c": None, "file-blank": _SPACE_A},
        names={"file-a": "a.pdf", "file-b": "b.pdf", "file-c": "c.pdf"},
    )
    return documents, files


@pytest.mark.parametrize("space_id", [None, _SPACE_A, _SPACE_B])
async def test_file_candidates_answer_exactly_what_the_per_file_walk_answered(
    space_id: str | None,
) -> None:
    """No behavioural change, only fewer round trips — proven against the old
    algorithm rather than against a hand-copied expectation. Every difference
    the two walks care about is in this corpus: a dangling file, an empty
    name, a file two documents share, and three spaces' worth of narrowing.
    """
    ctx = _ctx("ws1")
    documents, files = _mixed_corpus()
    expected, _total = await _names_the_old_way(ctx, documents, files, space_id=space_id, cap=None)

    candidates = await ListFileCandidates(documents, files).execute(ctx, space_id=space_id)

    # The old walk dropped empty names at the same point this assertion does;
    # everything else -- order, ids, which documents survive -- is compared
    # exactly as it was produced.
    assert candidates == tuple(
        FileCandidate(document_id=doc_id, file_name=name) for doc_id, name in expected if name
    )


@pytest.mark.parametrize("limit", [1, 2, 50])
async def test_the_corpus_header_answers_exactly_what_the_per_file_walk_answered(
    limit: int,
) -> None:
    """The header's half of the same proof, across a cap that bites, a cap
    that bites later, and no cap in practice -- `total` included, which is
    the number the bulk read could most easily have broken."""
    ctx = _ctx("ws1")
    documents, files = _mixed_corpus()
    expected, expected_total = await _names_the_old_way(
        ctx, documents, files, space_id=_SPACE_A, cap=limit
    )

    result = await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A, limit=limit)

    assert result == DocumentNames(names=tuple(name for _, name in expected), total=expected_total)


async def test_both_walks_stay_bounded_as_the_corpus_grows() -> None:
    """The review's own arithmetic, on one turn's worth of walking: candidates
    plus header used to cost about `D + 50` sequential name reads before
    retrieval began. Both now cost a number that grows with PAGES, and the
    header's does not grow at all.
    """
    ctx = _ctx("ws1")
    documents, files, size = _big_corpus(pages=3)

    await ListFileCandidates(documents, files).execute(ctx, space_id=_SPACE_A)
    await ListDocumentNames(documents, files).execute(ctx, space_id=_SPACE_A)

    # The SAME two walks written the old way, on the same corpus, counted by
    # the same double -- so the number below is measured against the shipped
    # alternative rather than against a comment.
    old_documents, per_file, _size = _big_corpus(pages=3)
    await _names_the_old_way(ctx, old_documents, per_file, space_id=_SPACE_A, cap=None)
    await _names_the_old_way(
        ctx, old_documents, per_file, space_id=_SPACE_A, cap=_DEFAULT_MAX_CORPUS_NAMES
    )

    # 4 pages of candidates + 1 capped header read, against `D + 50`.
    assert len(files.reads) == 5
    assert len(per_file.reads) == size + _DEFAULT_MAX_CORPUS_NAMES


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
            space_id=_SPACE_A,
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
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )

    results = await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-north"], space_id=_SPACE_A
    )

    assert [chunk.document_id for chunk in results] == ["doc-north"]
    # FILE ids in, DOCUMENT ids out: the caller never has to know documents
    # exist, and the payload the filter runs against only knows document ids.
    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-north"],
        "space": _SPACE_A,
    }
    # The tenant filter is never REPLACED by the scope (DD-04).
    assert vectors.search_sparse_calls[-1][2]["workspace_id"] == "ws1"


async def test_an_unscoped_retrieval_still_sees_the_whole_corpus() -> None:
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _indexed_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )

    results = await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A)

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
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )
    searches_before = len(vectors.search_calls)

    results = await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-never-indexed"], space_id=_SPACE_A
    )

    assert results == []
    # And it short-circuits: no vector round trip is made for a scope that
    # cannot match anything.
    assert len(vectors.search_calls) == searches_before


# --------------------------------------------------------------------------- #
# RouteQuestion / KnowledgeRetrievalService.answer                            #
# (retrieval plan §3.4/§4 row 11 -- P-21, س-16 = أ)                           #
# --------------------------------------------------------------------------- #
async def _routing_service(
    ctx: ExecutionContext,
    embeddings: _FakeEmbeddings,
    vectors: _FakeHybridVectors,
    names: dict[str, str] | None = None,
    *,
    documents: _FakeDocumentRepository | None = None,
    files: _FakeReadableFiles | None = None,
) -> tuple[KnowledgeRetrievalService, _FakeSummaryStarter]:
    """The service over the REAL `RouteQuestion`, the REAL `RetrieveContext`
    and the REAL `ListFileCandidates`/`resolve_file`, on the two-file corpus
    every scope test above uses.

    `names` is what those two files are CALLED (retrieval plan §4 row 14):
    without it no file in the corpus has a readable name, so the resolver has
    nothing to match and every summarisation question that is not pinned to
    one document falls through to CONTENT — which is exactly the pre-row-14
    behaviour the tests written before it assert.

    `documents` swaps in a different corpus (row 15 needs a third file that
    is named but holds nothing), and `files` lets a test keep its OWN
    reference to the seam so it can count the `get_readable` walk — both
    keyword-only, both defaulting to what every earlier test already gets.
    """
    if documents is None:
        documents = await _indexed_corpus(ctx, embeddings, vectors)
    summaries = _FakeSummaryStarter()
    if files is None:
        files = (
            _FakeReadableFiles(dict.fromkeys(names), names=names) if names else _FakeReadableFiles()
        )
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        files,
        summaries,
    )
    return service, summaries


# The two files of `_indexed_corpus`, named — distinctly enough for one
# question to name exactly one of them (`_NAMED_CORPUS`), and identically
# enough for another to name both (`_TIED_CORPUS`).
_NAMED_CORPUS = {"file-north": "التقرير الشمالي.pdf", "file-south": "التقرير الجنوبي.pdf"}
_TIED_CORPUS = {"file-north": "الميزانية 2024.pdf", "file-south": "الميزانية 2025.pdf"}
# `_NAMED_CORPUS` plus a third file that is named and registered but has NO
# points in the index (retrieval plan §4 row 15) — the corpus in which "the
# file you named holds nothing" and "another file holds the answer" are both
# true at once.
_HANDBOOK_CORPUS = {**_NAMED_CORPUS, "file-handbook": "دليل الموظفين.pdf"}


async def _corpus_with_an_empty_named_file(
    ctx: ExecutionContext, embeddings: _FakeEmbeddings, vectors: _FakeHybridVectors
) -> _FakeDocumentRepository:
    """`_indexed_corpus`'s two indexed documents plus `doc-handbook`, which
    is a real, named, INDEXED document with nothing retrievable under it.

    Staged as "no points" rather than "points that do not match" because
    `_FakeHybridVectors.search` is a brute-force cosine sort with no floor:
    any point inside the filter comes back, whatever it says. Both stagings
    reach `RetrieveContext` as the same thing — a filtered search that
    returned no hit — which is the state row 15 is about.
    """
    documents = await _indexed_corpus(ctx, embeddings, vectors)
    documents.docs["doc-handbook"] = _document(
        doc_id="doc-handbook",
        file_id="file-handbook",
        status=IndexStatus.INDEXED,
        chunk_count=0,
    )
    return documents


async def test_answer_routes_a_content_question_to_retrieval() -> None:
    """`classify_intent` finally has a live caller (plan fact ح-18): an
    ordinary question classifies CONTENT and comes back with chunks, and no
    summary is queued behind the caller's back."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors)

    svc: KnowledgeRetrieval = service
    routed = await svc.answer(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A)

    assert routed.intent is Intent.CONTENT
    assert {chunk.document_id for chunk in routed.chunks} == {"doc-north", "doc-south"}
    assert routed.summary_job_id is None
    assert summaries.calls == []


async def test_answer_routes_a_summarisation_question_to_request_summary() -> None:
    """The other route, end to end: a pin naming exactly one FILE is
    translated to the DOCUMENT the summary is keyed on, and the build is
    queued instead of a similarity search being run."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors)
    searches_before = len(vectors.search_calls)

    routed = await service.answer(ctx, "لخص لي هذا الملف", 5, ["file-north"], space_id=_SPACE_A)

    assert routed.intent is Intent.SUMMARIZE_DOC
    # FILE in, DOCUMENT out -- the same translation `retrieve` does, and the
    # reason the summarisation route is reachable from a caller that only
    # ever speaks about files.
    assert summaries.calls == [("doc-north", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
    assert routed.summary_job_id == "job-1"
    assert routed.chunks == ()
    # Nothing was retrieved: the two routes are alternatives, not a pipeline.
    assert len(vectors.search_calls) == searches_before


async def test_a_routed_summary_asks_for_a_bounded_overview_in_the_documents_language() -> None:
    """`OVERVIEW`/`AUTO` are the routed defaults, and the choice is a cost
    guard: this path can be entered by a regex false positive (§6 risk 4), and
    `FULL` would answer one of those with a map-reduce over every chunk of the
    document."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors)

    await service.answer(ctx, "summarize it", 5, ["file-south"], space_id=_SPACE_A)

    (_document_id, kind, lang) = summaries.calls[0]
    assert kind is SummaryKind.OVERVIEW
    assert lang is SummaryLanguage.AUTO


@pytest.mark.parametrize(
    "file_ids",
    [
        # Unscoped: nothing in the question has been resolved to a document
        # yet (plan step 13's `P-04` is what will).
        None,
        # A scope that resolves to nothing -- it names no document either.
        ["file-never-indexed"],
        # Two documents: a router that picked one would be guessing.
        ["file-north", "file-south"],
    ],
)
async def test_a_summarisation_question_without_one_named_document_never_guesses_one(
    file_ids: list[str] | None,
) -> None:
    """Plan §3.5/س-18: alpha does not guess when the target is ambiguous, and
    neither does this. The pin names no single document, and this corpus has
    no readable file names for row 14's resolver to match the question
    against either, so nothing identifies a target and the question falls
    through to CONTENT retrieval -- with its `intent` still reported HONESTLY
    as SUMMARIZE_DOC. What row 14 changed is the case where the resolver DOES
    have names to work with (see below); what it did not change is this: an
    unidentifiable target is never guessed at."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors)

    routed = await service.answer(
        ctx, "لخص لي أرقام الإيرادات الفصلية", 5, file_ids, space_id=_SPACE_A
    )

    assert summaries.calls == []
    assert routed.summary_job_id is None
    assert routed.intent is Intent.SUMMARIZE_DOC


# --------------------------------------------------------------------------- #
# The clarification question (retrieval plan §3.5/§4 row ١٤ — P-04, س-18 = أ) #
# --------------------------------------------------------------------------- #
async def test_a_summarisation_question_that_names_one_file_resolves_and_queues_it() -> None:
    """Row 13's resolver, on the live path at last (its §7 entry: "وصل
    المُحلِّل بمسار حيّ"). The question names «التقرير الشمالي» and no pin says
    anything, so the EXACT layer identifies one document and the build is
    queued against THAT document's id -- not the file's, and not the other
    file that shares the word «التقرير».
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _NAMED_CORPUS)

    routed = await service.answer(ctx, "لخّص لي التقرير الشمالي", 5, space_id=_SPACE_A)

    assert routed.intent is Intent.SUMMARIZE_DOC
    assert summaries.calls == [("doc-north", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
    assert routed.summary_job_id == "job-1"
    assert routed.clarification_options == ()


async def test_a_tie_comes_back_as_names_to_ask_the_user_about_and_queues_nothing() -> None:
    """THE step (س-18 = أ). Two files match «الميزانية» equally well, so the
    resolver refuses to choose and the router hands its caller the NAMES to
    ask about instead of an answer. Nothing is queued, nothing is retrieved,
    and the `intent` stays honest.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _TIED_CORPUS)
    searches_before = len(vectors.search_calls)

    routed = await service.answer(ctx, "لخص لي ملف الميزانية", 5, space_id=_SPACE_A)

    assert set(routed.clarification_options) == {"الميزانية 2024.pdf", "الميزانية 2025.pdf"}
    assert routed.intent is Intent.SUMMARIZE_DOC
    assert routed.summary_job_id is None
    assert summaries.calls == []
    # A question is not an answer: there is nothing to synthesise from, so no
    # similarity search is paid for either.
    assert routed.chunks == ()
    assert len(vectors.search_calls) == searches_before


async def test_a_tie_never_collapses_into_the_top_candidate() -> None:
    """The one thing this path must never do (plan §3.5: «أعلى مرشّح دائمًا»
    أسوأ فشل ممكن هنا). There is no fallback that picks the best candidate
    when the user says nothing -- asking the same question twice asks TWICE,
    and never quietly summarises whichever file happened to sort first.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _TIED_CORPUS)

    first = await service.answer(ctx, "لخص لي ملف الميزانية", 5, space_id=_SPACE_A)
    second = await service.answer(ctx, "لخص لي ملف الميزانية", 5, space_id=_SPACE_A)

    assert summaries.calls == []
    assert first.summary_job_id is None and second.summary_job_id is None
    assert set(first.clarification_options) == set(second.clarification_options)


async def test_answering_the_clarification_question_is_what_finally_resolves_it() -> None:
    """Why acting on `ResolvedFile` belongs to THIS row: the clarification is
    only worth asking if answering it works. The user is shown two names,
    replies with one of them, and that reply resolves EXACT and queues the
    build -- the second half of one behaviour, not a later step.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _TIED_CORPUS)

    asked = await service.answer(ctx, "لخص لي ملف الميزانية", 5, space_id=_SPACE_A)
    reply = await service.answer(ctx, f"لخص {asked.clarification_options[0]}", 5, space_id=_SPACE_A)

    assert reply.summary_job_id is not None
    assert reply.clarification_options == ()
    assert len(summaries.calls) == 1


async def test_a_pin_naming_one_document_beats_the_question_s_own_words() -> None:
    """Two sources of a target, tried in order and never blended: a caller
    who pinned exactly one document has already made the identification, so
    the resolver is not even consulted -- the question names the NORTHERN
    report and the pinned SOUTHERN document is what gets summarised.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _NAMED_CORPUS)

    await service.answer(ctx, "لخّص لي التقرير الشمالي", 5, ["file-south"], space_id=_SPACE_A)

    assert summaries.calls == [("doc-south", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]


async def test_resolution_cannot_reach_outside_the_callers_pin() -> None:
    """A pin is a statement about which documents this conversation works
    with, so the resolver matches INSIDE it. Here it resolves to no document
    at all, and a question that names a real file by name still resolves to
    nothing rather than reaching past the pin to summarise it.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _NAMED_CORPUS)

    routed = await service.answer(
        ctx, "لخّص لي التقرير الشمالي", 5, ["file-never-indexed"], space_id=_SPACE_A
    )

    assert summaries.calls == []
    assert routed.summary_job_id is None
    assert routed.clarification_options == ()
    assert routed.intent is Intent.SUMMARIZE_DOC


# --------------------------------------------------------------------------- #
# Strict file scoping on the CONTENT route -- صارم                            #
# (retrieval plan §3.3/§4 row 15 — P-25)                                      #
# --------------------------------------------------------------------------- #
async def test_a_content_question_that_names_a_file_is_searched_inside_that_file() -> None:
    """Row 15's narrowing, and WHERE it happens: the question names the
    northern report, so the document it resolved to reaches the vector store
    as a `document_id` condition beside `workspace_id` (plan fact ح-13 —
    `_build_filter` builds `must` + `MatchValue`/`MatchAny`), on BOTH legs.

    A scope applied to the search is not the same thing as a scope applied to
    its results: filtering afterwards would spend the whole `k` budget on
    chunks it then throws away, and the southern report would be competing
    for slots the northern one needed.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _NAMED_CORPUS)

    routed = await service.answer(
        ctx, "ما هي أرقام الإيرادات في التقرير الشمالي؟", 5, space_id=_SPACE_A
    )

    assert routed.intent is Intent.CONTENT
    assert [chunk.document_id for chunk in routed.chunks] == ["doc-north"]
    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-north"],
        "space": _SPACE_A,
    }
    assert vectors.search_sparse_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-north"],
        "space": _SPACE_A,
    }
    # The CONTENT route stays the CONTENT route: naming a file narrows the
    # search, it does not queue a summary.
    assert summaries.calls == []
    assert routed.summary_job_id is None


async def test_a_named_file_with_nothing_in_it_never_answers_from_another_file() -> None:
    """**THE step** (§4 row 15: «ملفّ مسمّى بلا نتائج يُجيب بأمانة بدل السحب
    من ملفّ آخر»). The same question is asked twice over the same corpus, and
    the only difference is that the second one names a file:

    * unnamed — the two indexed reports answer it;
    * naming «دليل الموظفين», which holds nothing — NOTHING comes back.

    Not the reports. A confident answer built out of a document the user did
    not ask about is undetectable downstream (its citations look exactly like
    a right answer's), so the empty result stands and the honest-fallback
    gate of plan step 5 (`P-33`) is what the caller renders — the fallback
    that already exists, with §3.6's corpus header telling the user which
    files DO exist.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _corpus_with_an_empty_named_file(ctx, embeddings, vectors)
    service, _summaries = await _routing_service(
        ctx, embeddings, vectors, _HANDBOOK_CORPUS, documents=documents
    )

    wide = await service.answer(ctx, "ما هي أرقام الإيرادات", 5, space_id=_SPACE_A)
    dense_before, sparse_before = len(vectors.search_calls), len(vectors.search_sparse_calls)
    strict = await service.answer(
        ctx, "ما هي أرقام الإيرادات في دليل الموظفين", 5, space_id=_SPACE_A
    )

    # The corpus DOES answer this question -- when nobody named a file.
    assert {chunk.document_id for chunk in wide.chunks} == {"doc-north", "doc-south"}
    # Naming the file that holds nothing answers with nothing.
    assert strict.chunks == ()
    # And STRICT means there was no second, wider attempt: every search made
    # for that question carried the named document's filter. A retry without
    # it is the one thing this row exists to forbid.
    searched = [
        *vectors.search_calls[dense_before:],
        *vectors.search_sparse_calls[sparse_before:],
    ]
    assert searched
    scoped = {"workspace_id": "ws1", "document_id": ["doc-handbook"], "space": _SPACE_A}
    assert all(call[2] == scoped for call in searched)


async def test_an_ambiguous_file_reference_leaves_a_content_search_unscoped() -> None:
    """The decision row 15 had to make and the plan did not spell out: on the
    CONTENT route an ambiguous reference narrows NOTHING (see
    `RouteQuestion`'s docstring for why it is not row 14's question).

    Two files match «الميزانية» equally, so the resolver refuses to choose --
    and refusing to choose means no file is chosen, not that some file is.
    The question is answered from the whole corpus, with each chunk labelled
    by the file it came from, and no clarification is attached: a cited
    answer beats a round trip when there IS an answer to give.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors, _TIED_CORPUS)

    routed = await service.answer(ctx, "ما هي أرقام الميزانية", 5, space_id=_SPACE_A)

    assert routed.intent is Intent.CONTENT
    assert {chunk.document_id for chunk in routed.chunks} == {"doc-north", "doc-south"}
    # Neither guessed at (a scope of one) nor narrowed to the tied pair.
    assert "document_id" not in vectors.search_calls[-1][2]
    assert routed.clarification_options == ()
    assert summaries.calls == []


async def test_a_content_question_cannot_name_its_way_past_the_callers_pin() -> None:
    """The pin still wins, on this route too (`_candidates`): the question
    names «دليل الموظفين» while the conversation is pinned to the two
    reports, and the search stays inside the pin instead of reaching out to
    the file the question named.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _corpus_with_an_empty_named_file(ctx, embeddings, vectors)
    service, _summaries = await _routing_service(
        ctx, embeddings, vectors, _HANDBOOK_CORPUS, documents=documents
    )

    routed = await service.answer(
        ctx,
        "ما هي أرقام الإيرادات في دليل الموظفين",
        5,
        ["file-north", "file-south"],
        space_id=_SPACE_A,
    )

    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-north", "doc-south"],
        "space": _SPACE_A,
    }
    assert {chunk.document_id for chunk in routed.chunks} == {"doc-north", "doc-south"}


async def test_a_pin_of_one_document_resolves_no_names_at_all() -> None:
    """The short-circuit: a pin that already names one document is as narrow
    as a file name could make it, so the corpus walk (`ListFileCandidates`,
    one `get_readable` per document — its own §7 entry) is not paid to
    re-derive it. Not a behaviour choice: resolution over that single
    candidate could only return it or fall through to it.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    files = _FakeReadableFiles(dict.fromkeys(_NAMED_CORPUS), names=_NAMED_CORPUS)
    service, _summaries = await _routing_service(ctx, embeddings, vectors, files=files)

    routed = await service.answer(
        ctx, "ما هي أرقام الإيرادات في التقرير الشمالي؟", 5, ["file-south"], space_id=_SPACE_A
    )

    assert files.calls == []
    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-south"],
        "space": _SPACE_A,
    }
    assert [chunk.document_id for chunk in routed.chunks] == ["doc-south"]


async def test_a_summarisation_question_that_falls_through_is_not_resolved_twice() -> None:
    """A SUMMARIZE_DOC question whose target is unidentifiable falls through
    to CONTENT retrieval (row 11), and the CONTENT route does NOT re-run the
    resolver over the same candidates: the answer could only be the same
    `NoFileMatch`, and the corpus walk is one `get_readable` per document.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    files = _FakeReadableFiles(dict.fromkeys(_NAMED_CORPUS), names=_NAMED_CORPUS)
    service, summaries = await _routing_service(ctx, embeddings, vectors, files=files)

    routed = await service.answer(ctx, "لخص لي أرقام الإيرادات الفصلية", 5, space_id=_SPACE_A)

    assert routed.intent is Intent.SUMMARIZE_DOC
    assert summaries.calls == []
    # Two documents, two lookups: one walk, not two.
    assert len(files.calls) == 2
    assert "document_id" not in vectors.search_calls[-1][2]


async def test_answer_is_a_routed_answer_and_retrieve_is_still_plain_retrieval() -> None:
    """The port keeps BOTH faces (`ports/inbound.py`): `POST /knowledge/search`
    asks for chunks and means chunks -- routing a REST search through the
    classifier would let it queue a summary job nobody requested."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _routing_service(ctx, embeddings, vectors)

    routed = await service.answer(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A)
    chunks = await service.retrieve(ctx, "لخص لي هذا الملف", 5, ["file-north"], space_id=_SPACE_A)

    assert isinstance(routed, RoutedAnswer)
    assert isinstance(chunks, list)
    assert summaries.calls == []


# --------------------------------------------------------------------------- #
# RetrieveContext confidence signals (retrieval plan §3.3/§3.11, P-28,        #
# step 4) -- best-dense/best-bm25, snapshotted BEFORE RRF, "best" = MAXIMUM   #
# (the alpha scale-direction inversion, §6 risk #3). RetrieveContext.execute  #
# is called DIRECTLY here (not through KnowledgeRetrievalService), because    #
# the port (`KnowledgeRetrieval.retrieve` -> `list[RetrievedChunk]`) never    #
# carries these signals -- only `RetrievalResult` does (see                   #
# `application/retrieval.py`'s module docstring).                            #
# --------------------------------------------------------------------------- #
async def test_confidence_signals_are_raw_pre_rrf_scores_not_the_chunks_own_rrf_score() -> None:
    """The decisive proof that the snapshot is taken UPSTREAM of RRF: a
    query identical to the one indexed chunk's text scores ~1.0 cosine
    similarity, wildly different from the tiny RRF fraction
    (0.5 / (60 + rank + 1)) `RetrievedChunk.score` carries."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    text = "quarterly revenue figures for the northern region"
    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-1",
        space_id=_SPACE_A,
        parsed=_parsed_document([_parsed_chunk(text, order=0)]),
        model="embed-1",
        api_key="key-1",
    )

    result = await RetrieveContext(
        embeddings, vectors, _FakeDocumentRepository(), tuning=_UNGATED
    ).execute(ctx, query=text, model="embed-1", api_key="key-1", k=5, space_id=_SPACE_A)

    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) == 1
    # Raw cosine similarity of a vector against itself: ~1.0.
    assert result.best_dense_score == pytest.approx(1.0, abs=1e-6)
    # Raw sparse dot product of the query's own terms against themselves: > 0.
    assert result.best_bm25_score is not None
    assert result.best_bm25_score > 0.0
    # NOT the same scale as the RRF score the returned chunk itself carries
    # (0.5/61 + 0.5/61 ~= 0.0164 for a chunk ranked first on both legs) --
    # proof `best_dense_score`/`best_bm25_score` are not just an alias for
    # `chunks[0].score`.
    assert result.chunks[0].score < 0.1
    assert result.best_dense_score > result.chunks[0].score


async def test_best_scores_are_the_maximum_across_all_hits_never_the_minimum() -> None:
    """§6 risk #3: alpha's `best_dense_distance` is a MINIMUM (nearer L2
    distance = better); AIZZAK's cosine scale is the opposite, and this
    proves the code takes the MAXIMUM, not alpha's reduction verbatim."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    query_text = "quarterly revenue figures for the northern region"
    unrelated_text = "a recipe for baking sourdough bread at high altitude"
    for doc_id, text in (("doc-match", query_text), ("doc-unrelated", unrelated_text)):
        await IndexDocument(embeddings, vectors).execute(
            ctx,
            document_id=doc_id,
            space_id=_SPACE_A,
            parsed=_parsed_document([_parsed_chunk(text, order=0)]),
            model="embed-1",
            api_key="key-1",
        )

    result = await RetrieveContext(
        embeddings, vectors, _FakeDocumentRepository(), tuning=_UNGATED
    ).execute(ctx, query=query_text, model="embed-1", api_key="key-1", k=5, space_id=_SPACE_A)

    # The best score is the near-identical match's ~1.0, not the unrelated
    # document's much lower (or negative) cosine similarity -- a `min()`
    # instead of `max()` would fail this assertion.
    assert result.best_dense_score == pytest.approx(1.0, abs=1e-6)


async def test_best_bm25_score_is_none_when_the_sparse_leg_returns_no_hits() -> None:
    """An empty leg is an honest ``None``, never ``0.0`` -- ``0.0`` is a real,
    meaningful score on this dot-product scale."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    await IndexDocument(embeddings, vectors).execute(
        ctx,
        document_id="doc-1",
        space_id=_SPACE_A,
        parsed=_parsed_document([_parsed_chunk("quarterly revenue figures", order=0)]),
        model="embed-1",
        api_key="key-1",
    )
    # Punctuation-only: every tokenizer this module dispatches to strips it
    # down to zero tokens, so the query's OWN sparse vector is empty --
    # `search_sparse` then finds nothing to score above `0.0` regardless of
    # what is indexed (`domain/sparse.py`: "Empty or stopword-only text
    # returns an empty SparseTerms").
    query = "!!! ??? ..."
    assert build_sparse_terms(query).indices == ()

    result = await RetrieveContext(
        embeddings, vectors, _FakeDocumentRepository(), tuning=_UNGATED
    ).execute(ctx, query=query, model="embed-1", api_key="key-1", k=5, space_id=_SPACE_A)

    assert result.best_bm25_score is None
    # The dense leg is unaffected -- it embeds the raw query text regardless
    # of tokenization, so it still has a real (if not necessarily large) score.
    assert result.best_dense_score is not None


async def test_both_confidence_signals_are_none_over_an_empty_corpus() -> None:
    """A workspace nobody has indexed anything into (no Qdrant collection) is
    a normal state (module docstring, `qdrant_store.py`) -- both legs return
    no hits, so both signals are honestly absent."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()

    result = await RetrieveContext(
        embeddings, vectors, _FakeDocumentRepository(), tuning=_UNGATED
    ).execute(
        ctx,
        query="quarterly revenue figures",
        model="embed-1",
        api_key="key-1",
        k=5,
        space_id=_SPACE_A,
    )

    assert result == RetrievalResult(chunks=[], best_dense_score=None, best_bm25_score=None)


async def test_a_scope_of_unindexed_documents_short_circuits_with_none_signals() -> None:
    """The same BE-RAG-005 short circuit as
    ``test_a_scope_of_unindexed_files_retrieves_nothing_rather_than_everything``,
    but exercised directly against ``RetrieveContext`` (``document_ids=[]``,
    its own vocabulary) -- no vector round trip happens at all, so there is
    no leg to have scored anything."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    await _indexed_corpus(ctx, embeddings, vectors)
    searches_before = len(vectors.search_calls)

    result = await RetrieveContext(
        embeddings, vectors, _FakeDocumentRepository(), tuning=_UNGATED
    ).execute(
        ctx,
        query="quarterly revenue figures",
        model="embed-1",
        api_key="key-1",
        k=5,
        document_ids=[],
        space_id=_SPACE_A,
    )

    assert result == RetrievalResult(chunks=[], best_dense_score=None, best_bm25_score=None)
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
        content_hash="hash-abc",
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
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
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
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )

    await service.retrieve(
        ctx, "quarterly revenue figures", 5, ["file-doc-research"], space_id=_SPACE_A
    )

    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-research"],
        "space": _SPACE_A,
    }


@pytest.mark.parametrize("missing", [None, "", "   "])
async def test_an_unspaced_retrieval_is_refused_instead_of_seeing_every_space(
    missing: str | None,
) -> None:
    """The GUARD (س-32, owner decision 2026-08-26) — and the test it replaced
    said the opposite.

    ``test_an_unspaced_retrieval_still_sees_every_space`` used to pin ``None``
    as "all spaces", with the ``space`` key left out of the filter entirely.
    That reading is gone: spaces are isolated completely, so a search spans one
    of them or it does not run. The refusal is raised before an embedding is
    computed and before either leg is searched — proven here by both fakes
    having recorded nothing at all — so an unscoped caller costs a workspace no
    provider call on its way to a 422.

    The blank strings are the same case wearing a different value: ``" "``
    would otherwise reach the filter, match no point, and turn a broken caller
    into an empty result nobody could explain.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    service = KnowledgeRetrievalService(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )

    embedded_before = len(embeddings.calls)

    with pytest.raises(ValidationError) as excinfo:
        # `type: ignore` on the `None` case: the seam is typed non-nullable
        # now, and this test is about the callers mypy never sees — the wire,
        # a stored row, an adapter nobody has written yet.
        await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=missing)  # type: ignore[arg-type]

    assert excinfo.value.code == "knowledge.space_required"
    assert excinfo.value.status == 422
    # Nothing was spent on the way to the refusal. Measured against the
    # seeding above (`_spaced_corpus` embeds and upserts through these same
    # fakes), so what is asserted is that the CALL added nothing.
    assert vectors.search_calls == []
    assert vectors.search_sparse_calls == []
    assert len(embeddings.calls) == embedded_before


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
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeEmbeddingResolver(model="embed-1", api_key="key-1"),
        documents,
        _FakeReadableFiles(),
        _FakeSummaryStarter(),
    )

    assert await service.retrieve(ctx, "quarterly revenue figures", 5, space_id=_SPACE_A) == []
    # And it is genuinely there — the point exists, it simply carries no
    # `space` key to match. This half used to be asserted by retrieving with
    # `space_id=None`, the "every space" call س-32 removed; the store is asked
    # directly now, because there is no longer any caller of this module that
    # could see the point at all.
    assert [point.payload["text"] for point in vectors.points["kn-ws1"].values()] == [
        "quarterly revenue figures"
    ]
    # ⇒ §5-أ got STRICTER with the decision, not looser: pre-spaces content is
    # now unreachable through every face of the module rather than through the
    # space-scoped ones only. The mandated re-index is the only cure, as it was.


# --------------------------------------------------------------------------- #
# A named file is resolved INSIDE the space being searched                    #
# (branch review §7, over the spaces plan's step 8)                           #
# --------------------------------------------------------------------------- #
# `_spaced_corpus`'s two documents, NAMED. Three tokens each, so a CONTENT
# question that names one really does narrow the search (the review §3 bar in
# `_narrows_content_scope`), and far enough apart that neither resolves to the
# other: inside one space, the OTHER space's file is a plain `NoFileMatch` —
# the ordinary miss this router already answers honestly.
_SPACED_NAMES = {
    "file-doc-research": "خطة التسويق السنوية.pdf",  # doc-research, space (أ)
    "file-doc-drafts": "التقرير المالي الفصلي.pdf",  # doc-drafts, space (ب)
}
# The same file — the one that lives in (ب) — named on each of the two routes.
_SUMMARISE_THE_DRAFTS_FILE = "لخص لي التقرير المالي الفصلي"
_ASK_ABOUT_THE_DRAFTS_FILE = "ما جاء في التقرير المالي الفصلي عن الإيرادات؟"


async def _spaced_routing_service(
    ctx: ExecutionContext, embeddings: _FakeEmbeddings, vectors: _FakeHybridVectors
) -> tuple[KnowledgeRetrievalService, _FakeSummaryStarter]:
    """The routing service of the tests above, over the two-SPACE corpus of
    the space tests above: the REAL `RouteQuestion`, the REAL
    `ListFileCandidates` and the REAL `resolve_file`, so the space is proven
    across every seam it has to cross rather than at the first one only.
    """
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    return await _routing_service(ctx, embeddings, vectors, _SPACED_NAMES, documents=documents)


class _RecordingCandidates:
    """A structural `FileCandidates` that records the SPACE each walk was
    asked for — the one thing `RouteQuestion` decides about the corpus its
    resolver is shown."""

    def __init__(self, candidates: Sequence[FileCandidate] = ()) -> None:
        self._candidates = tuple(candidates)
        self.spaces: list[str | None] = []

    async def execute(
        self, ctx: ExecutionContext, *, space_id: str | None
    ) -> Sequence[FileCandidate]:
        self.spaces.append(space_id)
        return self._candidates


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        (_SUMMARISE_THE_DRAFTS_FILE, Intent.SUMMARIZE_DOC),
        ("quarterly revenue figures", Intent.CONTENT),
    ],
)
async def test_the_router_walks_the_candidates_of_the_space_it_was_asked_about(
    question: str, intent: Intent
) -> None:
    """The wiring itself, on BOTH routes: the space that reaches the candidate
    walk is the space the question was asked in — not `None`, which is what
    every one of these walks used to be.

    Both routes resolve names through the same `_candidates`, so both had to
    receive it; a router that forwarded the space to the SEARCH only would
    build a scope out of names it found in some other space.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    documents = await _spaced_corpus(ctx, embeddings, vectors)
    files = _RecordingCandidates(
        [FileCandidate(document_id="doc-research", file_name=_SPACED_NAMES["file-doc-research"])]
    )
    router = RouteQuestion(
        RetrieveContext(embeddings, vectors, documents, tuning=_UNGATED),
        _FakeSummaryStarter(),
        files,
    )

    routed = await router.execute(
        ctx, question=question, model="embed-1", api_key="key-1", space_id=_SPACE_A
    )

    assert routed.intent is intent
    assert files.spaces == [_SPACE_A]


async def test_a_summarisation_question_resolves_a_name_inside_its_own_space() -> None:
    """The half that has to keep working: asked in the space that HOLDS the
    file it names, the question resolves and the build is queued, exactly as
    it does with no space at all."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _spaced_routing_service(ctx, embeddings, vectors)

    routed = await service.answer(ctx, _SUMMARISE_THE_DRAFTS_FILE, 5, space_id=_SPACE_B)

    assert summaries.calls == [("doc-drafts", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
    assert routed.summary_job_id == "job-1"


async def test_a_question_in_one_space_never_summarises_a_file_from_another() -> None:
    """**THE trap (review §7).** The question is asked in (أ) and names the
    file that lives in (ب). Resolved over every space it would resolve — and
    queue a summary of a document the asker's own search can never reach.

    Resolved inside (أ) it is an ordinary `NoFileMatch`: nothing is queued,
    the question falls through to CONTENT retrieval with its intent still
    reported honestly, and the search that runs is (أ)'s own — unscoped
    within it, never scoped to (ب)'s document.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _spaced_routing_service(ctx, embeddings, vectors)

    routed = await service.answer(ctx, _SUMMARISE_THE_DRAFTS_FILE, 5, space_id=_SPACE_A)

    assert summaries.calls == []
    assert routed.summary_job_id is None
    assert routed.intent is Intent.SUMMARIZE_DOC
    # No clarification either: the name matched nothing in this space, it did
    # not tie between two things in it.
    assert routed.clarification_options == ()
    # And the fall-through search is (أ)'s whole corpus, with no `document_id`
    # from (ب) ANDed onto it -- the pair that would have returned zero chunks.
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws1", "space": _SPACE_A}


async def test_a_content_question_never_narrows_the_search_to_another_spaces_document() -> None:
    """The same trap on the CONTENT route, where it is quieter still: row 15
    is STRICT, so a scope narrowed to (ب)'s document would be searched once,
    return nothing, and reach the honest-fallback gate as «لا أملك معلومات
    كافية» — about a question (أ) may well answer in a file of its own.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, _summaries = await _spaced_routing_service(ctx, embeddings, vectors)

    routed = await service.answer(ctx, _ASK_ABOUT_THE_DRAFTS_FILE, 5, space_id=_SPACE_A)

    assert routed.intent is Intent.CONTENT
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws1", "space": _SPACE_A}
    assert "doc-drafts" not in {chunk.document_id for chunk in routed.chunks}


async def test_the_same_content_question_still_narrows_inside_the_space_that_holds_it() -> None:
    """Not narrowing is not a new refusal to narrow. The identical question,
    asked in the space the named file actually lives in, still scopes the
    search to that one document — both conditions ANDed, neither replacing
    the other."""
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, _summaries = await _spaced_routing_service(ctx, embeddings, vectors)

    await service.answer(ctx, _ASK_ABOUT_THE_DRAFTS_FILE, 5, space_id=_SPACE_B)

    assert vectors.search_calls[-1][2] == {
        "workspace_id": "ws1",
        "document_id": ["doc-drafts"],
        "space": _SPACE_B,
    }


async def test_an_unspaced_question_is_refused_before_a_name_is_resolved() -> None:
    """The guard on the ROUTING half (س-32) — and it has to fire here, not
    only inside the search.

    Two tests used to live at this spot, both pinning the opposite behaviour:
    an unspaced summarisation question resolved its file name across every
    space and queued a build for it, and an unspaced content question narrowed
    to a document out of any space with no ``space`` key on the filter at all.
    Both are exactly the leak the decision names — a question asked nowhere,
    answered from a file the asker may not be able to open.

    ``RouteQuestion`` walks the candidate corpus BEFORE it calls
    ``RetrieveContext``, so a guard living only in the search would let an
    unscoped question read every space's file NAMES first and be refused
    second. Proven by the summary starter and both vector legs being untouched.
    """
    ctx = _ctx("ws1")
    embeddings, vectors = _FakeEmbeddings(dim=6), _FakeHybridVectors()
    service, summaries = await _spaced_routing_service(ctx, embeddings, vectors)

    searched_before = len(vectors.search_calls)

    with pytest.raises(ValidationError) as excinfo:
        await service.answer(ctx, _SUMMARISE_THE_DRAFTS_FILE, 5, space_id=None)  # type: ignore[arg-type]

    assert excinfo.value.code == "knowledge.space_required"
    assert summaries.calls == []
    assert len(vectors.search_calls) == searched_before
