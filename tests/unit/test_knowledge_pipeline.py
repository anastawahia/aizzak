"""Unit tests for the knowledge module's 3.k3 hybrid (dense + BM25-sparse)
indexing/retrieval pipeline: the chunker, sparse-term builder, relevance
filter, intent classifier, and file-name resolver (all pure domain), plus the
``IndexDocument``/``RetrieveContext`` application use-cases over fake
``EmbeddingProvider``/``HybridVectorStore`` ports. Pure unit tests: no
markers, no Docker, no
optional dependencies -- ``ParsedDocument``/``ParsedChunk`` fixtures are
built directly rather than run through the (optional-dependency-gated) real
parser adapters exercised by ``test_knowledge_parsers.py``.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import logging
import math
import os
import pathlib
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.framework.agent_runtime.source_label import format_context_block, format_labeled_chunk
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.observability.logging import JsonFormatter
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.settings.settings import Limits, RetrievalSettings
from app.framework.types import Json
from app.modules.knowledge.application import retrieval as retrieval_module
from app.modules.knowledge.application.indexing import (
    IndexDocument,
    IndexOutcome,
    _table_to_segments,
)
from app.modules.knowledge.application.retrieval import (
    RetrievalResult,
    RetrievalTuning,
    RetrieveContext,
)
from app.modules.knowledge.domain import file_resolution, mmr
from app.modules.knowledge.domain.chunking import (
    MIN_NODE_CHARS,
    SPLIT_OVERLAP_RATIO,
    SourceSegment,
    chunk_segments,
    max_words_for_token_limit,
    semantic_boundaries,
)
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.context_budget import estimate_tokens
from app.modules.knowledge.domain.errors import InvalidKnowledgeInput
from app.modules.knowledge.domain.file_resolution import (
    AmbiguousFiles,
    FileCandidate,
    NoFileMatch,
    ResolutionMethod,
    ResolvedFile,
    resolve_file,
)
from app.modules.knowledge.domain.intent import Intent, classify_intent
from app.modules.knowledge.domain.mmr import MmrCandidate, maximal_marginal_relevance
from app.modules.knowledge.domain.relevance import ScoredChunk, filter_relevant
from app.modules.knowledge.domain.sparse import SparseTerms, build_sparse_terms, term_id
from app.modules.knowledge.domain.value_objects import ParentChunkText
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)
from app.modules.knowledge.ports.retrieval import RetrievedChunk

# The SHIPPED retrieval configuration (plan step 18, `P-30` `P-40`, س-24) --
# the same singleton `RetrieveContext` falls back to when no Composition Root
# injects one, so a test that reads a knob here reads the number that ships.
# `dataclasses.replace` off it is how a test enables ONE knob without
# restating the other sixteen.
_TUNING = retrieval_module._DEFAULT_TUNING


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
        # Every leg's `with_vectors` flag, in call order (plan row 20,
        # `P-23`): the fake returns each point's own dense vector only when
        # asked, so a test can prove BOTH that MMR gets its input and that a
        # store which never fills `VectorHit.vector` still retrieves.
        self.with_vectors_calls: list[bool] = []

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
        self,
        collection: str,
        vector: list[float],
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
    ) -> list[VectorHit]:
        self.search_calls.append((collection, k, flt))
        self.with_vectors_calls.append(with_vectors)
        candidates = [
            p for p in self.points.get(collection, {}).values() if _payload_matches(p.payload, flt)
        ]
        scored = sorted(
            ((p, _cosine(vector, p.vector)) for p in candidates),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            VectorHit(
                id=p.id,
                score=score,
                payload=p.payload,
                vector=list(p.vector) if with_vectors else None,
            )
            for p, score in scored[:k]
        ]

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
        self.with_vectors_calls.append(with_vectors)
        candidates = [
            p for p in self.points.get(collection, {}).values() if _payload_matches(p.payload, flt)
        ]
        scored = sorted(
            ((p, _sparse_score(p, sparse)) for p in candidates),
            key=lambda item: item[1],
            reverse=True,
        )
        scored = [(p, score) for p, score in scored if score > 0.0]
        return [
            VectorHit(
                id=p.id,
                score=score,
                payload=p.payload,
                vector=list(p.vector) if with_vectors else None,
            )
            for p, score in scored[:k]
        ]

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        bucket = self.points.get(collection, {})
        for point_id in ids:
            bucket.pop(point_id, None)


class FakeParentRepo:
    """In-memory ``ParentChunkRepository`` fake (plan step 9, ``P-34``):
    ``parents`` maps a Qdrant point (leaf) ``chunk_id`` straight onto the
    ``ParentChunkText`` its parent-widening lookup should resolve to -- the
    same shape the real ``SqlDocumentRepository.parent_texts_for_chunk_ids``
    returns. Two different keys mapped to ``ParentChunkText`` instances that
    share the same ``.id`` is how a test seeds "these two leaves share one
    parent". A ``chunk_id`` absent from ``parents`` is absent from the
    returned mapping too (the real port's own contract), so the default
    (empty ``parents``) makes every candidate degrade to its own leaf text --
    behaviourally a no-op, which is why every PRE-EXISTING test in this file
    can pass ``FakeParentRepo()`` unchanged."""

    def __init__(self, parents: dict[str, ParentChunkText] | None = None) -> None:
        self.parents = parents or {}
        self.calls: list[list[str]] = []

    async def parent_texts_for_chunk_ids(
        self, ctx: ExecutionContext, chunk_ids: Sequence[str]
    ) -> dict[str, ParentChunkText]:
        self.calls.append(list(chunk_ids))
        return {
            chunk_id: self.parents[chunk_id] for chunk_id in chunk_ids if chunk_id in self.parents
        }


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
# mmr.maximal_marginal_relevance (P-23, plan §4 row 20, §3.9 + س-20)          #
# --------------------------------------------------------------------------- #
def _mmr(chunk_id: str, relevance: float, vector: list[float]) -> MmrCandidate:
    return MmrCandidate(chunk_id=chunk_id, relevance=relevance, vector=vector)


def test_mmr_returns_nothing_for_an_empty_pool_or_a_non_positive_top_n() -> None:
    """The two degenerate inputs, answered the same way ``fusion.py`` answers
    them -- an empty list, never an exception."""
    candidate = _mmr("a", 1.0, [1.0, 0.0])
    assert maximal_marginal_relevance([], top_n=5) == []
    assert maximal_marginal_relevance([candidate], top_n=0) == []
    assert maximal_marginal_relevance([candidate], top_n=-1) == []


def test_mmr_opens_with_the_most_relevant_candidate() -> None:
    """§3.7's "الأكثر صلة في `[#1]`": the first pick has nothing selected to
    be redundant with, so the diversity term cannot move it -- whatever the
    incoming order."""
    selected = maximal_marginal_relevance(
        [_mmr("weak", 0.1, [1.0, 0.0]), _mmr("strong", 0.9, [0.0, 1.0])], top_n=2
    )
    assert selected[0] == "strong"


def test_mmr_demotes_a_near_duplicate_below_a_less_relevant_distinct_chunk() -> None:
    """§3.9's whole reason to exist, in one assertion: "خمس قطع من الفقرة
    نفسها نتيجة مشروعة اليوم". Five candidates share one direction in vector
    space (one paragraph, five near-duplicate chunks) and rank above a sixth
    that points elsewhere. Ranked by relevance alone the top three would be
    three copies of the same paragraph; MMR puts the DISTINCT chunk second,
    even though it is the least relevant candidate in the pool."""
    duplicates = [_mmr(f"dup-{index}", 1.0 - index * 0.01, [1.0, 0.0]) for index in range(5)]
    distinct = _mmr("distinct", 0.90, [0.0, 1.0])

    selected = maximal_marginal_relevance([*duplicates, distinct], top_n=3)

    assert selected[0] == "dup-0"
    assert selected[1] == "distinct"
    # And the demotion is real, not merely a re-ordering that still ships all
    # five: at `top_n = 3` only ONE of the five near-duplicates survives.
    assert sum(1 for chunk_id in selected if chunk_id.startswith("dup-")) == 2
    assert "dup-4" not in selected


def test_mmr_at_lambda_one_is_pure_relevance_and_keeps_every_duplicate() -> None:
    """The control for the test above, and the knob's own upper end: at
    ``λ = 1.0`` the diversity term is multiplied by zero, so MMR degenerates
    to "sort by relevance" and the same pool ships three copies of the same
    paragraph. This is what the shipped ``0.7`` is buying."""
    duplicates = [_mmr(f"dup-{index}", 1.0 - index * 0.01, [1.0, 0.0]) for index in range(5)]
    distinct = _mmr("distinct", 0.90, [0.0, 1.0])

    selected = maximal_marginal_relevance([*duplicates, distinct], top_n=3, lambda_=1.0)

    assert selected == ["dup-0", "dup-1", "dup-2"]


def test_mmr_at_lambda_zero_ignores_relevance_entirely() -> None:
    """The knob's lower end: with ``λ = 0`` only redundancy counts, so after
    the opening pick (a relevance tie broken by input order) the candidate
    FARTHEST from what is selected wins, however irrelevant."""
    selected = maximal_marginal_relevance(
        [
            _mmr("first", 1.0, [1.0, 0.0]),
            _mmr("twin", 0.99, [1.0, 0.0]),
            _mmr("opposite", 0.01, [-1.0, 0.0]),
        ],
        top_n=2,
        lambda_=0.0,
    )
    assert selected == ["first", "opposite"]


def test_mmr_clamps_a_lambda_outside_the_unit_interval() -> None:
    """A misconfigured λ stays extreme rather than turning INVERTED: a
    negative diversity weight would actively reward repetition, which is
    never what a number in ``Settings`` meant."""
    pool = [
        _mmr("first", 1.0, [1.0, 0.0]),
        _mmr("twin", 0.99, [1.0, 0.0]),
        _mmr("other", 0.98, [0.0, 1.0]),
    ]
    assert maximal_marginal_relevance(pool, top_n=3, lambda_=5.0) == maximal_marginal_relevance(
        pool, top_n=3, lambda_=1.0
    )
    assert maximal_marginal_relevance(pool, top_n=3, lambda_=-5.0) == maximal_marginal_relevance(
        pool, top_n=3, lambda_=0.0
    )


def test_mmr_breaks_an_exact_tie_in_the_callers_own_order() -> None:
    """The ``fusion.py`` determinism rule, for the same reason: identical
    candidates must not be ordered by anything hash-dependent. Two pools that
    differ ONLY in input order each keep their own first entry."""

    def pool(first: str, second: str) -> list[MmrCandidate]:
        return [_mmr(first, 1.0, [1.0, 0.0]), _mmr(second, 1.0, [1.0, 0.0])]

    assert maximal_marginal_relevance(pool("a", "b"), top_n=2) == ["a", "b"]
    assert maximal_marginal_relevance(pool("b", "a"), top_n=2) == ["b", "a"]


def test_mmr_survives_a_zero_vector_without_raising() -> None:
    """A degenerate embedding has no direction, so it is neither relevant nor
    redundant -- it must never become a ``ZeroDivisionError`` in the middle of
    answering a question."""
    selected = maximal_marginal_relevance(
        [_mmr("zero", 1.0, [0.0, 0.0]), _mmr("real", 0.5, [1.0, 0.0])], top_n=2
    )
    assert sorted(selected) == ["real", "zero"]


def test_mmr_relevance_is_read_as_a_fraction_of_the_pool_best_not_min_max() -> None:
    """The scale decision, pinned. RRF scores are thousandths clustered close
    together; expressing them as a fraction of the pool's best preserves those
    ratios, so a pool whose candidates are nearly equally relevant lets the
    diversity term decide. Min-max normalisation would stretch the SAME pool
    across the whole ``[0, 1]`` and hand the top candidate's near-twin an
    unearned 1.0-vs-0.0 advantage.

    Here every candidate is within a hair of the best (RRF's actual
    behaviour), so the near-duplicate loses to the distinct chunk. Under
    min-max the distinct chunk -- the pool minimum -- would score 0.0 and
    could not win at any λ."""
    selected = maximal_marginal_relevance(
        [
            _mmr("best", 0.01639, [1.0, 0.0]),
            _mmr("twin", 0.01626, [1.0, 0.0]),
            _mmr("distinct", 0.01613, [0.0, 1.0]),
        ],
        top_n=2,
    )
    assert selected == ["best", "distinct"]


def test_mmr_pool_with_no_positive_relevance_degrades_instead_of_dividing() -> None:
    """A pool whose best relevance is not positive cannot be expressed as a
    fraction of it. Unreachable from RRF (its scores are always positive), so
    this is the corrupt-input path: every relevance term reads ``0.0`` and
    diversity alone decides, opening at the caller's first entry."""
    selected = maximal_marginal_relevance(
        [_mmr("a", 0.0, [1.0, 0.0]), _mmr("b", 0.0, [1.0, 0.0]), _mmr("c", 0.0, [0.0, 1.0])],
        top_n=2,
    )
    assert selected == ["a", "c"]


def test_mmr_module_imports_stdlib_only() -> None:
    """Decision س-20: "خوارزمية نقيّة في `domain/`". Read off the module's own
    AST, the `file_resolution.py` precedent -- no port, no provider, no
    vector-store client. MMR RECEIVES vectors; fetching them (and paying
    §3.9's declared `with_vectors=True` price) is the application layer's job,
    and it could not do otherwise without breaking import-linter contract 2."""
    tree = ast.parse(inspect.getsource(mmr))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert imported == {"__future__", "collections.abc", "dataclasses", "math"}


# --------------------------------------------------------------------------- #
# intent.classify_intent                                                      #
# --------------------------------------------------------------------------- #
def test_intent_has_exactly_the_two_routes_the_module_owns() -> None:
    """Retrieval plan §3.4/§4 row 11 (`P-21`): `METADATA` is an EXCLUDED path
    (§7), so the enum is the two routes `knowledge` actually has —
    `RetrieveContext` and `RequestSummary`. Pinned as a set membership rather
    than left implicit, because a third member reappearing is exactly the
    regression the routing use-case cannot handle."""
    assert {member.value for member in Intent} == {"content", "summarize_doc"}


@pytest.mark.parametrize(
    "query",
    [
        # Alpha's METADATA anchors, every one of them, now that the route is
        # excluded: a corpus-level question is answered from the
        # corpus-awareness header on the CONTENT path (§3.6, `P-36`), not by
        # a branch of its own.
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
def test_classify_intent_corpus_level_questions_are_content_now(query: str) -> None:
    assert classify_intent(query) is Intent.CONTENT


@pytest.mark.parametrize(
    "query",
    [
        "كم ملف يتحدث عن الرواتب؟",
        "كم عدد الملفات حول الميزانية؟",
        "How many files mention the budget?",
        "How many documents are about marketing?",
    ],
)
def test_classify_intent_topically_conditioned_questions_are_still_content(query: str) -> None:
    """These are the queries the deleted `_TOPICAL_GUARD_PATTERNS` existed to
    rescue — a content question wearing a METADATA-shaped prefix. They still
    land on CONTENT, by falling through instead of being demoted: the guard
    had exactly one job, and removing METADATA did that job permanently. The
    cases stay under test so the ANSWER is pinned, not the mechanism."""
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


# --- The SUMMARIZE_DOC calibration (plan §3.4 / §4 row 12, `P-22`, س-17 = ب) --
#
# With two routes the classifier's whole accuracy is SUMMARIZE_DOC's accuracy
# — CONTENT is a fall-through that cannot be wrong on its own — so these four
# tests are the calibration's contract, and the three examples §3.4 argues
# from are quoted here verbatim, shadda and all.


@pytest.mark.parametrize(
    "query",
    [
        "لخّص لي هذا",  # §3.4's decisive example, verbatim: names NOTHING
        "لخّصه",
        "لخص",
        "Summarize this",
        "summarise it please",
        "Please provide a summary of the report",
    ],
)
def test_an_imperative_summarize_verb_classifies_with_no_object(query: str) -> None:
    """Rule 1: a REQUEST is taken at its word, unconditionally. «لخّص لي هذا»
    identifies no document and is still unmistakably a summarization request
    — precisely the case س-17's rejected option أ (objecthood on *every*
    pattern) would have handed to CONTENT."""
    assert classify_intent(query) is Intent.SUMMARIZE_DOC


@pytest.mark.parametrize(
    "query",
    [
        "هل يوجد ملخّص تنفيذي في التقرير؟",  # §3.4's decisive counter-example
        "Is there an executive summary in the report?",
        "ورد ملخص تنفيذي في الصفحة الأولى",
    ],
)
def test_a_summary_noun_with_no_document_object_is_content(query: str) -> None:
    """Rule 2: a bare noun is a DESCRIPTION until a document noun turns up as
    its object. §3.4's counter-example is a question about what a report
    contains, and the old bare `ملخص`/`summary` anchors misrouted it — a
    false positive that costs a legitimate content answer (§6 risk 4).

    The Arabic half also pins the one subtlety substring matching creates:
    «ملخّص» *contains* «لخص», so rule 1's verb must refuse the «م» prefix
    rather than claim this query before rule 2 ever sees it."""
    assert classify_intent(query) is Intent.CONTENT


@pytest.mark.parametrize(
    "query",
    [
        "ملخص الملف من فضلك",  # ملف
        "ملخص المستند من فضلك",  # مستند
        "ملخص الكتاب",  # كتاب
        "ملخص كتب المحاسبة",  # كتب
        "تلخيص هذا المرجع",  # مرجع
        "ملخصات المراجع",  # مراجع
        "ما هو مستند الجودة؟",  # the descriptive frame, same condition
        "summary of the book",
    ],
)
def test_a_summary_noun_summarizes_once_a_widened_doc_noun_is_its_object(query: str) -> None:
    """`_DOC_NOUN` widened to alpha's full stem list `ملف|مستند|كتاب|كتب|مرجع|
    مراجع` — one query per stem, each with the noun in the object position
    the rule demands."""
    assert classify_intent(query) is Intent.SUMMARIZE_DOC


@pytest.mark.parametrize(
    "query",
    [
        "ما هو الحدّ الأقصى في ملف السياسات؟",  # §3.4's third example, verbatim
        "ما هي بنود العقد في المستند الثاني؟",
        "كم فصلاً في كتاب المحاسبة؟",
        "كم صفحة في كتب المحاسبة؟",
        "أين ذُكر المرجع في التقرير؟",
        "ما هي المراجع المستخدمة في البحث؟",
        "what is the docker setup in this repo?",
    ],
)
def test_a_widened_doc_noun_that_is_not_the_object_stays_content(query: str) -> None:
    """The objecthood condition is what makes the widening safe: every query
    here NAMES a document noun and every one of them is a content question
    about something else. Widening `_DOC_NOUN` without the condition would
    convert all six stems into false positives — §3.4 keeps «ما هو الحدّ
    الأقصى في ملف السياسات؟» as content for exactly this reason."""
    assert classify_intent(query) is Intent.CONTENT


@pytest.mark.parametrize(
    "query",
    [
        "يلخّص المؤلف الفصل الأول",
        "سألخّص لك ما ورد",
        "ملخصات هذا الكتاب",
    ],
)
def test_arabic_summarize_anchors_still_match_as_substrings(query: str) -> None:
    """Arabic is derivational — «لخص» lives inside «يلخّص/سألخّص» and «ملخص»
    inside «ملخصات» — so the anchors are matched as substrings, never with
    word boundaries. §3.4 states this is correct and intended; the test
    exists so nobody later "fixes" it into `\\b` and silently loses recall."""
    assert classify_intent(query) is Intent.SUMMARIZE_DOC


# --------------------------------------------------------------------------- #
# file_resolution.resolve_file -- the file-name cascade                       #
# (retrieval plan §3.5 / §4 row 13, `P-04`)                                   #
# --------------------------------------------------------------------------- #
_MAINTENANCE = FileCandidate("doc-maintenance", "تقرير_الصيانة.pdf")
_POLICY = FileCandidate("doc-policy", "سياسة_الموارد_البشرية.docx")
_QUARTER = FileCandidate("doc-quarter", "Q3_Report.pdf")
_CORPUS = (_MAINTENANCE, _POLICY, _QUARTER)

# The semantic layer takes vectors, never an embedding client (the domain is
# pure), so the fixtures are exact by construction: against `_QUERY_VECTOR`,
# `_label_at_cosine(c)` scores exactly `c`.
_QUERY_VECTOR = (1.0, 0.0)
# A query that DESCRIBES a document instead of naming it — zero lexical
# overlap with either Latin file name below, so the cascade always falls
# through layers 1 and 2 and the semantic layer is what is under test.
_DESCRIBED = "ما الوثيقة التي تشرح إجراءات السلامة؟"


def _label_at_cosine(cosine: float) -> tuple[float, float]:
    return (cosine, math.sqrt(1.0 - cosine * cosine))


def test_resolve_file_empty_corpus_is_no_match() -> None:
    assert resolve_file("لخص تقرير الصيانة", []) == NoFileMatch()


def test_resolve_file_exact_name_in_the_query_resolves_at_score_one() -> None:
    assert resolve_file("لخص تقرير الصيانة", _CORPUS) == ResolvedFile(
        document_id="doc-maintenance",
        file_name="تقرير_الصيانة.pdf",
        method=ResolutionMethod.EXACT,
        score=1.0,
    )


def test_resolve_file_exact_layer_folds_case_extension_and_separators() -> None:
    """One test for the three things `_norm_name` does before matching: alpha's
    `normalize_ar` lower-cases (file names are Latin at least as often as
    Arabic), the extension is dropped, and `_`/`-`/`.` become spaces — so a
    file stored as `Q3_Report.pdf` is reachable by typing `q3 report`."""
    result = resolve_file("summarize the q3 report", _CORPUS)

    assert isinstance(result, ResolvedFile)
    assert result.document_id == "doc-quarter"
    assert result.method is ResolutionMethod.EXACT


def test_resolve_file_two_exact_hits_do_not_pick_one() -> None:
    """Ambiguity is decided per LAYER, not at the end: two file names both
    present in the query is already undecidable, and alpha returns candidates
    from the exact layer rather than letting the fuzzy scores break the tie."""
    corpus = (
        FileCandidate("doc-report", "تقرير.pdf"),
        FileCandidate("doc-sales", "تقرير المبيعات.pdf"),
    )

    assert resolve_file("لخص تقرير المبيعات", corpus) == AmbiguousFiles(
        corpus, ResolutionMethod.EXACT
    )


def test_resolve_file_fuzzy_resolves_a_confident_lone_candidate() -> None:
    """«ملف الصيانة» names no file exactly, but scores 0.85 against
    «تقرير_الصيانة.pdf» — above `_HIGH` and alone — via alpha's blend
    `max(0.6·containment + 0.4·difflib_ratio, jaccard)`."""
    result = resolve_file("لخص ملف الصيانة", _CORPUS)

    assert isinstance(result, ResolvedFile)
    assert result.document_id == "doc-maintenance"
    assert result.method is ResolutionMethod.FUZZY
    assert result.score == pytest.approx(0.85)


def test_resolve_file_a_fuzzy_tie_returns_every_tied_candidate_and_guesses_nothing() -> None:
    """`P-04`'s headline behaviour and the reason it is worth porting at all
    (plan §3.5): two sales reports differing only by year are genuinely
    indistinguishable from «لخص تقرير المبيعات», and "always take the top
    candidate" would summarize one of them — the WRONG one, half the time,
    with full confidence and nothing downstream able to notice."""
    corpus = (
        FileCandidate("doc-2023", "تقرير_المبيعات_2023.pdf"),
        FileCandidate("doc-2024", "تقرير_المبيعات_2024.pdf"),
    )

    result = resolve_file("لخص تقرير المبيعات", corpus)

    assert isinstance(result, AmbiguousFiles)
    assert result.method is ResolutionMethod.FUZZY
    assert tuple(candidate.document_id for candidate in result.candidates) == (
        "doc-2023",
        "doc-2024",
    )


def test_resolve_file_the_band_admits_only_near_ties_not_the_whole_corpus() -> None:
    """`_BAND = 0.10` is a TIE window, not "return everything": an unrelated
    file in the same corpus is not made a candidate by the ambiguity of two
    others."""
    corpus = (
        FileCandidate("doc-2023", "تقرير_المبيعات_2023.pdf"),
        FileCandidate("doc-2024", "تقرير_المبيعات_2024.pdf"),
        _POLICY,
    )

    result = resolve_file("لخص تقرير المبيعات", corpus)

    assert isinstance(result, AmbiguousFiles)
    assert _POLICY not in result.candidates


def test_resolve_file_a_lone_candidate_below_the_confidence_bar_is_still_undecided() -> None:
    """Ambiguity is not only "several files". A single candidate that clears
    `_LOW` but not `_HIGH` (here 0.705) is returned as `AmbiguousFiles` with
    ONE member — alpha's behaviour and the honest one: "did you mean X?" is a
    different statement from "this is X"."""
    corpus = (
        FileCandidate("doc-annual", "تقرير الصيانة السنوي للمباني والمرافق.pdf"),
        FileCandidate("doc-budget", "الميزانية.pdf"),
    )

    result = resolve_file("لخص ملف الصيانة", corpus)

    assert isinstance(result, AmbiguousFiles)
    assert tuple(candidate.document_id for candidate in result.candidates) == ("doc-annual",)


def test_resolve_file_caps_the_candidate_list_at_five() -> None:
    """A user cannot be asked to choose between twenty files. The cap is
    alpha's `max_candidates=5`, applied in rank order — and the order of
    equal scores is the order the corpus was given in, so the same corpus
    always produces the same five."""
    corpus = tuple(
        FileCandidate(f"doc-{year}", f"تقرير_المبيعات_{year}.pdf") for year in range(2019, 2025)
    )

    result = resolve_file("لخص تقرير المبيعات", corpus)

    assert isinstance(result, AmbiguousFiles)
    assert result.candidates == corpus[:5]


def test_resolve_file_below_the_usable_floor_is_no_match_not_a_best_guess() -> None:
    """No budget file exists in `_CORPUS`. Nothing clears `_LOW`, and the
    answer is "none" rather than the least-bad of three wrong files."""
    assert resolve_file("ما هو ملف الميزانية؟", _CORPUS) == NoFileMatch()


def test_resolve_file_strips_the_arabic_definite_article_from_long_tokens() -> None:
    """alpha's own note calls «التقرير» vs «تقرير» "a very common mismatch";
    stripping «ال» on both sides is what closes it."""
    result = resolve_file("لخص التقرير", (FileCandidate("doc-report", "تقرير.pdf"),))

    assert isinstance(result, ResolvedFile)
    assert result.document_id == "doc-report"


def test_resolve_file_keeps_the_definite_article_on_a_four_letter_token() -> None:
    """The strip applies only to tokens LONGER than four characters, so «الرد»
    survives intact and a query about «رد» does not reach it. Pinned because
    the bound is the whole safety of the rule: without it every short Arabic
    word beginning with those two letters would be silently truncated."""
    assert resolve_file("لخص رد المدير", (FileCandidate("doc-reply", "الرد.pdf"),)) == NoFileMatch()


def test_resolve_file_a_query_of_nothing_but_request_words_names_no_file() -> None:
    """«لخص لي هذا» IS a summarization request — `classify_intent` says so —
    and identifies no document whatsoever. The resolver refuses instead of
    falling back on the newest file: this is precisely the query row 14's
    clarification question exists to answer."""
    assert classify_intent("لخص لي هذا") is Intent.SUMMARIZE_DOC
    assert resolve_file("لخص لي هذا", _CORPUS) == NoFileMatch()


def test_resolve_file_semantic_layer_runs_only_when_a_query_vector_is_given() -> None:
    """alpha's `embed_model=None` optionality, minus the I/O: the domain
    cannot embed anything, so the vectors are passed in. Same query, same
    corpus — without vectors the cascade ends after FUZZY."""
    corpus = (
        FileCandidate("doc-safety", "handbook.pdf", _label_at_cosine(0.70)),
        FileCandidate("doc-manual", "manual.pdf", _label_at_cosine(0.30)),
    )

    assert resolve_file(_DESCRIBED, corpus) == NoFileMatch()

    result = resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR)
    assert isinstance(result, ResolvedFile)
    assert result.document_id == "doc-safety"
    assert result.method is ResolutionMethod.SEMANTIC
    assert result.score == pytest.approx(0.70)


def test_resolve_file_semantic_tie_inside_its_band_returns_candidates() -> None:
    """The refusal to guess is the same at layer 3, on alpha's tighter
    semantic band (0.05): 0.70 and 0.68 are not a decision."""
    corpus = (
        FileCandidate("doc-safety", "handbook.pdf", _label_at_cosine(0.70)),
        FileCandidate("doc-manual", "manual.pdf", _label_at_cosine(0.68)),
    )

    result = resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR)

    assert isinstance(result, AmbiguousFiles)
    assert result.method is ResolutionMethod.SEMANTIC
    assert len(result.candidates) == 2


def test_resolve_file_semantic_lone_candidate_below_its_confidence_bar_is_undecided() -> None:
    """0.50 clears the semantic floor (0.45) but not the semantic confidence
    bar (0.60) — usable enough to show, not confident enough to act on."""
    corpus = (
        FileCandidate("doc-safety", "handbook.pdf", _label_at_cosine(0.50)),
        FileCandidate("doc-manual", "manual.pdf", _label_at_cosine(0.30)),
    )

    result = resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR)

    assert isinstance(result, AmbiguousFiles)
    assert tuple(candidate.document_id for candidate in result.candidates) == ("doc-safety",)


