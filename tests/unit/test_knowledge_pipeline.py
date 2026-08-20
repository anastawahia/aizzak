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
import inspect
import json
import logging
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
from app.modules.knowledge.application.indexing import (
    IndexDocument,
    IndexOutcome,
    _table_to_segments,
)
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.domain.chunking import (
    MIN_NODE_CHARS,
    SPLIT_OVERLAP_RATIO,
    SourceSegment,
    chunk_segments,
    max_words_for_token_limit,
    semantic_boundaries,
)
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
    words = [f"word{i}" for i in range(10)]  # 5-char tokens -- windows clear MIN_NODE_CHARS (15)
    text = " ".join(words)
    chunks = chunk_segments([_segment(text, 0)], max_tokens=4, overlap_tokens=2, min_chars=0)
    assert [c.text for c in chunks] == [
        "word0 word1 word2 word3",
        "word2 word3 word4 word5",
        "word4 word5 word6 word7",
        "word6 word7 word8 word9",
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
    seg1 = _segment("one one one one", 0, kind="a")
    seg2 = _segment("two two two two", 0, kind="b")
    chunks = chunk_segments([seg1, seg2], max_tokens=10, overlap_tokens=0, min_chars=0)
    assert [c.kind for c in chunks] == ["a", "b"]


def test_chunk_segments_merges_short_trailing_window_into_previous() -> None:
    text = "aa bb cc dd ee ff"
    chunks = chunk_segments([_segment(text, 0)], max_tokens=5, overlap_tokens=1, min_chars=10)
    # naive windows: [aa,bb,cc,dd,ee] then [ee,ff] ("ee ff" is 5 chars, < min_chars=10) -> merged
    assert len(chunks) == 1
    assert chunks[0].text == "aa bb cc dd ee ee ff"
    assert chunks[0].token_count == 7


def test_chunk_segments_single_window_segment_is_kept_even_if_shorter_than_min_chars() -> None:
    """The MERGE parameter (``min_chars``) never touches a lone, unsplit
    segment -- distinct from the P-15 ``MIN_NODE_CHARS`` FILTER below, which
    this segment's text (19 chars) still comfortably clears."""
    chunks = chunk_segments(
        [_segment("a lone segment here", 0)], max_tokens=512, overlap_tokens=64, min_chars=100
    )
    assert len(chunks) == 1
    assert chunks[0].text == "a lone segment here"


def test_chunk_segments_propagates_kind_and_metadata_per_chunk() -> None:
    meta = {"page_number": 3}
    chunks = chunk_segments(
        [_segment("hello world today", 0, kind="table", metadata=meta)], max_tokens=10
    )
    assert chunks[0].kind == "table"
    assert chunks[0].metadata == {"page_number": 3}
    assert chunks[0].metadata is not meta  # shallow-copied per chunk, not aliased


# --------------------------------------------------------------------------- #
# chunking.chunk_segments -- P-15 node filtering (plan §4 step 8)             #
# --------------------------------------------------------------------------- #
def test_chunk_segments_drops_nodes_shorter_than_min_node_chars() -> None:
    # "hi" is 2 chars, well under MIN_NODE_CHARS (15) -- dropped even though
    # it is the segment's only window (nothing to merge it into).
    assert chunk_segments([_segment("hi", 0)]) == []


def test_chunk_segments_keeps_a_node_at_exactly_min_node_chars() -> None:
    text = "x" * MIN_NODE_CHARS
    chunks = chunk_segments([_segment(text, 0)])
    assert [c.text for c in chunks] == [text]


def test_chunk_segments_drops_duplicate_node_text_keeping_first_occurrence() -> None:
    seg1 = _segment("identical repeated boilerplate text", 0, kind="a")
    seg2 = _segment("identical repeated boilerplate text", 1, kind="b")
    chunks = chunk_segments([seg1, seg2], max_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].kind == "a"  # first occurrence survives, the later one is dropped


def test_chunk_segments_dedup_is_scoped_across_segment_boundaries() -> None:
    """Duplicate detection is NOT per-segment -- a repeated boilerplate line
    (a header/footer/disclaimer) recurring across two DIFFERENT segments is
    still caught."""
    seg1 = _segment("page footer disclaimer text repeated everywhere", 0)
    seg2 = _segment("completely unrelated other paragraph content here", 1)
    seg3 = _segment("page footer disclaimer text repeated everywhere", 2)
    chunks = chunk_segments([seg1, seg2, seg3], max_tokens=100)
    assert len(chunks) == 2


def test_chunk_segments_seq_stays_gap_free_after_filtering_drops_some_nodes() -> None:
    segments = [
        _segment("hi", 0),  # dropped: shorter than MIN_NODE_CHARS
        _segment("a genuinely long enough segment of text", 1),
        _segment("identical repeated boilerplate text here", 2),
        _segment("identical repeated boilerplate text here", 3),  # dropped: duplicate
    ]
    chunks = chunk_segments(segments, max_tokens=100)
    assert [c.seq for c in chunks] == [0, 1]


# --------------------------------------------------------------------------- #
# chunking.max_words_for_token_limit (P-16, plan §4 step 9, §3.5 + س-11)      #
# --------------------------------------------------------------------------- #
def test_max_words_for_token_limit_matches_the_ported_alpha_formula_at_the_default() -> None:
    # max(int((512 / 1.3) * 0.9), 32) == max(int(354.4615...), 32) == 354
    assert max_words_for_token_limit(512) == 354


def test_max_words_for_token_limit_scales_with_the_configured_ceiling() -> None:
    assert max_words_for_token_limit(128) == max(int((128 / 1.3) * 0.9), 32)
    assert max_words_for_token_limit(8192) == max(int((8192 / 1.3) * 0.9), 32)


def test_max_words_for_token_limit_never_drops_below_the_floor() -> None:
    # A pathologically small ceiling still yields at least MIN_MAX_WORDS (32).
    assert max_words_for_token_limit(1) == 32
    assert max_words_for_token_limit(0) == 32


def test_max_words_for_token_limit_is_pure_and_deterministic() -> None:
    assert max_words_for_token_limit(512) == max_words_for_token_limit(512)


def test_split_overlap_ratio_is_ten_percent() -> None:
    # P-16's other half (plan §3.5's trailing comment): a 10% overlap when a
    # node is split for length, applied to `max_words_for_token_limit`'s OWN
    # result -- not to `embedding_max_input_tokens` itself.
    assert SPLIT_OVERLAP_RATIO == 0.1
    max_words = max_words_for_token_limit(512)
    assert int(max_words * SPLIT_OVERLAP_RATIO) == 35


# --------------------------------------------------------------------------- #
# chunking.semantic_boundaries (P-20, plan §4 step 13, §3.4)                  #
#                                                                              #
# Pure math over SYNTHETIC vectors -- no EmbeddingProvider, no network, which #
# is the stated point of splitting the algorithm out of the application's    #
# I/O half (§3.4's own words: "the unit test works with no network").        #
# --------------------------------------------------------------------------- #
def test_semantic_boundaries_fewer_than_two_vectors_returns_empty() -> None:
    assert semantic_boundaries([]) == []
    assert semantic_boundaries([[1.0, 0.0]]) == []


def test_semantic_boundaries_identical_vectors_find_no_break() -> None:
    # Every consecutive distance is 0 -- the 95th-percentile threshold is
    # also 0, and the ">" comparison (never ">=") means nothing clears it.
    vectors = [[1.0, 0.0]] * 6
    assert semantic_boundaries(vectors) == []


def test_semantic_boundaries_splits_at_the_one_real_topic_shift_buffer_zero() -> None:
    """Two clean clusters -- three sentences near (1, 0), three near (0, 1) --
    with ``buffer=0`` (raw, unsmoothed pairwise distance) so the algorithm is
    hand-verifiable: distances are [0, 0, 1, 0, 0], the 95th percentile of
    that distribution sits at 0.8, and only the single 1.0 clears it -- a
    break BEFORE sentence index 3 (the first sentence of the second
    cluster)."""
    vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]
    assert semantic_boundaries(vectors, buffer=0, breakpoint_percentile=95) == [3]


