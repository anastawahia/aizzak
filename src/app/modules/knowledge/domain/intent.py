"""Layer-1 (regex-only) query-intent classification — pure algorithm (docs/
migration/refs/retrieval.md §4.7; 06-domain-models §7; 3.k3).

Ports alpha's ALWAYS-ON layer 1 of ``intent_router.classify_intent``:
Arabic+English regex, priority SUMMARIZE_DOC → CONTENT.

**Two routes, not three (retrieval plan §3.4/§4 row 11, ``P-21``).**
``Intent.METADATA`` — alpha's corpus-level "how many files do you have?"
branch — is an EXCLUDED path (plan §7, a locked exclusion), and with it went
``_METADATA_PATTERNS`` and the "topical-condition guard"
(``_TOPICAL_GUARD_PATTERNS``). The guard existed ONLY to demote a
METADATA-shaped match back to CONTENT when the query also carried a topical
connector (عن/حول/يتحدث/about/regarding/mentions) — «كم ملف يتحدث عن
الرواتب؟» is a content question wearing a METADATA-shaped prefix — so with
METADATA gone it has nothing left to guard: those queries reach CONTENT by
falling through, which is the same answer by a shorter road.

What a corpus-level question gets instead is the corpus-awareness header
(plan §3.6, ``P-36``, س-23 = ج): «كم ملفًا لديك؟» classifies as CONTENT,
usually retrieves nothing, and is answered from the file-name header the RAG
agent puts on BOTH its paths. That is why §3.6 calls the header mandatory
rather than optional once METADATA is excluded.

The two surviving routes are the two the knowledge module already owns —
``RetrieveContext`` and ``RequestSummary`` — which is what let the routing
use-case live inside this module at all (``application/routing.py``, س-16 =
أ).

**The rules are deliberately asymmetric (plan §3.4/§4 row 12, ``P-22``, س-17
= ب).** Because there are only two routes, the classifier's whole accuracy
is SUMMARIZE_DOC's accuracy — CONTENT is a fall-through that cannot itself
be wrong — so every SUMMARIZE_DOC false positive breaks a legitimate content
question (plan §6 risk 4). The calibration therefore splits the anchors in
two: an imperative («لخّص», ``summarize``) is a request and classifies with
no further condition, while a bare noun («ملخّص», ``summary``) is only a
description until a document noun appears as its OBJECT.

Layer 2 (alpha's optional LLM-assisted disambiguation, ``classify_intent_llm``)
is deliberately NOT ported here: it read the process-global ``Settings.llm``
instead of the per-conversation client — a documented alpha bug, not a
decision worth repeating (retrieval.md §4.7, §7 risk #5: "لا يُعاد إنتاجه في
AIZZAK"; retrieval plan §7, same exclusion).

The anchor phrases below are the illustrative examples enumerated in
retrieval.md §4.7 (alpha's own literal regex source is not part of this
migration's reference material), generalized into concrete, testable
patterns. Unlike ``fusion.py``'s RRF formula or ``tokenization.py``'s
stop-word lists — both given verbatim in their reference sections — this is a
reconstruction from the *documented behaviour*, not a byte-for-byte port.
**A second question about the same sentence (``F-8``, plan §3.9).**
``asks_for_full_summary`` reads the DEPTH a question asked for, and it lives
here for the reason everything else here does: it is a decision about the
user's words, needing the same normalization and the same substring
discipline as the rules above, and a second Arabic-matching site in the
application layer would put one doctrine in two places.

It is deliberately not part of ``classify_intent``. The route and the depth
are independent readings of one sentence — the router asks for the second
only after the first said SUMMARIZE_DOC — and folding them into one enum
would have to invent a third member for a question that is not a third kind
of question. Nor is it from alpha: alpha has no routed ``FULL`` at all, so
unlike the rules above this is a decision, not a reconstruction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from app.modules.knowledge.domain.tokenization import normalize_arabic


class Intent(StrEnum):
    """A query's routing intent (retrieval.md §4.7 ``intent_router.Intent``),
    reduced to the two routes that exist in this module (retrieval plan §3.4,
    ``P-21``)."""

    CONTENT = "content"
    SUMMARIZE_DOC = "summarize_doc"


# =============================================================================
# SUMMARIZE_DOC calibration (retrieval plan §3.4 / §4 row 12, ``P-22``,
# س-17 = ب).
#
# Document nouns, alpha's ``_DOC_NOUN``, widened to its full stem list. The
# OBJECTHOOD condition rides along with the widening and is what keeps the
# widening safe: a document noun merely PRESENT in the query proves nothing
# («ما هو الحدّ الأقصى في ملف السياسات؟» is a content question about a limit,
# not a request to summarize the policy file) — it has to be the thing the
# question is *about*.
# =============================================================================
_DOC_NOUN = r"(?:ال)?(?:ملف|مستند|كتاب|كتب|مرجع|مراجع)"
_DOC_NOUN_EN = r"(?:documents?|docs?|files?|books?|references?)\b"
_DET_EN = r"(?:(?:the|this|that|a|an|my|our|your)\s+)?"
_DEM_AR = r"(?:(?:هذا|هذه|ذلك|تلك)\s+)?"

# Rule 1 — a REQUEST classifies unconditionally. «لخّص» / ``summarize`` needs
# no object at all: «لخّص لي هذا» is unambiguously a summarization request
# even though it names nothing (this is precisely the case option أ of س-17
# would have lost). The negative lookbehind on «م» is the whole subtlety of
# the Arabic side: substring matching means the *noun* «ملخّص» contains the
# *verb* «لخص», so the verb rule has to refuse the one prefix that turns the
# request into a description — and hand that string to rule 2 instead.
_IMPERATIVE_PATTERNS: tuple[str, ...] = (
    r"(?<!م)لخص",
    r"summari[sz]e",
    r"(?:اريد|اعطني|اكتب|اعمل|جهز|قدم|هات|ارسل|زودني|محتاج)\s+(?:لي\s+)?(?:ال)?(?:ملخص|تلخيص)",
    r"(?:give|provide|write|generate|produce|create|make|prepare|draft|need|want)"
    r"\s+(?:me\s+)?(?:a|an|the)?\s*summary",
)

# Rule 2 — a NOUN classifies only with a document noun as its object. Bare
# «ملخّص» / ``summary`` is not a request: «هل يوجد ملخّص تنفيذي في التقرير؟»
# asks about the *contents* of a report and must reach CONTENT, and under the
# old bare patterns it did not. The same objecthood test governs alpha's
# descriptive frame («ما هو ملف السياسات؟» summarizes, «ما هو الحدّ الأقصى في
# ملف السياسات؟» does not) — restricted to «ما هو», because the widened
# ``_DOC_NOUN`` would otherwise let the plural «ما هي المراجع المستخدمة؟»
# through as a summarization request.
_OBJECTHOOD_PATTERNS: tuple[str, ...] = (
    rf"(?:ملخص|تلخيص)\w*\s+{_DEM_AR}{_DOC_NOUN}",
    rf"summary\s+of\s+{_DET_EN}{_DOC_NOUN_EN}",
    rf"ما\s+هو\s+{_DOC_NOUN}",
    rf"what\s+is\s+{_DET_EN}{_DOC_NOUN_EN}",
)

# Arabic patterns are matched as SUBSTRINGS, never with word boundaries: the
# language is derivational and «لخص» lives inside «يلخّص / سألخّص», «ملخص»
# inside «ملخصًا / ملخصات» (retrieval.md §4.7; plan §3.4 — "هذا صحيح ومقصود
# ولا يُستبدَل بحدود كلمات"). English carries `\b` only where a short stem
# («doc») would otherwise fire inside an unrelated word («docker»).
_SUMMARIZE_DOC_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _IMPERATIVE_PATTERNS + _OBJECTHOOD_PATTERNS
)


# =============================================================================
# Depth (``F-8``, plan §3.9): did the question ask for the EXPENSIVE summary?
#
# ``OVERVIEW`` reads a document's opening chunks and ``FULL`` is a map-reduce
# over all of them, so this predicate is the one thing between a routed
# question and a corpus-sized bill. Its calibration therefore runs OPPOSITE
# to ``classify_intent``'s: there recall is what matters, because CONTENT is
# a fall-through that cannot be wrong on its own; here a MISS costs one
# bounded summary the user can ask for again — or get in full from `POST
# /documents/{id}/summary`, where a human names the depth — while a FALSE
# FIRE spends the map-reduce. Precision first, and the list stays short.
#
# Only ADVERBIAL and ADJECTIVAL depth words qualify. «مفصل» and ``detailed``
# describe the summary; «تفاصيل» and ``details`` are a NOUN naming what to
# summarize, and «لخص تفاصيل البند الثالث» asks for a summary OF one section
# — answering it with a map-reduce over the whole document is expensive AND
# not what was asked. The same objecthood instinct as rule 2 above, pointed
# the other way.
#
# «كامل» carries the one affix trap on this side, and it is `(?<!م)لخص`'s
# again: «التكامل» · «متكامل» · «تكاملية» all contain «كامل» behind a «ت»,
# and «لخص لي ملف التكامل» is an ordinary request to summarize the
# integration file. English needs a LEFT word boundary for the mirror-image
# reason — ``carefully`` · ``thoughtfully`` · ``successfully`` all END in
# ``full``.
# =============================================================================
_FULL_SUMMARY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?<!ت)كامل",
        r"مفصل",
        r"تفصيل",
        r"شامل",
        r"\b(?:full|fully|detailed|comprehensive|complete)\b",
        r"\bin\s+detail\b",
    )
)


def _matches_any(patterns: Sequence[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify_intent(query: str) -> Intent:
    """Classify ``query`` with alpha's always-on layer-1 regex rules,
    priority SUMMARIZE_DOC → CONTENT (retrieval.md §4.7; retrieval plan §3.4).

    The query is run through ``normalize_arabic`` first — alpha's layer 1
    normalizes before it matches, and here it is load-bearing rather than
    cosmetic: «لخّص» carries a shadda, and an unnormalized substring match
    would miss the single most common spelling of the imperative. The
    patterns above are therefore written in NORMALIZED form (bare alef, no
    tashkeel), which is why they read «اريد» and not «أريد».

    Blank input, and any query matching none of the rules, falls through to
    the CONTENT default — the honest default, since CONTENT is the route that
    can answer anything the corpus holds.
    """
    if not query or not query.strip():
        return Intent.CONTENT
    if _matches_any(_SUMMARIZE_DOC_PATTERNS, normalize_arabic(query)):
        return Intent.SUMMARIZE_DOC
    return Intent.CONTENT


def asks_for_full_summary(query: str) -> bool:
    """Whether ``query`` explicitly asked for the DEEP summary (``F-8``, plan
    §3.9) — «لخّص هذا الملفّ كاملاً», ``summarize it in full``.

    Asked only of a question already classified SUMMARIZE_DOC, and answered
    from the phrasing alone. The DEFAULT is not here: it belongs to the
    router, which is where the cost argument for it is written down, so this
    says what was asked and nothing about what happens when nothing was.

    ``False`` therefore means "the question said nothing about depth", never
    "a brief summary was asked for". The two coincide today only because
    ``OVERVIEW`` is already the default — which is also why no phrase asking
    for brevity is matched: detecting one would change nothing.

    Normalized first, for the reason ``classify_intent`` is: «مفصّل» carries
    a shadda and «كاملاً» a tanween, and both are how the words are actually
    typed.

    **Two limits it cannot see, both bounded, both recorded in the plan's
    §7.** A file NAMED «التقرير الكامل» reads as a depth word here, and so
    would the given name «كامل»; each buys a map-reduce on a document the
    user really did name — the wrong PRICE for a right answer, not a wrong
    answer. And a phrase this reads as depth can stop the sentence reaching
    the route at all: rule 2 above wants the document noun ADJACENT to
    «ملخص», which is exactly the slot an adjective takes, so «ملخص كامل
    للمستند» classifies CONTENT. Widening that is row 12's calibration to
    move, not this predicate's.
    """
    return _matches_any(_FULL_SUMMARY_PATTERNS, normalize_arabic(query))