def test_resolve_file_semantic_below_the_floor_is_no_match() -> None:
    corpus = (
        FileCandidate("doc-safety", "handbook.pdf", _label_at_cosine(0.40)),
        FileCandidate("doc-manual", "manual.pdf", _label_at_cosine(0.30)),
    )

    assert resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR) == NoFileMatch()


def test_resolve_file_a_zero_label_vector_scores_zero_instead_of_dividing_by_zero() -> None:
    """alpha's `1e-8` norm epsilon, ported: a label that embedded to zeros is
    maximally unrelated, not a crash."""
    corpus = (FileCandidate("doc-empty", "handbook.pdf", (0.0, 0.0)),)

    assert resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR) == NoFileMatch()


def test_resolve_file_a_lexical_decision_is_never_revisited_by_the_semantic_layer() -> None:
    """The cascade stops at the first layer with an opinion. A perfect label
    vector on the wrong document does not overturn a confident fuzzy match —
    file names are lexical by nature, which is why semantics is the LAST
    resort and not a tiebreaker."""
    corpus = (
        FileCandidate("doc-maintenance", "تقرير_الصيانة.pdf", _label_at_cosine(0.10)),
        FileCandidate("doc-policy", "سياسة_الموارد_البشرية.docx", _label_at_cosine(1.0)),
    )

    result = resolve_file("لخص ملف الصيانة", corpus, query_vector=_QUERY_VECTOR)

    assert isinstance(result, ResolvedFile)
    assert result.document_id == "doc-maintenance"
    assert result.method is ResolutionMethod.FUZZY


