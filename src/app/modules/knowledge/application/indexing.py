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
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorPoint
from app.framework.types import Json, Uuid
from app.modules.knowledge.domain.chunking import (
    SPLIT_OVERLAP_RATIO,
    ChunkToIndex,
    SourceSegment,
    chunk_segments,
    max_words_for_token_limit,
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

# The payload citation allowlist: parser-attached diagnostic keys (see
# ``ParsedChunk.metadata`` in ``ports/content_extractor.py`` and the parser
# adapters in 3.k1) worth copying forward for citation rendering. The rest of
# ``metadata`` is deliberately NOT copied wholesale.
_CITATION_KEYS = ("page_number", "sheet_name", "section_type", "table_name")


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
                exploded = _table_to_segments(chunk, parent_key=f"table-{index}")
                if exploded is not None:
                    table_segments, parent_draft = exploded
                    segments.extend(table_segments)
                    if parent_draft is not None:
                        parent_drafts.append(parent_draft)
                    continue
                segments.append(_plain_segment(chunk))
                continue
            segments.append(_plain_segment(chunk))
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
            result = await self._embeddings.embed([chunk.text for chunk in batch], model, api_key)
            points = [
                _build_point(ctx, document_id, space_id, chunk, vector)
                for chunk, vector in zip(batch, result.vectors, strict=True)
            ]
            await self._vectors.upsert(collection, points)
            indexed.extend(
                IndexedChunk(
                    chunk_id=point.id,
                    seq=chunk.seq,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    parent_key=chunk.metadata.get(_TABLE_PARENT_KEY),
                )
                for chunk, point in zip(batch, points, strict=True)
            )

        return IndexOutcome(
            collection=collection,
            dimensions=dim,
            chunks=tuple(indexed),
            parents=parents,
        )


def _plain_segment(chunk: ParsedChunk) -> SourceSegment:
    """One ``SourceSegment`` covering the WHOLE of ``chunk.text``, unsplit --
    used for a TABLE-kind chunk that failed to explode into rows and for
    every other, non-table chunk."""
    return SourceSegment(
        text=chunk.text,
        order=chunk.order,
        kind=str(chunk.kind),
        metadata=dict(chunk.metadata),
    )


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
        if key in chunk.metadata:
            payload[key] = chunk.metadata[key]
    return payload
