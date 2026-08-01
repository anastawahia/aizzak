"""Layer-1 (regex-only) query-intent classification — pure algorithm (docs/
migration/refs/retrieval.md §4.7; 06-domain-models §7; 3.k3).

Ports only alpha's ALWAYS-ON layer 1 of ``intent_router.classify_intent``:
Arabic+English regex, priority METADATA → SUMMARIZE_DOC → CONTENT, with the
"topical-condition guard" that demotes a METADATA match back down to CONTENT
when the query also carries a topical connector (عن/حول/يتحدث/about/
regarding/mentions...) — e.g. «كم ملف يتحدث عن الرواتب؟» ("how many files
talk about salaries?") is a content question wearing a METADATA-shaped
prefix, not a genuine file-count query (retrieval.md §4.7).

Layer 2 (alpha's optional LLM-assisted disambiguation, ``classify_intent_llm``)
is deliberately NOT ported here: it read the process-global ``Settings.llm``
instead of the per-conversation client — a documented alpha bug, not a
decision worth repeating (retrieval.md §4.7, §7 risk #5: "لا يُعاد إنتاجه في
AIZZAK"). A future LLM-assisted layer, if ever added, belongs in
``rag_agent`` against its own injected ``LLMProvider``, never here.

**Dormant in v1:** this module is built and unit-tested as a ready building
block for the Phase-4 ``rag_agent`` (mirrors how ``fusion.py`` shipped one
step ahead of ``application/retrieval.py`` back in 3.k2) — v1
``application/retrieval.py`` does not import it; v1 retrieval is
CONTENT-only (retrieval.md §6.4 option (b)).

The anchor phrases below are the illustrative examples enumerated in
retrieval.md §4.7 (alpha's own literal regex source is not part of this
migration's reference material), generalized into concrete, testable
patterns. Unlike ``fusion.py``'s RRF formula or ``tokenization.py``'s
stop-word lists — both given verbatim in their reference sections — this is a
reconstruction from the *documented behaviour*, not a byte-for-byte port.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum


class Intent(StrEnum):
    """A query's routing intent (retrieval.md §4.7 ``intent_router.Intent``)."""

    CONTENT = "content"
    SUMMARIZE_DOC = "summarize_doc"
    METADATA = "metadata"


# METADATA anchors: corpus-level questions ("how many files...", "list the
# files...") that need no similarity search at all (retrieval.md §4.7).
_METADATA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"كم\s+عدد",
        r"كم\s+ملف",
        r"عدد\s+الملفات",
        r"اعرض\s+الملفات",
        r"how\s+many\s+(?:files|documents|docs)",
        r"list\s+(?:the\s+)?(?:files|documents|docs)",
    )
)

# The topical-condition guard: any of these connectors turns a METADATA-
# shaped match back into an ordinary CONTENT question about what is IN the
# files, not about the files themselves (retrieval.md §4.7).
_TOPICAL_GUARD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"عن\b",
        r"حول\b",
        r"يتحدث",
        r"about\b",
        r"regarding\b",
        r"mentions?\b",
    )
)

# SUMMARIZE_DOC anchors: explicit summarization verbs, or a phrasing where a
# named file is the grammatical OBJECT of the question ("what IS file X"),
# which alpha distinguishes from a query that merely mentions a file in
# passing (retrieval.md §4.7).
_SUMMARIZE_DOC_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"لخص",
        r"تلخيص",
        r"ملخص",
        r"summari[sz]e",
        r"summary",
        r"ما\s+(?:هو|هي)\s+ملف",
        r"what\s+is\s+(?:the\s+)?file",
    )
)


def _matches_any(patterns: Sequence[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify_intent(query: str) -> Intent:
    """Classify ``query`` with alpha's always-on layer-1 regex rules,
    priority METADATA → SUMMARIZE_DOC → CONTENT (retrieval.md §4.7).

    A METADATA match is demoted to CONTENT when a topical connector is also
    present (the "topical-condition guard"). Blank input, and any query
    matching none of the rules, falls through to the CONTENT default.
    """
    if not query or not query.strip():
        return Intent.CONTENT
    if _matches_any(_METADATA_PATTERNS, query):
        if _matches_any(_TOPICAL_GUARD_PATTERNS, query):
            return Intent.CONTENT
        return Intent.METADATA
    if _matches_any(_SUMMARIZE_DOC_PATTERNS, query):
        return Intent.SUMMARIZE_DOC
    return Intent.CONTENT