def test_resolve_file_semantic_layer_refuses_a_partially_embedded_corpus() -> None:
    """Skipping the un-embedded candidates instead would run the comparison
    over a silently partial corpus and could return a confident match while
    the right file sat outside it — the failure this whole module exists to
    prevent, reintroduced by a convenience. alpha's `except Exception` +
    `print` (a silent downgrade to "no match") is not ported either."""
    corpus = (
        FileCandidate("doc-safety", "handbook.pdf", _label_at_cosine(0.90)),
        FileCandidate("doc-manual", "manual.pdf"),
    )

    with pytest.raises(InvalidKnowledgeInput):
        resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR)


def test_resolve_file_semantic_layer_rejects_a_mismatched_vector_dimension() -> None:
    corpus = (FileCandidate("doc-safety", "handbook.pdf", (1.0, 0.0, 0.0)),)

    with pytest.raises(InvalidKnowledgeInput):
        resolve_file(_DESCRIBED, corpus, query_vector=_QUERY_VECTOR)


def test_resolve_file_semantic_layer_rejects_an_empty_query_vector() -> None:
    corpus = (FileCandidate("doc-safety", "handbook.pdf", ()),)

    with pytest.raises(InvalidKnowledgeInput):
        resolve_file(_DESCRIBED, corpus, query_vector=())


def test_resolve_file_is_pure_and_deterministic() -> None:
    assert resolve_file("لخص ملف الصيانة", _CORPUS) == resolve_file("لخص ملف الصيانة", _CORPUS)


def test_ambiguous_files_cannot_be_empty() -> None:
    """An "undecided" outcome with nothing in it is `NoFileMatch` wearing the
    wrong type, and a caller branching on the union would render an empty
    question. The invariant is enforced at construction."""
    with pytest.raises(InvalidKnowledgeInput):
        AmbiguousFiles((), ResolutionMethod.FUZZY)


def test_ambiguous_files_offers_no_way_to_collapse_itself_into_one_answer() -> None:
    """Plan §3.5's rationale, made structural. `AmbiguousFiles` carries the
    candidates and the layer that produced them and NOTHING else — no
    `document_id`, no `best`, no indexing or iteration protocol — so
    `mypy --strict` rejects `.document_id` on a `FileResolution` that has not
    been narrowed to `ResolvedFile` first, and there is no accidental one-line
    path from "undecided" to a single confident answer."""
    ambiguous = AmbiguousFiles((_MAINTENANCE, _POLICY), ResolutionMethod.FUZZY)

    assert {field.name for field in dataclasses.fields(ambiguous)} == {"candidates", "method"}
    for attribute in ("document_id", "file_name", "score", "best", "top", "first"):
        assert not hasattr(ambiguous, attribute)
    assert not hasattr(type(ambiguous), "__getitem__")
    assert not hasattr(type(ambiguous), "__iter__")


