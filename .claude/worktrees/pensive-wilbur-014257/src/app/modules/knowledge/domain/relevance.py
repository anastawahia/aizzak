"""Relevance filtering — pure algorithm (06-domain-models §7; docs/migration/
refs/retrieval.md §4.8, §6.1; 3.k3).

Ports alpha's ``RelevanceFilter.filter`` (``relevance_filter.py``) gate
sequence: absolute floor → relative floor → Jaccard near-duplicate dedup
(retrieval.md §4.8 — "منطق تطبيقي صرف", a pure application-logic port).
Deliberate deviations from alpha:

* alpha's ``min_score``/``relative_floor`` thresholds are calibrated for raw
  FAISS **L2 distance** (smaller = closer) over a 384-dim MiniLM embedding —
  numbers that are meaningless to copy verbatim onto this module's
  cosine-similarity, RRF-fused score scale (retrieval.md §7 risk #6). Both
  floors still default to ``0.0`` here, but on THIS module's scale that
  default means "disabled" for a genuinely different reason (an RRF score is
  always > 0 for any candidate that made it this far) — an explicit,
  uncalibrated-until-integration note, not a silent copy of alpha's numbers.
* The Jaccard near-duplicate dedup threshold (``0.95``) is alpha's one
  metric-independent constant (retrieval.md §4.8: "عتبة ثابتة 0.95 غير
  قابلة للضبط") — ported verbatim, and left the only gate that defaults ON.

Word-set Jaccard runs over the sibling ``tokenization.tokenize`` output
(default multilingual pipeline) rather than alpha's raw whitespace
``.split()`` — this module already owns a tokenizer, so reusing it
(Arabic-normalization- and stop-word-aware sets) is a strict quality
improvement at zero extra dependency; it needs no separate decision record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.knowledge.domain.tokenization import tokenize

_DEFAULT_JACCARD_THRESHOLD = 0.95
_MIN_CANDIDATES_FOR_DEDUP = 2  # a single survivor has nothing to be a near-duplicate of


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """One candidate chunk with its fused retrieval score, ready for
    relevance filtering."""

    chunk_id: str
    document_id: str
    text: str
    score: float
    seq: int


def filter_relevant(
    candidates: Sequence[ScoredChunk],
    *,
    min_score: float = 0.0,
    relative_floor: float = 0.0,
    dedup: bool = True,
    jaccard_threshold: float = _DEFAULT_JACCARD_THRESHOLD,
) -> list[ScoredChunk]:
    """Gate ``candidates`` through three sequential filters (retrieval.md
    §4.8): an absolute score floor, a relative floor (a fraction of the
    surviving max score), and — unless ``dedup`` is ``False`` — an O(n²)
    pairwise Jaccard near-duplicate dedup that keeps the higher-scoring
    member of any pair reaching ``jaccard_threshold`` (ties keep whichever of
    the pair appears first in ``candidates``). Both floors default to
    ``0.0``, i.e. disabled (see the module docstring); ``dedup`` defaults to
    ``True`` at the ported alpha threshold. The result preserves each
    survivor's original relative position in ``candidates`` — this function
    never re-sorts.
    """
    survivors = [candidate for candidate in candidates if candidate.score >= min_score]

    if relative_floor > 0.0 and survivors:
        floor = relative_floor * max(candidate.score for candidate in survivors)
        survivors = [candidate for candidate in survivors if candidate.score >= floor]

    if not dedup or len(survivors) < _MIN_CANDIDATES_FOR_DEDUP:
        return survivors
    return _dedup_near_duplicates(survivors, jaccard_threshold=jaccard_threshold)


def _dedup_near_duplicates(
    candidates: Sequence[ScoredChunk], *, jaccard_threshold: float
) -> list[ScoredChunk]:
    """Evaluate every pair once (``O(n²)``); for any pair whose tokenized
    word sets meet ``jaccard_threshold``, mark the lower-scoring member of
    *that pair* as dropped (a chunk can be the loser of more than one pair).
    Survivors are returned in their original ``candidates`` order."""
    word_sets = [frozenset(tokenize(candidate.text)) for candidate in candidates]
    dropped: set[int] = set()
    total = len(candidates)
    for i in range(total):
        for j in range(i + 1, total):
            if _jaccard(word_sets[i], word_sets[j]) >= jaccard_threshold:
                loser = j if candidates[i].score >= candidates[j].score else i
                dropped.add(loser)
    return [candidate for index, candidate in enumerate(candidates) if index not in dropped]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