def test_semantic_boundaries_splits_at_the_one_real_topic_shift_default_buffer() -> None:
    """Same two-cluster input, but at the DEFAULT ``buffer=1`` (calibration
    carried verbatim from alpha, plan §3.4): neighbour-averaging smooths
    every distance, but the percentile threshold is relative to the smoothed
    distribution too, so the single genuine transition (between sentence 2
    and sentence 3) still clears it and nothing else does."""
    vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]
    assert semantic_boundaries(vectors) == [3]


def test_semantic_boundaries_finds_every_genuine_shift_in_three_clusters() -> None:
    """Three clean clusters -- ``buffer=0``'s raw distances are
    ``[0, 0, 1, 0, 0, 1, 0, 0]`` (two real transitions, both distance 1.0).
    ``breakpoint_percentile=80`` (not the default 95) is used deliberately:
    with two EQUAL maxima, the 95th percentile of this exact synthetic
    distribution lands exactly ON 1.0 by linear interpolation, and the
    ">" comparison never admits a distance equal to its own threshold --
    an artefact of two tied maxima that real embeddings essentially never
    produce, not a property of the algorithm worth pinning to the default
    calibration here."""
    vectors = [
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
    ]
    boundaries = semantic_boundaries(vectors, buffer=0, breakpoint_percentile=80)
    assert boundaries == [3, 6]


def test_semantic_boundaries_zero_vector_is_maximally_distant_not_a_crash() -> None:
    # A degenerate all-zero embedding must never raise a division-by-zero;
    # it is simply treated as maximally distant from its neighbours.
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    boundaries = semantic_boundaries(vectors, buffer=0)
    assert all(isinstance(b, int) for b in boundaries)


def test_semantic_boundaries_is_pure_and_deterministic() -> None:
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    assert semantic_boundaries(vectors) == semantic_boundaries(vectors)


def test_semantic_boundaries_default_calibration_matches_the_ported_alpha_values() -> None:
    # س-06's carried-verbatim calibration (plan §3.4): buffer=1,
    # breakpoint_percentile=95 -- proof the DEFAULTS are these, not just
    # that callers can pass them explicitly.
    sig = inspect.signature(semantic_boundaries)
    assert sig.parameters["buffer"].default == 1
    assert sig.parameters["breakpoint_percentile"].default == 95


# --------------------------------------------------------------------------- #
# chunking's P-17 multi-signal ordering key (plan §4 step 10)                 #
#                                                                              #
# Metadata shapes below are copied verbatim from each real producer's own     #
# code (not invented): pdf_text.py::_build_metadata, pdf_tables.py::          #
# _build_metadata, image_ocr.py::_pdf_groups/parse_office_images, docx.py::   #
# _text_metadata/_table_to_chunk, excel.py::_table_to_chunk. This is what     #
# lets these tests pin CROSS-PRODUCER ordering, not just chunk_segments'      #
# own internal windowing mechanics (already covered above).                  #
# --------------------------------------------------------------------------- #
def _pdf_block(
    text: str, *, page_number: int, position_in_doc: int, chunk_index: int, order: int
) -> SourceSegment:
    """Mirrors ``pdf_text.py::_build_metadata``."""
    return _segment(
        text,
        order,
        kind="text",
        metadata={
            "page_number": page_number,
            "position_in_doc": position_in_doc,
            "chunk_index": chunk_index,
            "section_type": "paragraph",
        },
    )


def _pdf_table(text: str, *, page_number: int, order: int) -> SourceSegment:
    """Mirrors ``pdf_tables.py::_build_metadata`` -- carries `page_number`
    but deliberately NEVER `position_in_doc`/`chunk_index` (a table is one
    coarse region, not a positioned paragraph)."""
    return _segment(
        text, order, kind="table", metadata={"page_number": page_number, "table_index": 0}
    )


def _pdf_ocr_group(
    text: str, *, page_number: int, position_in_doc: int, order: int
) -> SourceSegment:
    """Mirrors ``image_ocr.py::_pdf_groups`` -- carries `page_number` AND
    `position_in_doc`, but never `chunk_index`."""
    return _segment(
        text,
        order,
        kind="ocr",
        metadata={
            "page_number": page_number,
            "position_in_doc": position_in_doc,
            "section_type": "page_merged_images",
        },
    )


def _docx_paragraph(text: str, *, paragraph_number: int, position_in_doc: int) -> SourceSegment:
    """Mirrors ``docx.py::_text_metadata`` -- no `page_number` at all."""
    return _segment(
        text,
        position_in_doc * 1000,
        kind="text",
        metadata={"paragraph_number": paragraph_number, "position_in_doc": position_in_doc},
    )


def _docx_table(text: str, *, position_in_doc: int) -> SourceSegment:
    """Mirrors ``docx.py::_table_to_chunk`` -- `position_in_doc` but no
    `paragraph_number`, no `page_number`."""
    return _segment(
        text, position_in_doc * 1000, kind="table", metadata={"position_in_doc": position_in_doc}
    )


