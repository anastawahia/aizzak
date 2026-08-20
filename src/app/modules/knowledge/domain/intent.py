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
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum


class Intent(StrEnum):
    """A query's routing intent (retrieval.md §4.7 ``intent_router.Intent``),
    reduced to the two routes that exist in this module (retrieval plan §3.4,
    ``P-21``)."""

    CONTENT = "content"
    SUMMARIZE_DOC = "summarize_doc"


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
    priority SUMMARIZE_DOC → CONTENT (retrieval.md §4.7; retrieval plan §3.4).

    Blank input, and any query matching none of the rules, falls through to
    the CONTENT default — the honest default, since CONTENT is the route that
    can answer anything the corpus holds.
    """
    if not query or not query.strip():
        return Intent.CONTENT
    if _matches_any(_SUMMARIZE_DOC_PATTERNS, query):
        return Intent.SUMMARIZE_DOC
    return Intent.CONTENT
