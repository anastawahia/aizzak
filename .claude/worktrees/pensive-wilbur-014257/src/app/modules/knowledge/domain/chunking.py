"""Deterministic fixed word-window chunker (06-domain-models §7; docs/
migration/refs/parsers.md §3, retrieval.md §7 risk #7; 3.k3).

Consumes ``ports/content_extractor.py``'s coarse, structurally-ordered
``ParsedChunk`` stream (the application layer maps each one to a
``SourceSegment`` here — this module stays framework/port-free) and produces
the fine-grained ``ChunkToIndex`` windows that actually get embedded/hashed
and upserted into Qdrant — the ``node_builder.py`` equivalent flagged as
deferred by `parsers.md §3`.

**Determinism (INV-K1, retrieval.md §7 risk #7):** segments are sorted by
their structural ``order`` first (a stable sort — ties keep their original
relative position), and ``seq`` is assigned from a single global 0-based
counter walked across the flattened, ordered stream of every window of every
segment. Rebuilding from the same parsed input therefore always yields the
same ``seq`` for the same logical window — unique and gap-free by
construction, never derived from wall-clock processing order or a content
hash.

Splitting operates on **whitespace words**, not model tokens: a
deterministic, model-independent proxy that needs no tokenizer model
dependency in the domain (``token_count`` is that same whitespace-word
count, not a true LLM token count).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSegment:
    """One structurally-ordered source segment to be split into chunks."""

    text: str
    order: int
    kind: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChunkToIndex:
    """One fixed word-window, ready for embedding/hashing and indexing."""

    seq: int
    text: str
    token_count: int
    kind: str
    metadata: dict[str, Any]


def chunk_segments(
    segments: Sequence[SourceSegment],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
    min_chars: int = 24,
) -> list[ChunkToIndex]:
    """Split ``segments`` into fixed, overlapping whitespace-word windows.

    Segments are processed in ascending ``order`` (stable — ties keep their
    original relative position among ``segments``). Each segment's text is
    split into ``max_tokens``-word windows that overlap by ``overlap_tokens``
    words; a trailing window shorter than ``min_chars`` characters is merged
    into the previous window of the *same* segment rather than emitted as its
    own tiny chunk (a lone segment that never gets split — its whole text fits
    in one window — is always kept as-is, regardless of length: the merge
    rule only applies to a split's leftover remainder). ``seq`` is one global
    0-based counter across every window of every segment, in processing
    order (INV-K1 — see the module docstring). ``kind``/``metadata`` are
    propagated verbatim from the owning segment (each chunk gets its own
    shallow copy of ``metadata``). Segments whose text is empty/whitespace-
    only contribute no chunks. Empty ``segments`` (or an input where every
    segment is empty) returns ``[]``.
    """
    ordered = sorted(segments, key=lambda segment: segment.order)

    chunks: list[ChunkToIndex] = []
    seq = 0
    for segment in ordered:
        words = segment.text.split()
        if not words:
            continue
        windows = _word_windows(words, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        windows = _merge_short_trailing_window(windows, min_chars=min_chars)
        for window in windows:
            chunks.append(
                ChunkToIndex(
                    seq=seq,
                    text=" ".join(window),
                    token_count=len(window),
                    kind=segment.kind,
                    metadata=dict(segment.metadata),
                )
            )
            seq += 1
    return chunks


def _word_windows(words: list[str], *, max_tokens: int, overlap_tokens: int) -> list[list[str]]:
    """Slide a ``max_tokens``-word window across ``words``, advancing by
    ``max_tokens - overlap_tokens`` words each step (clamped to at least 1
    word of progress, and at least a 1-word window, so degenerate parameters
    can never loop forever); the final window is clipped to whatever remains.
    """
    span = max(max_tokens, 1)
    step = max(max_tokens - overlap_tokens, 1)
    total = len(words)

    windows: list[list[str]] = []
    start = 0
    while start < total:
        end = min(start + span, total)
        windows.append(words[start:end])
        if end >= total:
            break
        start += step
    return windows


_MIN_WINDOWS_TO_MERGE = 2  # a lone (unsplit) window is never merged away


def _merge_short_trailing_window(windows: list[list[str]], *, min_chars: int) -> list[list[str]]:
    """Fold the last window into the previous one when its joined text is
    shorter than ``min_chars`` — only meaningful once a segment produced more
    than one window (a single-window segment is never merged away)."""
    if len(windows) < _MIN_WINDOWS_TO_MERGE:
        return windows
    trailing_length = len(" ".join(windows[-1]))
    if trailing_length >= min_chars:
        return windows
    return [*windows[:-2], [*windows[-2], *windows[-1]]]