def test_file_resolution_outcomes_are_frozen() -> None:
    resolved = ResolvedFile("doc-1", "a.pdf", ResolutionMethod.EXACT, 1.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.document_id = "doc-2"


def test_file_resolution_lexical_thresholds_are_alphas_calibration() -> None:
    """`_BAND = 0.10` is named verbatim in plan §4 row 13. These three grade a
    token-overlap-and-`difflib` similarity in [0, 1] computed in this very
    module, so — unlike the L2-calibrated retrieval thresholds the plan's
    header warns about — they carry over unchanged, the same standing as
    `relevance.py`'s 0.95 Jaccard constant."""
    assert file_resolution._BAND == 0.10
    assert file_resolution._HIGH == 0.75
    assert file_resolution._LOW == 0.40
    assert file_resolution._MAX_CANDIDATES == 5


def test_file_resolution_module_imports_stdlib_and_sibling_domain_only() -> None:
    """Plan §3.5: "وحدة دومين نقيّة فوق `ListDocuments` — تُنقَل الخوارزمية لا
    تخزين JSON الذي يستعمله alpha". Read off the module's own AST: no `os`, no
    `json`, no repository, and — the one that matters most — no embedding
    provider. The semantic layer receives vectors; it does not fetch them, and
    it never could without breaking import-linter contract 2."""
    tree = ast.parse(inspect.getsource(file_resolution))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert imported == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "difflib",
        "enum",
        "math",
        "operator",
        "re",
        "app.modules.knowledge.domain.errors",
        "app.modules.knowledge.domain.tokenization",
    }


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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx,
        space_id=None,
        query="quarterly revenue figures for the northern region",
        model="m",
        api_key="k",
        k=1,
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].document_id == "doc-1"
    assert result.chunks[0].text == "quarterly revenue figures for the northern region"
    assert result.chunks[0].chunk_id == chunk_point_id("doc-1", 0)


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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx,
        space_id=None,
        query="quarterly revenue figures for the northern region",
        model="m",
        api_key="k",
        k=1,
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].file_name == "quarterly-report.pdf"
    assert result.chunks[0].page_number == 4
    assert result.chunks[0].section == "Regional Breakdown"


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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx,
        space_id=None,
        query="cafeteria menu changes for next month",
        model="m",
        api_key="k",
        k=1,
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].file_name is None
    assert result.chunks[0].page_number is None
    assert result.chunks[0].section is None


async def test_retrieve_context_both_legs_called_with_workspace_filter() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["alpha beta gamma report content"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
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

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="revenue figures quarterly", model="m", api_key="k", k=5
    )

    assert result.chunks[0].chunk_id == chunk_point_id("doc-1", 0)


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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query=query, model="m", api_key="k", k=1
    )

    assert len(result.chunks) == 1
    # far_text -- the sparse-rescued chunk
    assert result.chunks[0].chunk_id == chunk_point_id("doc-1", 1)
    assert "ZX9000QRS" in result.chunks[0].text


async def test_retrieve_context_clamps_k_below_minimum_up_to_one() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=0
    )

    # k clamped up to >=1: search_k = clamped_k * the widened overfetch
    # (plan row 20 -- `max(search_overfetch, mmr_overfetch)`) == 1 * 6 == 6.
    assert vectors.search_calls[-1][1] == 6
    assert vectors.search_sparse_calls[-1][1] == 6


async def test_retrieve_context_clamps_k_above_maximum() -> None:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=1000
    )

    # k clamped down to <=50: search_k = min(50 * search_overfetch,
    # max_search_candidates) == 100
    assert vectors.search_calls[-1][1] == 100
    # ... and the SPARSE leg alone is then narrowed again by the candidate
    # ceiling (plan step 16, `P-27`): min(search_k, max_sparse_candidates).
    assert vectors.search_sparse_calls[-1][1] == _TUNING.max_sparse_candidates


# --------------------------------------------------------------------------- #
# Per-leg floors + sparse candidate ceiling (plan step 16, `P-27`, §3.8)      #
# --------------------------------------------------------------------------- #
def _scored_hit(score: float, point_id: str = "point-1") -> VectorHit:
    return VectorHit(id=point_id, score=score, payload={})


def test_per_leg_score_floors_ship_disabled() -> None:
    """Retrieval plan §3.8 ("الآليّة تُشحَن والأرقام لا") + decision س-22: the
    per-leg knobs exist, and every one of them ships at ``0.0`` = disabled
    until the evaluation set ``P-38`` waits on exists. The one alpha constant
    that is INDEPENDENT of the score scale -- Jaccard dedup at ``0.95``
    (plan fact ح-17) -- is the sole gate that stays on."""
    assert _TUNING.min_dense_score == 0.0
    assert _TUNING.min_bm25_score == 0.0
    # Plan step 18 (`P-30`) moved `filter_relevant`'s own three gates into the
    # same injected tuning, so the shipped configuration has to be asserted
    # BOTH where the numbers now live and where the algorithm's own defaults
    # still sit -- they must agree, or one of the two is dead.
    assert _TUNING.min_fused_score == 0.0
    assert _TUNING.relative_floor == 0.0
    assert _TUNING.jaccard_threshold == 0.95

    floors = inspect.signature(filter_relevant).parameters
    assert floors["min_score"].default == _TUNING.min_fused_score
    assert floors["relative_floor"].default == _TUNING.relative_floor
    assert floors["jaccard_threshold"].default == _TUNING.jaccard_threshold


def test_gate_by_score_at_the_shipped_default_is_inert_even_for_negative_scores() -> None:
    """``0.0`` must mean DISABLED, not "keep scores >= 0". The dense leg is
    cosine similarity over ``[-1, 1]``, so an arithmetic-only "disabled"
    default would silently drop every negatively correlated hit -- an
    uncalibrated gate wearing a disabled default's clothes, which is exactly
    what س-22 forbids."""
    hits = [_scored_hit(0.9, "a"), _scored_hit(0.0, "b"), _scored_hit(-0.42, "c")]

    assert retrieval_module._gate_by_score(hits, _TUNING.min_dense_score) == hits
    assert retrieval_module._gate_by_score(hits, _TUNING.min_bm25_score) == hits
    assert retrieval_module._gate_by_score(hits, 0.0) == hits


def test_gate_by_score_when_enabled_keeps_scores_at_or_above_the_floor() -> None:
    """Direction proof (retrieval plan header/§3.3/§3.8, §6 risk #3): AIZZAK
    scores are cosine / IDF-weighted dot product, where HIGHER is better, so
    a floor keeps ``score >= floor``. alpha's floors gate an L2 DISTANCE
    (lower is nearer) and its comparison therefore runs the other way -- no
    alpha number, and no alpha comparison, is copied."""
    hits = [_scored_hit(0.61, "above"), _scored_hit(0.60, "exactly"), _scored_hit(0.59, "below")]

    kept = retrieval_module._gate_by_score(hits, 0.60)

    assert [hit.id for hit in kept] == ["above", "exactly"]


def test_gate_by_score_preserves_the_legs_own_rank_order() -> None:
    """RRF downstream reads RANK, not score, so the gate must only remove --
    never reorder -- what the store returned best-first."""
    hits = [_scored_hit(0.9, "a"), _scored_hit(0.1, "b"), _scored_hit(0.8, "c")]

    assert [hit.id for hit in retrieval_module._gate_by_score(hits, 0.5)] == ["a", "c"]


async def test_retrieve_context_floors_gate_the_legs_but_never_the_confidence_signals() -> None:
    """The mechanism is genuinely wired (set a floor above every hit and both
    legs empty), and the ``P-28`` confidence signals are snapshotted BEFORE
    it: their contract is "the maximum over EVERY hit that leg returned", and
    a floor that erased them would blind the structured log (``P-29``) and
    any future calibration at the one moment they matter most.

    Since plan step 18 (``P-30``) the floors arrive as INJECTED configuration
    rather than module constants, so enabling one is a constructor argument
    here instead of a ``monkeypatch`` -- which is itself the proof that the
    knob is reachable from ``Settings`` at all."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    baseline = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k"
    )
    assert baseline.chunks  # the SHIPPED configuration retrieves it
    assert baseline.best_dense_score is not None
    assert baseline.best_bm25_score is not None

    # Derived from the baseline rather than hard-coded: the dense leg is
    # cosine (bounded by 1.0) but the sparse leg is a raw IDF-weighted dot
    # product with NO upper bound -- one more reason the two floors live on
    # separate scales and neither may be guessed.
    gated = await RetrieveContext(
        embeddings,
        vectors,
        FakeParentRepo(),
        tuning=replace(
            _TUNING,
            min_dense_score=baseline.best_dense_score + 1.0,
            min_bm25_score=baseline.best_bm25_score + 1.0,
        ),
    ).execute(ctx, space_id=None, query="document content", model="m", api_key="k")

    assert gated.chunks == []
    assert gated.best_dense_score == baseline.best_dense_score
    assert gated.best_bm25_score == baseline.best_bm25_score


async def test_retrieve_context_the_two_floors_are_independent_per_leg() -> None:
    """``P-27`` is "عتبات مطلقة لكلّ ساق" -- two separate floors, not one
    shared gate: shutting the dense leg entirely leaves the BM25-sparse leg
    free to surface the same chunk on lexical recall alone."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    result = await RetrieveContext(
        embeddings,
        vectors,
        FakeParentRepo(),
        tuning=replace(_TUNING, min_dense_score=2.0),
    ).execute(ctx, space_id=None, query="document content", model="m", api_key="k")

    assert [chunk.chunk_id for chunk in result.chunks] == [chunk_point_id("doc-1", 0)]


def test_sparse_candidate_ceiling_carries_a_real_value_and_spares_the_default_k() -> None:
    """The other half of ``P-27``, and the half §3.8 grants a REAL number: a
    cap on the **count** of sparse candidates, not on any score, so س-22
    never reaches it. Chosen as alpha's own sparse-leg candidate count -- a
    count is the one class of alpha number that survives the L2 -> cosine
    direction flip untouched. It sits at or above the default ``k = 5``
    path's fetch depth, so this step narrows nothing that ships today."""
    assert _TUNING.max_sparse_candidates == 20
    assert _TUNING.max_sparse_candidates >= _TUNING.default_k * _TUNING.search_overfetch


async def test_retrieve_context_caps_the_sparse_leg_alone_at_a_large_k() -> None:
    """The ceiling is spent at the sparse leg's FETCH DEPTH (asking the store
    for 100 hits and discarding 80 payloads is pure waste), and it touches
    only that leg -- the dense fetch keeps its full ``search_k``."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=50
    )

    assert vectors.search_calls[-1][1] == 100  # dense: uncapped search_k
    assert vectors.search_sparse_calls[-1][1] == _TUNING.max_sparse_candidates


async def test_retrieve_context_sparse_cap_now_binds_at_the_default_k() -> None:
    """⚠️ A behaviour change plan row 20 brought with it, pinned so it cannot
    happen silently. Plan step 16 shipped ``max_sparse_candidates = 20`` and
    recorded (§7) that it "does not touch the default path", because
    ``search_k`` was then ``5 x 3 = 15``. Row 20's widened search makes the
    dense leg fetch ``5 x 6 = 30``, so the sparse ceiling is now the binding
    constraint at the SHIPPED ``k``: the dense leg fetches 30 and the BM25 leg
    stops at 20. That is the cap doing exactly the job step 16 gave it --
    guarding against the BM25 tail, where its precision collapses -- and it is
    also why the two legs no longer fetch identically."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k", k=5
    )

    assert vectors.search_calls[-1][1] == 30
    assert vectors.search_sparse_calls[-1][1] == 20