def _excel_table(text: str, *, sheet_index: int, segment_index: int) -> SourceSegment:
    """Mirrors ``excel.py::_table_to_chunk`` -- none of the four ordering
    columns at all; only `order` (``sheet_index * 100_000 + segment_index``)."""
    return _segment(
        text,
        sheet_index * 100_000 + segment_index,
        kind="table",
        metadata={"sheet_number": sheet_index + 1, "sheet_name": f"Sheet{sheet_index + 1}"},
    )


def _archive_ocr_group(text: str) -> SourceSegment:
    """Mirrors ``image_ocr.py::parse_office_images`` -- a `.docx`/`.xlsx`
    archive's whole-file media group: no ordering columns, and a fixed huge
    `order` (`_MEDIA_ORDER = 1_000_000_000`) so it always trails."""
    return _segment(text, 1_000_000_000, kind="ocr", metadata={"chunk_type": "docx_media_images"})


def test_ordering_key_sorts_pdf_blocks_by_page_then_position_in_doc() -> None:
    """Two pages of ordinary PDF text blocks, submitted out of order."""
    p2b0 = _pdf_block(
        "page two first block text", page_number=2, position_in_doc=3, chunk_index=3, order=1000
    )
    p1b1 = _pdf_block(
        "page one second block text", page_number=1, position_in_doc=1, chunk_index=1, order=1
    )
    p1b0 = _pdf_block(
        "page one first block text", page_number=1, position_in_doc=0, chunk_index=0, order=0
    )
    chunks = chunk_segments([p2b0, p1b1, p1b0], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.text for c in chunks] == [
        "page one first block text",
        "page one second block text",
        "page two first block text",
    ]


def test_ordering_key_resolves_the_pdf_table_vs_block_rank_tie_via_real_position() -> None:
    """§6 risk 8 / §7's own note: a table and a block can land on the exact
    same structural `order` (`page * 1000 + index`) on the same page, because
    `pdf_tables.py` and `pdf_text.py` count independently. The ordering key
    resolves it via "NULLS LAST" at `position_in_doc` (module docstring): the
    block carries a real value, the table carries none, so the block --
    which has more specific positional information -- sorts first. This is a
    deliberate refinement of the OLD tie-break (insertion/phase order,
    "tables first") that §7 explicitly hands to this key ("the geometric
    ordering within the page is step 10's business"), not a reproduction of
    it -- and it holds even though `page_number` itself ties."""
    block = _pdf_block(
        "tied block content text", page_number=3, position_in_doc=9, chunk_index=2, order=2005
    )
    table = _pdf_table("tied table content text", page_number=3, order=2005)  # SAME structural rank
    assert block.order == table.order  # the documented tie

    chunks = chunk_segments([block, table], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.text for c in chunks] == ["tied block content text", "tied table content text"]

    # Order of the INPUT list must not matter -- it is a real signal, not the
    # old insertion-order tie-break this key replaces.
    reversed_chunks = chunk_segments([table, block], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.text for c in reversed_chunks] == [
        "tied block content text",
        "tied table content text",
    ]


def test_ordering_key_a_pdf_page_images_group_outranks_that_pages_table() -> None:
    """A known, documented limitation (module docstring's "Known
    limitation" paragraph), pinned rather than left implicit: `pdf_tables.py`
    chunks NEVER carry `position_in_doc`, while `image_ocr.py`'s per-page OCR
    group always does (`_pdf_groups`) -- so on the same page, the OCR group's
    real (if page-index-scaled, not document-counter-scaled) value beats the
    table's absence at that column every time, regardless of its magnitude.
    This is the OPPOSITE of `_PAGE_OCR_ORDER_INDEX=999`'s pre-step-10 intent
    (images last via raw `order`); fixing it needs a genuinely comparable
    `position_in_doc` for OCR groups in `image_ocr.py`, out of this step's
    (`domain/chunking.py`-only) scope."""
    table = _pdf_table("table content on page four", page_number=4, order=3000)
    ocr = _pdf_ocr_group(
        "images content on page four", page_number=4, position_in_doc=30, order=3999
    )
    chunks = chunk_segments([ocr, table], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.text for c in chunks] == ["images content on page four", "table content on page four"]


def test_ordering_key_sorts_docx_paragraphs_and_tables_by_position_in_doc() -> None:
    """DOCX carries no `page_number` at all; `position_in_doc` alone (present
    on BOTH paragraphs and tables, unlike PDF's table/block asymmetry) fully
    orders a mixed paragraph/table document."""
    table = _docx_table("the docx table content", position_in_doc=2)
    para1 = _docx_paragraph("first docx paragraph text", paragraph_number=0, position_in_doc=0)
    para2 = _docx_paragraph("second docx paragraph text", paragraph_number=1, position_in_doc=1)
    chunks = chunk_segments([table, para2, para1], max_tokens=100, overlap_tokens=0, min_chars=0)
    assert [c.text for c in chunks] == [
        "first docx paragraph text",
        "second docx paragraph text",
        "the docx table content",
    ]


def test_ordering_key_sorts_excel_tables_by_their_structural_order_alone() -> None:
    """No PDF/DOCX signal ever appears in Excel metadata -- every one of the
    four columns ties (absent on both sides), so ordering falls all the way
    through to `segment.order` (`sheet_index * 100_000 + segment_index`)."""
    sheet2 = _excel_table("sheet two, block zero content", sheet_index=1, segment_index=0)
    sheet1_b1 = _excel_table("sheet one, block one content", sheet_index=0, segment_index=1)
    sheet1_b0 = _excel_table("sheet one, block zero content", sheet_index=0, segment_index=0)
    chunks = chunk_segments(
        [sheet2, sheet1_b1, sheet1_b0], max_tokens=100, overlap_tokens=0, min_chars=0
    )
    assert [c.text for c in chunks] == [
        "sheet one, block zero content",
        "sheet one, block one content",
        "sheet two, block zero content",
    ]


def test_ordering_key_sorts_an_archived_ocr_group_last_of_all() -> None:
    """A `.docx`/`.xlsx` archive's whole-file media group carries none of
    the four columns AND the fixed trailing `_MEDIA_ORDER`, so it sorts after
    every positioned DOCX segment regardless of input order."""
    archive_images = _archive_ocr_group("embedded picture descriptions")
    para = _docx_paragraph("some body text content", paragraph_number=0, position_in_doc=0)
    table = _docx_table("a small docx table", position_in_doc=1)
    chunks = chunk_segments(
        [archive_images, table, para], max_tokens=100, overlap_tokens=0, min_chars=0
    )
    assert [c.text for c in chunks] == [
        "some body text content",
        "a small docx table",
        "embedded picture descriptions",
    ]


