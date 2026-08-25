"""Context budget -- pure algorithm (rag-retrieval-plan.md §3.7 / §4 row 10,
``P-35``), a sibling of `fusion.py` and `relevance.py`: stdlib only, no
network, no tokenizer download, no model load -- and every number it obeys
arrives as an ARGUMENT (decision س-24: the values live in `Settings` and are
passed INTO the domain; there is no `os.getenv` here, and no per-request
override anywhere).

**Where this runs.** §3.7's pipeline, verbatim:

    fusion -> keep 3x k -> filter_relevant -> replace with parent text
        -> **context budget** -> final k

so it runs AFTER parent expansion (plan step 9, `P-34` -- the step that makes
every surviving candidate BIGGER) and BEFORE the caller's `k` is applied.
Both halves of that placement are load-bearing: budgeting before the widening
would measure text nobody sends, and budgeting after the final `k` would let a
handful of oversized parents blow the prompt with no recourse left.

**A DUAL budget, and the SMALLER cap wins** (§3.7: "و يُؤخَذ الأصغر"). The cut
falls at whichever ceiling -- `max_chars` or `max_tokens` -- is breached first.
Two caps rather than one because neither alone is honest here: characters are
exact but say nothing about what the model actually charges for, while tokens
are what the model charges for but can only be ESTIMATED without a real
tokenizer (see `estimate_tokens`). The character cap is the hard, exactly
measurable floor under an estimate that could drift; the token cap is what
actually tracks the model's window.

Which of the two bites first is a CONFIGURATION property, not a property of
this function -- and the shipped pair is chosen so that it is always the
character cap (`Settings.Limits.max_context_tokens` carries the derivation).
That matters because `estimate_tokens` charges Arabic more than twice what it
charges English: any pair where the token cap CAN bite first cuts an Arabic
context short while passing the identical English one, so the ceiling actually
in force stops being the exactly measurable one and starts depending on the
script. The pair shipped before 2026-08-25 did exactly that.

**Descending order, then cut -- never a reorder.** `candidates` arrive
best-first (RRF-sorted; `filter_relevant` and the parent widening both
preserve order) and this function preserves that order exactly: it keeps a
PREFIX of the list, so the highest-scoring chunk is always `[#1]` in what the
model reads. `LongContextReorder` -- moving the best chunk to the END of the
context -- is an explicitly REJECTED design (retrieval plan §3.7 and §7,
carrying alpha's own rejection): it hurts the small (<=7B) models this
platform targets, which attend to the START of the context.

**A cut never empties the context.** The first (best) candidate is kept even
when it alone breaches both ceilings. An empty return here would be a lie with
consequences: zero chunks is exactly the signal the trust gate (plan step 5,
`P-33`) reads as "retrieval found nothing", and the user would be told the
workspace has no answer when in truth one relevant-but-large passage was
found and silently discarded by a budget. Truncating that passage's TEXT is
not this function's job either -- it is generic over the item type and does not
know how to rebuild one -- and the leaf/parent texts it sees are already capped
upstream (`RetrievalTuning.max_parent_chunk_chars`), so the overflow is
bounded rather than unbounded.

**Measured on the RENDERED text, not the raw text.** Each candidate is handed
in WITH the exact string that will be shown to the model -- source label
included (§3.2's `[file p.N | section: S]`, built by the one shared formatter).
One source of truth: the budget can never drift from what is actually sent. The
only thing deliberately NOT counted is the separator a caller joins the chunks
with (two characters per boundary), a rounding-level term against a
four-figure budget, and one this module refuses to guess at because the join
belongs to the caller.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

# The Arabic Unicode block, written as literal boundary characters to match
# this module's siblings (`tokenization.py`'s own note on the convention).
_ARABIC_CHAR_RE = re.compile("[؀-ۿ]")

# `estimate_tokens`' two rates -- see its docstring for why they differ and why
# the Arabic one is deliberately the pessimistic side of the range.
_ARABIC_CHARS_PER_TOKEN = 2.0
_OTHER_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Estimate `text`'s token count -- an ESTIMATE, never an exact count.

    Deliberately arithmetic-only: no `tiktoken`, no HuggingFace tokenizer, no
    model download, no network. A context budget must be computable in a pure
    domain function during every retrieval, and the exact number would in any
    case be wrong the moment the deployment switched models -- each model
    tokenizes differently, so precision here would be false precision.

    Two rates, because one is dishonest across this platform's two languages:

    * Arabic-block characters cost ~2 characters per token. Arabic is
      poorly covered by the mostly-English BPE vocabularies in use, so words
      shatter into many sub-tokens; 2.0 is the pessimistic (over-estimating)
      end of the usual 2-3 range, chosen because over-estimating spends less
      budget than allowed while under-estimating overflows the model's
      window -- and only one of those two failures is recoverable.
    * Everything else (Latin text, digits, whitespace, punctuation) costs the
      familiar ~4 characters per token.

    Rounded UP, so any non-empty string costs at least one token.
    """
    arabic_chars = len(_ARABIC_CHAR_RE.findall(text))
    other_chars = len(text) - arabic_chars
    estimate = arabic_chars / _ARABIC_CHARS_PER_TOKEN + other_chars / _OTHER_CHARS_PER_TOKEN
    return math.ceil(estimate)


def fit_to_context_budget[T](
    candidates: Sequence[tuple[T, str]],
    *,
    max_chars: int,
    max_tokens: int,
) -> list[T]:
    """Keep the longest best-first PREFIX of `candidates` that fits both
    ceilings, and return those candidates' items in their original order
    (retrieval plan §3.7, `P-35`).

    `candidates` pairs each item with the exact rendered string that will be
    shown to the model (source label included -- see the module docstring's
    "measured on the RENDERED text"): the second element of the pair is what
    gets measured, the first is what gets returned, so this function stays
    ignorant of the item type entirely.

    Accumulates both a character count and an `estimate_tokens` count as it
    walks the list, and stops at the FIRST candidate that would push either
    total past its ceiling -- so the effective budget is always the SMALLER of
    the two (§3.7: "و يُؤخَذ الأصغر"). It stops rather than skipping ahead: the
    result is a prefix of a descending-score ranking, never a cherry-picked
    subset that quietly promotes a small low-scoring chunk over a large
    high-scoring one.

    The FIRST candidate always survives, even when it alone breaches both
    ceilings (and even when a ceiling is zero or negative) -- see the module
    docstring: a budget that returned nothing would be indistinguishable from
    "retrieval found nothing", which is the trust gate's fallback signal.
    `[]` in gives `[]` back, which is the one honest way to get an empty
    result out of this function.

    The token total is a sum of per-candidate `estimate_tokens` calls, each
    rounded up, so it over-counts by less than one token per candidate against
    estimating the concatenation in one go -- conservative in the same
    direction as the estimator itself, and cheap to reason about.
    """
    kept: list[T] = []
    used_chars = 0
    used_tokens = 0
    for item, rendered in candidates:
        chars = used_chars + len(rendered)
        tokens = used_tokens + estimate_tokens(rendered)
        if kept and (chars > max_chars or tokens > max_tokens):
            break
        kept.append(item)
        used_chars = chars
        used_tokens = tokens
    return kept
