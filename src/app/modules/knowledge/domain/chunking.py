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

**Node filtering (P-15, rag-indexing-plan.md §4 step 8).** After windowing,
every window is put through one more gate before it becomes a
``ChunkToIndex``: empty (whitespace-only) windows are dropped, windows
shorter than ``MIN_NODE_CHARS`` are dropped (a node too short to answer
anything is pure embedding/storage/retrieval-noise cost with no retrieval
value), and a window whose text is a byte-for-byte duplicate of an
earlier-surviving window's text is dropped too (the FIRST occurrence
survives, mirroring ``domain/relevance.py``'s ``filter_relevant`` dedup
order) -- cheap boilerplate (a repeated header/footer/disclaimer line, or a
table's own noise-column-only row rendering the same sentence twice) would
otherwise cost an embedding call and a Qdrant point for text retrieval
already has, gaining nothing. ``seq`` is assigned only to the SURVIVORS, so
it stays the same gap-free 0-based counter INV-K1 promises regardless of how
many windows a filtering pass removes.

**Real token budget (P-16, rag-indexing-plan.md §4 step 9).**
``max_words_for_token_limit`` turns ``Settings.embedding_service.
embedding_max_input_tokens`` (an ``application``-layer concern -- this
module never imports ``Settings``) into the actual ``max_tokens`` word
budget ``chunk_segments`` splits against, so an over-long node cannot be
silently truncated at the embedding HTTP boundary (§3.5's failure mode).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# P-15 (plan §4 step 8): a node shorter than this many characters carries no
# retrieval value on its own -- dropped entirely, not merged (the
# ``min_chars`` parameter below is a DIFFERENT, earlier concern: whether a
# split's leftover remainder gets folded into its neighbour before either
# ever reaches this gate).
MIN_NODE_CHARS = 15

# P-16 (plan §4 step 9, §3.5 + decision س-11) -- alpha's token-to-word
# calibration, ported verbatim into ``max_words_for_token_limit`` below.
#
# ``_TOKENS_PER_WORD`` is NOT the adapter's own truncation estimate
# (``external_embedding.py``'s ``len(text)//4`` is a *characters*-per-token
# guess); it is words-per-token, and Arabic costs MORE sub-word tokens per
# character than English, so a plain 1.0 (one token per word) would
# UNDER-count and let a chunk that silently truncates at the embedding HTTP
# boundary through -- the one failure mode §3.5 calls out by name (the
# truncated tail is invisible to everyone: the point still gets a vector, it
# is just a vector for less text than the payload claims). Erring toward
# FEWER words per chunk is the safe direction, so this factor is a floor, not
# a target, and MUST NOT be lowered.
_TOKENS_PER_WORD = 1.3
# A further 10% safety margin on top of ``_TOKENS_PER_WORD`` -- the same
# "erring toward fewer words" reasoning one layer further, ported from alpha
# unchanged.
_MAX_WORDS_SAFETY_MARGIN = 0.9
# A hard floor so a pathologically small ``embedding_max_input_tokens`` can
# never collapse chunking into one-word (or zero-word) windows.
MIN_MAX_WORDS = 32
# The 10% overlap alpha applies when a node is split for length (plan §3.5's
# trailing comment on the formula) -- a fraction OF ``max_words_for_token_
# limit``'s own result, not of ``embedding_max_input_tokens``.
SPLIT_OVERLAP_RATIO = 0.1


def max_words_for_token_limit(embedding_max_input_tokens: int) -> int:
    """The real per-chunk word budget for a given embedding token limit
    (P-16, plan §3.5) -- alpha's formula ported verbatim:
    ``max(int((embedding_max_input_tokens / 1.3) * 0.9), 32)``.

    Pure and framework-free on purpose (``lint-imports``' "Domain is pure"
    contract, plan §0): the caller (``application/indexing.py``) is what
    reads ``Settings.embedding_service.embedding_max_input_tokens`` and
    passes the plain ``int`` in here -- this function never touches
    ``Settings``, env, or an ``EmbeddingProvider`` (ح-6/ح-7, plan §2).
    """
    return max(
        int((embedding_max_input_tokens / _TOKENS_PER_WORD) * _MAX_WORDS_SAFETY_MARGIN),
        MIN_MAX_WORDS,
    )


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
    rule only applies to a split's leftover remainder).

    Every resulting window then passes through the P-15 node filter (module
    docstring): empty, shorter than ``MIN_NODE_CHARS``, or a duplicate of an
    earlier-surviving window's text is dropped. ``seq`` is one global 0-based
    counter across every SURVIVING window, in processing order (INV-K1 — see
    the module docstring) — gap-free regardless of how much filtering
    removed. ``kind``/``metadata`` are propagated verbatim from the owning
    segment (each chunk gets its own shallow copy of ``metadata``). Segments
    whose text is empty/whitespace-only contribute no windows. Empty
    ``segments`` (or an input where every segment is empty, or where every
    window is filtered away) returns ``[]``.
    """
    ordered = sorted(segments, key=lambda segment: segment.order)

    chunks: list[ChunkToIndex] = []
    seen_hashes: set[str] = set()
    seq = 0
    for segment in ordered:
        words = segment.text.split()
        if not words:
            continue
        windows = _word_windows(words, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        windows = _merge_short_trailing_window(windows, min_chars=min_chars)
        for window in windows:
            text = " ".join(window)
            if not _keep_node(text, seen_hashes):
                continue
            chunks.append(
                ChunkToIndex(
                    seq=seq,
                    text=text,
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


def _keep_node(text: str, seen_hashes: set[str]) -> bool:
    """Whether one window's ``text`` survives into the indexed stream (P-15,
    module docstring): not empty/whitespace-only, at least
    ``MIN_NODE_CHARS`` long, and not a byte-for-byte duplicate of an
    earlier-surviving window's text — the FIRST occurrence of a repeated
    text wins (mirroring ``domain/relevance.py``'s ``filter_relevant`` dedup
    order), the rest are dropped.

    A ``sha256`` hash of the (already-stripped) text is what actually goes
    into ``seen_hashes`` rather than the text itself: a hash-set membership
    check is O(1) regardless of how long a node's text is, and it is the
    same non-cryptographic-use hashing pattern this module's neighbours
    (``domain/sparse.py``'s ``term_id``) already use for a stable, cheap
    dedup key. ``seen_hashes`` is mutated as a side effect — the caller owns
    one set for the whole ``chunk_segments`` call, so a duplicate is caught
    across segment boundaries too, not only within one segment.
    """
    stripped = text.strip()
    if not stripped or len(stripped) < MIN_NODE_CHARS:
        return False
    digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    if digest in seen_hashes:
        return False
    seen_hashes.add(digest)
    return True
