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
``P-34`` + plan step 10's ``P-35``, with plan step 20's ``P-23`` on the first
arrow): fusion → **MMR keeps a diverse 3x k** → ``filter_relevant`` →
*(optional cross-encoder rerank — plan step 21, ``P-24``, off as shipped)* →
**replace with parent text, dedup by parent** → **context budget** → final
``k``. ``execute`` widens back OUT to ``3 * k`` candidates right after RRF
fusion (``RetrievalTuning.fusion_retention``, decoupled from
``.search_overfetch``) — narrowing straight to ``k`` here, before
``filter_relevant`` even runs, would starve the parent-expansion step below of
the diversity it needs to fill ``k`` with distinct sections instead of
collapsing into two parents (§3.7's own stated failure mode). The PUBLIC
result still honours the caller's ``k`` exactly: only the very last line of
``execute`` truncates to it.

**MMR (plan step 20, ``P-23``; retrieval plan §3.9, decision س-20).** WHICH
``3 * k`` survive that first arrow is a diversity decision, not a rank one.
RRF fuses two *rankings* and has no notion of how alike two candidates are, so
"خمس قطع من الفقرة نفسها" — five chunks off one paragraph — is a legitimate
result it will happily produce. ``reciprocal_rank_fusion`` therefore fuses to
the WIDER ``mmr_pool_k`` (``RetrievalTuning.mmr_overfetch``, the "``search_k``
موسَّع" of plan row 20), and ``_mmr_rerank`` selects ``retain_k`` out of it by
``λ·sim(q,d) - (1-λ)·max sim(d,dⱼ)`` — the PURE ``domain/mmr.py``, stdlib
only, no I/O of its own. The surplus between the two factors is what lets that
stage genuinely DISCARD a near-duplicate rather than merely re-order it.

It sits before ``filter_relevant`` and everything after it because all of
those stages are prefix-sensitive — dedup-by-parent keeps the FIRST candidate
to reach a parent, the context budget keeps a best-first PREFIX, and the last
line keeps ``[:k]`` — so a diversity decision taken later would be taken on a
list three stages had already cut on the undiversified order. MMR's own first
pick has nothing to be redundant with, so the most relevant chunk is still
``[#1]`` (§3.7); ``LongContextReorder`` remains a rejected design, never code.

**The declared price** (§3.9, §6 risk #5, accepted by س-20): both legs search
with ``with_vectors=True``, so every candidate's full dense vector crosses the
network. The scope is the widened ``search_k``/``sparse_k`` — never the corpus
— and the vectors are read once here and never stored.

**The reranker (plan step 21, ``P-24``; retrieval plan §3.10, decision
س-21).** OFF as this ships (``RetrievalTuning.rerank_enabled = False``), and
when a deployment turns it on it re-orders the ~10-20 candidates that
survived ``filter_relevant`` with a cross-encoder reached over HTTP —
``RerankProvider``, whose only adapter keeps its model weights in a separate
service and out of every image this repository builds. It cannot shorten an
answer (the ``final_top_n`` guard, ``_apply_rerank``) and it cannot fail one
(an outage degrades to the unreranked order). ``_rerank`` has the placement
argument, the guard and the failure behaviour in full.

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
mechanism that lets ``RetrievalTuning.fusion_retention``'s 3x pool actually
pay off: the freed slot is filled by the next distinct candidate rather than repeating a
section already shown. A candidate with no parent (``parent_id`` null, or
the parent row missing/unreadable) degrades HONESTLY to its own leaf text —
never dropped, never an error. ⚠️ **So does a candidate whose parent is
INCOMPLETE** (``ParentChunkText.is_complete`` false — P-13's header-only
parent for a table past ``TABLE_PARENT_MAX_ROWS``): substituting it would
hand the model a passage that no longer contains the text which matched the
query, so the widening is skipped and the leaf stands. The same rule, for
the same reason, that ``P-42`` already applies on the summarisation side.
The substituted parent text is capped at
``RetrievalTuning.max_parent_chunk_chars`` so one oversized parent cannot
swallow the whole context by itself; a candidate's OWN leaf text is never
capped (it is already window-sized). This runs over the FULL ``retain_k``-deep
candidate list, before the final truncation below — the same reason
``retain_k`` is wider than ``k`` in the first place.

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
download), and the cut falls wherever either is breached first. Both ride in
on the constructor-injected ``RetrievalTuning``, sourced from
``Settings.Limits`` (س-24: the values live in ``Settings`` and are passed as
ARGUMENTS into the domain — no ``os.getenv`` there, and no per-request
override anywhere). Order is DESCENDING then cut:
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
تُشحَن والأرقام لا"): ``min_dense_score``/``min_bm25_score`` gate each leg's
hits (``_gate_by_score``) between that snapshot and RRF fusion, and both ship
at ``0.0`` = **disabled**. Decision س-22 forbids shipping an UNCALIBRATED
number, not the mechanism, so the knob is wired and the number waits for the
evaluation set ``P-38`` needs (§7). No answer path changes today: at the
shipped configuration ``_gate_by_score`` returns its input untouched. The one
number that step DID pick is ``max_sparse_candidates`` — a cap on the
**count** of BM25-sparse candidates, not on any score, which is why س-22
never reaches it.

**All of those numbers now live in ``Settings`` (plan step 18, ``P-30``
``P-40``, س-24 = أ).** ``RetrievalTuning`` is the whole set, injected once at
construction and mapped by the Composition Root from
``Settings.retrieval``/``Settings.limits``; ``execute`` passes each one on as
an ARGUMENT to the pure domain algorithm that consumes it. There is no
``os.getenv`` and no ``Settings`` import anywhere in this module or in
``knowledge/domain``, and no per-request override path exists — a caller may
still ask for a ``k`` (that is ``POST /knowledge/search``'s published
result-set size, 03 §2), and nothing else about retrieval is a request's to
choose. See ``RetrievalTuning`` for the field-by-field mapping.

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

**``context_text`` as an INTERNAL capability (plan step 19, ``P-39``;
retrieval plan §3.11, س-25 = أ).** ``RetrievalResult.context_text`` renders
this call's delivered ``chunks`` as the one context block a model would be
sent — through ``format_context_block``, §3.2's single shared formatting
unit, which is the very same call the RAG agent's synthesis path makes. That
sharing is the point of the step and of §3.2's "لا صيغتان تنحرفان": there is
one place the block's shape (label, then text, blank line between chunks)
is decided, so the internal context and the prompt context cannot drift into
two formats. It rides the SAME س-25 rule as ``stages`` above — a property on
an application-layer dataclass that no port returns, absent from every
response model, from ``openapi.yaml`` and from the ``token``/``final``
streaming contract — and, like the two confidence signals, it is READY
rather than consumed: no caller asks for it today.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from app.framework.agent_runtime.source_label import (
    format_context_block,
    format_labeled_chunk,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.observability import get_logger
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.rerank_provider import RerankedDocument, RerankProvider
from app.framework.ports.vector_store import HybridVectorStore, SparseVector, VectorHit
from app.framework.types import Json
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.context_budget import fit_to_context_budget
from app.modules.knowledge.domain.fusion import FusedChunk, reciprocal_rank_fusion
from app.modules.knowledge.domain.mmr import MmrCandidate, maximal_marginal_relevance
from app.modules.knowledge.domain.relevance import ScoredChunk, filter_relevant
from app.modules.knowledge.domain.sparse import build_sparse_terms
from app.modules.knowledge.domain.value_objects import ParentChunkText
from app.modules.knowledge.ports.retrieval import ParentChunkRepository, RetrievedChunk

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalTuning:
    """Every tunable number this use-case uses, in ONE injected value object
    (retrieval plan §4 row 18, ``P-30`` ``P-40``, decision س-24 = أ).

    Until this step each of these was a module constant here (and, for
    ``default_k``, a constant in the RAG agent). س-24 = أ says configuration
    lives in ``Settings`` and reaches the code that uses it as ARGUMENTS —
    never an ``os.getenv`` or a ``Settings`` import inside the domain or the
    application layer — so the Composition Root maps
    ``Settings.retrieval``/``Settings.limits`` onto this object once, and
    ``execute`` passes the individual numbers on into the pure domain
    algorithms (``reciprocal_rank_fusion``, ``filter_relevant``,
    ``fit_to_context_budget``).

    **A plain frozen dataclass, not the pydantic ``RetrievalSettings``
    itself.** The application layer stays free of the configuration
    contract's own type (10-code-standards §4: it consumes injected values,
    it does not read global config), and one object rather than seventeen
    keyword arguments is what lets plan row 20 add ``mmr_lambda`` and its
    widened ``search_k`` by adding a FIELD, with no second injection
    mechanism invented for them.

    **Every default here MIRRORS its ``Settings`` home byte for byte** — the
    ``_MAX_K``/``_DEFAULT_MAX_CONTEXT_CHARS`` pattern this replaces, kept for
    the same reason: a direct construction (a test, a script) must get the
    SHIPPED numbers rather than an accidental second configuration. The
    mirror is asserted by a test, because a mirror nobody checks is a
    duplicate waiting to drift. The reasoning behind each VALUE lives with
    the value, in ``framework/settings/settings.py``
    (``RetrievalSettings``/``Limits``), and is deliberately not repeated
    here.

    The mapping, field by field:

    ``weight_dense`` ``weight_bm25`` ``rrf_k`` ``search_overfetch``
    ``max_search_candidates`` ``max_sparse_candidates`` ``fusion_retention``
    ``default_k`` ``min_dense_score`` ``min_bm25_score`` ``min_fused_score``
    ``relative_floor`` ``jaccard_threshold`` ``max_parent_chunk_chars``
    ``mmr_lambda`` ``mmr_overfetch`` ``rerank_enabled`` ``rerank_candidates``
    come
    from ``Settings.retrieval``; ``max_k`` ``max_context_chars``
    ``max_context_tokens`` come from ``Settings.limits`` (``max_rag_k`` and
    the dual budget are platform GUARDRAILS — 07-nfr-slo §4 — that other
    layers also honour, so they keep their existing home rather than being
    duplicated into a second one).

    ``min_fused_score``/``relative_floor``/``jaccard_threshold`` are
    ``domain/relevance.filter_relevant``'s three keyword arguments, which
    ``execute`` used to leave at that function's OWN defaults. Passing them
    explicitly is what makes §7's promise true — that switching a floor on
    once ``P-38``'s evaluation set exists is "a configuration line, not a
    step" — and it changes nothing today: the values passed are exactly the
    defaults they replace.
    """

    weight_dense: float = 0.5
    weight_bm25: float = 0.5
    rrf_k: int = 60
    search_overfetch: int = 3
    max_search_candidates: int = 100
    max_sparse_candidates: int = 20
    fusion_retention: int = 3
    default_k: int = 20
    max_k: int = 50
    min_dense_score: float = 0.0
    min_bm25_score: float = 0.0
    min_fused_score: float = 0.0
    relative_floor: float = 0.0
    jaccard_threshold: float = 0.95
    max_parent_chunk_chars: int = 4_000
    mmr_lambda: float = 0.87
    mmr_overfetch: int = 6
    rerank_enabled: bool = False
    rerank_candidates: int = 20
    max_context_chars: int = 12_000
    max_context_tokens: int = 3_000


# The shipped tuning, as a module-level singleton so it is not rebuilt per
# construction and so `RetrieveContext.__init__`'s default is a NAME rather
# than a call (a call in a default argument is evaluated once anyway, but the
# name says so).
_DEFAULT_TUNING = RetrievalTuning()
# --------------------------------------------------------------------------- #
# The structured stage log (plan step 17, `P-29`; retrieval plan §3.11)       #
# --------------------------------------------------------------------------- #
# How deep into a ranking the log quotes ACTUAL numbers. The head of a ranking
# is the part any calibration reads (the floors `P-38` will set live at the
# TOP of a leg's scores, and `min_bm25_score`'s scale can only be learnt from
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
    # How many candidates MMR (plan step 20, `P-23`) handed on. Below
    # `fused_count` is the diversity cut: that gap is how many near-duplicates
    # the widened pool bought the right to discard, the one number that says
    # whether `mmr_overfetch` is paying for the vectors it puts on the wire.
    "mmr_count": 0,
    "relevant_count": 0,
    # How many candidates the reranker (plan row 21, `P-24`) actually placed.
    # `0` on every shipped deployment — the stage is OFF by default — and `0`
    # too when it ran and failed, which is not ambiguous: a failure emits its
    # own `knowledge.rerank_degraded` warning beside this record, and nothing
    # emits anything when the switch is off. Below the number of candidates
    # offered is the starvation guard doing its work (`_apply_rerank`).
    "rerank_count": 0,
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

    ``context_text`` is the third thing this carrier exists for (plan row 19,
    ``P-39``) — see its own docstring. Like the two signals it is INTERNAL:
    this dataclass is an application-layer type that never crosses the
    module's inbound port (``KnowledgeRetrievalService.retrieve`` passes on
    ``.chunks`` and nothing else), which is precisely what makes it the right
    home for a capability س-25 = أ keeps out of the contract.
    """

    chunks: list[RetrievedChunk]
    best_dense_score: float | None
    best_bm25_score: float | None

    @property
    def context_text(self) -> str:
        """This result's ``chunks`` rendered as the ONE context block a
        consumer would send to a model — ``P-39``, plan row 19, §3.11:
        "``context_text`` جاهز يُبنى بوحدة التنسيق نفسها (§3.2) ويُستعمَل
        داخل الوحدة — ولا يظهر في ``openapi.yaml``".

        **The same formatting unit, not a second one.** It calls
        ``format_context_block`` (``framework/agent_runtime/source_label.py``)
        — the identical function the RAG agent's synthesis path calls to
        build the ``Context:`` block of its system prompt, over the identical
        per-chunk ``format_labeled_chunk`` the context budget already
        measures through ``_labeled_text``. §3.2 asks for one source of truth
        for the source label so the two paths can never diverge ("لا صيغتان
        تنحرفان"); one shared call is the only way to make that structurally
        true rather than a comment two call sites promise to honour.

        **Order: descending, then cut — the most relevant chunk is ``[#1]``**
        (§3.7). ``chunks`` is already the best-first, budget-fitted,
        ``k``-truncated prefix ``execute`` returns, and nothing here re-sorts
        it. ``LongContextReorder`` (which moves the strongest chunk to the
        END) is an explicitly rejected design that hurts ≤7B models — a
        design note in §3.7/§7, never code.

        **INTERNAL, and internal by construction** (س-25 = أ): a computed
        property on an application-layer dataclass that no port returns. It
        is absent from ``RetrievedChunk``, from ``RetrievedChunkOut``, from
        ``openapi.yaml`` and from the agent's ``token``/``final`` streaming
        contract — §7 records the reason (the assembled context exposes index
        structure and would need permission scoping of its own).

        Computed on demand rather than stored: the two live callers of
        ``execute`` want ``chunks``, so a retrieval that nobody asks a context
        for pays nothing, and a stored copy would be a second thing to keep
        in step with ``chunks``. Empty ``chunks`` gives ``""`` — the honest
        "no context", which is the trust gate's condition (plan row 5,
        ``P-33``), never a manufactured sentence.
        """
        return format_context_block(self.chunks)


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

    ``tuning`` (plan step 18, ``P-30`` ``P-40``) is EVERY tunable number this
    use-case uses — the fusion weights, the RRF constant, both overfetch
    factors, the per-leg floors, the relevance floors, the parent cap and the
    dual context budget — injected ONCE at construction from ``Settings``
    (``RetrievalTuning``'s own docstring has the field-by-field mapping).
    Never read from a request, and never from the environment inside the
    domain (س-24). Construction-time rather than a parameter of ``execute``
    is the shape that decision demands: a per-call argument would BE a
    per-request override, which س-24 rules out (option ب, rejected — plan §7).

    ``reranker`` (plan step 21, ``P-24``, decision س-21) is the optional
    cross-encoder — see ``_rerank``. ``None`` is the SHIPPED wiring: with
    ``RetrievalTuning.rerank_enabled`` off, the Composition Root builds no
    rerank client at all, so a deployment that does not want the latency pays
    not even a connection for it. A constructor argument for the same reason
    ``tuning`` is one: enabling a reranker is a DEPLOYMENT's decision, and
    ``execute`` has no parameter a request could reach it through.
    """

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vectors: HybridVectorStore,
        documents: ParentChunkRepository,
        *,
        tuning: RetrievalTuning = _DEFAULT_TUNING,
        reranker: RerankProvider | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._documents = documents
        self._tuning = tuning
        self._reranker = reranker

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        query: str,
        model: str,
        api_key: str,
        k: int | None = None,
        document_ids: Sequence[str] | None = None,
        space_id: str | None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        if not query.strip():
            raise ValidationError("retrieval query must not be empty")
        tuning = self._tuning
        # `k = None` means "however many this deployment is configured to
        # return" (plan step 18, `P-40`) -- the number that used to be
        # `rag_agent.agent._TOP_K = 5` and this signature's own literal
        # default. A caller that DOES name a `k` is asking for a result-set
        # size on a published contract (`POST /knowledge/search`, 03 §2), not
        # overriding a tuning knob, which is why that stays possible.
        k = tuning.default_k if k is None else k
        k = max(1, min(k, tuning.max_k))
        # The "search_k موسَّع" of plan step 20 (`P-23`, §3.9). MMR selects a
        # DIVERSE `retain_k` out of a pool that has to be wider than
        # `retain_k`, or there is nothing surplus to discard and the whole
        # stage collapses into a re-ordering. So the legs fetch at whichever
        # factor is larger -- `mmr_overfetch` today -- and that single depth
        # is also the scope `with_vectors=True` pays for on the wire (§6 risk
        # #5: the widened `search_k`, never the corpus).
        search_k = min(
            k * max(tuning.search_overfetch, tuning.mmr_overfetch), tuning.max_search_candidates
        )
        # The BM25-sparse candidate ceiling (plan step 16, `P-27`) -- a cap on
        # the sparse leg ALONE, never on the dense one. Spent at fetch DEPTH
        # rather than on the returned list, because asking the store for 100
        # hits and discarding 80 payloads is pure waste; the number of sparse
        # candidates entering fusion is the same either way.
        sparse_k = min(search_k, tuning.max_sparse_candidates)
        # Post-fusion retention (plan step 8, `P-26`). Independent of
        # `search_k`: this is how many of the FUSED, ranked candidates survive
        # into `filter_relevant` below, not how many raw hits each leg
        # fetches -- which is why the two factors are separate knobs even
        # though both ship at 3.
        retain_k = min(k * tuning.fusion_retention, tuning.max_search_candidates)
        # The pool MMR chooses from (plan step 20, `P-23`) -- what RRF fuses
        # to, in place of the blind `retain_k` truncation it used to do. The
        # cut from `mmr_pool_k` down to `retain_k` is now a DIVERSITY
        # decision instead of a rank one; when the two factors are equal, MMR
        # re-orders the pool and drops nothing, which is the honest
        # degenerate case rather than a special branch.
        mmr_pool_k = min(k * tuning.mmr_overfetch, tuning.max_search_candidates)

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
            "mmr_pool_k": mmr_pool_k,
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

        # `with_vectors=True` on BOTH legs (plan step 20, `P-23`, decision
        # س-20; §3.9) -- MMR's diversity term is candidate-to-candidate
        # similarity, and nothing but the candidates' own vectors can supply
        # it. The declared price is a full float vector per hit crossing the
        # network (§3.9 "الثمن المُعلَن", §6 risk #5 "مقبول بالقرار س-20"),
        # and it is bounded by `search_k`/`sparse_k` -- the widened SEARCH,
        # never the corpus.
        #
        # The sparse leg asks too, not just the dense one: a candidate only
        # BM25 saw is exactly the lexical-recall win the hybrid pipeline
        # exists for, and leaving it vector-less would let it into the answer
        # as the one candidate nothing checked for redundancy.
        dense_hits: list[VectorHit] = await self._vectors.search(
            collection, q_vector, search_k, flt, with_vectors=True
        )
        sparse_hits: list[VectorHit] = await self._vectors.search_sparse(
            collection, q_sparse, sparse_k, flt, with_vectors=True
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
        dense_hits = _gate_by_score(dense_hits, tuning.min_dense_score)
        sparse_hits = _gate_by_score(sparse_hits, tuning.min_bm25_score)
        # Counted AFTER the gate and reported beside the pre-gate counts
        # above: with the shipped `0.0` floors the two pairs are equal, and
        # the day a floor carries a number the difference between them IS the
        # gate's effect — the measurement `P-38` has to read to calibrate it.
        stages.update({"dense_kept": len(dense_hits), "sparse_kept": len(sparse_hits)})

        # `top_k=mmr_pool_k`, NOT `search_k` and no longer `retain_k` (plan
        # step 8, `P-26`, as widened by step 20's `P-23`). The list handed to
        # MMR below is the widened pool; MMR is what cuts it to the `3 * k`
        # `filter_relevant` and the parent-expansion widening (plan step 9,
        # `P-34`) then work over. Only the FINAL `chunks` line below narrows
        # back down to the caller's `k`.
        fused = reciprocal_rank_fusion(
            [hit.id for hit in dense_hits],
            [hit.id for hit in sparse_hits],
            top_k=mmr_pool_k,
            weight_dense=tuning.weight_dense,
            weight_bm25=tuning.weight_bm25,
            rrf_k=tuning.rrf_k,
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
        # MMR (plan step 20, `P-23`, decision س-20; §3.9) -- placed HERE, on
        # the arrow §3.7 draws as "الدمج ──► احتفظ بـ 3xk": it IS that
        # retention now, choosing a DIVERSE `retain_k` out of the widened
        # fused pool instead of taking RRF's top `retain_k` blindly. Before
        # `filter_relevant` and everything after it, because every one of
        # those stages is prefix-sensitive -- dedup-by-parent keeps the FIRST
        # candidate to reach a parent, the context budget keeps a best-first
        # PREFIX, and the last line keeps `[:k]` -- so a diversity decision
        # taken any later would be taken on a list three stages had already
        # cut on the undiversified order. (It also spares `filter_relevant`'s
        # O(n²) Jaccard pass the entries MMR just discarded.)
        vectors_by_id: dict[str, Sequence[float]] = {
            hit.id: hit.vector for hit in (*dense_hits, *sparse_hits) if hit.vector is not None
        }
        ranked = _mmr_rerank(fused, vectors_by_id, top_n=retain_k, lambda_=tuning.mmr_lambda)
        stages["mmr_count"] = len(ranked)

        scored = [_to_scored_chunk(chunk, payload_by_id[chunk.chunk_id]) for chunk in ranked]
        # The three relevance gates' numbers are passed EXPLICITLY (plan step
        # 18, `P-30`) rather than left at `filter_relevant`'s own defaults --
        # same values, one home. Both floors are `0.0` = disabled (س-22: the
        # mechanism ships, the number waits for `P-38`'s evaluation set) and
        # they live on a THIRD scale, the fused RRF score, which is why the
        # settings field is spelt `min_fused_score` next to the two per-leg
        # ones. `jaccard_threshold` is the one gate shipped ON, at alpha's
        # single scale-independent constant (plan fact ح-17).
        relevant = filter_relevant(
            scored,
            min_score=tuning.min_fused_score,
            relative_floor=tuning.relative_floor,
            jaccard_threshold=tuning.jaccard_threshold,
        )
        stages["relevant_count"] = len(relevant)

        # The cross-encoder reranker (plan step 21, `P-24`, decision س-21) --
        # OFF unless a deployment turned it on, and a no-op then. See
        # `_rerank` for the placement argument, the starvation guard and the
        # degrade-on-outage behaviour.
        relevant = await self._rerank(relevant, query=query, stages=stages)

        # Parent expansion (plan step 9, `P-34`) -- runs over the FULL
        # `relevant` list (up to `retain_k` deep), BEFORE the caller's `k` is
        # applied: see the module docstring for why this is critical rather
        # than an optimisation, and `_widen_to_parents` for the dedup-by-
        # parent mechanics that let `RetrievalTuning.fusion_retention`'s 3x pool pay off.
        parent_texts = await self._documents.parent_texts_for_chunk_ids(
            ctx, [candidate.chunk_id for candidate in relevant]
        )
        widened = _widen_to_parents(
            relevant, parent_texts, max_parent_chunk_chars=tuning.max_parent_chunk_chars
        )
        # `widened_count` below `relevant_count` is the dedup-BY-PARENT drop
        # (plan step 9, `P-34`): the gap between the two is how many
        # candidates collapsed into a section an earlier one already carried —
        # the number that says whether `RetrievalTuning.fusion_retention`'s 3x pool is
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
            max_chars=tuning.max_context_chars,
            max_tokens=tuning.max_context_tokens,
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

    async def _rerank(
        self, candidates: Sequence[ScoredChunk], *, query: str, stages: dict[str, object]
    ) -> list[ScoredChunk]:
        """Re-order the surviving candidates with a cross-encoder (plan step
        21, ``P-24``; retrieval plan §3.10, decision س-21).

        **Off unless a deployment says otherwise.** ``rerank_enabled`` ships
        ``False`` (س-21: "مطفأ افتراضيًّا كما في alpha"), because the accuracy
        it buys is paid for in latency on EVERY request — §6 risk ٦, "التفعيل
        قرار نشر واعٍ بثمنه". The flag is read off the injected
        ``RetrievalTuning``, so it is a ``Settings`` value (س-24) and there is
        nothing on ``execute`` a request could flip: §7 records that a
        per-request toggle "يحتاج قرارًا جديدًا" and is not this step's to
        invent. ``self._reranker is None`` is checked beside it because the
        Composition Root does not even build a client for a disabled stage —
        the two say the same thing from the two ends of the wiring, and
        neither alone would be honest.

        **Where it sits, and why here.** After ``filter_relevant`` and before
        parent expansion:

        * **After fusion and MMR**, because §3.10's scope is "أوّل 10-20
          مرشّحًا بعد الدمج، لا الكوربوس" — a cross-encoder reads every
          (query, document) pair, so it may only ever see a short list.
        * **After MMR specifically**, not before it. MMR decides MEMBERSHIP
          (which candidates survive the widened pool) from the FUSED RRF
          score, and — by plan step 20's own rule — nothing overwrites
          ``FusedChunk.score``. A rerank placed before MMR would therefore be
          erased by it: MMR would re-sort on the RRF numbers the reranker
          never touched. Reranking after leaves the two stages doing
          complementary jobs, diversity then precision, neither undoing the
          other.
        * **After ``filter_relevant``**, so the near-duplicates the Jaccard
          gate removes never cross the wire — the cheapest way to spend less
          of the latency §6 risk ٦ is about.
        * **Before parent expansion**, because everything from there on is
          prefix-sensitive: dedup-by-parent keeps the FIRST candidate to
          reach a parent, the context budget keeps a best-first PREFIX, and
          the last line keeps ``[:k]``. An ordering decided later would be
          decided on a list those stages had already cut the other way.
          Placing it here also means the reranker reads each candidate's own
          LEAF text — window-sized, which is what a cross-encoder wants —
          rather than a substituted parent section capped at 4000 characters.

        **The starvation guard (§3.10, ported from alpha): "ألّا يجوّع المُعيد
        ``final_top_n`` — إن أعاد أقلّ من المطلوب يُكمَّل من ترتيب RRF ولا
        يُقصَّر الجواب".** This stage NEVER shortens its input: whatever the
        reranker placed comes first in its order, and every candidate it did
        not return follows in the order it already had (RRF as re-ordered by
        MMR — the ranking this pipeline would have used with the reranker
        off). ``_apply_rerank`` is that rule, and it is a pure function so
        the property is provable without a network. A reranker that answers
        with two entries out of fifteen therefore costs the answer nothing:
        ``k`` chunks still leave ``execute``.

        **An outage costs the improvement, never the answer.** Every failure
        the adapter can produce — timeout, connection refused, 5xx,
        off-contract body — arrives here as an ``AppError``
        (``external_rerank.py`` translates the lot, so no raw ``httpx``
        exception can slip past this ``except``). It is logged as a WARNING
        with the code and swallowed, and retrieval carries on with the exact
        ordering it would have produced with the switch off. The alternative
        — letting an optional accuracy stage fail a retrieval that had
        already found its answer — would make enabling the reranker a
        reduction in availability, which is not what س-21 asked to be able to
        turn on.

        Note what does NOT move: ``ScoredChunk.score`` keeps its fused RRF
        value. The cross-encoder's own score is on a third scale entirely,
        and ``RetrievedChunk.score`` is a PUBLISHED field (03 §2) — writing a
        different quantity into it would change what every consumer thinks it
        is reading. Like MMR, this stage decides ORDER.
        """
        if not self._tuning.rerank_enabled or self._reranker is None or not candidates:
            return list(candidates)
        # §3.10's scope, as a slice: only the head is offered, and whatever
        # falls past `rerank_candidates` keeps its place after it. The tail is
        # never dropped -- this stage removes nothing, it only re-orders.
        scope = list(candidates[: self._tuning.rerank_candidates])
        tail = list(candidates[len(scope) :])
        try:
            ranked = await self._reranker.rerank(query, [chunk.text for chunk in scope])
        except AppError as exc:
            # The whole of "a reranker outage cannot take down a retrieval
            # that would otherwise have answered". `error_code` rather than
            # `code`: `extra` keys land on the `LogRecord`, and a name of our
            # own cannot collide with one logging reserves.
            log.warning(
                "knowledge.rerank_degraded",
                extra={"error_code": exc.code, "rerank_candidates": len(scope)},
            )
            return list(candidates)
        stages["rerank_count"] = len(ranked)
        return _apply_rerank(scope, ranked) + tail


def _apply_rerank(
    candidates: Sequence[ScoredChunk], ranked: Sequence[RerankedDocument]
) -> list[ScoredChunk]:
    """Apply a reranker's placements to ``candidates`` WITHOUT losing any of
    them — the "ألّا يجوّع ``final_top_n``" guard of retrieval plan §3.10, as
    a pure function.

    The reranked candidates come first, in the reranker's order; every
    candidate it left unplaced follows in ``candidates``' own order, which is
    the RRF/MMR ranking the pipeline would have used had the reranker been
    off. So ``len(result) == len(candidates)`` always, and the stages
    downstream — parent dedup, the context budget, the final ``[:k]`` — have
    exactly as much to work with as they did before. "يُكمَّل من ترتيب RRF ولا
    يُقصَّر الجواب", verbatim.

    Out-of-range and repeated indices are already impossible
    (``external_rerank._parse_response`` rejects both at the boundary), and
    they are skipped here anyway: this function is the pipeline's own
    invariant, and an invariant that depends on a remote service behaving is
    not one.
    """
    reordered: list[ScoredChunk] = []
    placed: set[int] = set()
    for document in ranked:
        if not 0 <= document.index < len(candidates) or document.index in placed:
            continue
        placed.add(document.index)
        reordered.append(candidates[document.index])
    reordered.extend(candidate for index, candidate in enumerate(candidates) if index not in placed)
    return reordered


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


def _mmr_rerank(
    fused: Sequence[FusedChunk],
    vectors: Mapping[str, Sequence[float]],
    *,
    top_n: int,
    lambda_: float,
) -> list[FusedChunk]:
    """Re-rank the fused pool by Maximal Marginal Relevance and cut it to
    ``top_n`` (plan step 20, ``P-23``; retrieval plan §3.9), keeping the
    ``FusedChunk`` records the rest of the pipeline reads.

    The pure algorithm (``domain/mmr.py``) speaks in ids, relevance numbers
    and vectors alone, so this is the whole of the translation: build a
    candidate per fused chunk the store actually returned a vector for, ask
    MMR for its selection, and re-order ``fused`` by it.

    ``MmrCandidate.relevance`` is the FUSED RRF score, deliberately — MMR does
    not recompute a query similarity of its own. Half of this pipeline's
    ranking comes from a BM25-sparse leg no embedding can see, so a dense
    ``sim(q, d)`` computed inside MMR would overrule RRF and demote exactly
    the candidates the sparse leg rescued (``domain/mmr.py``'s own docstring
    has the argument, and
    ``test_retrieve_context_lexical_only_recall_surfaces_via_sparse_leg``
    is the case it protects).

    Two honest degradations, both "never dropped, never an error" —
    ``_widen_to_parents``' rule, for the same reason (a ranking must not be
    the thing that fails):

    * **No vectors at all.** Nothing to diversify with, so the RRF order is
      returned, cut to ``top_n`` — exactly what this stage replaced. That is
      also what makes every store that ignores ``with_vectors`` behave as it
      did before.
    * **Some vectors missing.** The candidates MMR could rank come first, in
      its order; the ones it could not follow in RRF order and are cut by
      ``top_n`` like anything else. They are never *preferred* over a
      diversity-checked candidate, and never silently discarded either.

    Note what does NOT move: ``FusedChunk.score`` is still RRF's own number.
    MMR decides ORDER and MEMBERSHIP, and writing its internal score onto the
    record would put a cosine-scale value in a field the relevance floors read
    as an RRF-scale one.
    """
    candidates = [
        MmrCandidate(chunk_id=chunk.chunk_id, relevance=chunk.score, vector=vectors[chunk.chunk_id])
        for chunk in fused
        if chunk.chunk_id in vectors
    ]
    if not candidates:
        return list(fused[:top_n])
    selected = maximal_marginal_relevance(candidates, top_n=top_n, lambda_=lambda_)
    order = {chunk_id: rank for rank, chunk_id in enumerate(selected)}
    # `len(fused)` is past every real rank, so an unranked candidate sorts to
    # the tail; `sorted` is stable, so those keep their RRF order among
    # themselves.
    unranked = len(fused)
    ranked = sorted(fused, key=lambda chunk: order.get(chunk.chunk_id, unranked))
    return ranked[:top_n]


def _gate_by_score(hits: Sequence[VectorHit], min_score: float) -> list[VectorHit]:
    """Drop the hits of ONE leg whose raw score falls below ``min_score``
    (plan step 16, ``P-27``; retrieval plan §3.8).

    ``min_score <= 0.0`` is DISABLED, and that is an explicit early return
    rather than a comparison that happens to pass everything. On this
    module's scales it has to be: the dense leg is Qdrant cosine similarity
    over ``[-1, 1]``, so ``hit.score >= 0.0`` would quietly discard every
    negatively correlated hit — an uncalibrated gate disguised as a disabled
    default, exactly what decision س-22 forbids shipping. The shipped
    configuration (``Settings.retrieval.min_dense_score``/``.min_bm25_score``,
    both ``0.0``) takes this branch, so the function is inert until somebody
    sets a number.

    ⚠️ The surviving comparison is ``score >= min_score`` — HIGHER is better
    on both legs. alpha's floors gate an L2 DISTANCE where lower is nearer,
    so its comparison runs the other way and none of its numbers transfers
    (retrieval plan §6 risk #3).
    """
    if min_score <= 0.0:
        return list(hits)
    return [hit for hit in hits if hit.score >= min_score]


def _widen_to_parents(
    candidates: Sequence[ScoredChunk],
    parents: Mapping[str, ParentChunkText],
    *,
    max_parent_chunk_chars: int,
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
    * ⚠️ A parent was resolved but is INCOMPLETE
      (``ParentChunkText.is_complete`` is ``False``) — kept AS IS too, and
      NOT registered as a seen parent, because nothing was substituted for
      it. An incomplete parent is P-13's header-only row for a table past
      ``TABLE_PARENT_MAX_ROWS``: it holds the column names and not one value
      under them, so putting it in place of the row that was actually
      retrieved does not widen the match, it DELETES it — the passage the
      model is handed no longer contains the text that matched the query.
      This is the identical rule ``DocumentRepository.chunk_texts`` states
      for the summarisation side (``P-42``: "letting that stand in for its
      rows would summarise a data file as a list of headings"), and
      ``ChunkParent``'s docstring names it as binding on *every* consumer
      that substitutes a parent for its rows. Widening is an improvement the
      answer can always do without; losing the evidence is not.
    * A parent was resolved, is COMPLETE, and this is the FIRST candidate
      seen to widen to that ``ParentChunkText.id`` — kept, with ``text``
      replaced by the parent's text, capped at ``max_parent_chunk_chars``
      (``Settings.retrieval``, passed in as an argument — س-24).
    * A parent was resolved, but some EARLIER (higher- or equal-ranked)
      candidate already widened to the SAME ``id`` — dropped entirely. This
      is dedup BY PARENT (retrieval plan §3.7): keyed on the parent's ``id``,
      never on text equality, so two candidates that happen to widen to the
      identical parent text are recognised as duplicates even before either
      is truncated. Dropping the whole entry (not merely deduplicating the
      text) is what frees the slot ``RetrievalTuning.fusion_retention``'s 3x pool exists to
      fill with the next distinct candidate.
    """
    widened: list[ScoredChunk] = []
    seen_parent_ids: set[str] = set()
    for candidate in candidates:
        parent = parents.get(candidate.chunk_id)
        # `is_complete` is checked with the same `continue` as "no parent at
        # all" on purpose: both mean THIS candidate keeps its own text, and
        # neither may mark the parent seen -- a later candidate under the
        # same incomplete parent carries different text and is not a
        # duplicate of anything that was substituted, because nothing was.
        if parent is None or not parent.is_complete:
            widened.append(candidate)
            continue
        if parent.id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent.id)
        widened.append(replace(candidate, text=parent.text[:max_parent_chunk_chars]))
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

    Per-chunk on purpose, while ``RetrievalResult.context_text`` (plan step
    19, ``P-39``) renders the whole block: the budget has to measure each
    candidate SEPARATELY to know where to cut, and the block is what survives
    that cut. They agree by construction, since ``format_context_block`` is
    ``format_labeled_chunk`` applied per chunk and joined — so what the
    budget counted is exactly what the context carries, plus the separators.
    """
    return format_labeled_chunk(
        chunk.text,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section=chunk.section,
    )