def test_ordering_key_is_stamped_after_the_length_split_not_before() -> None:
    """P-17's own requirement (plan §4 step 10): the split-part index is the
    LAST column, so a segment split by P-16's length window keeps its parts
    adjacent and in split order, even sandwiched between two OTHER segments
    that would otherwise interleave by structural rank alone."""
    long_block = _pdf_block(
        " ".join(f"word{i}" for i in range(12)),
        page_number=1,
        position_in_doc=1,
        chunk_index=1,
        order=1,
    )
    before = _pdf_block(
        "first block text content", page_number=1, position_in_doc=0, chunk_index=0, order=0
    )
    after = _pdf_block(
        "last block text content", page_number=1, position_in_doc=2, chunk_index=2, order=2
    )
    chunks = chunk_segments(
        [after, long_block, before], max_tokens=5, overlap_tokens=1, min_chars=0
    )
    assert [c.text for c in chunks] == [
        "first block text content",
        "word0 word1 word2 word3 word4",
        "word4 word5 word6 word7 word8",
        "word8 word9 word10 word11",
        "last block text content",
    ]


def test_ordering_key_full_cross_producer_document_scrambled_input() -> None:
    """One synthetic document mixing every current producer's metadata shape
    (module comment), submitted in a deliberately scrambled order, must come
    back out fully re-ordered by `page_number` first (`p2_block` last, the
    only page-2 item) and then, within page 1, by `position_in_doc` --
    columns 1-4 alone fully decide this document (`segment.order`, the
    would-be tie-breaker for `p1_table`/`p1_block_a`'s identical structural
    rank, is never even reached): every page-1 item that CARRIES
    `position_in_doc` (both blocks, and the OCR group) sorts by that value
    ascending, and `p1_table` -- which never carries it -- sorts after all
    of them ("NULLS LAST", module docstring)."""
    p1_table = _pdf_table("p1 table content text", page_number=1, order=0)  # tied w/ p1_block_a
    p1_block_a = _pdf_block(
        "p1 block a content text", page_number=1, position_in_doc=0, chunk_index=0, order=0
    )
    p1_block_b = _pdf_block(
        "p1 block b content text", page_number=1, position_in_doc=1, chunk_index=1, order=1
    )
    p1_ocr = _pdf_ocr_group(
        "p1 ocr images content text", page_number=1, position_in_doc=2, order=999
    )
    p2_block = _pdf_block(
        "p2 block content text", page_number=2, position_in_doc=3, chunk_index=2, order=1000
    )

    scrambled = [p2_block, p1_ocr, p1_block_b, p1_table, p1_block_a]
    chunks = chunk_segments(scrambled, max_tokens=100, overlap_tokens=0, min_chars=0)

    assert [c.text for c in chunks] == [
        "p1 block a content text",
        "p1 block b content text",
        "p1 ocr images content text",
        "p1 table content text",
        "p2 block content text",
    ]


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


async def test_index_document_splits_at_the_real_token_derived_word_budget() -> None:
    """P-16 (plan §4 step 9): the default constructor arg
    (``embedding_max_input_tokens=512``) must actually drive the split --
    354 words/35-word overlap (``max_words_for_token_limit(512)``), NOT the
    old bare 512-word/64-word ``chunk_segments`` default this use-case used
    to fall back to. A 400-word segment sits strictly between 354 and 512:
    it only splits at all under the wired-through formula."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)  # default embedding_max_input_tokens=512
    ctx = _ctx("ws1")
    text = " ".join(f"word{i}" for i in range(400))
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 2
    by_seq = {c.seq: c for c in outcome.chunks}
    assert by_seq[0].token_count == 354
    assert by_seq[1].token_count == 81  # 400 - (354 - 35) step


async def test_index_document_embedding_max_input_tokens_is_configurable() -> None:
    """A smaller configured ceiling produces a smaller, differently-overlapped
    split -- proof the value flows through, not just the default."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors, embedding_max_input_tokens=40)
    ctx = _ctx("ws1")
    text = " ".join(f"word{i}" for i in range(50))
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 2
    by_seq = {c.seq: c for c in outcome.chunks}
    assert by_seq[0].token_count == 32  # MIN_MAX_WORDS floor at embedding_max_input_tokens=40
    assert by_seq[1].token_count == 21


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


async def test_index_document_falls_back_to_per_chunk_embedding_when_batch_call_fails() -> None:
    """P-19 (plan §4 step 12; alpha `_safe_parse`): a batch-level embedding
    failure (simulating one poison chunk tripping the whole HTTP call) does
    not lose the OTHER, perfectly fine chunks in that batch -- `execute`
    retries the failed batch one chunk at a time and still indexes every
    one of them."""
    attempts: list[list[str]] = []

    class _FlakyOnBatches(FakeEmbeddings):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            attempts.append(texts)
            if len(texts) > 1:
                raise RuntimeError("simulated batch failure")
            return await super().embed(texts, model, api_key)

    embeddings = _FlakyOnBatches()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk("first paragraph about revenue growth", order=0),
            _parsed_chunk("second paragraph about headcount changes", order=1),
            _parsed_chunk("third paragraph about office relocation", order=2),
        ]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 3
    assert len(vectors.points["kn-ws1"]) == 3
    # one failed batch-of-3 call, then 3 individual per-chunk retries
    assert len(attempts) == 4
    assert len(attempts[0]) == 3
    assert [len(a) for a in attempts[1:]] == [1, 1, 1]


async def test_index_document_a_chunk_that_still_fails_alone_still_raises() -> None:
    """A genuinely bad chunk is never silently dropped (plan §3.7's "no
    silent skip / no partial index" philosophy, carried into P-19): the
    whole `execute` call still raises exactly as any pipeline failure did
    before this step, so the caller (`IndexRegisteredDocument.run`) still
    fails the WHOLE document rather than leaving it `indexed` with a quietly
    smaller `chunk_count`."""

    class _AlwaysFailsOnPoison(FakeEmbeddings):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            if any("poison" in text for text in texts):
                raise RuntimeError("simulated embedding failure")
            return await super().embed(texts, model, api_key)

    embeddings = _AlwaysFailsOnPoison()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk("first paragraph about revenue growth", order=0),
            _parsed_chunk("poison paragraph that always fails to embed", order=1),
        ]
    )

    with pytest.raises(RuntimeError, match="simulated embedding failure"):
        await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )


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


