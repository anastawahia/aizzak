"""Reciprocal Rank Fusion (RRF) — pure retrieval algorithm (06-domain-models.md
§7 `knowledge`; docs/migration/refs/retrieval.md §4.1).

Ports alpha's `rag/retrieval/reranking.py::reciprocal_rank_fusion` with the
llama_index `NodeWithScore` node type swapped out entirely: RRF is purely
rank-based (it only ever looks at each id's *position* in the two input
lists, never any raw score value), so it needs no node type at all — it
operates on plain ranked id sequences and returns neutral `FusedChunk`
records. This keeps the module pure stdlib: it imports no framework and no
other module (import-linter contract #2, "domain-purity").

Deliberate deviations from alpha (see retrieval.md §4.1 and §7 risk #8):

* **Naming fix.** alpha overloads the parameter name ``k`` for the
  *post-fusion result count* while separately also taking an ``rrf_k``
  damping constant — an easy footgun. Here the post-fusion cut is named
  ``top_k`` and the damping constant stays ``rrf_k``.
* **Uniform RRF-score semantics (behavioural change, documented and
  deliberate).** alpha, when exactly one of the two input lists is empty,
  short-circuits and returns the *other list untouched*, truncated to
  ``k``, with each node's **raw** score still attached (an L2 distance or a
  raw BM25 score) — so the meaning of "score" after "fusion" was not
  consistent (retrieval.md §4.1 flags this as a conscious decision to make
  on the rebuild). Here we *always* compute rank-based RRF for whichever
  list(s) are non-empty, so a returned `FusedChunk.score` is *always* an
  RRF score, never a raw score. Relative order within a single non-empty
  list is preserved (rank 0, 1, 2, … maps to a strictly decreasing score).
  Downstream thresholds (e.g. a relevance floor) will need to be
  recalibrated against this uniform scale in 3.k3 (retrieval.md §7 risk #6).
* **Determinism fix.** alpha builds the id union via ``set(list(...) +
  list(...))``, whose iteration order is not guaranteed to be stable across
  processes/runs (Python's string hashing is randomized per-process by
  default) — a real risk for exact tied-score reproducibility (retrieval.md
  §7 risk #8: "لا-حتمية طفيفة في تعادل RRF"). Here the id union is built by
  inserting ids into an insertion-ordered ``dict`` (first ``dense_ids``,
  then ``bm25_ids``, skipping ids already seen) — a language-guaranteed
  deterministic order, never a ``set``. Combined with Python's stable
  ``sort``, an exact score tie always breaks in that same deterministic
  first-seen order, in every process, on every run.

Weights (``weight_dense``/``weight_bm25``) are applied exactly as given;
alpha normalizes them to sum to 1.0 in its caller (`_fuse`), not inside
`reciprocal_rank_fusion` itself — this module keeps that same division of
responsibility and does not normalize internally.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FusedChunk:
    """One chunk id's fused Reciprocal Rank Fusion result."""

    chunk_id: str
    score: float


def reciprocal_rank_fusion(
    dense_ids: Sequence[str],
    bm25_ids: Sequence[str],
    *,
    top_k: int,
    weight_dense: float = 0.5,
    weight_bm25: float = 0.5,
    rrf_k: int = 60,
) -> list[FusedChunk]:
    """Fuse two best-first-ranked id lists with weighted Reciprocal Rank Fusion.

    ``dense_ids``/``bm25_ids`` are already-ranked, best-first sequences of
    chunk ids (rank = 0-based index into the sequence). For an id ``n`` at
    rank ``r`` in a list, that list's contribution to ``n``'s score is
    ``weight * 1.0 / (rrf_k + r + 1)``; an id present in both lists sums both
    contributions — a natural de-duplication (retrieval.md §4.1, formula
    verbatim). ``weight_dense``/``weight_bm25`` are used exactly as given —
    the caller is responsible for normalizing them to sum to 1.0, matching
    alpha's `_fuse`.

    Returns the ``top_k`` highest-scoring `FusedChunk`s, sorted by score
    descending; exact ties keep the deterministic first-seen order (all
    ``dense_ids`` in order, then any ``bm25_ids`` not already seen, in order
    — see the module docstring). Returns ``[]`` when both input lists are
    empty, or when ``top_k <= 0``.
    """
    if top_k <= 0:
        return []

    dense_ranks = {chunk_id: rank for rank, chunk_id in enumerate(dense_ids)}
    bm25_ranks = {chunk_id: rank for rank, chunk_id in enumerate(bm25_ids)}

    ordered_ids: dict[str, None] = dict.fromkeys(dense_ids)
    for chunk_id in bm25_ids:
        ordered_ids.setdefault(chunk_id, None)

    fused: list[FusedChunk] = []
    for chunk_id in ordered_ids:
        score = 0.0
        dense_rank = dense_ranks.get(chunk_id)
        if dense_rank is not None:
            score += weight_dense * (1.0 / (rrf_k + dense_rank + 1))
        bm25_rank = bm25_ranks.get(chunk_id)
        if bm25_rank is not None:
            score += weight_bm25 * (1.0 / (rrf_k + bm25_rank + 1))
        fused.append(FusedChunk(chunk_id=chunk_id, score=score))

    fused.sort(key=lambda item: item.score, reverse=True)
    return fused[:top_k]
