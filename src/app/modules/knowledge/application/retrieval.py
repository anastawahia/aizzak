"""RetrieveContext use-case — hybrid (dense + BM25-sparse) retrieval fused by
Reciprocal Rank Fusion (06-domain-models §7; docs/migration/refs/
retrieval.md §4.1-§4.3, §4.8, §6.2, §6.4; 3.k3).

Reuses 3.k2's pure ``fusion.reciprocal_rank_fusion`` + ``tokenization`` (via
``sparse.build_sparse_terms``) unmodified: both legs — dense/embedding and
BM25-sparse/term-hash — run against the SAME per-workspace hybrid collection
and are fused by rank alone, then relevance-filtered
(``relevance.filter_relevant``) before being truncated to the caller's ``k``
and mapped onto the ``RetrievedChunk`` port DTO (03 §2 ``RetrievedChunkOut``).
No LLM/synthesis layer here (retrieval.md §6.4 option (b): v1 retrieval is
CONTENT-only) and no Postgres hydrate — each Qdrant point's payload already
carries its own ``text`` (written by ``IndexDocument``), so fused ranking is
the only extra work needed.

Every vector search filter carries ``workspace_id`` on BOTH legs (DD-04) —
the single per-workspace collection is shared by every document, so tenant
isolation here is a payload filter, exactly like ``memory.RecallRelevant``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorHit
from app.framework.types import Json
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.fusion import FusedChunk, reciprocal_rank_fusion
from app.modules.knowledge.domain.relevance import ScoredChunk, filter_relevant
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.ports.retrieval import RetrievedChunk

_MAX_K = 50  # mirrors Settings.Limits.max_rag_k (07-nfr-slo §4)
# fusion.py does not normalize weights internally (3.k2) -- these already sum
# to 1.0.
_W_DENSE = 0.5
_W_BM25 = 0.5
_RRF_K = 60
_SEARCH_OVERFETCH = 3
_MAX_SEARCH = 100


class RetrieveContext:
    """Embed + hash the query, search both legs of the per-workspace hybrid
    Qdrant collection, fuse with RRF, and relevance-filter down to ``k``
    chunks (06 §7 ``RetrieveContext(workspace, query, k)``)."""

    def __init__(self, embeddings: EmbeddingProvider, vectors: HybridVectorStore) -> None:
        self._embeddings = embeddings
        self._vectors = vectors

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        query: str,
        model: str,
        api_key: str,
        k: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValidationError("retrieval query must not be empty")
        k = max(1, min(k, _MAX_K))
        search_k = min(k * _SEARCH_OVERFETCH, _MAX_SEARCH)

        collection = knowledge_collection(ctx.workspace_id)
        flt: Json = {"workspace_id": ctx.workspace_id}
        # BE-RAG-005 — a narrowing scope on TOP of the tenant filter, never in
        # place of it: `workspace_id` stays on both legs whatever the caller
        # scoped to (DD-04), so a document id from another tenant would still
        # match nothing.
        #
        # `None` and `[]` are deliberately DIFFERENT here, and the distinction
        # is load-bearing. `None` means "unscoped" — search the whole workspace
        # corpus, which is what every caller did before this parameter existed.
        # `[]` means the caller HAS a scope and it resolved to no documents (it
        # pinned files that were never indexed), so the honest answer is no
        # chunks rather than a silent widening back to everything — the pins
        # would otherwise stop constraining the search precisely when they
        # matter most.
        if document_ids is not None:
            if not document_ids:
                return []
            flt["document_id"] = list(document_ids)

        embedded = await self._embeddings.embed([query], model, api_key)
        q_vector = embedded.vectors[0]
        q_terms = build_sparse_terms(query)
        q_sparse = SparseVector(indices=list(q_terms.indices), values=list(q_terms.values))

        dense_hits: list[VectorHit] = await self._vectors.search(
            collection, q_vector, search_k, flt
        )
        sparse_hits: list[VectorHit] = await self._vectors.search_sparse(
            collection, q_sparse, search_k, flt
        )

        fused = reciprocal_rank_fusion(
            [hit.id for hit in dense_hits],
            [hit.id for hit in sparse_hits],
            top_k=search_k,
            weight_dense=_W_DENSE,
            weight_bm25=_W_BM25,
            rrf_k=_RRF_K,
        )

        payload_by_id: dict[str, Json] = {
            hit.id: hit.payload for hit in (*dense_hits, *sparse_hits)
        }
        scored = [_to_scored_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in fused]
        relevant = filter_relevant(scored)
        return [_to_retrieved_chunk(chunk) for chunk in relevant[:k]]


def _to_scored_chunk(chunk: FusedChunk, payload: Json) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk.chunk_id,
        document_id=payload["document_id"],
        text=payload["text"],
        score=chunk.score,
        seq=int(payload.get("seq", 0)),
    )


def _to_retrieved_chunk(chunk: ScoredChunk) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        score=chunk.score,
    )
