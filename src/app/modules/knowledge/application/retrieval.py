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
visible. They exist for the structured log (``P-29``, plan step 17) and as
the ready-made input for any future calibration.

Per-leg thresholds (plan step 16, ``P-27``; retrieval plan §3.8 — "الآليّة
تُشحَن والأرقام لا"): ``_MIN_DENSE_SCORE``/``_MIN_BM25_SCORE`` gate each leg's
hits (``_gate_by_score``) between that snapshot and RRF fusion, and both ship
at ``0.0`` = **disabled**. Decision س-22 forbids shipping an UNCALIBRATED
number, not the mechanism, so the knob is wired and the number waits for the
evaluation set ``P-38`` needs (§7). No answer path changes today: at the
shipped defaults ``_gate_by_score`` returns its input untouched. The one
number this step DOES pick is ``_MAX_SPARSE_CANDIDATES`` — a cap on the
**count** of BM25-sparse candidates, not on any score, which is why س-22
never reaches it.

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

**The structured stage log (plan step 17, ``P-29``; retrieval plan §3.11,
س-25 = أ).** Every call to ``execute`` emits ONE ``knowledge.retrieval``
record through ``get_logger`` carrying the count and the scores of each
stage, the per-candidate ``retrieval_origin`` tag, and the
``total_ms``/``context_nodes``/``fallback`` measurements — see
``_log_stages`` for the field list and ``_STAGE_LOG_DEFAULTS`` for why every
path emits the SAME shape. Two properties of it are load-bearing:

* **The per-leg numbers are snapshotted BEFORE fusion.** ``dense_scores`` /
  ``sparse_scores`` (and the two ``best_*`` signals) are read off
  ``dense_hits``/``sparse_hits`` at the same point ``P-28``'s snapshot is
  taken, which is lexically ABOVE the ``reciprocal_rank_fusion`` call.
  ``FusedChunk.score`` is a brand-new RRF number on a different scale
  entirely (``Σ w/(60+rank)`` — thousandths, however good the candidate), so
  a snapshot taken any later would report RRF's arithmetic under the legs'
  names. The two live side by side in the record, never in the same field:
  raw per-leg scores in ``dense_scores``/``sparse_scores``, RRF's own in
  each ``candidates`` entry's ``rrf_score``.
* **Nothing from the documents themselves is logged** (10-code-standards
  §10: no sensitive user content). Counts, scores, ids, origins and
  durations only — never a chunk's text, never a file name, and never the
  query, which is present as ``query_chars`` (its LENGTH) alone.

This is observability, NOT contract: س-25 = أ keeps ``stages`` out of the
response models, out of ``openapi.yaml`` and out of the streaming contract
(the stages expose index structure and would need permission scoping — plan
§7).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from app.framework.agent_runtime.source_label import format_labeled_chunk
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.observability import get_logger
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

log = get_logger(__name__)

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
# Per-leg ABSOLUTE score floors (plan step 16, `P-27`; retrieval plan §3.8).
#
# **The mechanism ships; the number does not.** Decision س-22 forbids shipping
# an UNCALIBRATED threshold, not the knob itself -- so the knob exists here,
# wired all the way through `_gate_by_score` below, and its value stays `0.0`
# (= disabled) until the evaluation set `P-38` waits on exists (§7: real
# documents and 15-30 questions with reference answers -- an input nobody may
# invent). `domain/relevance.py`'s own `min_score`/`relative_floor` stay `0.0`
# for the same reason, and its Jaccard dedup stays `0.95` because that one is
# alpha's single SCALE-INDEPENDENT constant (plan fact ح-17).
#
# `0.0` means disabled by an EXPLICIT branch, never by arithmetic: on the
# dense leg's cosine scale a bare `score >= 0.0` is NOT inert -- cosine spans
# [-1, 1], so such a comparison would silently drop every negatively
# correlated hit. That is a real, uncalibrated gate wearing a disabled
# default's clothes, and it is precisely what س-22 rules out. See
# `_gate_by_score`.
#
# ⚠️ Direction is INVERTED from alpha (retrieval plan header, §3.3, §3.8; §6
# risk #3). A floor here reads "keep the hit if its score is **>=** the
# floor", because HIGHER is better on BOTH legs (Qdrant cosine similarity;
# raw IDF-weighted sparse dot product). alpha's `min_dense_score` is
# calibrated against FAISS L2 DISTANCE, where lower is nearer, so its
# comparison is the mirror image of this one -- no alpha number is copied.
_MIN_DENSE_SCORE = 0.0
_MIN_BM25_SCORE = 0.0
# Ceiling on how many BM25-sparse candidates may reach fusion (plan step 16,
# `P-27`) -- and, unlike the two floors above, this one carries a REAL value.
# س-22 does not reach it: it caps a **count**, not a **score** (retrieval plan
# §3.8 says exactly that), so there is nothing here to calibrate and no scale
# for it to be wrong on. `20` is alpha's own sparse-leg candidate count
# (`rag-pipeline-alpha-vs-aizzak.md` appendix, "أعلى-k سَبارس | 20") -- a
# count is the one class of alpha number that survives the L2 -> cosine
# direction flip untouched, because it is never compared against a score.
#
# Why a cap at all: BM25 precision decays fast down its own ranking (deep
# ranks are chunks sharing a single mid-frequency term), while RRF grants
# EVERY returned rank non-zero mass -- `_W_BM25 / (_RRF_K + rank)`, still
# ~0.003 at rank 100. An uncapped sparse fetch therefore injects tail noise
# into the fused pool that the dense leg never voted for. Applied to the
# sparse leg's fetch DEPTH (`sparse_k` in `execute`) rather than to the
# returned list, because asking the store for 100 hits and discarding 80
# payloads is pure waste; the number of sparse candidates entering fusion is
# the same either way.
#
# `20` is >= `k * _SEARCH_OVERFETCH` for every `k <= 6`, so it does not narrow
# the default `k = 5` path (`search_k == 15`) at all: it engages exactly where
# the tail actually grows -- at the `_MAX_K` ceiling it cuts the sparse fetch
# from 100 down to 20 -- and the dense leg is never capped by it.
_MAX_SPARSE_CANDIDATES = 20
# --------------------------------------------------------------------------- #
# The structured stage log (plan step 17, `P-29`; retrieval plan §3.11)       #
# --------------------------------------------------------------------------- #
# How deep into a ranking the log quotes ACTUAL numbers. The head of a ranking
# is the part any calibration reads (the floors `P-38` will set live at the
# TOP of a leg's scores, and `_MIN_BM25_SCORE`'s scale can only be learnt from
# real values there); the tail is a magnitude, and every `*_count` field
# carries it EXACTLY. So a `k = 50` request logs a bounded record instead of
# 100 dense scores + 100 sparse scores + 100 candidate tags, at no cost to
# anything the numbers are for. Not a `Settings` knob: it is a log-shape
# constant, and س-24 governs RETRIEVAL tuning, none of which this changes.
_LOG_RANKING_SAMPLE = 20
# The three `retrieval_origin` values (retrieval plan §3.11) — which leg(s)
# voted for a fused candidate. `both` is the interesting one: it is the
# hybrid pipeline's whole justification showing up as data (a candidate that
# only the sparse leg saw is `bm25`, and lexical-only recall is exactly what
# a dense-only retriever would have missed).
_ORIGIN_DENSE = "dense"
_ORIGIN_BM25 = "bm25"
_ORIGIN_BOTH = "both"
# Unreachable by construction — every fused candidate id came from one of the
# two leg id lists RRF was handed. It exists because a LOG must never be the
# thing that raises: a `KeyError` from an observability path would turn a
# reporting bug into a failed retrieval.
_ORIGIN_UNKNOWN = "unknown"
_MS_PER_SECOND = 1000
# Every field the record can carry, at the value that means "this stage never
# ran". `execute` starts from a copy and each stage overwrites its own keys in
# place, so the EARLY return (an empty `document_ids` scope, which searches
# nothing) emits the same SHAPE as a full run rather than a short record a log
# query would have to special-case. Ordered as the pipeline runs.
#
# A stage REBINDS its key and never mutates a default value in place, so the
# nested `origin_counts` mapping being shared by every record that never
# reached fusion is safe — and it is the only mutable default here.
_STAGE_LOG_DEFAULTS: Mapping[str, object] = {
    "dense_count": 0,
    "sparse_count": 0,
    "dense_scores": (),
    "sparse_scores": (),
    "best_dense_score": None,
    "best_bm25_score": None,
    "dense_kept": 0,
    "sparse_kept": 0,
    "fused_count": 0,
    "candidates": (),
    "origin_counts": {_ORIGIN_DENSE: 0, _ORIGIN_BM25: 0, _ORIGIN_BOTH: 0},
    "relevant_count": 0,
    "widened_count": 0,
    "budgeted_count": 0,
}


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
        started = time.perf_counter()
        if not query.strip():
            raise ValidationError("retrieval query must not be empty")
        k = max(1, min(k, _MAX_K))
        search_k = min(k * _SEARCH_OVERFETCH, _MAX_SEARCH)
        # The BM25-sparse candidate ceiling (plan step 16, `P-27`) -- a cap on
        # the sparse leg ALONE, never on the dense one; see
        # `_MAX_SPARSE_CANDIDATES` for why a count cap is untouched by س-22 and
        # why it is spent at fetch depth.
        sparse_k = min(search_k, _MAX_SPARSE_CANDIDATES)
        # Post-fusion retention (plan step 8, `P-26`) -- see `_FUSION_RETENTION`'s
        # own comment above. Independent of `search_k`: this is how many of
        # the FUSED, ranked candidates survive into `filter_relevant` below,
        # not how many raw hits each leg fetches.
        retain_k = min(k * _FUSION_RETENTION, _MAX_SEARCH)

        # The structured stage log's accumulator (plan step 17, `P-29`) —
        # filled in place by each stage as it runs and emitted ONCE, on every
        # return path, by `_log_stages`. A local dict rather than a field:
        # this use-case is stateless and shared by every workspace, so per-call
        # measurements may only live on the call's own stack.
        #
        # `query_chars` is the QUERY's LENGTH, and it is the only trace of the
        # question in the whole record: the text itself is user content
        # (10-code-standards §10) while its length still explains an empty
        # sparse leg or a degenerate embedding.
        stages: dict[str, object] = {
            "query_chars": len(query),
            "k": k,
            "search_k": search_k,
            "sparse_k": sparse_k,
            "retain_k": retain_k,
            "scoped_document_count": None if document_ids is None else len(document_ids),
            "space_scoped": space_id is not None,
            **_STAGE_LOG_DEFAULTS,
        }

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
        #
        # Since plan step 15 (`P-25`) a scope can also arrive from the
        # QUESTION's own words — `RouteQuestion._content_scope` resolves a
        # file name to one document id and passes it here, because this is
        # where a scope becomes a Qdrant-side `must` condition (plan fact
        # ح-13) instead of a client-side discard. Nothing below distinguishes
        # the two origins, and that is the whole of what "صارم" needs from
        # this file: a scope narrows the search and NOTHING here ever widens
        # it back, so a named file with no matching chunk yields no chunk —
        # never another file's. The honest answer to that is the trust gate's
        # (plan step 5, `P-33`), one layer up.
        if document_ids is not None:
            if not document_ids:
                # Logged like any other outcome, and it is the one outcome
                # nothing downstream could otherwise explain: zero chunks with
                # no search behind them. `scoped_document_count == 0` next to
                # `dense_count == 0` says "a scope resolved to nothing", not
                # "the corpus had nothing to say".
                _log_stages(stages, started=started, chunks=[])
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
            collection, q_sparse, sparse_k, flt
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

        # The stage log's PRE-RRF snapshot (plan step 17, `P-29`; retrieval
        # plan §3.11 "لقطة قبل أن يدهس RRF الدرجات الخامّ") — the same instant
        # as the two confidence signals above, and for the same reason
        # amplified: after `reciprocal_rank_fusion` below there is no raw
        # per-leg score left anywhere in this function. `FusedChunk.score` is
        # RRF's own arithmetic (`Σ w/(60+rank)`, thousandths regardless of
        # candidate quality), so a "dense score" read after fusion would be a
        # different quantity on a different scale wearing the same name. The
        # two full counts sit next to the sampled heads: `_LOG_RANKING_SAMPLE`
        # bounds the NUMBERS, never the counts.
        stages.update(
            {
                "dense_count": len(dense_hits),
                "sparse_count": len(sparse_hits),
                "dense_scores": [hit.score for hit in dense_hits[:_LOG_RANKING_SAMPLE]],
                "sparse_scores": [hit.score for hit in sparse_hits[:_LOG_RANKING_SAMPLE]],
                "best_dense_score": best_dense_score,
                "best_bm25_score": best_bm25_score,
            }
        )

        # Per-leg absolute floors (plan step 16, `P-27`) -- applied HERE, i.e.
        # AFTER the confidence snapshot above and BEFORE fusion below.
        #
        # After, because the snapshot's documented contract is "the maximum
        # over EVERY hit that leg returned" (see `RetrievalResult`): a floor
        # that emptied a leg would otherwise erase the very number that
        # explains why, leaving the structured log (`P-29`, plan step 17) and
        # any future calibration blind at the one moment they most need to
        # see it.
        #
        # Before, because a gated-out hit must cast no RRF vote at all -- RRF
        # reads rank, not score, so a candidate discarded after fusion would
        # already have shifted every rank below it.
        #
        # At the shipped configuration (both floors `0.0`) this pair of lines
        # is an identity transform; it is the knob, not a behaviour change.
        dense_hits = _gate_by_score(dense_hits, _MIN_DENSE_SCORE)
        sparse_hits = _gate_by_score(sparse_hits, _MIN_BM25_SCORE)
        # Counted AFTER the gate and reported beside the pre-gate counts
        # above: with the shipped `0.0` floors the two pairs are equal, and
        # the day a floor carries a number the difference between them IS the
        # gate's effect — the measurement `P-38` has to read to calibrate it.
        stages.update({"dense_kept": len(dense_hits), "sparse_kept": len(sparse_hits)})

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

        # The per-candidate `retrieval_origin` tag (retrieval plan §3.11) —
        # built from the very id lists RRF was handed, so a hit the per-leg
        # floor above removed casts no vote here either and is credited to
        # neither leg. `origin_counts` aggregates the WHOLE fused pool while
        # `candidates` quotes the head of it (`_LOG_RANKING_SAMPLE`), so the
        # hybrid split is exact even when the listing is sampled.
        origins = _retrieval_origins(
            [hit.id for hit in dense_hits], [hit.id for hit in sparse_hits]
        )
        stages.update(
            {
                "fused_count": len(fused),
                "origin_counts": _origin_counts(
                    origins.get(chunk.chunk_id, _ORIGIN_UNKNOWN) for chunk in fused
                ),
                "candidates": [
                    {
                        "chunk_id": chunk.chunk_id,
                        "retrieval_origin": origins.get(chunk.chunk_id, _ORIGIN_UNKNOWN),
                        "rrf_score": chunk.score,
                    }
                    for chunk in fused[:_LOG_RANKING_SAMPLE]
                ],
            }
        )

        payload_by_id: dict[str, Json] = {
            hit.id: hit.payload for hit in (*dense_hits, *sparse_hits)
        }
        scored = [_to_scored_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in fused]
        relevant = filter_relevant(scored)
        stages["relevant_count"] = len(relevant)

        # Parent expansion (plan step 9, `P-34`) -- runs over the FULL
        # `relevant` list (up to `retain_k` deep), BEFORE the caller's `k` is
        # applied: see the module docstring for why this is critical rather
        # than an optimisation, and `_widen_to_parents` for the dedup-by-
        # parent mechanics that let `_FUSION_RETENTION`'s 3x pool pay off.
        parent_texts = await self._documents.parent_texts_for_chunk_ids(
            ctx, [candidate.chunk_id for candidate in relevant]
        )
        widened = _widen_to_parents(relevant, parent_texts)
        # `widened_count` below `relevant_count` is the dedup-BY-PARENT drop
        # (plan step 9, `P-34`): the gap between the two is how many
        # candidates collapsed into a section an earlier one already carried —
        # the number that says whether `_FUSION_RETENTION`'s 3x pool is
        # actually paying for itself.
        stages["widened_count"] = len(widened)

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
        # Below `widened_count` means the DUAL budget cut (plan step 10,
        # `P-35`); equal to it means the budget was never the binding
        # constraint. Distinguishing those two is the whole reason this stage
        # gets a count of its own rather than being read off `context_nodes`,
        # which the caller's `k` can cap first.
        stages["budgeted_count"] = len(budgeted)

        # The ONLY narrowing to the caller's `k` in this whole pipeline (plan
        # step 8's ordering rule "٨ قبل ٩" -- widen here, narrow later, never
        # the reverse).
        chunks = budgeted[:k]
        _log_stages(stages, started=started, chunks=chunks)
        return RetrievalResult(
            chunks=chunks, best_dense_score=best_dense_score, best_bm25_score=best_bm25_score
        )


def _log_stages(
    stages: dict[str, object], *, started: float, chunks: Sequence[RetrievedChunk]
) -> None:
    """Close the accumulator with the delivery-side measurements and emit the
    ONE ``knowledge.retrieval`` record for this call (plan step 17, ``P-29``;
    retrieval plan §3.11).

    The four terminal fields:

    * ``delivered_chunk_ids`` — WHICH chunks the caller got, so the record
      joins back to ``candidates`` above it. Not a prefix of that list:
      relevance filtering, parent dedup and the budget each remove entries
      from the middle, and seeing which survived is the point.
    * ``context_nodes`` — how many, the plan's own measurement name.
    * ``fallback`` — ``True`` exactly when this retrieval returned nothing,
      which is precisely the condition the honest-fallback trust gate one
      layer up fires on (plan step 5, ``P-33``): zero chunks after a
      retrieval that was actually attempted. It is recorded HERE, from the
      only vantage point that can also say WHY it was empty — the stages
      above it.
    * ``total_ms`` — the whole use-case, embedding call and both searches and
      the parent lookup included, on the monotonic clock.

    ``INFO``, not ``DEBUG``: 10-code-standards §10 puts business events at
    ``INFO``, and a retrieval that answered (or honestly did not) is the
    module's business event. Level filtering is the deployment's call; a
    record that is never emitted cannot be turned on after the fact.
    """
    stages["delivered_chunk_ids"] = [chunk.chunk_id for chunk in chunks]
    stages["context_nodes"] = len(chunks)
    stages["fallback"] = not chunks
    stages["total_ms"] = _elapsed_ms(started)
    log.info("knowledge.retrieval", extra=stages)


def _elapsed_ms(started: float) -> int:
    """Whole milliseconds since ``started`` on the MONOTONIC clock — never the
    wall clock, which an NTP step could run backwards mid-retrieval and turn
    a measurement into a negative number."""
    return round((time.perf_counter() - started) * _MS_PER_SECOND)


def _retrieval_origins(dense_ids: Sequence[str], bm25_ids: Sequence[str]) -> dict[str, str]:
    """Which leg(s) voted for each candidate id — ``dense`` / ``bm25`` /
    ``both`` (retrieval plan §3.11).

    Takes the two ID lists rather than the hits, because those are exactly
    what ``reciprocal_rank_fusion`` is handed: a tag derived from anything
    else could disagree with the fusion the record is describing.
    """
    origins = dict.fromkeys(dense_ids, _ORIGIN_DENSE)
    for chunk_id in bm25_ids:
        origins[chunk_id] = _ORIGIN_BOTH if chunk_id in origins else _ORIGIN_BM25
    return origins


def _origin_counts(origins: Iterable[str]) -> dict[str, int]:
    """How the fused pool splits across the three origins, with all three keys
    ALWAYS present — a missing key and a zero read the same to a human and
    very differently to a log query."""
    counts = {_ORIGIN_DENSE: 0, _ORIGIN_BM25: 0, _ORIGIN_BOTH: 0}
    for origin in origins:
        counts[origin] = counts.get(origin, 0) + 1
    return counts


def _gate_by_score(hits: Sequence[VectorHit], min_score: float) -> list[VectorHit]:
    """Drop the hits of ONE leg whose raw score falls below ``min_score``
    (plan step 16, ``P-27``; retrieval plan §3.8).

    ``min_score <= 0.0`` is DISABLED, and that is an explicit early return
    rather than a comparison that happens to pass everything. On this
    module's scales it has to be: the dense leg is Qdrant cosine similarity
    over ``[-1, 1]``, so ``hit.score >= 0.0`` would quietly discard every
    negatively correlated hit — an uncalibrated gate disguised as a disabled
    default, exactly what decision س-22 forbids shipping. The shipped
    defaults (``_MIN_DENSE_SCORE``/``_MIN_BM25_SCORE``, both ``0.0``) take
    this branch, so the function is inert until somebody sets a number.

    ⚠️ The surviving comparison is ``score >= min_score`` — HIGHER is better
    on both legs. alpha's floors gate an L2 DISTANCE where lower is nearer,
    so its comparison runs the other way and none of its numbers transfers
    (retrieval plan §6 risk #3).
    """
    if min_score <= 0.0:
        return list(hits)
    return [hit for hit in hits if hit.score >= min_score]


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