async def test_retrieve_context_widens_past_k_after_fusion_before_narrowing_to_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P-26 (retrieval plan §3.7, plan step 8): the pipeline widens back OUT
    to ``3 * k`` fused candidates right after RRF fusion -- BEFORE
    ``filter_relevant`` runs -- so the later narrowing stage (plan step 9's
    parent expansion, ``_widen_to_parents``) has enough distinct candidates
    to fill ``k`` with distinct sections instead of collapsing into two
    parents (§3.7's own stated failure mode). Proven by spying on
    ``filter_relevant`` itself (the very next stage after fusion in the
    pipeline diagram): with a fused pool wider than ``k``, strictly MORE than
    ``k`` candidates must reach it, even though the PUBLIC result still
    honours ``k`` exactly."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    k = 2
    # Ten distinct (non-near-duplicate) chunks, each sharing the query's
    # "quarterly planning" terms so BOTH legs surface a full `search_k`
    # (== 6, below) worth of hits -- comfortably more than `k`.
    texts = [
        "revenue projections for the sales department in quarterly planning",
        "employee benefits enrollment closes soon per quarterly planning",
        "office renovation timeline shifts under quarterly planning",
        "marketing budget allocation grows under quarterly planning",
        "support ticket volume dropped during quarterly planning",
        "supply chain delays were flagged in quarterly planning",
        "software rollout schedule moved after quarterly planning",
        "training enrollment rose sharply this quarterly planning",
        "facilities maintenance was prioritized in quarterly planning",
        "vendor contracts are under review per quarterly planning",
    ]
    await _seed_corpus(vectors, ctx, "doc-1", texts)

    seen_candidate_counts: list[int] = []
    real_filter_relevant = retrieval_module.filter_relevant

    def _spy_filter_relevant(
        candidates: Sequence[ScoredChunk], **kwargs: float | bool
    ) -> list[ScoredChunk]:
        seen_candidate_counts.append(len(candidates))
        return real_filter_relevant(candidates, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(retrieval_module, "filter_relevant", _spy_filter_relevant)

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="quarterly planning review", model="m", api_key="k", k=k
    )

    assert seen_candidate_counts  # filter_relevant was actually reached
    # min(k * fusion_retention, max_search_candidates) == min(2 * 3, 100) == 6 -- both
    # legs return a full `search_k` (== 6) worth of hits out of the 10-chunk
    # corpus, so their union is always >= 6.
    assert seen_candidate_counts[-1] == 6
    assert seen_candidate_counts[-1] > k  # the widening the step exists for
    assert len(result.chunks) <= k  # the PUBLIC result still honours k


async def test_retrieve_context_substitutes_the_parents_text_not_the_leafs() -> None:
    """Plan step 9 (``P-34``): the surviving candidate's text is REPLACED by
    its parent's text, not merely accompanied by it -- and everything else
    about the candidate (its own ``chunk_id``/``document_id``, the citation
    it carries) still names the original LEAF, only ``text`` changes."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    leaf_text = "quarterly revenue figures for the northern region"
    await _seed_corpus(vectors, ctx, "doc-1", [leaf_text])
    point_id = chunk_point_id("doc-1", 0)
    parent_text = "the full parent section, considerably longer than any one leaf window"
    parent_repo = FakeParentRepo({point_id: ParentChunkText(id="parent-A", text=parent_text)})

    result = await RetrieveContext(embeddings, vectors, parent_repo).execute(
        ctx, space_id=None, query=leaf_text, model="m", api_key="k", k=1
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].text == parent_text
    assert result.chunks[0].text != leaf_text
    # Identity fields still belong to the original leaf -- only `text` moved.
    assert result.chunks[0].chunk_id == point_id
    assert result.chunks[0].document_id == "doc-1"


async def test_retrieve_context_widened_parent_text_appears_once_when_two_leaves_share_it() -> None:
    """Retrieval plan §3.7 (``P-34``): two SURVIVING leaves that widen to the
    SAME parent collapse into ONE entry -- dedup BY PARENT (keyed on
    ``ParentChunkText.id``), not by text equality -- freeing the slot
    ``fusion_retention``'s 3x pool exists to let a third, distinct candidate
    fill instead of the same section appearing twice."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    texts = [
        "revenue projections for the sales department in quarterly planning",
        "office renovation timeline shifts under quarterly planning",
        "training enrollment rose sharply this quarterly planning",
    ]
    await _seed_corpus(vectors, ctx, "doc-1", texts)
    point0 = chunk_point_id("doc-1", 0)
    point1 = chunk_point_id("doc-1", 1)
    shared_parent_text = "the one shared parent section both leaves widen to"
    parent_repo = FakeParentRepo(
        {
            point0: ParentChunkText(id="parent-A", text=shared_parent_text),
            point1: ParentChunkText(id="parent-A", text=shared_parent_text),
        }
    )

    result = await RetrieveContext(embeddings, vectors, parent_repo).execute(
        ctx, space_id=None, query="quarterly planning", model="m", api_key="k", k=3
    )

    # Only 2 survive: the ONE deduped parent entry + the third leaf's own
    # text -- never 3, even though 3 distinct leaves matched and k allows 3.
    assert len(result.chunks) == 2
    result_texts = [chunk.text for chunk in result.chunks]
    assert result_texts.count(shared_parent_text) == 1
    assert texts[2] in result_texts


async def test_retrieve_context_caps_substituted_parent_text_at_max_parent_chunk_chars() -> None:
    """Plan step 9 (``P-34``): ``max_parent_chunk_chars`` is a length cap on
    the SUBSTITUTED parent text, so one oversized parent cannot swallow the
    whole context -- proven directly against the module's own constant
    rather than a hard-coded number, so the test tracks the real cap."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    leaf_text = "quarterly revenue figures for the northern region"
    await _seed_corpus(vectors, ctx, "doc-1", [leaf_text])
    point_id = chunk_point_id("doc-1", 0)
    cap = _TUNING.max_parent_chunk_chars
    oversized_parent_text = "x" * (cap + 500)
    parent_repo = FakeParentRepo(
        {point_id: ParentChunkText(id="parent-A", text=oversized_parent_text)}
    )

    result = await RetrieveContext(embeddings, vectors, parent_repo).execute(
        ctx, space_id=None, query=leaf_text, model="m", api_key="k", k=1
    )

    assert len(result.chunks) == 1
    assert len(result.chunks[0].text) == cap
    assert result.chunks[0].text == oversized_parent_text[:cap]


async def test_retrieve_context_degrades_to_leaf_text_when_no_parent_resolves() -> None:
    """A chunk with no resolvable parent (``parent_id`` null, or the parent
    row missing/unreadable -- ``ParentChunkRepository`` simply omits it from
    the returned mapping either way) keeps its OWN leaf text -- never
    dropped, never a crash. The default, parent-less ``FakeParentRepo()`` is
    exactly this case, and every test above this one in the file already
    relies on it implicitly; this test pins it explicitly."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    leaf_text = "quarterly revenue figures for the northern region"
    await _seed_corpus(vectors, ctx, "doc-1", [leaf_text])

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query=leaf_text, model="m", api_key="k", k=1
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].text == leaf_text


# --------------------------------------------------------------------------- #
# The dual context budget in the pipeline (retrieval plan §3.7/§4 row 10,     #
# `P-35`): runs AFTER the parent widening above and BEFORE the caller's `k`,  #
# on the LABELLED text, with the smaller of the two ceilings winning.         #
# --------------------------------------------------------------------------- #
_BUDGET_CORPUS = (
    "revenue projections for the sales department in quarterly planning",
    "employee benefits enrollment closes soon per quarterly planning",
    "office renovation timeline shifts under quarterly planning",
    "marketing budget allocation grows under quarterly planning",
    "support ticket volume dropped during quarterly planning",
)


def _labelled(chunk: RetrievedChunk) -> str:
    """What the budget measures -- the chunk rendered by the ONE shared
    source-label formatter (retrieval plan §3.2), which is exactly what
    ``RetrieveContext._labeled_text`` hands to the budget and what the RAG
    agent later joins into its prompt."""
    return format_labeled_chunk(
        chunk.text,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section=chunk.section,
    )


async def _budget_run(max_context_chars: int, max_context_tokens: int) -> list[RetrievedChunk]:
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", _BUDGET_CORPUS)

    result = await RetrieveContext(
        embeddings,
        vectors,
        FakeParentRepo(),
        tuning=replace(
            _TUNING,
            max_context_chars=max_context_chars,
            max_context_tokens=max_context_tokens,
        ),
    ).execute(ctx, space_id=None, query="quarterly planning review", model="m", api_key="k", k=5)
    return result.chunks


async def test_retrieve_context_context_budget_cuts_at_the_character_ceiling() -> None:
    """Plan step 10 (``P-35``): with the TOKEN ceiling out of the way, the
    character ceiling alone decides where the ranked list is cut -- and the
    survivors are the top of that list, not a re-picked subset."""
    generous = await _budget_run(100_000, 100_000)
    assert len(generous) == len(_BUDGET_CORPUS)  # the whole seeded corpus survives

    rendered = [_labelled(chunk) for chunk in generous]
    exactly_two = len(rendered[0]) + len(rendered[1])
    tight = await _budget_run(exactly_two, 100_000)

    assert [chunk.chunk_id for chunk in tight] == [chunk.chunk_id for chunk in generous[:2]]
    assert sum(len(_labelled(chunk)) for chunk in tight) <= exactly_two
    # One character less and even the second no longer fits -- proof the cut
    # tracks the ceiling rather than landing on a coincidence.
    assert len(await _budget_run(exactly_two - 1, 100_000)) == 1


async def test_retrieve_context_context_budget_cuts_at_the_token_ceiling() -> None:
    """The same list, cut by the OTHER ceiling: the character ceiling is left
    wide open and the estimated token count alone decides. `estimate_tokens`
    is the pure, network-free estimator (no `tiktoken`, no tokenizer
    download) -- the test computes the ceiling with it rather than hard-coding
    a number, so it tracks the estimator."""
    generous = await _budget_run(100_000, 100_000)
    rendered = [_labelled(chunk) for chunk in generous]
    exactly_two = estimate_tokens(rendered[0]) + estimate_tokens(rendered[1])

    tight = await _budget_run(100_000, exactly_two)

    assert [chunk.chunk_id for chunk in tight] == [chunk.chunk_id for chunk in generous[:2]]
    assert len(await _budget_run(100_000, exactly_two - 1)) == 1


async def test_retrieve_context_context_budget_keeps_the_best_chunks_first() -> None:
    """Descending by score, then cut (retrieval plan §3.7): what survives is a
    PREFIX of the unbudgeted ranking, so the most relevant chunk is still
    `[#1]` in what the model reads. `LongContextReorder` -- which would move it
    to the END -- is an explicitly rejected design (§3.7, §7)."""
    generous = await _budget_run(100_000, 100_000)
    rendered = [_labelled(chunk) for chunk in generous]
    tight = await _budget_run(len(rendered[0]) + len(rendered[1]) + len(rendered[2]), 100_000)

    scores = [chunk.score for chunk in tight]
    assert scores == sorted(scores, reverse=True)
    assert [chunk.chunk_id for chunk in tight] == [chunk.chunk_id for chunk in generous[:3]]
    assert tight[0].chunk_id == generous[0].chunk_id  # the best chunk leads, never trails


