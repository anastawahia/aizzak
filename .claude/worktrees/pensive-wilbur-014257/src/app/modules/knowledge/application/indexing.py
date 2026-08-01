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
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorPoint
from app.framework.types import Json, Uuid
from app.modules.knowledge.domain.chunking import ChunkToIndex, SourceSegment, chunk_segments
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.ports.content_extractor import ParsedDocument

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
    ``knowledge.chunks.text`` is ``NOT NULL`` (01-data-model §2.7)."""

    chunk_id: str
    seq: int
    text: str
    token_count: int


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    """The result of one ``IndexDocument.execute`` call."""

    collection: str
    dimensions: int
    chunks: tuple[IndexedChunk, ...]


class IndexDocument:
    """Chunk a parsed document, embed it (dense) and hash it (sparse), and
    upsert one hybrid Qdrant point per chunk."""

    def __init__(self, embeddings: EmbeddingProvider, vectors: HybridVectorStore) -> None:
        self._embeddings = embeddings
        self._vectors = vectors

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        parsed: ParsedDocument,
        model: str,
        api_key: str,
    ) -> IndexOutcome:
        segments = [
            SourceSegment(
                text=chunk.text,
                order=chunk.order,
                kind=str(chunk.kind),
                metadata=dict(chunk.metadata),
            )
            for chunk in parsed.chunks
        ]
        to_index = chunk_segments(segments)

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
                _build_point(ctx, document_id, chunk, vector)
                for chunk, vector in zip(batch, result.vectors, strict=True)
            ]
            await self._vectors.upsert(collection, points)
            indexed.extend(
                IndexedChunk(
                    chunk_id=point.id, seq=chunk.seq, text=chunk.text, token_count=chunk.token_count
                )
                for chunk, point in zip(batch, points, strict=True)
            )

        return IndexOutcome(collection=collection, dimensions=dim, chunks=tuple(indexed))


def _build_point(
    ctx: ExecutionContext, document_id: Uuid, chunk: ChunkToIndex, vector: list[float]
) -> VectorPoint:
    point_id = chunk_point_id(document_id, chunk.seq)
    terms = build_sparse_terms(chunk.text)
    sparse = SparseVector(indices=list(terms.indices), values=list(terms.values))
    payload = _payload(ctx, document_id, point_id, chunk)
    return VectorPoint(id=point_id, vector=vector, payload=payload, sparse=sparse)


def _payload(ctx: ExecutionContext, document_id: Uuid, point_id: str, chunk: ChunkToIndex) -> Json:
    payload: Json = {
        "workspace_id": ctx.workspace_id,
        "document_id": document_id,
        "chunk_id": point_id,
        "seq": chunk.seq,
        "text": chunk.text,
        "kind": chunk.kind,
    }
    for key in _CITATION_KEYS:
        if key in chunk.metadata:
            payload[key] = chunk.metadata[key]
    return payload
