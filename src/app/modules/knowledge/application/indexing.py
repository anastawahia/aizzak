"""IndexDocument use-case — hybrid (dense + BM25-sparse) indexing pipeline
(06-domain-models §7; docs/migration/refs/retrieval.md §4.1-§4.2, §6.2,
§7 risk #1; 3.k3).

Bridges the pure domain (``chunking``/``sparse``/``collections``) to the
injected ``EmbeddingProvider``/``HybridVectorStore`` ports, mirroring
``memory``'s ``RecallRelevant`` as this module's use-case that touches vector
infrastructure directly (``memory/application/use_cases.py``). One Qdrant
point per chunk carries BOTH a dense ``.vector`` (from the embedding model)
and a sparse ``.sparse`` (raw term-frequency counts; deferred-IDF — see
``HybridVectorStore``'s docstring) — the hybrid design chosen for 3.k3.

Each point's id is ``chunk_point_id(document_id, seq)`` — deterministic
(``uuid5``), so re-running ``execute`` for the same document (a retry after a
worker crash, at-least-once event redelivery) naturally upserts the same
points rather than duplicating them (mirrors INV-K1's
``UNIQUE(document_id, seq)`` at the Postgres layer, projected onto Qdrant).

The Qdrant point payload only carries a small, explicit citation allowlist
copied out of each parser's free-form ``ParsedChunk.metadata`` (the payload
schema is not a decided storage contract — parsers.md §6 risk #4) — not the
whole metadata dict.

Since the spaces plan's step 8 it also carries ``space`` — the document's
owning space, the key ``RetrieveContext`` filters on and the one step 9 will
give an ``is_tenant`` payload index (§3.4). It is the reason §5-أ's re-index
is mandatory: every point written before this step lacks the key, and Qdrant
matches no point that is missing a filtered field.

**Semantic pre-splitting (P-20, rag-indexing-plan.md §4 step 13, §3.4).**
Before a non-table ``ParsedChunk`` reaches the pure word-window chunker
(``domain/chunking.py``'s ``chunk_segments``), ``execute`` splits its text
into sentences and calls ``EmbeddingProvider.embed`` on THEM -- a SECOND,
permanent, declared embedding pass over every document's sentences, on top
of the one every surviving node already pays for (plan §3.4 constraint 3:
"the extra embedding pass is a permanent, declared operational cost"). The
resulting per-sentence vectors are handed to pure math, not this module's own:
``domain/chunking.py``'s ``semantic_boundaries`` decides WHERE the segment
actually breaks topically; this module only owns the I/O half (splitting
text into sentences and calling the provider) and the glue that turns
those boundaries back into several ``SourceSegment``s, one per semantic
part, each still subject to the ordinary word-window hard cap (constraint
1) exactly like any other segment. Table-kind chunks (``ParsedChunkKind.
TABLE``, the tagging step 7 already attaches) skip this entirely
(constraint 2: a table row is already the retrieval atom, §3.3) and a
segment with fewer than two sentences skips it too (constraint 3: nothing
to compare, no reason to pay for the call).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorPoint
from app.framework.types import Json, Uuid
from app.modules.knowledge.domain.chunking import (
    SEMANTIC_BREAKPOINT_PERCENTILE,
    SEMANTIC_BUFFER,
    SPLIT_OVERLAP_RATIO,
    ChunkToIndex,
    SourceSegment,
    chunk_segments,
    max_words_for_token_limit,
    semantic_boundaries,
)
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.domain.tables import explode_table
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)

log = get_logger(__name__)

# The scratch metadata key a table row/overflow ``SourceSegment`` carries its
# owning table's parent-chunk key under (``_table_to_segments``) -- private
# to this module: it never survives into a Qdrant payload (``_payload`` only
# copies ``_CITATION_KEYS``) and it is stripped back out into
# ``IndexedChunk.parent_key`` before this module returns anything.
_TABLE_PARENT_KEY = "_table_parent_key"

# Mirrors Settings.Limits.embedding_batch (07-nfr-slo §4) -- kept as a local
# constant so the application layer does not depend on the framework Settings
# model (same convention as memory/application/use_cases.py's _MAX_RECALL_K).
_EMBED_BATCH = 128

# P-20 (plan §4 step 13, §3.4) sentence boundaries for Arabic text: the four
# terminal punctuation marks, a paragraph break, and the dedicated Unicode
# Arabic full stop (U+06D4, "۔") -- distinct from the plain "." also in this  # noqa: RUF003
# set. Deliberately NOT `nltk` (plan §3.4's own constraint: "no new
# dependency") -- alpha's own sentence splitter is this same regex-based
# approach, just with Latin-only punctuation, which is exactly what plan §2
# ح-١٤ says does not work unmodified on an Arabic document. One or more
# consecutive punctuation marks (e.g. "!!" or "؟!") collapse to a single
# split, and a blank line (two or more consecutive newlines) is its own
# paragraph-level split.
_SENTENCE_SPLIT = re.compile(r"[.؟!؛۔]+|\n{2,}")  # noqa: RUF001 -- Arabic full stop U+06D4

# The minimum sentence count semantic splitting needs to do anything useful
# (plan §3.4 constraint 3: "skipped gracefully when a segment has fewer than
# two sentences") -- one sentence has no neighbour to compare against, so
# there is no boundary to find and no reason to pay for the embedding call.
_MIN_SENTENCES_TO_SPLIT = 2

# The minimum semantic-part count worth returning as SEVERAL SourceSegments
# rather than the single, unsplit ``_plain_segment`` fallback -- mirrors
# ``_MIN_SENTENCES_TO_SPLIT``'s reasoning one level up: a lone part (every
# sentence landed on the same side of every boundary `semantic_boundaries`
# considered, or it returned none) is not a split worth acting on.
_MIN_SEMANTIC_PARTS = 2

# The payload citation allowlist (P-18, plan §3.9): parser-attached
# diagnostic keys (see ``ParsedChunk.metadata`` in ``ports/content_extractor.py``
# and the parser adapters in 3.k1) worth copying forward for citation
# rendering. The rest of ``metadata`` is deliberately NOT copied wholesale.
#
# ``section`` is handled specially in ``_payload`` below: no parser writes a
# literal ``"section"`` metadata key today -- ``docx.py``'s heading-breadcrumb
# and ``pdf_tables.py``'s caption both land under ``"title"`` (parsers.md §6
# risk #4 -- the payload schema was never a parser contract). ``_payload``
# falls back from ``"section"`` to ``"title"`` so the plan's exact
# ``_CITATION_KEYS`` tuple below can stay literal instead of drifting from the
# design doc, while the value it copies still comes from the field a producer
# genuinely emits.
#
# ``file_name`` (plan §3.9, ح-١٣/ح-١٦): every ``ParsedChunk.metadata``
# carries it since ``adapters/parsers/extractor.py``'s ``_enrich`` -- the one
# layer that already receives ``extract()``'s ``filename`` argument -- stamps
# it on every chunk, for every route (PDF text, PDF tables, DOCX, Excel,
# JSON, images, OCR alike). Nothing in THIS module resolves the filename
# itself; ``_payload`` below still just copies whatever ``chunk.metadata``
# carries, the same as every other key in this tuple -- a chunk that somehow
# arrives without it (a future producer that forgets to route through
# ``_enrich``) still degrades to a silently-absent payload key rather than a
# crash, exactly like any other citation key here.
_CITATION_KEYS = (
    "file_name",
    "page_number",
    "section",
    "sheet_name",
    "section_type",
    "table_name",
)


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """One indexed chunk's identity + text, returned for the caller's own
    bookkeeping (e.g. persisting ``knowledge.chunks`` rows in a later step --
    3.k4's ``IndexRegisteredDocument``). ``text`` is carried because
    ``knowledge.chunks.text`` is ``NOT NULL`` (01-data-model §2.7).

    ``parent_key`` (P-13, plan §3.3) is ``None`` for every chunk that did not
    come from a table row explosion; for one that did, it is the key of the
    matching entry in the SAME call's ``IndexOutcome.parents`` -- the caller
    (``IndexRegisteredDocument.finalize``) mints the real ``ParentChunk.id``
    and resolves this key to it before building the ``Chunk`` row, the same
    way ``Chunk.id`` itself is minted one layer up rather than here.
    """

    chunk_id: str
    seq: int
    text: str
    token_count: int
    parent_key: str | None = None


@dataclass(frozen=True, slots=True)
class ParentChunkDraft:
    """One parent-chunk candidate produced while exploding a table (P-13,
    plan §3.3) -- not yet a persisted ``ParentChunk`` row: id minting +
    the ``add_parent_chunks`` call are the application layer's job one level
    up (``IndexRegisteredDocument.finalize``), mirroring how ``Chunk.id``
    itself is minted there and not in this module."""

    key: str
    order: int
    text: str


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    """The result of one ``IndexDocument.execute`` call.

    ``parents`` (P-13, plan §3.3) holds one draft per table that actually
    contributed at least one indexed row chunk -- a table whose every row
    filtered away to nothing (§3.3's "an entirely noise/empty row") never
    grows an orphan ``parent_chunks`` row nobody's ``Chunk.parent_id`` points
    at.
    """

    collection: str
    dimensions: int
    chunks: tuple[IndexedChunk, ...]
    parents: tuple[ParentChunkDraft, ...] = ()


class IndexDocument:
    """Chunk a parsed document, embed it (dense) and hash it (sparse), and
    upsert one hybrid Qdrant point per chunk."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vectors: HybridVectorStore,
        *,
        embedding_max_input_tokens: int = 512,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        # P-16 (plan §4 step 9, §3.5 + decision س-11): the real token budget
        # is a `Settings` value (the constructor's own default mirrors
        # `EmbeddingServiceSettings.embedding_max_input_tokens`'s default),
        # resolved to a word window ONCE here rather than per `execute` call
        # -- the pure formula lives in `domain/chunking.py`, this layer only
        # supplies the argument (ح-6/ح-7, plan §2).
        self._max_words = max_words_for_token_limit(embedding_max_input_tokens)
        self._overlap_words = int(self._max_words * SPLIT_OVERLAP_RATIO)

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        space_id: Uuid | None,
        parsed: ParsedDocument,
        model: str,
        api_key: str,
    ) -> IndexOutcome:
        segments: list[SourceSegment] = []
        parent_drafts: list[ParentChunkDraft] = []
        for index, chunk in enumerate(parsed.chunks):
            if chunk.kind is ParsedChunkKind.TABLE:
                # P-20 constraint 2 (plan §3.4): a TABLE-kind chunk skips
                # semantic pre-splitting entirely, whether or not it actually
                # explodes into rows below -- the tagging IS `chunk.kind`,
                # not "did `_table_to_segments` succeed".
                exploded = _table_to_segments(chunk, parent_key=f"table-{index}")
                if exploded is not None:
                    table_segments, parent_draft = exploded
                    segments.extend(table_segments)
                    if parent_draft is not None:
                        parent_drafts.append(parent_draft)
                    continue
                segments.append(_plain_segment(chunk))
                continue
            segments.extend(await self._semantic_segments(chunk, model, api_key))
        to_index = chunk_segments(
            segments, max_tokens=self._max_words, overlap_tokens=self._overlap_words
        )

        # Only keep a parent draft that at least one surviving node actually
        # points at (IndexOutcome's own docstring) -- a table exploded above
        # but merged away entirely by `chunk_segments` (an all-empty row
        # sentence, or a future node filter) must not orphan a parent row.
        referenced_keys = {
            key
            for chunk_to_index in to_index
            if (key := chunk_to_index.metadata.get(_TABLE_PARENT_KEY)) is not None
        }
        parents = tuple(draft for draft in parent_drafts if draft.key in referenced_keys)

        dim = self._embeddings.dimensions(model)
        collection = knowledge_collection(ctx.workspace_id)
        await self._vectors.ensure_hybrid_collection(collection, dim, distance="cosine")

        if not to_index:
            return IndexOutcome(collection=collection, dimensions=dim, chunks=())

        indexed: list[IndexedChunk] = []
        for start in range(0, len(to_index), _EMBED_BATCH):
            batch = to_index[start : start + _EMBED_BATCH]
            try:
                indexed.extend(
                    await self._embed_and_upsert(
                        ctx, document_id, space_id, collection, batch, model, api_key
                    )
                )
            except Exception:
                # P-19 (plan §4 step 12; alpha `_safe_parse`, plan
                # §3-rag-pipeline-alpha-vs-aizzak.md line 224: "يجرّب الدفعة
                # ثم يسقط إلى مستند-بمستند"). One malformed chunk's text can
                # trip a hard failure in the embedding provider for the WHOLE
                # HTTP call carrying the other, perfectly fine chunks in this
                # batch. AIZZAK's atomic unit inside one `execute` call is the
                # CHUNK (there is no multi-document batch in this
                # single-document-per-request pipeline, plan §3.7), so the
                # fallback isolates one chunk per call at a time instead of
                # one document per call.
                #
                # A chunk that STILL fails alone is a genuine failure, not a
                # batch-size artefact: it re-raises and propagates out of
                # `execute` exactly as any pipeline failure already did before
                # this step -- caught by `IndexRegisteredDocument.run`, which
                # fails the WHOLE document with that reason (`IndexAttempt`
                # `.error`, plan §3.7's "no silent skip / no partial index"
                # philosophy: a document is never left `indexed` with a
                # quietly smaller `chunk_count`). No new mechanism, no new
                # field -- this reuses the same propagate-and-let-the-caller-
                # fail-the-document path.
                if len(batch) == 1:
                    raise
                log.warning(
                    "indexing.embed_batch_failed_retrying_per_chunk",
                    extra={"document_id": document_id, "batch_size": len(batch)},
                )
                for one_chunk in batch:
                    indexed.extend(
                        await self._embed_and_upsert(
                            ctx, document_id, space_id, collection, [one_chunk], model, api_key
                        )
                    )

        return IndexOutcome(
            collection=collection,
            dimensions=dim,
            chunks=tuple(indexed),
            parents=parents,
        )

    async def _embed_and_upsert(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        space_id: Uuid | None,
        collection: str,
        batch: Sequence[ChunkToIndex],
        model: str,
        api_key: str,
    ) -> list[IndexedChunk]:
        """Embed + upsert exactly the chunks in ``batch`` as one unit -- the
        granularity ``execute`` retries at when a larger batch fails (P-19,
        plan §4 step 12)."""
        result = await self._embeddings.embed([chunk.text for chunk in batch], model, api_key)
        points = [
            _build_point(ctx, document_id, space_id, chunk, vector)
            for chunk, vector in zip(batch, result.vectors, strict=True)
        ]
        await self._vectors.upsert(collection, points)
        return [
            IndexedChunk(
                chunk_id=point.id,
                seq=chunk.seq,
                text=chunk.text,
                token_count=chunk.token_count,
                parent_key=chunk.metadata.get(_TABLE_PARENT_KEY),
            )
            for chunk, point in zip(batch, points, strict=True)
        ]

    async def _semantic_segments(
        self, chunk: ParsedChunk, model: str, api_key: str
    ) -> list[SourceSegment]:
        """P-20 (plan §4 step 13, §3.4): the I/O half of semantic
        pre-splitting for one non-table ``ParsedChunk``. Splits ``chunk.text``
        into sentences, and -- only when there are at least
        ``_MIN_SENTENCES_TO_SPLIT`` of them (constraint 3: gracefully skipped
        otherwise) -- calls ``EmbeddingProvider.embed`` on the sentences and
        hands the resulting vectors to the pure ``domain.chunking.
        semantic_boundaries`` to decide where to cut.

        Returns one ``SourceSegment`` per semantic part when a genuine
        boundary was found, each stamped with its own ``sub_order`` (0, 1,
        2, ...) so ``domain/chunking.py``'s ordering key keeps them in
        reading order (``SourceSegment``'s own docstring) -- or a single
        ``SourceSegment`` (``sub_order=0``, the default) covering the whole,
        unsplit ``chunk.text`` when there was nothing to split (too few
        sentences) or ``semantic_boundaries`` found no genuine break. Either
        way, every returned segment still goes through the ordinary
        word-window hard cap in ``chunk_segments`` afterwards (constraint 1)
        -- this method only decides where the SOFT, topical cuts go.
        """
        sentences = _split_sentences(chunk.text)
        if len(sentences) < _MIN_SENTENCES_TO_SPLIT:
            return [_plain_segment(chunk)]

        result = await self._embeddings.embed(sentences, model, api_key)
        boundaries = semantic_boundaries(
            result.vectors,
            buffer=SEMANTIC_BUFFER,
            breakpoint_percentile=SEMANTIC_BREAKPOINT_PERCENTILE,
        )
        parts = _group_sentences(sentences, boundaries)
        if len(parts) < _MIN_SEMANTIC_PARTS:
            return [_plain_segment(chunk)]

        return [
            SourceSegment(
                text=part,
                order=chunk.order,
                kind=str(chunk.kind),
                metadata=dict(chunk.metadata),
                sub_order=part_index,
            )
            for part_index, part in enumerate(parts)
        ]


def _plain_segment(chunk: ParsedChunk) -> SourceSegment:
    """One ``SourceSegment`` covering the WHOLE of ``chunk.text``, unsplit --
    the fallback every path that skips semantic pre-splitting (P-20, a
    TABLE-kind chunk, plan §3.4 constraint 2; a chunk with too few sentences
    to compare, constraint 3; or `semantic_boundaries` finding no genuine
    break) shares."""
    return SourceSegment(
        text=chunk.text,
        order=chunk.order,
        kind=str(chunk.kind),
        metadata=dict(chunk.metadata),
    )


def _split_sentences(text: str) -> list[str]:
    """Arabic-aware sentence splitting (P-20, plan §3.4): split on one or
    more of ``.`` ``؟`` ``!`` ``؛``, the dedicated Arabic full stop codepoint
    (U+06D4), or a paragraph break (two or more consecutive newlines) -- see
    ``_SENTENCE_SPLIT``'s own comment for why this stays a plain ``re``
    pattern instead of pulling in ``nltk``. Empty/whitespace-only pieces
    (a leading/trailing delimiter, or two delimiters back to back) are
    dropped; every surviving piece is stripped."""
    return [piece.strip() for piece in _SENTENCE_SPLIT.split(text) if piece.strip()]


def _group_sentences(sentences: list[str], boundaries: list[int]) -> list[str]:
    """Turn ``sentences`` plus ``domain.chunking.semantic_boundaries``'
    "split before index" list into the joined semantic-part texts
    (space-joined -- the sentences were split apart on punctuation, so
    rejoining with a single space is the least lossy plain-text
    reconstruction available without re-parsing the original whitespace).
    ``boundaries=[]`` returns the whole of ``sentences`` as one part."""
    parts: list[str] = []
    start = 0
    for boundary in boundaries:
        parts.append(" ".join(sentences[start:boundary]))
        start = boundary
    parts.append(" ".join(sentences[start:]))
    return parts


def _table_to_segments(
    chunk: ParsedChunk, *, parent_key: str
) -> tuple[list[SourceSegment], ParentChunkDraft | None] | None:
    """Explode one TABLE-kind ``ParsedChunk`` per §3.3 (P-13): decode its
    ``{headers, rows}`` JSON text (parsers.md §7 -- every table parser emits
    this exact shape) and hand it to the pure ``domain.tables.explode_table``.

    Returns ``None`` -- a deliberate, defensive fallback to the ordinary
    word-window path the caller already has for every other segment, NOT a
    raised error -- when ``chunk.text`` is not that shape (a malformed or
    future/unexpected table encoding) or explodes to zero rows. A genuine
    parse failure earlier in the pipeline is already turned into a `failed`
    document (plan §3.7); this is not that boundary.
    """
    try:
        payload = json.loads(chunk.text)
        headers = payload["headers"]
        rows = payload["rows"]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None

    exploded = explode_table(headers, rows)
    if not exploded.row_sentences:
        return None

    row_segments = [
        SourceSegment(
            text=sentence,
            order=chunk.order,
            kind=str(chunk.kind),
            metadata={**chunk.metadata, _TABLE_PARENT_KEY: parent_key},
        )
        for sentence in exploded.row_sentences
    ]
    if exploded.truncated:
        log.info(
            "indexing.table_row_cap_reached",
            extra={"kept_rows": len(exploded.row_sentences), "table_order": chunk.order},
        )
        if exploded.overflow_text:
            row_segments.append(
                SourceSegment(
                    text=exploded.overflow_text,
                    order=chunk.order,
                    kind=str(chunk.kind),
                    metadata={
                        **chunk.metadata,
                        _TABLE_PARENT_KEY: parent_key,
                        "table_truncated": True,
                    },
                )
            )

    parent_draft = (
        ParentChunkDraft(key=parent_key, order=chunk.order, text=exploded.parent_text)
        if exploded.parent_text
        else None
    )
    return row_segments, parent_draft


def _build_point(
    ctx: ExecutionContext,
    document_id: Uuid,
    space_id: Uuid | None,
    chunk: ChunkToIndex,
    vector: list[float],
) -> VectorPoint:
    point_id = chunk_point_id(document_id, chunk.seq)
    terms = build_sparse_terms(chunk.text)
    sparse = SparseVector(indices=list(terms.indices), values=list(terms.values))
    payload = _payload(ctx, document_id, space_id, point_id, chunk)
    return VectorPoint(id=point_id, vector=vector, payload=payload, sparse=sparse)


def _payload(
    ctx: ExecutionContext,
    document_id: Uuid,
    space_id: Uuid | None,
    point_id: str,
    chunk: ChunkToIndex,
) -> Json:
    payload: Json = {
        "workspace_id": ctx.workspace_id,
        "document_id": document_id,
        "chunk_id": point_id,
        "seq": chunk.seq,
        "text": chunk.text,
        "kind": chunk.kind,
    }
    if space_id is not None:
        # The space partition key (spaces plan §3.4), and the one step 9 will
        # index with `is_tenant=True`. Named `space`, not `space_id`: it is a
        # payload partition, and the plan writes the filter that reads it as
        # `flt["space"]`.
        #
        # OMITTED when there is no space, never written as `null`. A point
        # whose key is absent matches no `MatchValue` filter, which is exactly
        # right -- unspaced content must not answer a search inside a space --
        # and it is the same shape every point indexed before this step
        # already has (§5-أ), so re-indexing an old document and indexing a
        # spaceless new one produce identical payloads instead of two ways of
        # meaning "none".
        payload["space"] = space_id
    for key in _CITATION_KEYS:
        if key == "section":
            # See the ``_CITATION_KEYS`` comment: no parser writes a literal
            # "section" key -- the heading text it names lands under "title"
            # (docx.py's breadcrumb, pdf_tables.py's caption).
            section = chunk.metadata.get("section", chunk.metadata.get("title"))
            if section is not None:
                payload["section"] = section
        elif key in chunk.metadata:
            payload[key] = chunk.metadata[key]
    return payload
