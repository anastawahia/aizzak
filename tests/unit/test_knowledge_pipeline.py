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
import json
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
from app.modules.knowledge.domain.chunking import (
    MIN_NODE_CHARS,
    SPLIT_OVERLAP_RATIO,
    SourceSegment,
    chunk_segments,
    max_words_for_token_limit,
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


async def test_index_document_table_parent_text_never_reaches_the_qdrant_payload() -> None:
    """Constraint 1 (plan §3.2): the payload carries only what
    ``_CITATION_KEYS`` allowlists -- never a parent chunk's text, and never
    the internal ``_table_parent_key`` scratch metadata either."""
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