async def test_index_document_payload_section_falls_back_to_title_metadata_key() -> None:
    """P-18 (plan §4 step 11, §3.9): no parser writes a literal "section"
    metadata key -- ``docx.py``'s heading breadcrumb and ``pdf_tables.py``'s
    caption both land under "title" -- so ``_payload`` falls back to it."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(
                "responsibilities paragraph text under a heading",
                order=0,
                metadata={"title": "Responsibilities"},
            )
        ]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )
    point = vectors.points["kn-ws1"][outcome.chunks[0].chunk_id]

    assert point.payload["section"] == "Responsibilities"
    assert "title" not in point.payload  # "title" itself is not in the allowlist


async def test_index_document_payload_omits_file_name_when_absent_from_metadata() -> None:
    """``file_name`` is in ``_CITATION_KEYS`` (plan §3.9) and every real
    producer now supplies it (``extractor.py``'s ``_enrich``) -- but this
    module's OWN job is only to copy whatever ``chunk.metadata`` carries,
    the same as every other citation key. This test builds a ``ParsedChunk``
    directly (bypassing the extractor entirely, as every test in this file
    does -- see the module docstring), so its metadata genuinely omits
    ``file_name``: the payload build must never crash on that, just leave
    the key absent rather than write ``None``."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk("plain paragraph text here", order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )
    point = vectors.points["kn-ws1"][outcome.chunks[0].chunk_id]

    assert "file_name" not in point.payload


# --------------------------------------------------------------------------- #
# application.indexing.IndexDocument -- P-20 semantic pre-splitting          #
# (plan §4 step 13, §3.4)                                                     #
# --------------------------------------------------------------------------- #
async def test_index_document_semantic_split_calls_embed_on_sentences_then_final_nodes() -> None:
    """A genuine two-topic segment: `execute` first calls `embed` with the
    FOUR sentences (boundary detection), then -- once `semantic_boundaries`
    finds the one real transition -- with the TWO resulting semantic-part
    node texts (the ordinary final embedding pass). The two override
    clusters below reproduce the exact 2-vector-per-cluster case verified
    directly against `semantic_boundaries` (buffer=1 default): the
    boundary lands cleanly before sentence index 2."""
    sentence1 = "Quarterly revenue climbed sharply this period"
    sentence2 = "Revenue growth exceeded every prior forecast"
    sentence3 = "The office cafeteria unveiled a new lunch menu"
    sentence4 = "Lunch service now starts thirty minutes earlier"
    overrides = {
        sentence1: [1.0, 0.0],
        sentence2: [1.0, 0.0],
        sentence3: [0.0, 1.0],
        sentence4: [0.0, 1.0],
    }
    embeddings = FakeEmbeddings(overrides=overrides)
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = f"{sentence1}. {sentence2}. {sentence3}. {sentence4}."
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(embeddings.calls) == 2
    assert embeddings.calls[0] == [sentence1, sentence2, sentence3, sentence4]
    assert embeddings.calls[1] == [f"{sentence1} {sentence2}", f"{sentence3} {sentence4}"]

    assert len(outcome.chunks) == 2
    by_seq = {c.seq: c for c in outcome.chunks}
    assert by_seq[0].text == f"{sentence1} {sentence2}"
    assert by_seq[1].text == f"{sentence3} {sentence4}"


async def test_index_document_single_sentence_segment_skips_the_semantic_embed_call() -> None:
    """P-20 constraint 3 (plan §3.4): a segment with fewer than two
    sentences is skipped gracefully -- no boundary-detection `embed` call at
    all, just the ordinary final one."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = "a single sentence with no terminal punctuation at all"
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(embeddings.calls) == 1
    assert embeddings.calls[0] == [text]
    assert len(outcome.chunks) == 1
    assert outcome.chunks[0].text == text


async def test_index_document_table_chunk_skips_semantic_split_even_with_sentence_punctuation() -> (
    None
):
    """P-20 constraint 2 (plan §3.4): TABLE-kind chunks skip semantic
    pre-splitting entirely -- keyed off `chunk.kind`, not whether the text
    actually explodes into rows -- even when the (here malformed) table text
    happens to contain sentence-ending punctuation."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = "Not valid table json. It still has two sentences anyway."
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(embeddings.calls) == 1
    assert embeddings.calls[0] == [text]
    assert len(outcome.chunks) == 1


async def test_index_document_semantic_split_splits_arabic_sentences_on_every_separator() -> None:
    """The exact separator set (plan §3.4): `.` `؟` `!` `؛`, a paragraph
    break (``\\n\\n``), and the dedicated Arabic full stop codepoint
    (U+06D4) each end one sentence -- proven by feeding one segment with all
    six back to back and asserting the boundary-detection `embed` call
    receives exactly the six resulting sentences, in reading order."""
    parts = [
        "الجملة الأولى عن الإيرادات الفصلية",
        "الجملة الثانية عن عدد الموظفين",
        "الجملة الثالثة عن مكان العمل",
        "الجملة الرابعة عن ميزانية العام",
        "الجملة الخامسة عن خطة التوسع",
        "الجملة السادسة عن نتائج الفريق",
    ]
    text = f"{parts[0]}.{parts[1]}؟{parts[2]}!{parts[3]}؛{parts[4]}\n\n{parts[5]}۔"  # noqa: RUF001
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert embeddings.calls[0] == parts


async def test_index_document_semantic_split_still_hard_caps_a_long_part_by_the_word_window() -> (
    None
):
    """P-20 constraint 1 (plan §3.4): the word window remains a HARD CAP
    after semantic boundaries -- a semantic PART longer than
    `embedding_max_input_tokens`'s derived word budget still gets split by
    the window, exactly as an ordinary (non-semantically-split) segment
    would. Two 40-word "sentences" per cluster reproduce the same clean
    boundary already verified against `semantic_boundaries` directly
    (buffer=1 default: boundary before sentence index 2), so each of the
    two resulting 80-word semantic parts comfortably exceeds
    `MIN_MAX_WORDS` (32, `embedding_max_input_tokens=1`'s floor) and MUST
    itself be split by `chunk_segments`' word window."""
    sentence1 = " ".join(f"alpha{i}" for i in range(40))
    sentence2 = " ".join(f"alpha{i}" for i in range(40, 80))
    sentence3 = " ".join(f"beta{i}" for i in range(40))
    sentence4 = " ".join(f"beta{i}" for i in range(40, 80))
    overrides = {
        sentence1: [1.0, 0.0],
        sentence2: [1.0, 0.0],
        sentence3: [0.0, 1.0],
        sentence4: [0.0, 1.0],
    }
    embeddings = FakeEmbeddings(overrides=overrides)
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors, embedding_max_input_tokens=1)
    ctx = _ctx("ws1")
    text = f"{sentence1}. {sentence2}. {sentence3}. {sentence4}."
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert all(chunk.token_count <= 32 for chunk in outcome.chunks)
    # 2 semantic parts (80 words each), each windowed into more than one
    # node -- proof the split actually happened WITHIN a semantic part, not
    # just between the two of them.
    assert len(outcome.chunks) > 2


