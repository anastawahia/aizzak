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

Pipeline shape (retrieval plan §3.7, plan step 8's ``P-26`` + plan step 9's
``P-34`` + plan step 10's ``P-35``): fusion → **keep 3x k** →
``filter_relevant`` → **replace with parent text, dedup by parent** →
**context budget** → final ``k``. ``execute`` widens back OUT to ``3 * k``
candidates right after RRF fusion (``_FUSION_RETENTION``, decoupled from
``_SEARCH_OVERFETCH`` below — see that constant's own comment) — narrowing
straight to ``k`` here, before ``filter_relevant`` even runs, would starve the
parent-expansion step below of the diversity it needs to fill ``k`` with
distinct sections instead of collapsing into two parents (§3.7's own stated
failure mode). The PUBLIC result still honours the caller's ``k`` exactly:
only the very last line of ``execute`` truncates to it.

**Parent expansion (plan step 9, ``P-34``) — critical, not an optimisation
(§3.7 verbatim): with the sentence-window approach excluded, this is the
ONLY remaining mechanism that widens a matched chunk into its surrounding
context.** Every relevance-filtered candidate's own leaf text is replaced
with its ``knowledge.parent_chunks`` row's text (``_widen_to_parents``,
resolved through ``ParentChunkRepository.parent_texts_for_chunk_ids`` — a
repository lookup, ``chunk_id -> chunks.parent_id -> parent_chunks``,
rag-indexing-plan.md §3.2 constraint 1 — never a Qdrant payload read, because
the payload carries neither a parent's text nor even its id). Two candidates
that widen to the SAME parent row collapse into ONE surviving entry (dedup
BY PARENT, keyed on ``ParentChunkText.id``, not on text equality) — the
mechanism that lets ``_FUSION_RETENTION``'s 3x pool actually pay off: the
freed slot is filled by the next distinct candidate rather than repeating a
section already shown. A candidate with no parent (``parent_id`` null, or
the parent row missing/unreadable) degrades HONESTLY to its own leaf text —
never dropped, never an error. The substituted parent text is capped at
``_MAX_PARENT_CHUNK_CHARS`` so one oversized parent cannot swallow the whole
context by itself; a candidate's OWN leaf text is never capped (it is
already window-sized). This runs over the FULL ``retain_k``-deep candidate
list, before the final truncation below — the same reason ``retain_k`` is
wider than ``k`` in the first place.

**Context budget (plan step 10, ``P-35``)** — the stage between the widening
above and the final ``k``, and in that order for a reason: parent expansion is
what makes each candidate BIGGER, so a budget measured before it would measure
text nobody sends. ``execute`` renders each surviving candidate EXACTLY as the
consumer will (``format_labeled_chunk`` — §3.2's one shared source-label
formatter, the same unit the RAG agent's synthesis path joins into its
prompt), then hands those rendered strings, with the two ceilings, to the pure
``domain/context_budget.fit_to_context_budget``. The ceilings are DUAL and the
smaller wins: ``max_context_chars`` is exact, ``max_context_tokens`` is a
network-free ESTIMATE (``estimate_tokens`` — no ``tiktoken``, no tokenizer
download), and the cut falls wherever either is breached first. Both are
constructor-injected from ``Settings.Limits`` (س-24: the values live in
``Settings`` and are passed as ARGUMENTS into the domain — no ``os.getenv``
there, and no per-request override anywhere). Order is DESCENDING then cut:
the survivors are a best-first PREFIX, so the highest-scoring chunk stays
``[#1]``. ``LongContextReorder`` — which would move it to the END — is an
explicitly rejected design (§3.7, §7). The budget never returns an empty
context when candidates exist: see ``fit_to_context_budget`` for why an
emptiness manufactured here would be misread as the trust gate's "retrieval
found nothing" (plan step 5, ``P-33``).

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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.framework.agent_runtime.source_label import format_labeled_chunk
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorHit
from app.framework.types import Json
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.context_budget import fit_to_context_budget
from app.modules.knowledge.domain.fusion import FusedChunk, reciprocal_rank_fusion
from app.modules.knowledge.domain.relevance import ScoredChunk, filter_relevant
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.domain.value_objects import ParentChunkText
from app.modules.knowledge.ports.retrieval import ParentChunkRepository, RetrievedChunk

_MAX_K = 50  # mirrors Settings.Limits.max_rag_k (07-nfr-slo §4)
# fusion.py does not normalize weights internally (3.k2) -- these already sum
# to 1.0.
_W_DENSE = 0.5
_W_BM25 = 0.5
_RRF_K = 60
# How many raw hits EACH leg (dense, BM25-sparse) fetches from the vector
# store -- a search-recall concern, unrelated to how many FUSED candidates
# survive past RRF (see `_FUSION_RETENTION` below).
_SEARCH_OVERFETCH = 3
_MAX_SEARCH = 100
# Diversity retention factor, not a quality threshold (retrieval plan
# §3.7/§4 step 8, `P-26`; §3.8 keeps every quality threshold at `0.0` until a
# calibration set exists). Keeping 3x the caller's `k` past RRF fusion gives
# the parent-expansion step below (plan step 9, `P-34`, `_widen_to_parents`)
# enough distinct candidates to fill the final `k` with distinct sections
# instead of collapsing into two parents (§3.7's stated failure mode).
# Deliberately its OWN constant, decoupled from `_SEARCH_OVERFETCH` even
# though both default to 3 today -- plan step 18 (`P-30`) sweeps every
# module-level knob in this file, including this one, into `Settings`
# together.
_FUSION_RETENTION = 3
# Length cap on a SUBSTITUTED parent chunk's text (plan step 9, `P-34`) --
# NOT a quality threshold, so decision س-22 ("thresholds stay 0.0 until a
# calibration set exists") does not apply to it (retrieval plan §4 row 9's
# own instruction). A parent (one parsed `SourceSegment`) can hold several
# word-windows' worth of text -- unbounded, in principle -- so without a cap
# a single oversized section could dominate the whole context handed to the
# final `k`. 4000 was picked to comfortably exceed one leaf window (~512
# words is roughly 3000 characters, `domain/chunking.py::MIN_NODE_CHARS`'s
# neighbourhood) so widening is essentially never a no-op shrink, while
# staying well under the *whole-context* budget plan step 10 (`P-35`) will
# introduce (its own starting suggestion is 12000 characters across EVERY
# surviving chunk combined) so one parent cannot pre-empt that stage's job.
# A candidate's own (un-widened) leaf text is never capped here -- it is
# already window-sized by construction.
_MAX_PARENT_CHUNK_CHARS = 4000
# Fallback values for the DUAL context budget (plan step 10, `P-35`) when no
# caller injects them -- they MIRROR `Settings.Limits.max_context_chars` /
# `.max_context_tokens`, exactly as `_MAX_K` above mirrors
# `Settings.Limits.max_rag_k`, and the Composition Root passes the real
# configured values in (س-24: the numbers live in `Settings`). They exist so a
# test (or any other direct constructor call) gets the shipped budget rather
# than an unbounded one -- a defaulted-to-infinity budget would silently be no
# budget at all.
_DEFAULT_MAX_CONTEXT_CHARS = 12_000
_DEFAULT_MAX_CONTEXT_TOKENS = 3_000


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
    Qdrant collection, fuse with RRF, relevance-filter, and widen each
    survivor to its parent chunk's text (06 §7 ``RetrieveContext(workspace,
    query, k)``; plan step 9, ``P-34``).

    ``documents`` is typed against the narrow ``ParentChunkRepository`` seam
    (``ports/retrieval.py``), not the module's full ``DocumentRepository`` --
    see that Protocol's own docstring for why. The Composition Root passes
    the SAME ``SqlDocumentRepository`` it already builds for the module's
    other faces (structural typing needs no adapter of its own here).

    ``max_context_chars``/``max_context_tokens`` (plan step 10, ``P-35``) are
    the DUAL context budget, injected ONCE at construction from
    ``Settings.Limits`` — never read from a request, and never from the
    environment inside the domain (س-24). Construction-time rather than a
    parameter of ``execute`` is the shape that decision demands: a per-call
    argument would BE a per-request override, which س-24 rules out.
    """

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vectors: HybridVectorStore,
        documents: ParentChunkRepository,
        *,
        max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._documents = documents
        self._max_context_chars = max_context_chars
        self._max_context_tokens = max_context_tokens

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
        # Post-fusion retention (plan step 8, `P-26`) -- see `_FUSION_RETENTION`'s
        # own comment above. Independent of `search_k`: this is how many of
        # the FUSED, ranked candidates survive into `filter_relevant` below,
        # not how many raw hits each leg fetches.
        retain_k = min(k * _FUSION_RETENTION, _MAX_SEARCH)

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

        # `top_k=retain_k`, NOT `search_k` (plan step 8, `P-26`) -- the fused,
        # ranked list handed to `filter_relevant` below is `3 * k` deep, wider
        # than the vector-store fetch's own overfetch factor, so the parent-
        # expansion widening below (plan step 9, `P-34`) has enough distinct
        # candidates to work with. Only the FINAL `chunks` line below narrows
        # back down to the caller's `k`.
        fused = reciprocal_rank_fusion(
            [hit.id for hit in dense_hits],
            [hit.id for hit in sparse_hits],
            top_k=retain_k,
            weight_dense=_W_DENSE,
            weight_bm25=_W_BM25,
            rrf_k=_RRF_K,
        )

        payload_by_id: dict[str, Json] = {
            hit.id: hit.payload for hit in (*dense_hits, *sparse_hits)
        }
        scored = [_to_scored_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in fused]
        relevant = filter_relevant(scored)

        # Parent expansion (plan step 9, `P-34`) -- runs over the FULL
        # `relevant` list (up to `retain_k` deep), BEFORE the caller's `k` is
        # applied: see the module docstring for why this is critical rather
        # than an optimisation, and `_widen_to_parents` for the dedup-by-
        # parent mechanics that let `_FUSION_RETENTION`'s 3x pool pay off.
        parent_texts = await self._documents.parent_texts_for_chunk_ids(
            ctx, [candidate.chunk_id for candidate in relevant]
        )
        widened = _widen_to_parents(relevant, parent_texts)

        # The context budget (plan step 10, `P-35`) -- AFTER the widening
        # above (which is what makes each candidate bigger) and BEFORE the
        # caller's `k` below, exactly §3.7's order. The port DTOs are built
        # FIRST, because the budget must measure the text as it will actually
        # be shown -- source label and all (`_labeled_text`), and the label is
        # composed from the citation fields only `_to_retrieved_chunk` reads
        # out of the payload. Dual ceilings, smaller wins, best-first prefix
        # kept: see `fit_to_context_budget`.
        retrieved = [_to_retrieved_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in widened]
        budgeted = fit_to_context_budget(
            [(chunk, _labeled_text(chunk)) for chunk in retrieved],
            max_chars=self._max_context_chars,
            max_tokens=self._max_context_tokens,
        )

        # The ONLY narrowing to the caller's `k` in this whole pipeline (plan
        # step 8's ordering rule "٨ قبل ٩" -- widen here, narrow later, never
        # the reverse).
        chunks = budgeted[:k]
        return RetrievalResult(
            chunks=chunks, best_dense_score=best_dense_score, best_bm25_score=best_bm25_score
        )


def _widen_to_parents(
    candidates: Sequence[ScoredChunk], parents: Mapping[str, ParentChunkText]
) -> list[ScoredChunk]:
    """Substitute each candidate's own leaf text with its parent's (plan step
    9, ``P-34``), deduping by parent, and preserving ``candidates``' own
    order (best-first — RRF-sorted, and ``filter_relevant`` never re-sorts).

    For each candidate, in order:

    * No entry in ``parents`` (``ParentChunkRepository`` never resolved a
      parent for this ``chunk_id`` — no ``parent_id``, or the row is
      missing/unreadable) — kept AS IS, own leaf text untouched. This is the
      honest degradation the module docstring promises: never dropped, never
      an error.
    * A parent WAS resolved, and this is the FIRST candidate seen to widen to
      that ``ParentChunkText.id`` — kept, with ``text`` replaced by the
      parent's text, capped at ``_MAX_PARENT_CHUNK_CHARS``.
    * A parent was resolved, but some EARLIER (higher- or equal-ranked)
      candidate already widened to the SAME ``id`` — dropped entirely. This
      is dedup BY PARENT (retrieval plan §3.7): keyed on the parent's ``id``,
      never on text equality, so two candidates that happen to widen to the
      identical parent text are recognised as duplicates even before either
      is truncated. Dropping the whole entry (not merely deduplicating the
      text) is what frees the slot ``_FUSION_RETENTION``'s 3x pool exists to
      fill with the next distinct candidate.
    """
    widened: list[ScoredChunk] = []
    seen_parent_ids: set[str] = set()
    for candidate in candidates:
        parent = parents.get(candidate.chunk_id)
        if parent is None:
            widened.append(candidate)
            continue
        if parent.id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent.id)
        widened.append(replace(candidate, text=parent.text[:_MAX_PARENT_CHUNK_CHARS]))
    return widened


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


def _labeled_text(chunk: RetrievedChunk) -> str:
    """What the context budget MEASURES (plan step 10, ``P-35``): the chunk
    exactly as a consumer renders it — §3.2's ``[file p.N | section: S]``
    source label above the text — built by ``format_labeled_chunk``, the ONE
    shared formatter (``framework/agent_runtime/source_label.py``) the RAG
    agent's synthesis path already uses.

    Measuring the labelled form rather than the raw ``text`` is what keeps
    the budget from drifting from what is actually sent: a label is real
    characters and real tokens in the prompt (tens of each, per chunk), and a
    budget that ignored them would promise 12000 characters while shipping
    more. The formatter is reached through ``app.framework`` — the one
    package both this module and the agents layer may import (its own module
    docstring names this exact second consumer), so there is no second copy
    of the label's shape to drift.
    """
    return format_labeled_chunk(
        chunk.text,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section=chunk.section,
    )
