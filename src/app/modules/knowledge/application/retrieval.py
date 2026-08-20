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

``space`` (spaces plan §3.4, step 8) rides on the same filter, on both legs,
and is a narrowing INSIDE that tenant — never a substitute for it: the
workspace condition stays whatever the caller scoped to, so a space id from
another tenant still matches nothing.

Confidence signals (retrieval plan §3.3/§3.11, س-22, ``P-28``): ``execute``
returns ``RetrievalResult``, not a bare ``list[RetrievedChunk]``, so it can
carry ``best_dense_score``/``best_bm25_score`` alongside the chunks —
snapshotted straight off ``dense_hits``/``sparse_hits`` BEFORE
``reciprocal_rank_fusion`` ever runs (it only ever reads each hit's ``.id``,
never its ``.score``, and hands back brand-new RRF scores of its own), so
this is the one point in the pipeline the raw per-leg scores are still
visible. No numeric gate is built on them here (س-22 = أ — thresholds stay
``0.0``/"no results only"); they exist for the structured log (``P-29``, plan
step 17) and as the ready-made input for any future calibration.

⚠️ **Scale direction is INVERTED from alpha.** alpha's ``best_dense_distance``
is the LOWEST raw FAISS L2 distance (nearer = smaller = better). AIZZAK's
dense leg is Qdrant cosine similarity, where HIGHER is better —
``best_dense_score`` is therefore a MAXIMUM over ``dense_hits``, never a
minimum, and no alpha number is copied here (retrieval plan §3.3, §6 risk
#3). The BM25-sparse leg is likewise higher-is-better (a raw, IDF-weighted
dot product, per ``framework/ports/vector_store.py``), so ``best_bm25_score``
is a MAXIMUM too.

A leg that returned no hits at all makes its signal honestly absent
(``None``), never ``0.0`` — on a cosine (or dot-product) scale ``0.0`` is a
real, meaningful score, not "no data".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """``RetrieveContext.execute``'s outcome: the relevance-filtered,
    top-``k`` ``chunks`` plus the two raw confidence signals (retrieval plan
    §3.3/§3.11, ``P-28``) — see the module docstring for the pre-RRF
    snapshot timing and the alpha scale-direction inversion (§6 risk #3).

    ``best_dense_score``/``best_bm25_score`` are independent of ``chunks``:
    each is the MAXIMUM raw ``VectorHit.score`` seen on its leg's search,
    over EVERY hit that leg returned — not just the ones that survived
    relevance-filtering/truncation into ``chunks``. ``None`` means that leg
    returned no hits at all (an honestly absent signal, never ``0.0``).
    """

    chunks: list[RetrievedChunk]
    best_dense_score: float | None
    best_bm25_score: float | None


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
        space_id: str | None,
    ) -> RetrievalResult:
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
                return RetrievalResult(chunks=[], best_dense_score=None, best_bm25_score=None)
            flt["document_id"] = list(document_ids)
        # The space narrowing (spaces plan §3.4, step 8) — a SINGLE value, so
        # the adapter renders `MatchValue`, and ANDed with everything above by
        # `_build_filter`'s `must`. It reads the `space` key `IndexDocument`
        # writes.
        #
        # `None` is unscoped, and the key is left OUT rather than set to
        # `None`: the Qdrant adapter rejects a `None` filter value outright
        # (DD-04 — a filter shape it cannot render must fail loudly, never
        # degrade to "no filter"), so writing it would turn "search the whole
        # workspace" into a 400.
        #
        # ⚠️ Points indexed BEFORE step 8 carry no `space` key at all, and
        # Qdrant matches no point that is missing a filtered field — so the
        # first search that passes a space returns nothing from them, silently
        # and with no error. That is §5-أ, and the re-index it mandates is the
        # only cure; there is no `IsEmpty`/`should` branch here to paper over
        # it, because a filter that fell back to "or has no space" would
        # quietly leak every other space's older content.
        if space_id is not None:
            flt["space"] = space_id

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

        # Confidence-signal snapshot (retrieval plan §3.3/§3.11, ``P-28``) —
        # taken HERE, straight off each leg's raw ``VectorHit.score``, before
        # ``reciprocal_rank_fusion`` below ever runs: RRF only ever reads a
        # hit's ``.id`` (never its ``.score``) and returns brand-new RRF
        # scores of its own (``FusedChunk.score``), so this is the one place
        # downstream of the search calls where the raw per-leg scores are
        # still visible. ``max(..., default=None)`` makes an empty leg an
        # honest ``None`` rather than a misleading ``0.0`` (a real, meaningful
        # score on this cosine/dot-product scale) — see the module docstring
        # for why "best" is a MAXIMUM here, the inverse of alpha's minimum-L2
        # ``best_dense_distance``.
        best_dense_score = max((hit.score for hit in dense_hits), default=None)
        best_bm25_score = max((hit.score for hit in sparse_hits), default=None)

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
        chunks = [
            _to_retrieved_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in relevant[:k]
        ]
        return RetrievalResult(
            chunks=chunks, best_dense_score=best_dense_score, best_bm25_score=best_bm25_score
        )


def _to_scored_chunk(chunk: FusedChunk, payload: Json) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk.chunk_id,
        document_id=payload["document_id"],
        text=payload["text"],
        score=chunk.score,
        seq=int(payload.get("seq", 0)),
    )


def _to_retrieved_chunk(chunk: ScoredChunk, payload: Json) -> RetrievedChunk:
    """Map a relevance-filtered ``ScoredChunk`` onto the port DTO, reading the
    citation fields (retrieval plan §3.1/§3.9, س-19, ``P-18``) straight out
    of the SAME Qdrant point payload ``_to_scored_chunk`` already consulted —
    ``ScoredChunk`` itself stays the four-field shape the relevance algorithm
    needs and gains no citation fields of its own (``domain/relevance.py``
    stays pure and unaware of this port's DTO). A point that predates
    ``indexing._CITATION_KEYS`` (or whose parser never emitted one of these)
    is simply missing the key, and ``.get`` degrades that to ``None`` rather
    than raising — the same "unknown, not broken" contract as every OTHER
    citation key here.
    """
    return RetrievedChunk(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        score=chunk.score,
        file_name=payload.get("file_name"),
        page_number=payload.get("page_number"),
        section=payload.get("section"),
    )