async def test_index_document_semantic_pass_failure_degrades_to_the_unsplit_segment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P-20's sentence pass is an OPTIONAL refinement, so its failure must
    not fail the document -- that would undo, one step later, the very
    isolation P-19 added (plan §4 step 12): one embedding error landing the
    WHOLE document in `failed`. The chunk still indexes, whole and unsplit,
    and the degrade is logged rather than swallowed."""
    sentence1 = "Quarterly revenue climbed sharply this period"
    sentence2 = "The office cafeteria unveiled a new lunch menu"
    text = f"{sentence1}. {sentence2}."

    class _FailsTheSentencePass(FakeEmbeddings):
        """Fails ONLY the boundary-detection call (the sentences), and
        serves the mandatory node pass normally -- the shape of a provider
        hiccup on the one extra call P-20 introduced."""

        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            if texts == [sentence1, sentence2]:
                raise RuntimeError("simulated sentence-pass failure")
            return await super().embed(texts, model, api_key)

    embeddings = _FailsTheSentencePass()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    with caplog.at_level(logging.WARNING):
        outcome = await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )

    # Nothing lost: the ORIGINAL text (punctuation and all), as one node --
    # not the space-joined sentences a successful semantic split produces.
    assert [chunk.text for chunk in outcome.chunks] == [text]
    assert len(vectors.points["kn-ws1"]) == 1
    # only the mandatory pass ever completed
    assert embeddings.calls == [[text]]
    assert any(
        "semantic_split_failed_falling_back_to_plain_segment" in record.message
        for record in caplog.records
    )


async def test_index_document_semantic_pass_degrades_on_a_malformed_provider_response() -> None:
    """The guard spans the pure boundary math too, not just the ``await``:
    ``semantic_boundaries``' input IS the provider's response, so a
    malformed one (ragged vectors) raises INSIDE the domain from an
    I/O-shaped cause. Same degrade, same complete document."""
    sentence1 = "Quarterly revenue climbed sharply this period"
    sentence2 = "The office cafeteria unveiled a new lunch menu"
    text = f"{sentence1}. {sentence2}."

    class _RaggedOnTheSentencePass(FakeEmbeddings):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            if texts == [sentence1, sentence2]:
                return EmbeddingResult(
                    vectors=[[1.0, 0.0], [1.0]], model=model, dimensions=2, tokens=2
                )
            return await super().embed(texts, model, api_key)

    embeddings = _RaggedOnTheSentencePass()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert [chunk.text for chunk in outcome.chunks] == [text]


async def test_index_document_semantic_degrade_does_not_hide_a_provider_that_is_down() -> None:
    """The degrade must never turn a genuine outage into a quiet success
    (plan §3.7: no silent skip / no partial index): the MANDATORY node pass
    calls the SAME provider, and P-19 re-raises once a lone chunk still
    fails -- so `IndexRegisteredDocument.run` still fails the document."""

    class _AlwaysFails(FakeEmbeddings):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            raise RuntimeError("provider is down")

    embeddings = _AlwaysFails()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = "Quarterly revenue climbed sharply. The cafeteria unveiled a new menu."
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    with pytest.raises(RuntimeError, match="provider is down"):
        await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )


def _two_topic_sentences(prefix: str) -> list[str]:
    """Four sentences, two per topic -- the exact 2-vectors-per-cluster
    shape already verified directly against `semantic_boundaries`
    (buffer=1 default: the boundary lands cleanly before sentence index
    2), prefixed so several chunks of them stay distinct texts."""
    return [
        f"{prefix} quarterly revenue climbed sharply this period",
        f"{prefix} revenue growth exceeded every prior forecast",
        f"{prefix} the office cafeteria unveiled a new lunch menu",
        f"{prefix} lunch service now starts thirty minutes earlier",
    ]


def _two_topic_overrides(sentences: Sequence[str]) -> dict[str, list[float]]:
    return {
        sentences[0]: [1.0, 0.0],
        sentences[1]: [1.0, 0.0],
        sentences[2]: [0.0, 1.0],
        sentences[3]: [0.0, 1.0],
    }


def _sentences_to_text(sentences: Sequence[str]) -> str:
    return ". ".join(sentences) + "."


def _two_topic_parts(sentences: Sequence[str]) -> list[str]:
    return [f"{sentences[0]} {sentences[1]}", f"{sentences[2]} {sentences[3]}"]


async def test_index_document_semantic_pass_batches_every_chunks_sentences_into_one_call() -> None:
    """P-20's declared cost (plan §3.4 constraint 3) is ONE extra embedding
    pass over the DOCUMENT's sentences -- not one sequential provider round
    trip per `ParsedChunk`. Three eligible chunks therefore produce exactly
    two calls: the batched sentence call carrying all twelve sentences in
    reading order, then the ordinary node pass. Every chunk still gets its
    OWN boundary math -- the six node texts below are only produced if the
    boundaries were computed per chunk over its own four vectors, never
    across the concatenated batch."""
    groups = [_two_topic_sentences(prefix) for prefix in ("alpha", "bravo", "charlie")]
    overrides = {
        text: vector
        for sentences in groups
        for text, vector in _two_topic_overrides(sentences).items()
    }
    embeddings = FakeEmbeddings(overrides=overrides)
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(_sentences_to_text(sentences), order=order)
            for order, sentences in enumerate(groups)
        ]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    expected_parts = [part for sentences in groups for part in _two_topic_parts(sentences)]
    assert len(embeddings.calls) == 2
    assert embeddings.calls[0] == [sentence for sentences in groups for sentence in sentences]
    assert embeddings.calls[1] == expected_parts
    assert [chunk.text for chunk in outcome.chunks] == expected_parts


async def test_index_document_sentence_batches_respect_the_embed_batch_cap() -> None:
    """The batched pass honours the SAME 128-text cap the mandatory node
    pass uses (`_EMBED_BATCH`), which the per-chunk path ignored entirely:
    100 two-sentence chunks = 200 sentences packed as whole chunks into
    exactly two calls of 128 and 72 -- never one 200-text call, and never
    100 separate ones."""
    chunk_texts = [
        f"Sentence one of block {index} carries enough words to survive. "
        f"Sentence two of block {index} carries enough words as well."
        for index in range(100)
    ]
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [_parsed_chunk(text, order=order) for order, text in enumerate(chunk_texts)]
    )

    await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert [len(call) for call in embeddings.calls[:2]] == [128, 72]
    assert all(len(call) <= 128 for call in embeddings.calls)


async def test_index_document_a_chunk_longer_than_the_cap_is_served_by_several_calls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A chunk is never spread across two GROUPS (boundary detection needs
    all of its sentence vectors together), so the one chunk holding more
    sentences than the cap is the case served by several cap-sized calls
    whose vectors concatenate -- in order, completely, and without
    degrading."""
    sentences = [f"Block sentence number {index} about its own small topic" for index in range(200)]
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk(_sentences_to_text(sentences), order=0)])

    with caplog.at_level(logging.WARNING):
        outcome = await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )

    assert len(embeddings.calls[0]) == 128
    # the two calls together are the chunk's sentences, whole and in order
    assert embeddings.calls[0] + embeddings.calls[1] == sentences
    assert outcome.chunks
    # concatenation actually worked: a lost/short response would have
    # tripped the vector-count guard and degraded instead
    assert not [
        record
        for record in caplog.records
        if "semantic_split_failed_falling_back_to_plain_segment" in record.message
    ]