async def test_retrieve_context_context_budget_never_returns_an_empty_context() -> None:
    """One chunk larger than the WHOLE budget must not empty the context: zero
    chunks is precisely the signal the trust gate (plan step 5, ``P-33``) reads
    as "retrieval found nothing", so an emptiness manufactured by a budget
    would make the agent answer "I don't have enough information" while
    holding a relevant passage. The best candidate survives whole instead."""
    generous = await _budget_run(100_000, 100_000)

    starved = await _budget_run(1, 1)

    assert len(starved) == 1
    assert starved[0].chunk_id == generous[0].chunk_id
    assert starved[0].text == generous[0].text  # kept WHOLE -- the budget never truncates text


async def test_retrieve_context_context_budget_measures_the_labelled_text() -> None:
    """Retrieval plan §3.2/§3.7, single source of truth: the budget is computed
    on the text as it will actually be SENT -- source label included -- not on
    the raw chunk text. Proven with a ceiling that is comfortably above the two
    chunks' RAW length yet below their LABELLED length: measuring raw would
    keep both, measuring what the model sees keeps one."""
    embeddings = FakeEmbeddings(dim=6)
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    citation: Json = {
        "file_name": "quarterly-report.pdf",
        "page_number": 4,
        "section": "Regional Breakdown",
    }
    parsed = _parsed_document(
        [
            _parsed_chunk("revenue figures for the northern region", order=0, metadata=citation),
            _parsed_chunk("headcount plans for the southern region", order=1, metadata=citation),
        ]
    )
    await IndexDocument(embeddings, vectors).execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )

    async def _run(max_chars: int) -> list[RetrievedChunk]:
        result = await RetrieveContext(
            embeddings,
            vectors,
            FakeParentRepo(),
            tuning=replace(_TUNING, max_context_chars=max_chars, max_context_tokens=100_000),
        ).execute(
            ctx,
            space_id=None,
            query="revenue figures and headcount plans by region",
            model="m",
            api_key="k",
            k=2,
        )
        return result.chunks

    both = await _run(100_000)
    assert len(both) == 2
    assert all(chunk.file_name == "quarterly-report.pdf" for chunk in both)

    ceiling = len(_labelled(both[0]))
    # The premise: raw text alone would have fitted BOTH chunks under this
    # ceiling -- so a budget that ignored the label would keep two.
    assert sum(len(chunk.text) for chunk in both) <= ceiling

    assert len(await _run(ceiling)) == 1


def test_retrieval_tuning_defaults_mirror_settings_field_for_field() -> None:
    """Plan step 18 (``P-30`` ``P-40``, س-24): ``RetrievalTuning``'s defaults
    are declared to MIRROR their ``Settings`` home byte for byte, so a direct
    construction (a test, a script) gets the SHIPPED numbers rather than a
    second, accidental configuration. A mirror nobody checks is just a copy,
    so this is the check -- and it is exhaustive on purpose: a field added to
    one side and forgotten on the other is exactly the drift the mirror
    exists to prevent."""
    limits = Limits()
    settings = RetrievalSettings()

    assert (
        RetrievalTuning(
            weight_dense=settings.weight_dense,
            weight_bm25=settings.weight_bm25,
            rrf_k=settings.rrf_k,
            search_overfetch=settings.search_overfetch,
            max_search_candidates=settings.max_search_candidates,
            max_sparse_candidates=settings.max_sparse_candidates,
            fusion_retention=settings.fusion_retention,
            default_k=settings.default_k,
            max_k=limits.max_rag_k,
            min_dense_score=settings.min_dense_score,
            min_bm25_score=settings.min_bm25_score,
            min_fused_score=settings.min_fused_score,
            relative_floor=settings.relative_floor,
            jaccard_threshold=settings.jaccard_threshold,
            max_parent_chunk_chars=settings.max_parent_chunk_chars,
            mmr_lambda=settings.mmr_lambda,
            mmr_overfetch=settings.mmr_overfetch,
            max_context_chars=limits.max_context_chars,
            max_context_tokens=limits.max_context_tokens,
        )
        == _TUNING
    )
    # Every field of the dataclass is named above -- so a NEW knob cannot be
    # added to one side alone and still pass this test.
    assert len(dataclasses.fields(RetrievalTuning)) == 19


def test_retrieval_tuning_is_the_only_configuration_seam_no_getenv_anywhere() -> None:
    """س-24 = أ in two halves. First: the values are passed as ARGUMENTS, so
    the knowledge module reads neither the environment nor ``Settings``
    itself -- there is no ``os.getenv`` and no ``Settings`` import in the
    module's application or domain layers (``lint-imports`` guards the
    domain's purity; this guards the convention for both). Second: there is
    no PER-REQUEST override -- ``execute`` takes no tuning argument at all,
    so a request cannot reach one."""
    module_root = pathlib.Path(retrieval_module.__file__).parents[1]
    sources = [
        path for part in ("application", "domain") for path in (module_root / part).rglob("*.py")
    ]
    assert sources
    # Read as SYNTAX, not as text: these files discuss ``os.getenv`` and
    # ``Settings`` at length in their docstrings (saying exactly why neither is
    # there), so a substring check would fail on the very prose that documents
    # the rule. An import is the only way to REACH either, so the imports are
    # what is asserted.
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "os" for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module.split(".")[0] != "os", path
                assert not module.startswith("app.framework.settings"), path

    execute_params = inspect.signature(RetrieveContext.execute).parameters
    assert "tuning" not in execute_params
    assert "tuning" in inspect.signature(RetrieveContext.__init__).parameters


async def test_retrieve_context_default_k_comes_from_the_tuning_not_a_literal() -> None:
    """``P-40``: the ``k`` a caller does not name is the DEPLOYMENT's, which
    is what let the RAG agent drop its own ``_TOP_K = 5``. Proven by MOVING
    it — a tuning with a different ``default_k`` changes the fetch depth of a
    call that names no ``k`` at all — because an assertion that the shipped
    value is 5 would pass just as well against the hard-coded literal this
    step removed."""
    assert inspect.signature(RetrieveContext.execute).parameters["k"].default is None
    assert _TUNING.default_k == 5

    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["document content about a specific product line"])

    await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="document content", model="m", api_key="k"
    )
    assert vectors.search_calls[-1][1] == _TUNING.default_k * max(
        _TUNING.search_overfetch, _TUNING.mmr_overfetch
    )

    await RetrieveContext(
        embeddings, vectors, FakeParentRepo(), tuning=replace(_TUNING, default_k=2)
    ).execute(ctx, space_id=None, query="document content", model="m", api_key="k")
    assert vectors.search_calls[-1][1] == 2 * max(_TUNING.search_overfetch, _TUNING.mmr_overfetch)


async def test_retrieve_context_empty_query_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        await RetrieveContext(FakeEmbeddings(), FakeHybridVectors(), FakeParentRepo()).execute(
            _ctx(), space_id=None, query="   ", model="m", api_key="k"
        )


async def test_retrieve_context_empty_corpus_returns_empty_list() -> None:
    result = await RetrieveContext(FakeEmbeddings(), FakeHybridVectors(), FakeParentRepo()).execute(
        _ctx(), space_id=None, query="anything at all", model="m", api_key="k"
    )
    assert result.chunks == []
    # No hits on either leg -- the confidence signals are honestly absent
    # (retrieval plan §3.3, ``P-28``), never a misleading ``0.0``.
    assert result.best_dense_score is None
    assert result.best_bm25_score is None


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

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx_b, space_id=None, query=shared_text, model="m", api_key="k"
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].chunk_id == chunk_point_id("doc-b", 0)
    assert vectors.search_calls[-1][2] == {"workspace_id": "ws-b"}
    assert vectors.search_sparse_calls[-1][2] == {"workspace_id": "ws-b"}


# --------------------------------------------------------------------------- #
# MMR in the live pipeline (plan §3.9 / §4 row 20, `P-23`, س-20)              #
# --------------------------------------------------------------------------- #
# One paragraph said five ways: five chunks a query matches almost identically
# (the SAME vector direction) but which share few words, so `filter_relevant`'s
# lexical Jaccard dedup at 0.95 does not touch them -- exactly the case §3.9
# calls "خمس قطع من الفقرة نفسها نتيجة مشروعة اليوم". Plus two chunks on
# genuinely other subjects, pointing elsewhere in vector space.
_PARAPHRASES = (
    "annual leave accrues monthly for every full-time member of staff",
    "each permanent employee earns holiday entitlement as the year progresses",
    "vacation days build up over twelve months across the whole workforce",
    "paid time off is credited gradually throughout an ordinary working year",
    "staff accumulate their yearly break allowance on a rolling schedule",
    "colleagues gather days away steadily during each calendar twelvemonth",
)
_OTHER_SUBJECTS = (
    "reimbursement claims must reach finance within thirty days of travel",
    "the fire assembly point is the courtyard behind the loading bay",
)


async def _seed_one_paragraph_five_ways(vectors: FakeHybridVectors, ctx: ExecutionContext) -> None:
    """Six paraphrases on one axis, two other subjects on two more."""
    await vectors.upsert(
        knowledge_collection(ctx.workspace_id),
        [
            *(
                _hand_built_point(ctx, "doc-1", index, text, [1.0, 0.0, 0.0, 0.0])
                for index, text in enumerate(_PARAPHRASES)
            ),
            _hand_built_point(ctx, "doc-1", 10, _OTHER_SUBJECTS[0], [0.0, 1.0, 0.0, 0.0]),
            _hand_built_point(ctx, "doc-1", 11, _OTHER_SUBJECTS[1], [0.0, 0.0, 1.0, 0.0]),
        ],
    )


async def test_retrieve_context_asks_both_legs_for_the_candidates_vectors() -> None:
    """§3.9's declared price, paid explicitly: MMR's diversity term is
    candidate-to-candidate similarity, so both legs -- not just the dense one
    -- must return each hit's own vector. A sparse-only candidate with no
    vector would be the one entry nothing checked for redundancy."""
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["quarterly revenue figures for the north"])

    await RetrieveContext(FakeEmbeddings(), vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="quarterly revenue", model="m", api_key="k"
    )

    assert vectors.with_vectors_calls == [True, True]


async def test_retrieve_context_does_not_deliver_five_chunks_of_one_paragraph() -> None:
    """The behaviour plan row 20 exists to buy (§3.9). Six chunks say the same
    thing in different words and are the six best matches for the question;
    two chunks are about something else entirely and rank last. Ranked by
    relevance alone the delivered ``k = 3`` would be three retellings of one
    paragraph -- a legitimate result today, and a useless one. With MMR the
    caller gets the best of the six PLUS both other subjects."""
    query = "how does annual leave accrue"
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_one_paragraph_five_ways(vectors, ctx)

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query=query, model="m", api_key="k", k=3
    )

    delivered = [chunk.text for chunk in result.chunks]
    assert delivered[0] == _PARAPHRASES[0]  # the most relevant chunk is still `[#1]` (§3.7)
    assert set(delivered[1:]) == set(_OTHER_SUBJECTS)
    # ... and only ONE of the six near-duplicates got a slot.
    assert sum(1 for text in delivered if text in _PARAPHRASES) == 1


