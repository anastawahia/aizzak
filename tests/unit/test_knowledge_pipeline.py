"""Unit tests for the knowledge module's 3.k3 hybrid (dense + BM25-sparse)
indexing/retrieval pipeline: the chunker, sparse-term builder, relevance
filter, and intent classifier (all pure domain), plus the ``IndexDocument``/
``RetrieveContext`` application use-cases over fake ``EmbeddingProvider``/
``HybridVectorStore`` ports. Pure unit tests: no markers, no Docker, no
optional dependencies -- ``ParsedDocument``/``ParsedChunk`` fixtures are
built directly rather than run through the (optional-dependency-gated) real
parser adapters exercised by ``test_knowledge_parsers.py``.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.types import Json
from app.modules.knowledge.application.indexing import IndexDocument, IndexOutcome
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.domain.chunking import SourceSegment, chunk_segments
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.intent import Intent, classify_intent
from app.modules.knowledge.domain.relevance import ScoredChunk, filter_relevant
from app.modules.knowledge.domain.sparse import SparseTerms, build_sparse_terms, term_id
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _segment(
    text: str, order: int, kind: str = "text", metadata: dict[str, Any] | None = None
) -> SourceSegment:
    return SourceSegment(text=text, order=order, kind=kind, metadata=metadata or {})


def _scored(
    chunk_id: str, text: str, score: float, *, seq: int = 0, document_id: str = "doc1"
) -> ScoredChunk:
    return ScoredChunk(chunk_id=chunk_id, document_id=document_id, text=text, score=score, seq=seq)


def _parsed_chunk(
    text: str,
    order: int = 0,
    kind: ParsedChunkKind = ParsedChunkKind.TEXT,
    metadata: Json | None = None,
) -> ParsedChunk:
    return ParsedChunk(text=text, order=order, kind=kind, metadata=metadata or {})


def _parsed_document(chunks: Sequence[ParsedChunk]) -> ParsedDocument:
    return ParsedDocument(
        source_ext=".txt", content_type="text/plain", chunks=tuple(chunks), metadata={}
    )


def _seeded_vector(text: str, dim: int) -> list[float]:
    """A deterministic unit vector derived from ``text`` (``blake2b``, never
    the randomized builtin ``hash()``) -- distinct texts get distinct, but
    reproducible, vectors; the identical text always maps to the identical
    vector."""
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=dim * 2).digest()
    raw = [
        (int.from_bytes(digest[i : i + 2], "big") / 65535.0) * 2.0 - 1.0
        for i in range(0, len(digest), 2)
    ]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


def _hand_built_point(
    ctx: ExecutionContext, document_id: str, seq: int, text: str, vector: list[float]
) -> VectorPoint:
    point_id = chunk_point_id(document_id, seq)
    terms = build_sparse_terms(text)
    sparse = SparseVector(indices=list(terms.indices), values=list(terms.values))
    payload: Json = {
        "workspace_id": ctx.workspace_id,
        "document_id": document_id,
        "chunk_id": point_id,
        "seq": seq,
        "text": text,
        "kind": "text",
    }
    return VectorPoint(id=point_id, vector=vector, payload=payload, sparse=sparse)


async def _seed_corpus(
    vectors: FakeHybridVectors,
    ctx: ExecutionContext,
    document_id: str,
    texts: Sequence[str],
    *,
    dim: int = 8,
) -> None:
    collection = knowledge_collection(ctx.workspace_id)
    points = [
        _hand_built_point(ctx, document_id, seq, text, _seeded_vector(text, dim))
        for seq, text in enumerate(texts)
    ]
    await vectors.upsert(collection, points)


# --------------------------------------------------------------------------- #
# Fakes (EmbeddingProvider / HybridVectorStore)                               #
# --------------------------------------------------------------------------- #
class FakeEmbeddings:
    """Deterministic ``EmbeddingProvider`` fake -- each text's vector is a
    seed derived purely from the text itself, unless an explicit
    ``overrides`` vector is supplied for that exact text (lets a test place
    specific chunks/queries at exact points in vector space). Records every
    ``embed`` call's texts, so a test can assert both batch sizes and that a
    query embed call was exactly ``[query]``."""

    provider = "fake"

    def __init__(self, *, dim: int = 8, overrides: dict[str, list[float]] | None = None) -> None:
        self.dim = dim
        self._overrides = overrides or {}
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        texts = list(texts)
        self.calls.append(texts)
        vectors = [self._overrides.get(text, _seeded_vector(text, self.dim)) for text in texts]
        return EmbeddingResult(vectors=vectors, model=model, dimensions=self.dim, tokens=len(texts))

    def dimensions(self, model: str) -> int:
        return self.dim


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


def _sparse_score(point: VectorPoint, query: SparseVector) -> float:
    if point.sparse is None:
        return 0.0
    return _sparse_dot(query, point.sparse)


class FakeHybridVectors:
    """In-memory ``HybridVectorStore`` fake: brute-force cosine (dense) /
    dot-product (sparse) search over whatever has been upserted, scoped to
    the collection and filtered by an exact-match ``flt`` AND over each
    point's payload. ``search_sparse`` excludes non-positive dot products --
    mirroring a real inverted-index sparse engine, which never returns a
    document with zero matching terms. Records ``(collection, k, flt)`` for
    every ``search``/``search_sparse`` call for tenant-isolation/clamp
    assertions."""

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

    # Indexes are an adapter-side concern (the real one provisions them from
    # `ensure_hybrid_collection`, spaces plan step 9); a brute-force fake has
    # nothing to index, and no use-case calls this.
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
        scored = sorted(
            ((p, _sparse_score(p, sparse)) for p in candidates),
            key=lambda item: item[1],
            reverse=True,
        )
        scored = [(p, score) for p, score in scored if score > 0.0]
        return [VectorHit(id=p.id, score=score, payload=p.payload) for p, score in scored[:k]]

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        bucket = self.points.get(collection, {})
        for point_id in ids:
            bucket.pop(point_id, None)


# --------------------------------------------------------------------------- #
# chunking.chunk_segments                                                     #
# --------------------------------------------------------------------------- #
def test_chunk_segments_empty_input_returns_empty() -> None:
    assert chunk_segments([]) == []


def test_chunk_segments_empty_text_segment_returns_empty() -> None:
    assert chunk_segments([_segment("   ", 0)]) == []


def test_chunk_segments_is_deterministic() -> None:
    segments = [_segment("word " * 20, 0), _segment("term " * 20, 1)]
    first = chunk_segments(segments, max_tokens=5, overlap_tokens=1)
    second = chunk_segments(segments, max_tokens=5, overlap_tokens=1)
    assert first == second


def test_chunk_segments_seq_is_unique_and_gap_free_across_segments() -> None:
    segments = [
        _segment("alpha beta gamma delta epsilon zeta", 0),
        _segment("eta theta iota kappa lambda mu", 1),
    ]
    chunks = chunk_segments(segments, max_tokens=3, overlap_tokens=1, min_chars=0)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert len(chunks) > 2  # multiple windows per segment, across 2 segments


def test_chunk_segments_window_and_overlap() -> None:
    words = [f"w{i}" for i in range(10)]
    text = " ".join(words)
    chunks = chunk_segments([_segment(text, 0)], max_tokens=4, overlap_tokens=2, min_chars=0)
    assert [c.text for c in chunks] == [
        "w0 w1 w2 w3",
        "w2 w3 w4 w5",
        "w4 w5 w6 w7",
        "w6 w7 w8 w9",
    ]
    assert [c.token_count for c in chunks] == [4, 4, 4, 4]


def test_chunk_segments_sorts_by_order_not_input_position() -> None:
    seg_b = _segment("second segment text here", 1, kind="table")
    seg_a = _segment("first segment text here", 0, kind="text")
    chunks = chunk_segments([seg_b, seg_a], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.kind for c in chunks] == ["text", "table"]
    assert chunks[0].text.startswith("first")
    assert chunks[1].text.startswith("second")


def test_chunk_segments_ties_in_order_keep_original_relative_position() -> None:
    seg1 = _segment("one", 0, kind="a")
    seg2 = _segment("two", 0, kind="b")
    chunks = chunk_segments([seg1, seg2], max_tokens=10, overlap_tokens=0, min_chars=0)
    assert [c.kind for c in chunks] == ["a", "b"]


def test_chunk_segments_merges_short_trailing_window_into_previous() -> None:
    text = "a b c d e f"
    chunks = chunk_segments([_segment(text, 0)], max_tokens=5, overlap_tokens=1, min_chars=10)
    # naive windows: [a,b,c,d,e] then [e,f] ("e f" is 3 chars, < min_chars=10) -> merged
    assert len(chunks) == 1
    assert chunks[0].text == "a b c d e e f"
    assert chunks[0].token_count == 7


def test_chunk_segments_single_window_segment_is_kept_even_if_shorter_than_min_chars() -> None:
    chunks = chunk_segments([_segment("hi", 0)], max_tokens=512, overlap_tokens=64, min_chars=100)
    assert len(chunks) == 1
    assert chunks[0].text == "hi"


def test_chunk_segments_propagates_kind_and_metadata_per_chunk() -> None:
    meta = {"page_number": 3}
    chunks = chunk_segments(
        [_segment("hello world", 0, kind="table", metadata=meta)], max_tokens=10
    )
    assert chunks[0].kind == "table"
    assert chunks[0].metadata == {"page_number": 3}
    assert chunks[0].metadata is not meta  # shallow-copied per chunk, not aliased


# --------------------------------------------------------------------------- #
# sparse.build_sparse_terms / term_id                                         #
# --------------------------------------------------------------------------- #
def test_build_sparse_terms_empty_text_is_empty() -> None:
    assert build_sparse_terms("") == SparseTerms((), ())


def test_build_sparse_terms_stopword_only_text_is_empty() -> None:
    assert build_sparse_terms("the and") == SparseTerms((), ())  # both English stopwords


def test_build_sparse_terms_accumulates_term_frequency() -> None:
    result = build_sparse_terms("cat cat dog")
    cat_id, dog_id = term_id("cat"), term_id("dog")
    by_id = dict(zip(result.indices, result.values, strict=True))
    assert by_id[cat_id] == 2.0
    assert by_id[dog_id] == 1.0
    assert sum(result.values) == 3.0


def test_build_sparse_terms_indices_ascending_and_deduplicated() -> None:
    result = build_sparse_terms("cat dog cat bird dog cat")
    assert list(result.indices) == sorted(result.indices)
    assert len(set(result.indices)) == len(result.indices)


def test_build_sparse_terms_is_deterministic_on_repeat() -> None:
    text = "quarterly revenue report figures"
    assert build_sparse_terms(text) == build_sparse_terms(text)


def test_term_id_returns_a_uint32_range_int() -> None:
    assert 0 <= term_id("some-term") <= 0xFFFFFFFF


def test_term_id_is_stable_regardless_of_pythonhashseed() -> None:
    """Mirrors test_rrf_tie_order_independent_of_pythonhashseed
    (test_knowledge_retrieval_algorithms.py): term_id must use hashlib, never
    the builtin hash(), so a chunk indexed in one process and a query hashed
    in another process always agree on term ids."""
    script = (
        "from app.modules.knowledge.domain.sparse import build_sparse_terms\n"
        "r = build_sparse_terms('cat dog cat bird')\n"
        "print(r.indices, r.values)\n"
    )
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    outputs = []
    for seed in ("0", "1", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": src_path}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=True,
        )
        outputs.append(proc.stdout.strip())
    assert len(set(outputs)) == 1


# --------------------------------------------------------------------------- #
# relevance.filter_relevant                                                   #
# --------------------------------------------------------------------------- #
def test_filter_relevant_absolute_floor_drops_below_min_score() -> None:
    candidates = [_scored("a", "alpha text here", 0.9), _scored("b", "beta words only", 0.1)]
    result = filter_relevant(candidates, min_score=0.5)
    assert [c.chunk_id for c in result] == ["a"]


def test_filter_relevant_relative_floor_drops_below_fraction_of_max() -> None:
    candidates = [
        _scored("a", "alpha quarterly revenue report", 1.0),
        _scored("b", "beta staffing headcount update", 0.6),
        _scored("c", "gamma unrelated filler content", 0.05),
    ]
    result = filter_relevant(candidates, relative_floor=0.5)
    assert [c.chunk_id for c in result] == ["a", "b"]


def test_filter_relevant_dedup_keeps_higher_score_of_near_duplicate_pair() -> None:
    candidates = [
        _scored("a", "the quarterly revenue report is strong this year", 0.4),
        _scored("b", "the quarterly revenue report is strong this year", 0.9),
    ]
    result = filter_relevant(candidates)
    assert [c.chunk_id for c in result] == ["b"]


def test_filter_relevant_dedup_preserves_input_order_among_survivors() -> None:
    candidates = [
        _scored("a", "completely unrelated onboarding checklist content", 0.9),
        _scored("b", "the quarterly revenue report is strong this year", 0.4),
        _scored("c", "the quarterly revenue report is strong this year", 0.5),
    ]
    result = filter_relevant(candidates)
    assert [c.chunk_id for c in result] == ["a", "c"]  # "b" loses to "c" (higher score)


def test_filter_relevant_dedup_disabled_keeps_near_duplicates() -> None:
    candidates = [
        _scored("a", "the quarterly revenue report is strong this year", 0.9),
        _scored("b", "the quarterly revenue report is strong this year", 0.4),
    ]
    result = filter_relevant(candidates, dedup=False)
    assert [c.chunk_id for c in result] == ["a", "b"]


def test_filter_relevant_disabled_floors_and_dedup_pass_everything_through() -> None:
    candidates = [
        _scored("a", "identical filler text", 0.001),
        _scored("b", "identical filler text", 0.9),
    ]
    result = filter_relevant(candidates, dedup=False)
    assert [c.chunk_id for c in result] == ["a", "b"]


def test_filter_relevant_dedup_respects_custom_jaccard_threshold() -> None:
    # shared word-set jaccard = |{alpha,beta}| / |{alpha,beta,gamma,delta}| = 2/4 = 0.5
    candidates = [_scored("a", "alpha beta gamma", 0.9), _scored("b", "alpha beta delta", 0.5)]
    kept_at_threshold = filter_relevant(candidates, jaccard_threshold=0.5)
    kept_above_threshold = filter_relevant(candidates, jaccard_threshold=0.51)
    assert [c.chunk_id for c in kept_at_threshold] == ["a"]  # 0.5 >= 0.5 -> treated as dup
    assert [c.chunk_id for c in kept_above_threshold] == ["a", "b"]  # 0.5 < 0.51 -> kept separate


# --------------------------------------------------------------------------- #
# intent.classify_intent                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "query",
    [
        "كم عدد الملفات المرفوعة؟",
        "كم ملف لدينا في المساحة؟",
        "عدد الملفات في هذا المشروع",
        "اعرض الملفات من فضلك",
        "How many documents do we have?",
        "How many files are indexed?",
        "List the files in this workspace",
        "List documents",
    ],
)
def test_classify_intent_metadata_ar_en(query: str) -> None:
    assert classify_intent(query) is Intent.METADATA


@pytest.mark.parametrize(
    "query",
    [
        "كم ملف يتحدث عن الرواتب؟",
        "كم عدد الملفات حول الميزانية؟",
        "How many files mention the budget?",
        "How many documents are about marketing?",
    ],
)
def test_classify_intent_topical_guard_demotes_metadata_to_content(query: str) -> None:
    assert classify_intent(query) is Intent.CONTENT


@pytest.mark.parametrize(
    "query",
    [
        "لخص لي التقرير السنوي",
        "أريد تلخيص هذا الملف",
        "ما هو ملف الميزانية؟",
        "Can you summarize this document?",
        "Please provide a summary of the report",
        "What is the file quarterly_report.pdf?",
    ],
)
def test_classify_intent_summarize_doc_ar_en(query: str) -> None:
    assert classify_intent(query) is Intent.SUMMARIZE_DOC


@pytest.mark.parametrize(
    "query",
    [
        "What were the total profits last year?",
        "ما هي أرباح الشركة هذا العام؟",
        "Explain the marketing strategy",
    ],
)
def test_classify_intent_defaults_to_content(query: str) -> None:
    assert classify_intent(query) is Intent.CONTENT


def test_classify_intent_blank_query_is_content() -> None:
    assert classify_intent("   ") is Intent.CONTENT
    assert classify_intent("") is Intent.CONTENT


# --------------------------------------------------------------------------- #
# collections.knowledge_collection / chunk_point_id                           #
# --------------------------------------------------------------------------- #
def test_knowledge_collection_name() -> None:
    assert knowledge_collection("ws-123") == "kn-ws-123"


def test_chunk_point_id_is_deterministic() -> None:
    assert chunk_point_id("doc-1", 0) == chunk_point_id("doc-1", 0)


def test_chunk_point_id_differs_by_seq_and_document() -> None:
    a, b, c = chunk_point_id("doc-1", 0), chunk_point_id("doc-1", 1), chunk_point_id("doc-2", 0)
    assert len({a, b, c}) == 3


def test_chunk_point_id_is_a_valid_uuid_string() -> None:
    assert uuid.UUID(chunk_point_id("doc-1", 0))  # does not raise


# --------------------------------------------------------------------------- #
# application.indexing.IndexDocument                                          #
# --------------------------------------------------------------------------- #
async def test_index_document_ensures_hybrid_collection_and_upserts_hybrid_points() -> None:
    embeddings = FakeEmbeddings(dim=6)
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk("first document paragraph about revenue", order=0),
            _parsed_chunk("second document paragraph about headcount", order=1),
        ]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="embed-1", api_key="k"
    )

    assert outcome.collection == "kn-ws1"
    assert outcome.dimensions == 6
    assert len(outcome.chunks) == 2
    assert vectors.ensured_hybrid == [("kn-ws1", 6, "cosine")]

    stored = vectors.points["kn-ws1"]
    assert len(stored) == 2
    for point in stored.values():
        assert point.vector  # dense vector present
        assert point.sparse is not None
        assert point.payload["workspace_id"] == "ws1"
        assert point.payload["document_id"] == "doc-1"


async def test_index_document_point_ids_are_deterministic_across_reindex_runs() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk("stable content for reindexing", order=0)])

    first = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )
    second = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]
    assert first.chunks[0].chunk_id == chunk_point_id("doc-1", 0)
    # re-indexing overwrites the same point rather than duplicating it
    assert len(vectors.points["kn-ws1"]) == 1


async def test_index_document_batches_embed_calls_past_128_chunks() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    # 130 tiny single-word segments -> each becomes exactly one chunk, so the
    # chunk count (and thus the batch split) is easy to reason about.
    parsed = _parsed_document([_parsed_chunk(f"paragraphtoken{i}", order=i) for i in range(130)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 130
    assert len(vectors.points["kn-ws1"]) == 130
    assert len(embeddings.calls) == 2  # ceil(130 / 128) == 2 batches
    assert len(embeddings.calls[0]) == 128
    assert len(embeddings.calls[1]) == 2


async def test_index_document_empty_parsed_document_upserts_nothing() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=_parsed_document([]), model="m", api_key="k"
    )

    assert outcome == IndexOutcome(collection="kn-ws1", dimensions=8, chunks=())
    assert vectors.points.get("kn-ws1", {}) == {}
    assert embeddings.calls == []


async def test_index_document_payload_copies_citation_allowlist_keys_when_present() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(
                "revenue figures for q3 across all regions",
                order=0,
                kind=ParsedChunkKind.TABLE,
                metadata={"page_number": 4, "table_name": "revenue", "irrelevant_key": "drop-me"},
            )
        ]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )
    point = vectors.points["kn-ws1"][outcome.chunks[0].chunk_id]

    assert point.payload["page_number"] == 4
    assert point.payload["table_name"] == "revenue"
    assert "irrelevant_key" not in point.payload
    assert point.payload["kind"] == "table"


# --------------------------------------------------------------------------- #
# application.retrieval.RetrieveContext                                       #
# --------------------------------------------------------------------------- #
async def test_index_then_retrieve_round_trip() -> None:
    embeddings = FakeEmbeddings(dim=6)
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk("quarterly revenue figures for the northern region", order=0),
            _parsed_chunk("cafeteria menu changes for next month", order=1),
        ]
    )
    await IndexDocument(embeddings, vectors).execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    results = await RetrieveContext(embeddings, vectors).execute(
        ctx,
        space_id=None,
        query="quarterly revenue figures for the northern region",
        model="m",
        api_key="k",
        k=1,
    )

    assert len(results) == 1
    assert results[0].document_id == "doc-1"
    assert results[0].text == "quarterly revenue figures for the northern region"
    assert results[0].chunk_id == chunk_point_id("doc-1", 0)


async def test_retrieve_context_both_legs_called_with_workspace_filter() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["alpha beta gamma report content"])

    await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query="alpha report", model="m", api_key="k"
    )

    collection = knowledge_collection("ws1")
    assert vectors.search_calls[-1][0] == collection
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws1"}
    assert vectors.search_sparse_calls[-1][0] == collection
    assert vectors.search_sparse_calls[-1][2] == {"workspace_id": "ws1"}


async def test_retrieve_context_query_embed_call_is_exactly_the_query() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["alpha beta gamma report content"])

    await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query="alpha report", model="m", api_key="k"
    )

    assert embeddings.calls == [["alpha report"]]


async def test_retrieve_context_rrf_fuses_both_legs() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(
        vectors,
        ctx,
        "doc-1",
        [
            "quarterly revenue figures for the northern region",
            "unrelated onboarding checklist for new staff members",
        ],
    )

    results = await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query="revenue figures quarterly", model="m", api_key="k", k=5
    )

    assert results[0].chunk_id == chunk_point_id("doc-1", 0)


async def test_retrieve_context_lexical_only_recall_surfaces_via_sparse_leg() -> None:
    """The core hybrid justification: a chunk whose dense vector is FARTHER
    from the query than a competing chunk, but which shares a rare lexical
    query term, still wins the fused top-1 -- purely through the sparse leg.
    A dense-only retriever would rank it last and never surface it."""
    query = "find the ZX9000QRS calibration procedure"
    query_vector = [1.0, 0.0, 0.0, 0.0]
    near_text = "completely unrelated onboarding checklist for new employees"
    near_vector = [1.0, 0.0, 0.0, 0.0]  # identical to the query -- best possible cosine
    far_text = "refer to the ZX9000QRS calibration steps in appendix B"
    far_vector = [-1.0, 0.0, 0.0, 0.0]  # opposite the query -- worst possible cosine

    embeddings = FakeEmbeddings(
        dim=4, overrides={query: query_vector, near_text: near_vector, far_text: far_vector}
    )
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    collection = knowledge_collection(ctx.workspace_id)
    await vectors.upsert(
        collection,
        [
            _hand_built_point(ctx, "doc-1", 0, near_text, near_vector),
            _hand_built_point(ctx, "doc-1", 1, far_text, far_vector),
        ],
    )

    results = await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query=query, model="m", api_key="k", k=1
    )

    assert len(results) == 1
    assert results[0].chunk_id == chunk_point_id("doc-1", 1)  # far_text -- the sparse-rescued chunk
    assert "ZX9000QRS" in results[0].text


async def test_retrieve_context_clamps_k_below_minimum_up_to_one() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=0
    )

    # k clamped up to >=1: search_k = clamped_k * _SEARCH_OVERFETCH == 1 * 3 == 3
    assert vectors.search_calls[-1][1] == 3
    assert vectors.search_sparse_calls[-1][1] == 3


async def test_retrieve_context_clamps_k_above_maximum() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=1000
    )

    # k clamped down to <=50: search_k = min(50 * _SEARCH_OVERFETCH, _MAX_SEARCH) == 100
    assert vectors.search_calls[-1][1] == 100
    assert vectors.search_sparse_calls[-1][1] == 100


async def test_retrieve_context_empty_query_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        await RetrieveContext(FakeEmbeddings(), FakeHybridVectors()).execute(
            _ctx(), space_id=None, query="   ", model="m", api_key="k"
        )


async def test_retrieve_context_empty_corpus_returns_empty_list() -> None:
    results = await RetrieveContext(FakeEmbeddings(), FakeHybridVectors()).execute(
        _ctx(), space_id=None, query="anything at all", model="m", api_key="k"
    )
    assert results == []


async def test_retrieve_context_tenant_isolation_on_both_legs() -> None:
    """DD-04 defence-in-depth: even if two tenants' points ended up in the
    SAME Qdrant collection (deliberately forced here, to isolate what the
    `flt` layer alone accomplishes), workspace B's retrieval must still see
    zero workspace A chunks -- on BOTH the dense and the BM25-sparse leg."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()  # one store, shared by both workspaces
    ctx_a = _ctx("ws-a")
    ctx_b = _ctx("ws-b")
    shared_collection = knowledge_collection("ws-b")
    shared_text = "alpha beta gamma report content"
    shared_vector = _seeded_vector(shared_text, 8)

    await vectors.upsert(
        shared_collection,
        [
            _hand_built_point(ctx_a, "doc-a", 0, shared_text, shared_vector),
            _hand_built_point(ctx_b, "doc-b", 0, shared_text, shared_vector),
        ],
    )

    results = await RetrieveContext(embeddings, vectors).execute(
        ctx_b, space_id=None, query=shared_text, model="m", api_key="k"
    )

    assert len(results) == 1
    assert results[0].chunk_id == chunk_point_id("doc-b", 0)
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws-b"}
    assert vectors.search_sparse_calls[-1][2] == {"workspace_id": "ws-b"}
