"""Deterministic fixed word-window chunker (06-domain-models §7; docs/
migration/refs/parsers.md §3, retrieval.md §7 risk #7; 3.k3).

Consumes ``ports/content_extractor.py``'s coarse, structurally-ordered
``ParsedChunk`` stream (the application layer maps each one to a
``SourceSegment`` here — this module stays framework/port-free) and produces
the fine-grained ``ChunkToIndex`` windows that actually get embedded/hashed
and upserted into Qdrant — the ``node_builder.py`` equivalent flagged as
deferred by `parsers.md §3`.

**Determinism (INV-K1, retrieval.md §7 risk #7):** every window, across every
segment, is sorted by the P-17 multi-signal ordering key (module docstring
below) before ``seq`` is assigned from a single global 0-based counter walked
across that sorted stream. Rebuilding from the same parsed input therefore
always yields the same ``seq`` for the same logical window — unique and
gap-free by construction, never derived from wall-clock processing order or a
content hash.

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

**Multi-signal ordering key (P-17, rag-indexing-plan.md §4 step 10).** A
single ``SourceSegment.order`` int is a coarse, per-producer STRUCTURAL rank
(``page_index * 1000 + block_index`` for a PDF block, ``sheet_index *
100_000 + segment_index`` for an Excel table, ...) -- it is what
``ports/content_extractor.py``'s producers already guarantee, but on its own
it under-uses the finer per-chunk signals several of those same producers
already attach to ``SourceSegment.metadata`` (``page_number``,
``position_in_doc``, ``paragraph_number``, ``chunk_index`` -- see
``pdf_text.py``'s and ``docx.py``'s own docstrings). ``_order_key`` builds a
multi-column key out of that precedence chain -- ``page_number ->
position_in_doc -> paragraph_number -> chunk_index -> segment.order ->
segment.sub_order -> split_part`` -- comparing one column at a time and only
falling through to the next when the current one ties.

A segment whose metadata OMITS a column sorts AFTER every segment that has
it at THAT column ("NULLS LAST" — the ``ORDER BY`` convention this key
follows throughout), never before, and never raises on an absent field: a
segment carrying a real, specific value for a column is more informative
than one that carries none, so it is what gets to decide the order at that
column. This is why `image_ocr.py`'s archive-media OCR group (``.docx``'s
`word/media/` / ``.xlsx``'s `xl/media/`, module docstring's "Not in this
step" — it carries NONE of the four columns, only the fixed trailing
``_MEDIA_ORDER`` on ``segment.order`` itself) sorts after every OTHER
segment in its document at columns 1-4 already, regardless of how huge
``_MEDIA_ORDER`` is — the same outcome ``_MEDIA_ORDER``'s own comment
promises, reached one column earlier. `pdf_tables.py`'s chunks carry
`page_number` but never `position_in_doc` (a table is one coarse region, not
a positioned paragraph): on a page with a table and a text block that
happen to land on the exact same structural rank (`pdf_tables.py`'s and
`pdf_text.py`'s independent per-page counters CAN coincide -- plan §6 risk 8,
§7's own note), the block's real `position_in_doc` value is what decides —
NOT the OLD tie-break this key replaces (insertion/phase order, "tables
first"). That is a deliberate refinement, not an oversight: §7 explicitly
hands "the geometric ordering within the page" to this key, and a real
positional signal is more informative than an arbitrary phase order.
``segment.order``, ``segment.sub_order``, and the split-part index are the
last three columns because ALL THREE are always present for every producer
(``sub_order`` defaults to 0, ``SourceSegment``'s own docstring), which is
what keeps the whole key a total order: two windows can only ever compare
equal if they belong to the very same split of the very same segment.

``segment.sub_order`` (P-20, plan §4 step 13, §3.4) sits between
``segment.order`` and the split-part index for exactly one reason: semantic
pre-splitting (``application/indexing.py``) mints SEVERAL ``SourceSegment``s
out of one original ``ParsedChunk`` -- one per semantic part -- all sharing
that chunk's own ``order`` and metadata verbatim (there is only one
structural position to report; the parts are a finer subdivision of it, not
new positions). Without a column between them, those parts would only be
distinguished by their OWN, independently-restarting-at-0 split-part index,
which does not track reading order across parts (part 2's first window
would tie, then lose, against part 1's second window). ``sub_order`` is
that missing per-part sequence number; every producer that does not do
semantic pre-splitting stamps 0 on every ``SourceSegment`` it emits, so this
column is a silent no-op for them -- the key degrades to exactly what it was
before P-20.

⚠️ **Known limitation, inherited from the parsers, not introduced here:**
`position_in_doc` does not mean the same THING for every producer that
populates it. `pdf_text.py`'s blocks and `docx.py`'s paragraphs/tables use a
document-wide running counter; `image_ocr.py`'s PDF per-page OCR group
instead stamps its OWN page index (`_pdf_groups`) — a much smaller number on
any document with more than ~one block per page. Column 2 therefore
reliably orders same-page PDF text/table content and reliably orders DOCX
content (a single consistent counter there), but a PDF page's OCR-images
group -- which always CARRIES `position_in_doc`, unlike that page's own
tables, which never do -- sorts BEFORE that page's table (real value beats
"NULLS LAST") and, for a document with several blocks per page, typically
before that page's own text blocks too: a real but small value still beats
an absent one, and it is frequently smaller than a deep-document block's
running counter. Fixing this needs a genuinely comparable `position_in_doc`
for OCR groups in `image_ocr.py` — out of `domain/chunking.py`'s reach and
out of this step's scope (plan §4 step 10 touches only this module).

The key is built and sorted on AFTER windowing (module docstring's "Real
token budget" paragraph) -- P-17's own requirement (plan §4 step 10: "stamped
after the length split of step 9") -- so a segment split into several parts
for length gets each part its own ``seq``, ordered by that part's own index
within the split (the key's last column), not by the whole segment's.
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


# P-17 (plan §4 step 10) -- the metadata keys the ordering key consults, in
# PRECEDENCE order (an ``ORDER BY page_number, position_in_doc,
# paragraph_number, chunk_index`` over ``SourceSegment.metadata``, module
# docstring). ``segment.order`` and the split-part index are NOT metadata
# keys -- they are the dataclass field and the windowing loop's own index
# (see ``_order_key``), always present, and appended after these four.
_ORDER_SIGNAL_KEYS = ("page_number", "position_in_doc", "paragraph_number", "chunk_index")


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

    Each segment's text is split into ``max_tokens``-word windows that
    overlap by ``overlap_tokens`` words; a trailing window shorter than
    ``min_chars`` characters is merged into the previous window of the
    *same* segment rather than emitted as its own tiny chunk (a lone segment
    that never gets split — its whole text fits in one window — is always
    kept as-is, regardless of length: the merge rule only applies to a
    split's leftover remainder).

    Every resulting window is then sorted, GLOBALLY across every segment, by
    the P-17 multi-signal ordering key (module docstring) — the source of
    ``seq``'s ordering, not ``segments``' own input order or a segment's
    ``order`` alone. It then passes through the P-15 node filter (module
    docstring): empty, shorter than ``MIN_NODE_CHARS``, or a duplicate of an
    earlier-surviving window's text is dropped. ``seq`` is one global 0-based
    counter across every SURVIVING window, in that sorted order (INV-K1 — see
    the module docstring) — gap-free regardless of how much filtering
    removed. ``kind``/``metadata`` are propagated verbatim from the owning
    segment (each chunk gets its own shallow copy of ``metadata``). Segments
    whose text is empty/whitespace-only contribute no windows. Empty
    ``segments`` (or an input where every segment is empty, or where every
    window is filtered away) returns ``[]``.
    """
    candidates: list[tuple[tuple[int, ...], SourceSegment, list[str]]] = []
    for segment in segments:
        words = segment.text.split()
        if not words:
            continue
        windows = _word_windows(words, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        windows = _merge_short_trailing_window(windows, min_chars=min_chars)
        for split_part, window in enumerate(windows):
            candidates.append((_order_key(segment, split_part), segment, window))

    # `list.sort` is stable (Python guarantee): two candidates whose keys
    # compare equal — which `_order_key`'s own docstring says can only happen
    # for the same split of the same segment — keep their `candidates`
    # insertion order, i.e. never actually collide in practice.
    candidates.sort(key=lambda candidate: candidate[0])

    chunks: list[ChunkToIndex] = []
    seen_hashes: set[str] = set()
    seq = 0
    for _key, segment, window in candidates:
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


def _order_key(segment: SourceSegment, split_part: int) -> tuple[int, ...]:
    """P-17's multi-signal ordering key for one window (module docstring):
    ``page_number -> position_in_doc -> paragraph_number -> chunk_index ->
    segment.order -> split_part``, "NULLS LAST" at each metadata column.
    Each of the four metadata columns contributes a ``(present, value)``
    pair — ``(0, value)`` when the segment's metadata carries that key as an
    ``int``, ``(1, 0)`` otherwise — so a segment missing a column always
    sorts AFTER one that has it, at that column, without ever comparing
    ``None`` against an ``int``. ``segment.order`` and ``split_part`` close
    the key as plain ints because both are always present, which is what
    keeps this a TOTAL order (module docstring).
    """
    signals: list[int] = []
    for key in _ORDER_SIGNAL_KEYS:
        signals.extend(_signal(segment, key))
    return (*signals, segment.order, split_part)


def _signal(segment: SourceSegment, key: str) -> tuple[int, int]:
    """One ``_order_key`` column: ``(0, value)`` if ``segment.metadata[key]``
    is an ``int`` (bools excluded — ``isinstance(True, int)`` is ``True`` in
    Python, and no producer ever puts a bool under one of these keys), else
    the "NULLS LAST" sentinel ``(1, 0)``."""
    value = segment.metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    return (1, 0)


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