async def test_retrieve_context_without_mmr_would_deliver_the_same_paragraph_three_times() -> None:
    """The control for the test above, on the identical corpus: at
    ``mmr_lambda = 1.0`` the diversity term is multiplied by zero and the
    pipeline is exactly what it was before plan row 20. All three delivered
    chunks are then retellings of one paragraph -- which is what makes the
    previous test a change in behaviour rather than a restatement of it."""
    query = "how does annual leave accrue"
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_one_paragraph_five_ways(vectors, ctx)

    result = await RetrieveContext(
        embeddings, vectors, FakeParentRepo(), tuning=replace(_TUNING, mmr_lambda=1.0)
    ).execute(ctx, space_id=None, query=query, model="m", api_key="k", k=3)

    assert [chunk.text for chunk in result.chunks] == list(_PARAPHRASES[:3])


async def test_retrieve_context_logs_the_mmr_cut(caplog: pytest.LogCaptureFixture) -> None:
    """The stage log's own entry for row 20 (row 17's shape, one more count --
    never a second record). ``mmr_count`` below ``fused_count`` IS the
    diversity cut: the number that says whether the widened pool -- and the
    vectors it puts on the wire -- is paying for itself."""
    query = "how does annual leave accrue"
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_one_paragraph_five_ways(vectors, ctx)

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query=query, model="m", api_key="k", k=2
        )

    payload = _stage_payload(_stage_record(caplog))
    # `k = 2` -> the legs fetch 12, RRF fuses a pool of 12 and MMR hands on 6.
    assert (payload["mmr_pool_k"], payload["retain_k"]) == (12, 6)
    assert payload["fused_count"] == 8
    assert payload["mmr_count"] == 6


async def test_retrieve_context_still_retrieves_from_a_store_that_returns_no_vectors() -> None:
    """The honest degradation (``_mmr_rerank``): a store that ignores
    ``with_vectors`` leaves MMR nothing to diversify with, so the RRF order
    stands and retrieval keeps working exactly as it did before row 20 --
    never an exception, never an empty result."""

    class _VectorlessStore(FakeHybridVectors):
        async def search(  # type: ignore[override]
            self,
            collection: str,
            vector: list[float],
            k: int,
            flt: Json | None = None,
            *,
            with_vectors: bool = False,
        ) -> list[VectorHit]:
            hits = await super().search(collection, vector, k, flt, with_vectors=with_vectors)
            return [replace(hit, vector=None) for hit in hits]

        async def search_sparse(  # type: ignore[override]
            self,
            collection: str,
            sparse: SparseVector,
            k: int,
            flt: Json | None = None,
            *,
            with_vectors: bool = False,
        ) -> list[VectorHit]:
            hits = await super().search_sparse(
                collection, sparse, k, flt, with_vectors=with_vectors
            )
            return [replace(hit, vector=None) for hit in hits]

    query = "how does annual leave accrue"
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = _VectorlessStore()
    ctx = _ctx("ws1")
    await _seed_one_paragraph_five_ways(vectors, ctx)

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query=query, model="m", api_key="k", k=3
    )

    # Pure RRF order -- the pre-row-20 behaviour, which is exactly the point.
    assert [chunk.text for chunk in result.chunks] == list(_PARAPHRASES[:3])


# --------------------------------------------------------------------------- #
# The structured stage log (plan §3.11 / §4 row 17, `P-29`, س-25 = أ)          #
# --------------------------------------------------------------------------- #
# Every field one `knowledge.retrieval` record carries. Pinned as a SET, and
# pinned at all, because this is the whole deliverable of row 17: the record
# IS the interface (س-25 = أ keeps it out of the public contract, so nothing
# else describes it), and a field silently renamed or dropped breaks a
# dashboard with no test anywhere else to notice.
_STAGE_LOG_FIELDS = {
    "query_chars",
    "k",
    "search_k",
    "sparse_k",
    "mmr_pool_k",
    "retain_k",
    "scoped_document_count",
    "space_scoped",
    "dense_count",
    "sparse_count",
    "dense_scores",
    "sparse_scores",
    "best_dense_score",
    "best_bm25_score",
    "dense_kept",
    "sparse_kept",
    "fused_count",
    "candidates",
    "origin_counts",
    "mmr_count",
    "relevant_count",
    "widened_count",
    "budgeted_count",
    "delivered_chunk_ids",
    "context_nodes",
    "fallback",
    "total_ms",
}
_RETRIEVAL_LOGGER = "app.modules.knowledge.application.retrieval"


def _stage_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    """The ONE ``knowledge.retrieval`` record a call emits -- the count is
    part of the assertion: two records would mean a return path logged
    twice, and none would mean a path measures nothing at all."""
    records = [r for r in caplog.records if r.getMessage() == "knowledge.retrieval"]
    assert len(records) == 1
    return records[0]


def _stage_payload(record: logging.LogRecord) -> dict[str, Any]:
    """The record as a log sink actually receives it -- rendered through the
    real ``JsonFormatter`` (redaction included), then stripped of the
    envelope keys every record carries."""
    payload: dict[str, Any] = json.loads(JsonFormatter().format(record))
    for envelope in ("time", "level", "logger", "message", "request_id", "correlation_id"):
        payload.pop(envelope, None)
    payload.pop("workspace_id", None)
    return payload


async def test_retrieve_context_emits_one_stage_record_carrying_every_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plan §3.11's first requirement -- "عدّ ودرجات كلّ مرحلة": one record
    per call, with a count for every stage of the pipeline and the two
    ``P-28`` confidence signals, so a retrieval that returned two chunks can
    be explained without re-running it."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(
        vectors,
        ctx,
        "doc-1",
        [
            "quarterly revenue figures for the northern region",
            "quarterly revenue commentary for the southern region",
        ],
    )

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query="quarterly revenue figures", model="m", api_key="k", k=2
        )

    payload = _stage_payload(_stage_record(caplog))
    assert set(payload) == _STAGE_LOG_FIELDS
    assert payload["query_chars"] == len("quarterly revenue figures")
    assert (
        payload["k"],
        payload["search_k"],
        payload["sparse_k"],
        payload["mmr_pool_k"],
        payload["retain_k"],
    ) == (2, 12, 12, 12, 6)
    assert payload["scoped_document_count"] is None
    assert payload["space_scoped"] is False
    # The count chain, stage by stage: nothing appears out of nowhere and the
    # delivered chunks are what the caller actually got.
    assert payload["dense_count"] == payload["dense_kept"] == 2
    assert payload["sparse_count"] == payload["sparse_kept"] == 2
    assert payload["fused_count"] == payload["mmr_count"] == 2
    assert payload["relevant_count"] == payload["widened_count"] == payload["budgeted_count"] == 2
    assert payload["context_nodes"] == len(result.chunks) == 2
    assert payload["delivered_chunk_ids"] == [chunk.chunk_id for chunk in result.chunks]
    assert payload["fallback"] is False
    assert payload["total_ms"] >= 0
    # The `P-28` signals ride along, exactly as the plan's §3.11 asks
    # ("والدرجات الخامّ") -- the same numbers the result carries.
    assert payload["best_dense_score"] == pytest.approx(result.best_dense_score)
    assert payload["best_bm25_score"] == pytest.approx(result.best_bm25_score)


async def test_retrieve_context_stage_scores_are_the_legs_own_not_rrfs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plan §3.11's load-bearing detail -- "لقطة **قبل** أن يدهس RRF الدرجات
    الخامّ". The per-leg scores are captured BEFORE fusion, so they are the
    store's own cosine numbers; RRF's are a different quantity on a
    different scale (``Σ w/(60+rank)`` -- thousandths however good the
    candidate) and live in their own ``rrf_score`` field. Proved by
    arithmetic rather than by inspection: the raw dense scores here are
    EXACTLY ``1.0`` and ``-1.0`` (hand-placed vectors), values RRF can never
    produce."""
    query = "ZX9000QRS calibration"
    near_text = "the ZX9000QRS calibration procedure for the north wing"
    far_text = "an unrelated onboarding checklist for new employees"
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await vectors.upsert(
        knowledge_collection(ctx.workspace_id),
        [
            _hand_built_point(ctx, "doc-1", 0, near_text, [1.0, 0.0, 0.0, 0.0]),
            _hand_built_point(ctx, "doc-1", 1, far_text, [-1.0, 0.0, 0.0, 0.0]),
        ],
    )

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query=query, model="m", api_key="k", k=2
        )

    payload = _stage_payload(_stage_record(caplog))
    assert payload["dense_scores"] == pytest.approx([1.0, -1.0])
    assert payload["best_dense_score"] == pytest.approx(1.0)
    # A NEGATIVE score survived into the record: nothing between the store
    # and the log clipped or re-based the leg's own scale (the shipped floors
    # are `0.0` = disabled by an explicit branch -- plan step 16).
    assert min(payload["dense_scores"]) < 0.0
    # ... and RRF's numbers are demonstrably NOT those: every fused score is
    # below `weight_dense / (rrf_k + 1)`, the largest value the formula can
    # award a single-leg rank-1 candidate.
    rrf_ceiling = (_TUNING.weight_dense + _TUNING.weight_bm25) / (_TUNING.rrf_k + 1)
    assert [candidate["rrf_score"] for candidate in payload["candidates"]]
    assert all(candidate["rrf_score"] <= rrf_ceiling for candidate in payload["candidates"])
    assert all(
        candidate["rrf_score"] not in payload["dense_scores"] for candidate in payload["candidates"]
    )


async def test_retrieve_context_tags_each_candidate_with_the_leg_that_found_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plan §3.11's ``retrieval_origin`` tag: ``dense`` / ``bm25`` / ``both``.
    All three occur here at once -- a chunk both legs voted for, chunks only
    the dense leg reached, and a lexically-rescued chunk whose vector is the
    WORST in the corpus (the hybrid pipeline's own justification, showing up
    as data).

    The corpus is deliberately DEEPER than the dense leg's fetch (eight points
    against ``search_k = 1 x 6``, plan row 20's widened overfetch), because
    that cut is the only thing that can make a chunk sparse-ONLY: a dense leg
    that reaches every point in the store tags everything it also matched
    lexically ``both``."""
    query = "ZX9000QRS calibration"
    both_text = "the ZX9000QRS calibration procedure for the north wing"
    dense_text = "an onboarding checklist for new employees joining the team"
    sparse_text = "refer to the ZX9000QRS calibration steps in appendix B"
    # Four fillers with no lexical overlap, spread down the cosine ranking so
    # the dense leg's own top-6 cut falls ABOVE `sparse_text`.
    fillers = (
        ("cafeteria menu changes announced for the coming month", [0.6, 0.8, 0.0, 0.0]),
        ("parking permits renew automatically every summer", [0.4, 0.9, 0.0, 0.0]),
        ("the annual photography contest opens in autumn", [0.2, 0.98, 0.0, 0.0]),
        ("library opening hours during public holidays", [0.0, 1.0, 0.0, 0.0]),
    )
    embeddings = FakeEmbeddings(dim=4, overrides={query: [1.0, 0.0, 0.0, 0.0]})
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await vectors.upsert(
        knowledge_collection(ctx.workspace_id),
        [
            _hand_built_point(ctx, "doc-1", 0, both_text, [1.0, 0.0, 0.0, 0.0]),
            _hand_built_point(ctx, "doc-1", 1, dense_text, [0.8, 0.6, 0.0, 0.0]),
            # Ranked LAST by cosine, so the dense leg's cut excludes it.
            _hand_built_point(ctx, "doc-1", 3, sparse_text, [-1.0, 0.0, 0.0, 0.0]),
            *(
                _hand_built_point(ctx, "doc-1", 4 + index, text, vector)
                for index, (text, vector) in enumerate(fillers)
            ),
            _hand_built_point(
                ctx, "doc-1", 9, "swimming pool maintenance notice", [-0.5, 0.87, 0.0, 0.0]
            ),
        ],
    )

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query=query, model="m", api_key="k", k=1
        )

    payload = _stage_payload(_stage_record(caplog))
    tagged = {
        candidate["chunk_id"]: candidate["retrieval_origin"] for candidate in payload["candidates"]
    }
    assert tagged[chunk_point_id("doc-1", 0)] == "both"
    assert tagged[chunk_point_id("doc-1", 1)] == "dense"
    # The dense leg never returned it (its vector is the corpus's worst match
    # and the fetch stops above it); it is in the pool on lexical recall
    # alone.
    assert tagged[chunk_point_id("doc-1", 3)] == "bm25"
    assert payload["origin_counts"]["both"] == 1
    assert payload["origin_counts"]["bm25"] == 1
    assert sum(payload["origin_counts"].values()) == payload["fused_count"]