async def test_index_document_semantic_batch_failure_degrades_only_the_failing_chunk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Batching must not cost P-19's per-chunk isolation. When the batched
    sentence call fails, the pass narrows to ONE CHUNK PER CALL exactly as
    `execute` does for nodes, so the damage is contained to the chunk that
    still fails alone: it keeps its whole, unsplit text while its
    neighbours in the same failed batch still get their semantic split."""
    groups = [_two_topic_sentences(prefix) for prefix in ("alpha", "bravo", "charlie")]
    overrides = {
        text: vector
        for sentences in groups
        for text, vector in _two_topic_overrides(sentences).items()
    }
    poison = groups[1][0]

    class _FailsAnyCallTouchingBravo(FakeEmbeddings):
        """Fails the batched call AND bravo's own narrowed retry -- but not
        alpha's or charlie's, and not the mandatory node pass."""

        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            if poison in texts:
                raise RuntimeError("simulated sentence-pass failure")
            return await super().embed(texts, model, api_key)

    embeddings = _FailsAnyCallTouchingBravo(overrides=overrides)
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(_sentences_to_text(sentences), order=order)
            for order, sentences in enumerate(groups)
        ]
    )

    with caplog.at_level(logging.WARNING):
        outcome = await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )

    assert [chunk.text for chunk in outcome.chunks] == [
        *_two_topic_parts(groups[0]),
        _sentences_to_text(groups[1]),
        *_two_topic_parts(groups[2]),
    ]
    # the batch raised, then one call per chunk (bravo's raised again), then
    # the mandatory node pass -- never a call per chunk in the happy path
    assert len(embeddings.calls) == 3
    assert embeddings.calls[0] == groups[0]
    assert embeddings.calls[1] == groups[2]
    messages = [record.message for record in caplog.records]
    assert any("semantic_batch_failed_retrying_per_chunk" in message for message in messages)
    assert any(
        "semantic_split_failed_falling_back_to_plain_segment" in message for message in messages
    )


async def test_index_document_semantic_pass_degrades_when_the_provider_returns_too_few_vectors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Batching introduced offset arithmetic that a SHORT response would
    corrupt silently -- one chunk's sentences grouped against the next
    chunk's vectors. A response whose vector count does not match the texts
    it answers is refused outright, and refusal takes the ordinary degrade
    path (plan §3.4 constraint 4), never a wrong split."""
    sentences = _two_topic_sentences("alpha")

    class _DropsOneVector(FakeEmbeddings):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            texts = list(texts)
            result = await super().embed(texts, model, api_key)
            if texts == sentences:
                return EmbeddingResult(
                    vectors=result.vectors[:-1],
                    model=result.model,
                    dimensions=result.dimensions,
                    tokens=result.tokens,
                )
            return result

    embeddings = _DropsOneVector(overrides=_two_topic_overrides(sentences))
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = _sentences_to_text(sentences)
    parsed = _parsed_document([_parsed_chunk(text, order=0)])

    with caplog.at_level(logging.WARNING):
        outcome = await use_case.execute(
            ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
        )

    assert [chunk.text for chunk in outcome.chunks] == [text]
    assert any(
        "semantic_split_failed_falling_back_to_plain_segment" in record.message
        for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# application.indexing.IndexDocument -- P-13 table row explosion (§3.3)       #
# --------------------------------------------------------------------------- #
def _table_json(headers: list[str], rows: list[dict[str, object]]) -> str:
    return json.dumps({"headers": headers, "rows": rows})


async def test_index_document_explodes_table_rows_into_one_node_per_row() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = _table_json(
        ["Name", "Salary"],
        [{"Name": "Ahmad", "Salary": "5000"}, {"Name": "Sara", "Salary": "6000"}],
    )
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 2
    texts = {chunk.text for chunk in outcome.chunks}
    assert texts == {"Name: Ahmad; Salary: 5000", "Name: Sara; Salary: 6000"}


async def test_index_document_small_table_parent_is_full_table_text() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    text = _table_json(
        ["Name", "Salary"],
        [{"Name": "Ahmad", "Salary": "5000"}, {"Name": "Sara", "Salary": "6000"}],
    )
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.parents) == 1
    assert outcome.parents[0].text == "Name: Ahmad; Salary: 5000\nName: Sara; Salary: 6000"
    # The parent really does hold both rows, so P-42 may read it in their
    # place (`domain/tables.py::collapse_parent_runs`).
    assert outcome.parents[0].is_complete is True


async def test_index_document_large_table_parent_is_header_only() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    # Two columns so every row's sentence comfortably clears MIN_NODE_CHARS
    # (P-15, plan step 8) regardless of the row index's digit width.
    rows = [{"Name": f"person-{i}", "Dept": "engineering"} for i in range(21)]
    text = _table_json(["Name", "Dept"], rows)
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 21
    assert len(outcome.parents) == 1
    assert outcome.parents[0].text == "Name; Dept"
    # ...and it is marked as NOT holding those 21 rows, which is what stops
    # `chunk_texts` from summarising this table as its column names alone.
    assert outcome.parents[0].is_complete is False


