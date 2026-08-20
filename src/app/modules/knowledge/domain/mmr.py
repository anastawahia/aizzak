"""Maximal Marginal Relevance (MMR) — pure diversity re-ranking algorithm
(rag-retrieval-plan.md §3.9 / §4 row 20, ``P-23``, decision س-20).

    score(d) = λ·sim(q, d) - (1 - λ)·max sim(d, dⱼ)   over the already-selected dⱼ

**The problem it exists to solve** (§3.9 verbatim): "خمس قطع من الفقرة نفسها
نتيجة مشروعة اليوم" — five chunks from the same paragraph are a legitimate
retrieval result today. RRF fuses two *rankings*; nothing in it has any notion
of how similar two candidates are to EACH OTHER, so a passage that a query
matches five times over wins five of the caller's ``k`` slots and the answer is
built from one paragraph read five times. MMR is the standard fix: it selects
greedily, and every pick after the first is penalised by how much it repeats
what is already selected.

**Pure, and pure by construction** (س-20: "خوارزمية نقيّة في ``domain/``"). This
module sits beside ``fusion.py``, ``relevance.py``, ``intent.py`` and
``file_resolution.py`` and imports the standard library ONLY — no port, no
provider, no I/O. It receives vectors; it never fetches them (fetching is the
application layer's job, and the price of it — ``with_vectors=True`` over the
widened search — is §3.9's own declared cost and §6 risk #5). ``lint-imports``
contract 2 ("Domain is pure") enforces the rule; a test reads this module's own
AST and pins the import set, the ``file_resolution.py`` precedent.

**λ = 0.7 is shipped, and it is NOT a threshold.** Decision س-22 forbids
shipping uncalibrated NUMBERS as quality gates; §3.8's last row places λ
explicitly outside that ban — "مقايضة **تنوّع**، لا بوّابة «هل هذا جيّد كفاية»".
Nothing is admitted or rejected by comparing a score to λ: it only weighs
relevance against redundancy between candidates that all already passed every
gate. The value still arrives as an ARGUMENT (س-24) — the default here mirrors
``Settings.retrieval.mmr_lambda``, it is not a second configuration.

**``sim(q, d)`` is the caller's OWN relevance score, not a dense cosine
recomputed here — and that is a deliberate deviation from the textbook
formulation.** The classic MMR (and alpha's, and LangChain's
``max_marginal_relevance_search``) reads ``sim(q, d)`` off the query embedding,
because those pipelines are dense-only and the embedding IS the retriever. This
pipeline is HYBRID: half of its ranking comes from a BM25-sparse leg that no
embedding can see. Recomputing ``sim(q, d)`` from the query vector here would
silently overrule RRF and demote every candidate the sparse leg rescued —
precisely the "lexical-only recall" case that is the hybrid design's whole
justification, and which a shipped test pins. So relevance ARRIVES on
``MmrCandidate.relevance`` (the fused RRF score) and only the redundancy term
is computed from vectors.

**Which makes the two terms commensurate the one honest way.** A raw RRF score
is on a scale of its own (``Σ w/(60+rank)`` — thousandths however good the
candidate, ``fusion.py``); subtracting a cosine in ``[-1, 1]`` from it would
let the diversity term dominate by three orders of magnitude and λ would stop
meaning anything. Relevance is therefore expressed as a FRACTION OF THE POOL's
best (``score / max``), which lands it in ``[0, 1]`` beside the redundancy
cosine while preserving the ratios RRF actually produced. Deliberately not
min-max normalisation, which would stretch every pool — however tight — across
the full ``[0, 1]`` and so make the diversity penalty weakest exactly where the
candidates are most alike. This is the same scale discipline §6 risk #3 states
for alpha's numbers, applied inside one formula.

**Determinism** (the ``fusion.py`` rule, same reason): every comparison is a
strict ``>``, resolved by ``max``'s own first-wins semantics, so an exact score
tie always keeps the EARLIER candidate — i.e. the caller's incoming order,
which is RRF's ranking. No ``set`` iteration decides anything.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Mirrors ``Settings.retrieval.mmr_lambda``/``RetrievalTuning.mmr_lambda``
# (§3.8's last row). λ = 1.0 is pure relevance (MMR off, order = the caller's
# own); λ = 0.0 is pure diversity, which would happily open with an irrelevant
# candidate. 0.7 leans on relevance and spends the remainder on not repeating
# a paragraph.
_DEFAULT_LAMBDA = 0.7


@dataclass(frozen=True, slots=True)
class MmrCandidate:
    """One candidate for MMR selection: its id, the ``sim(q, d)`` its
    retriever already computed, and the dense vector the REDUNDANCY term is
    computed from.

    ``relevance`` is higher-is-better and needs no particular scale — the
    algorithm reads it as a fraction of the pool's best, so any monotone
    relevance score works (this pipeline passes the fused RRF score). It is
    the caller's, never recomputed here; see the module docstring for why a
    hybrid retriever must not have its ranking overruled by a dense cosine.

    Three plain fields rather than a retrieval DTO, exactly like
    ``fusion.reciprocal_rank_fusion``'s plain id sequences: MMR needs no text,
    no payload and no document id, and taking any of them would couple a
    general ranking algorithm to one caller's record shape.
    """

    chunk_id: str
    relevance: float
    vector: Sequence[float]


def maximal_marginal_relevance(
    candidates: Sequence[MmrCandidate],
    *,
    top_n: int,
    lambda_: float = _DEFAULT_LAMBDA,
) -> list[str]:
    """Select up to ``top_n`` of ``candidates`` by Maximal Marginal Relevance,
    returning their ``chunk_id``s in SELECTION order (best first).

    The first pick has nothing selected to be redundant with, so it is the
    candidate with the highest ``relevance`` — the most relevant chunk stays
    first (§3.7: "الأكثر صلة في ``[#1]``"; ``LongContextReorder``, which would
    move it to the end, is a rejected design). Every later pick maximises
    ``λ·sim(q, d) - (1 - λ)·max sim(d, dⱼ)`` over the candidates not yet
    chosen, where ``sim(q, d)`` is ``relevance`` as a fraction of the pool's
    best (module docstring) and ``dⱼ`` ranges over everything already
    selected.

    A pool whose best ``relevance`` is not positive cannot be expressed as a
    fraction of it, so every candidate's relevance term reads ``0.0`` and the
    selection is decided by diversity alone, opening at the caller's own first
    entry. RRF scores are always positive for anything that reached here, so
    this is the corrupt-input path — it degrades, it does not raise.

    ``candidates`` is expected best-first (an RRF ranking); that order is what
    breaks exact ties, and what the caller gets back for the entries MMR did
    not reach. Returns ``[]`` for ``top_n <= 0`` or no candidates. Duplicate
    ``chunk_id``s are not de-duplicated here — the fused pool has none, and
    inventing a de-dup would hide a caller's bug.

    ``lambda_`` is clamped to ``[0.0, 1.0]``: outside that interval one of the
    two terms turns into a REWARD for its own opposite (a negative diversity
    weight actively prefers repetition), which is never what a configured
    number meant. Clamping keeps a misconfiguration merely extreme rather than
    inverted — the ``max(1, min(k, max_k))`` precedent in the use-case, not a
    validation error raised out of a ranking.
    """
    if top_n <= 0 or not candidates:
        return []

    weight = min(1.0, max(0.0, lambda_))
    # Normalise the vectors ONCE, then every similarity is a plain dot
    # product. Cosine over raw vectors would recompute both norms on each of
    # the `top_n * len(candidates)` pair comparisons below; over unit vectors
    # the whole selection costs one pass of norms plus dot products.
    units = [_unit(candidate.vector) for candidate in candidates]
    # Relevance as a fraction of the pool's best, so it shares the redundancy
    # term's scale (module docstring). NOT min-max.
    best_relevance = max(candidate.relevance for candidate in candidates)
    relevance = [
        candidate.relevance / best_relevance if best_relevance > 0.0 else 0.0
        for candidate in candidates
    ]
    # `max sim(d, dⱼ)` for each not-yet-selected candidate, maintained
    # INCREMENTALLY: when a candidate is selected, every survivor is compared
    # against that ONE new vector and keeps the larger of the two. Recomputing
    # the max against the whole selected set each round would be
    # `O(top_n² · n · dim)` instead of `O(top_n · n · dim)`.
    #
    # Seeded at 0.0 only to have a value while nothing is selected; the first
    # selection ASSIGNS (never maxes with the seed), so a genuinely negative
    # similarity — a candidate pointing AWAY from what is selected, the most
    # diverse thing there is — is not silently floored to zero.
    redundancy = [0.0] * len(candidates)
    remaining = list(range(len(candidates)))
    selected: list[str] = []

    while remaining and len(selected) < top_n:
        # `max` returns the FIRST maximal element, so an exact tie keeps the
        # earlier (better-ranked by the caller) candidate — the determinism
        # rule `fusion.py` states for the same reason.
        best = max(
            remaining,
            key=lambda index: weight * relevance[index] - (1.0 - weight) * redundancy[index],
        )
        remaining.remove(best)
        selected.append(candidates[best].chunk_id)
        first_selection = len(selected) == 1
        for index in remaining:
            similarity = _dot(units[index], units[best])
            redundancy[index] = (
                similarity if first_selection else max(redundancy[index], similarity)
            )

    return selected


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    """``vector`` scaled to length 1, so a dot product IS its cosine
    similarity.

    A zero vector has no direction, so it is returned as-is: every dot product
    against it is ``0.0``, which reads as "neither relevant nor redundant" —
    such a candidate never wins the opening pick and never penalises anything.
    A degenerate embedding is a corrupt input, and a ranking must degrade
    rather than raise ``ZeroDivisionError`` in the middle of answering.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of two vectors — cosine similarity, given both are unit
    vectors (``_unit``).

    ``strict=False`` stops at the shorter sequence. Vectors of different
    lengths cannot occur in one Qdrant collection (a single ``dim`` per
    collection), so this is the corrupt-input path only, and it degrades to
    the shared prefix instead of raising: the ranking still returns an answer.
    """
    return sum(left * right for left, right in zip(a, b, strict=False))