async def test_retrieve_context_logs_fallback_true_when_it_returns_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The plan's ``fallback`` measurement, recorded from the one vantage
    point that can also say WHY: zero chunks here is the exact condition the
    honest-fallback trust gate one layer up fires on (plan step 5,
    ``P-33``), and the stage counts beside it are the explanation."""
    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        result = await RetrieveContext(
            FakeEmbeddings(), FakeHybridVectors(), FakeParentRepo()
        ).execute(_ctx(), space_id=None, query="anything at all", model="m", api_key="k")

    payload = _stage_payload(_stage_record(caplog))
    assert result.chunks == []
    assert payload["fallback"] is True
    assert payload["context_nodes"] == 0
    assert payload["dense_count"] == payload["sparse_count"] == 0
    # An empty leg's signal is honestly absent in the log too, never `0.0`
    # (retrieval plan §3.3) -- a real score on a cosine scale.
    assert payload["best_dense_score"] is None
    assert payload["best_bm25_score"] is None


async def test_retrieve_context_an_empty_scope_logs_the_same_record_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The early return (a scope that resolved to no documents -- BE-RAG-005)
    searches nothing, and it is the one outcome nothing downstream could
    otherwise explain. It emits the SAME field set as a full run, so a log
    query never has to special-case it: ``scoped_document_count == 0`` beside
    ``dense_count == 0`` says "a scope resolved to nothing", not "the corpus
    had nothing to say"."""
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    await _seed_corpus(vectors, ctx, "doc-1", ["quarterly revenue figures for the north"])

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        await RetrieveContext(FakeEmbeddings(), vectors, FakeParentRepo()).execute(
            ctx,
            space_id=None,
            query="quarterly revenue",
            model="m",
            api_key="k",
            document_ids=[],
        )

    payload = _stage_payload(_stage_record(caplog))
    assert set(payload) == _STAGE_LOG_FIELDS
    assert payload["scoped_document_count"] == 0
    assert payload["dense_count"] == 0
    assert payload["fallback"] is True
    assert vectors.search_calls == []  # nothing was searched, and the record says so


async def test_retrieve_context_stage_record_samples_the_numbers_never_the_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_LOG_RANKING_SAMPLE`` bounds how deep the record quotes ACTUAL
    numbers, so a large ``k`` cannot turn one retrieval into a log entry
    hundreds of scores long. The counts stay EXACT -- they are what the
    sample is bounded against."""
    sample = retrieval_module._LOG_RANKING_SAMPLE
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    corpus = [f"quarterly revenue figures for region number {index}" for index in range(sample + 5)]
    await _seed_corpus(vectors, ctx, "doc-1", corpus)

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query="quarterly revenue figures", model="m", api_key="k", k=50
        )

    payload = _stage_payload(_stage_record(caplog))
    assert payload["dense_count"] == len(corpus)
    assert payload["fused_count"] == len(corpus)
    assert len(payload["dense_scores"]) == sample
    assert len(payload["sparse_scores"]) <= sample
    assert len(payload["candidates"]) == sample
    # The aggregate still covers EVERY candidate, not just the sampled head.
    assert sum(payload["origin_counts"].values()) == len(corpus)


async def test_retrieve_context_stage_record_carries_no_document_or_query_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """10-code-standards §10: no sensitive user content in logs. The record
    holds counts, scores, ids, origins and durations -- never a chunk's text,
    never a file name, and never the question, which is present as its LENGTH
    alone. Asserted on the RENDERED line (the exact bytes a sink receives),
    so a future field cannot smuggle content past this test."""
    query = "what did the quarterly report say about the northern region?"
    chunk_text = "northern region revenue for the third quarter reached four million"
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(
                chunk_text,
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

    with caplog.at_level(logging.INFO, logger=_RETRIEVAL_LOGGER):
        result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
            ctx, space_id=None, query=query, model="m", api_key="k", k=1
        )

    assert result.chunks  # the record below describes a real, non-empty answer
    record = _stage_record(caplog)
    rendered = JsonFormatter().format(record)
    assert query not in rendered
    assert chunk_text not in rendered
    assert "quarterly-report.pdf" not in rendered
    assert "Regional Breakdown" not in rendered
    # What DOES stand in for the question: its length, which explains a
    # degenerate embedding or an empty sparse leg without quoting a word.
    assert _stage_payload(record)["query_chars"] == len(query)


# --------------------------------------------------------------------------- #
# The internal `context_text` capability (plan row ١٩, `P-39`; retrieval plan #
# §3.2/§3.11, س-25 = أ) — built by the ONE shared formatting unit, ordered    #
# descending, and absent from every published contract.                       #
# --------------------------------------------------------------------------- #
_CONTEXT_CORPUS = (
    "northern region revenue for the third quarter reached four million",
    "southern region revenue for the third quarter reached two million",
    "the maintenance responsibilities are listed in the third quarter annex",
)


async def _context_run(*, k: int = 3) -> RetrievalResult:
    """One end-to-end retrieval over a small indexed corpus that CARRIES the
    three citation fields, so the label the shared formatter builds is a real
    ``[file p.N | section: S]`` rather than the ``[unknown]`` degradation."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")
    parsed = _parsed_document(
        [
            _parsed_chunk(
                text,
                order=order,
                metadata={
                    "file_name": "quarterly-report.pdf",
                    "page_number": order + 1,
                    "section": f"Section {order}",
                },
            )
            for order, text in enumerate(_CONTEXT_CORPUS)
        ]
    )
    await IndexDocument(embeddings, vectors).execute(
        ctx, document_id="doc-1", space_id=None, parsed=parsed, model="m", api_key="k"
    )
    return await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="third quarter revenue", model="m", api_key="k", k=k
    )


async def test_context_text_is_the_delivered_chunks_rendered_by_the_shared_formatter() -> None:
    """Plan row ١٩ (``P-39``): ``context_text`` is READY, and it is built "بوحدة
    التنسيق نفسها" — row ٢'s formatter. Asserted against
    ``format_labeled_chunk``'s OWN output (the per-chunk unit) rather than a
    hand-copied shape, so a second formatter appearing anywhere on this path
    fails right here."""
    result = await _context_run()

    assert result.chunks  # a real, non-empty context to render
    assert result.context_text == "\n\n".join(
        format_labeled_chunk(
            chunk.text,
            file_name=chunk.file_name,
            page_number=chunk.page_number,
            section=chunk.section,
        )
        for chunk in result.chunks
    )
    # …and it is literally the shared block renderer's output, which is the
    # exact call the RAG agent's synthesis path makes (see
    # `test_rag_agent.py`'s drift test for the two being compared directly).
    assert result.context_text == format_context_block(result.chunks)
    # The §3.2 shape actually reached the string — not vacuously equal.
    assert "[quarterly-report.pdf p.1 | section: Section 0]" in result.context_text


async def test_context_text_puts_the_most_relevant_chunk_first() -> None:
    """§3.7's ordering rule: "الترتيب هنا: تنازليّ ثمّ قصّ، والأكثر صلة في
    ``[#1]``". ``context_text`` renders the delivered prefix in the delivered
    order — the top-ranked chunk OPENS the block. ``LongContextReorder``,
    which would move it to the END, is a rejected design note (§3.7/§7) and
    must never appear as code."""
    result = await _context_run()
    passages = result.context_text.split("\n\n")

    assert len(passages) == len(result.chunks)
    for passage, chunk in zip(passages, result.chunks, strict=True):
        assert passage.splitlines()[1] == chunk.text
    # Said once more the blunt way: the FIRST chunk `execute` returned (the
    # highest-ranked survivor) is the first thing a model would read.
    assert result.context_text.startswith(
        format_labeled_chunk(
            result.chunks[0].text,
            file_name=result.chunks[0].file_name,
            page_number=result.chunks[0].page_number,
            section=result.chunks[0].section,
        )
    )


async def test_context_text_is_empty_when_retrieval_delivered_nothing() -> None:
    """The honest empty: a scope that resolved to no documents returns no
    chunks, and the context is ``""`` — never a manufactured sentence, which
    would rob the trust gate (plan row 5, ``P-33``) of the very condition it
    fires on."""
    embeddings = FakeEmbeddings()
    vectors = FakeHybridVectors()
    ctx = _ctx("ws1")

    result = await RetrieveContext(embeddings, vectors, FakeParentRepo()).execute(
        ctx, space_id=None, query="anything", model="m", api_key="k", document_ids=[]
    )

    assert result.chunks == []
    assert result.context_text == ""


def test_context_text_is_a_computed_property_not_a_carried_field() -> None:
    """Internal AND cheap: a property on the application-layer result, so a
    retrieval nobody asks a context for pays nothing, and there is no stored
    second copy to fall out of step with ``chunks``."""
    assert isinstance(RetrievalResult.context_text, property)
    assert "context_text" not in {f.name for f in dataclasses.fields(RetrievalResult)}


def test_context_text_never_reaches_a_published_contract() -> None:
    """س-25 = أ, recorded in the plan's §7: ``context_text`` is an INTERNAL
    capability — it exposes index structure and would need permission scoping
    of its own. So it must appear in NO wire DTO, in NO API route module and
    nowhere in ``openapi.yaml``. Checked as text over the whole API layer
    rather than on one model, because the rule is about the contract surface,
    not about one class."""
    repo_root = pathlib.Path(__file__).parents[2]

    spec = (repo_root / "docs/design/openapi.yaml").read_text(encoding="utf-8")
    assert "context_text" not in spec

    api_sources = sorted((repo_root / "src/app/api").rglob("*.py"))
    assert api_sources  # the walk found the layer at all
    for source in api_sources:
        assert "context_text" not in source.read_text(encoding="utf-8"), source

    # The port DTO the API renders stays the four+three field shape row 1
    # fixed (`test_retrieved_chunk_contract.py` pins all three layers to it).
    assert "context_text" not in {f.name for f in dataclasses.fields(RetrievedChunk)}