async def test_index_document_table_parent_text_and_id_never_reach_the_qdrant_payload() -> None:
    """Constraint 1 (plan §3.2), negative half only: the Qdrant payload
    carries neither a parent chunk's text nor its id (only what
    ``_CITATION_KEYS`` allowlists), and never the internal
    ``_table_parent_key`` scratch metadata either. ``IndexDocument.execute``
    (exercised here) never resolves ``parent_key`` to a minted
    ``ParentChunk.id`` in the first place -- that resolution, and the
    positive half of constraint 1 (``parent_id`` IS stored, on
    ``chunks.parent_id`` in SQL), is pinned by
    ``test_knowledge_module.py::test_index_registered_document_wires_table_parent_id_end_to_end``,
    which exercises the full ``IndexRegisteredDocument`` flow and asserts
    ``chunk.parent_id == parent.id``."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    # Two rows, two columns (each row's own sentence clears MIN_NODE_CHARS,
    # P-15/plan step 8): the parent text (both rows' sentences, newline-
    # joined) is then necessarily distinct from any single row's own node
    # text, so the assertion below cannot pass by coincidence.
    text = _table_json(
        ["Name", "City"], [{"Name": "Ahmad", "City": "Amman"}, {"Name": "Sara", "City": "Amman"}]
    )
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    parent_text = outcome.parents[0].text
    for chunk in outcome.chunks:
        point = vectors.points["kn-ws1"][chunk.chunk_id]
        assert parent_text not in point.payload.values()
        assert "_table_parent_key" not in point.payload
        assert "parent_id" not in point.payload


async def test_index_document_malformed_table_json_falls_back_to_word_window() -> None:
    """A TABLE-kind chunk whose text is not the ``{headers, rows}`` shape
    (parsers.md §7) is a defensive fallback, not a crash: it goes through the
    ordinary word-window path exactly like before this step."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [_parsed_chunk("not valid json at all", order=0, kind=ParsedChunkKind.TABLE)]
    )

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    assert len(outcome.chunks) == 1
    assert outcome.chunks[0].text == "not valid json at all"
    assert outcome.parents == ()


async def test_index_document_table_row_hard_cap_truncates_and_declares() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    use_case = IndexDocument(embeddings, vectors)
    ctx = _ctx("ws1")
    # Zero-padded so every row's sentence has a consistent, comfortably-over-
    # MIN_NODE_CHARS length regardless of the row index's digit width.
    rows = [{"Name": f"person-{i:04d}"} for i in range(2003)]  # TABLE_ROW_HARD_CAP (2000) + 3
    text = _table_json(["Name"], rows)
    parsed = _parsed_document([_parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)])

    outcome = await use_case.execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    # 2000 exploded row nodes + (at least) one overflow node for the last 3
    assert len(outcome.chunks) >= 2001
    row_texts = {chunk.text for chunk in outcome.chunks}
    assert "Name: person-0000" in row_texts
    assert "Name: person-1999" in row_texts
    assert "Name: person-2000" not in row_texts  # rows past the cap never get their own node
    overflow_texts = [c.text for c in outcome.chunks if "person-2000" in c.text]
    assert (
        overflow_texts and "person-2001" in overflow_texts[0] and "person-2002" in overflow_texts[0]
    )
    assert len(outcome.parents) == 1
    assert outcome.parents[0].text == "Name"  # still header-only (2003 > TABLE_PARENT_MAX_ROWS)
    assert outcome.parents[0].is_complete is False


async def test_index_document_ranks_table_rows_by_sub_order_not_by_sort_stability() -> None:
    """Every row of one table shares its ``order`` and every metadata signal
    ``_order_key`` consults, so without ``sub_order`` their keys would be
    byte-identical and reading order would survive only as a side effect of
    ``list.sort`` being stable (``domain/chunking.py``'s TOTAL-order
    contract).

    Asserted by CONSTRUCTION rather than by output: the rows are handed to
    the chunker in reading order, so the emitted sequence looks the same
    either way. What this pins is that the ordering key can tell them
    apart -- feed the same segments in reversed order and the sort must put
    them back, which a partial order cannot do.
    """
    rows = [{"Name": f"person-{i:02d}", "Dept": "engineering"} for i in range(6)]
    text = _table_json(["Name", "Dept"], rows)
    chunk = _parsed_chunk(text, order=0, kind=ParsedChunkKind.TABLE)

    built = _table_to_segments(chunk, parent_key="p-1")
    assert built is not None
    segments, _parent = built
    assert [segment.sub_order for segment in segments] == [0, 1, 2, 3, 4, 5]

    shuffled = list(reversed(segments))
    restored = chunk_segments(shuffled, max_tokens=64, overlap_tokens=0)
    assert [node.text for node in restored] == [
        f"Name: person-{i:02d}; Dept: engineering" for i in range(6)
    ]


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


async def test_index_then_retrieve_round_trip_carries_citation_fields() -> None:
    """Retrieval plan §3.1/§3.9 (س-19, ``P-18``), §6 risk 1: the three
    citation fields must carry REAL values through the WHOLE pipeline --
    parser metadata -> ``indexing._payload``'s ``_CITATION_KEYS`` -> the
    Qdrant point payload -> ``RetrieveContext``'s ``_to_retrieved_chunk`` --
    not merely be present-but-``None``. That silent-``None`` shape is
    EXACTLY the failure §6 risk 1 warns about: shipping the widened contract
    before the indexing side actually writes these payload keys (closed by
    the indexing plan's own step 11, which this round trip proves end to
    end rather than trusting by inspection)."""
    embeddings = FakeEmbeddings(dim=6)
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(
                "quarterly revenue figures for the northern region",
                order=0,
                metadata={
                    "file_name": "quarterly-report.pdf",
                    "page_number": 4,
                    "section": "Regional Breakdown",
                },
            )
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
    assert results[0].file_name == "quarterly-report.pdf"
    assert results[0].page_number == 4
    assert results[0].section == "Regional Breakdown"


async def test_index_then_retrieve_round_trip_degrades_missing_citation_fields_to_none() -> None:
    """A chunk whose parser never emitted a citation key (or a point indexed
    before ``P-18``) must not crash retrieval -- each field degrades to
    ``None`` rather than a ``KeyError``, the same "unknown, not broken"
    contract as every other ``_CITATION_KEYS`` entry (mirrors
    ``test_index_document_payload_omits_file_name_when_absent_from_metadata``
    one layer up, on the read side)."""
    embeddings = FakeEmbeddings(dim=6)
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    parsed = _parsed_document([_parsed_chunk("cafeteria menu changes for next month", order=0)])
    await IndexDocument(embeddings, vectors).execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    results = await RetrieveContext(embeddings, vectors).execute(
        ctx,
        space_id=None,
        query="cafeteria menu changes for next month",
        model="m",
        api_key="k",
        k=1,
    )

    assert len(results) == 1
    assert results[0].file_name is None
    assert results[0].page_number is None
    assert results[0].section is None


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
