"""``RouteQuestion`` — classify a question, then send it down the route it
belongs to (retrieval plan §3.4/§4 row 11, ``P-21``, decision س-16 = أ).

``domain/intent.py`` has been built and unit-tested since 3.k3 without a
single live caller (plan fact ح-18, ``grep`` on ``classify_intent``), so this
module is a WIRING job, not a new algorithm: the classifier stays the pure
domain function it already was, and everything here is the application-layer
dispatch it never had.

**Why the routing lives in this module rather than in the agent.** After
``Intent.METADATA`` was excluded (plan §7) the two surviving routes —
``RetrieveContext`` and ``RequestSummary`` — are BOTH inside ``knowledge``.
Routing between two of a module's own use-cases is that module's business:
the agent stays thin and keeps its declared convention of importing no module
at all (ح-11, §6 risk 7), and every other consumer of the inbound port gets
the routing too instead of it being re-implemented per caller.

**What SUMMARIZE_DOC still cannot do here, and why that is deliberate.**
Naming the document to summarise is plan step 13's job (``P-04`` — the exact
→ fuzzy → semantic filename resolver), and step 14's when the resolution
ties. Until those land, this use-case will only summarise a document the
CALLER has already identified unambiguously — a scope pinned to exactly one
document — and any other summarisation question falls through to CONTENT
retrieval with its ``intent`` reported honestly, so step 14 can find it. It
never guesses a document: "summarising the wrong file with confidence" is
the worst failure available on this path (plan §3.5, س-18 = د rejected).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.domain.entities import SummaryJob
from app.modules.knowledge.domain.intent import Intent, classify_intent
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.inbound import RoutedAnswer

# What a routed summarisation asks for. Both are FIXED here rather than
# guessed from the question, and neither is a `Settings` knob (س-24 keeps
# runtime tuning in `Settings`, but these are contract choices, not tuning):
#
# `OVERVIEW`, not `FULL`, because this path can be entered by a REGEX false
# positive (plan §6 risk 4 — every false SUMMARIZE_DOC damages a legitimate
# content question). `OVERVIEW` is bounded to the document's opening chunks,
# `FULL` is a map-reduce over all of them, so a misfire on a 500-page
# document costs one bounded call instead of a corpus-sized bill. A caller
# that genuinely wants the map-reduce has `POST /documents/{id}/summary`,
# where a human named both the document and the depth.
#
# `AUTO`, because it is a real member and not a missing value (see
# `SummaryLanguage`): "answer in whatever language the document is written
# in" is the honest instruction when nobody stated a language — the question
# is not the document, and a question asked in English about an Arabic report
# is not a request for a translation.
_ROUTED_SUMMARY_KIND = SummaryKind.OVERVIEW
_ROUTED_SUMMARY_LANG = SummaryLanguage.AUTO


class SummaryStarting(Protocol):
    """The one summarisation capability this use-case needs: queue a build
    for one document and hand back the job.

    A narrow Protocol rather than a nominal import of ``RequestSummaryService``
    — which satisfies it structurally — for a plain mechanical reason and a
    design one. Mechanically, that class lives in ``application/use_cases.py``,
    which imports THIS module to build ``KnowledgeRetrievalService.answer``; a
    nominal import back would be a cycle. By design, routing depends on
    "something that can start a summary", and the atomic outbox+unit-of-work
    wrapping that ``RequestSummaryService`` adds around ``RequestSummary`` is
    exactly the kind of detail a router should not be able to see, let alone
    skip.
    """

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob: ...


class RouteQuestion:
    """Classify ``question`` with the pure domain classifier, then dispatch:
    SUMMARIZE_DOC to ``RequestSummary``, CONTENT to ``RetrieveContext``
    (plan §3.4).

    Stateless and injected, like every use-case here: one instance serves
    every workspace, and the tenant is the ``ExecutionContext`` each call
    carries.
    """

    def __init__(self, retrieval: RetrieveContext, summaries: SummaryStarting) -> None:
        self._retrieval = retrieval
        self._summaries = summaries

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        question: str,
        model: str,
        api_key: str,
        k: int = 5,
        document_ids: Sequence[Uuid] | None = None,
        space_id: Uuid | None,
    ) -> RoutedAnswer:
        """Route one question. The arguments are ``RetrieveContext.execute``'s,
        because the CONTENT route is that use-case unchanged — this adds a
        decision in front of it, not a second retrieval path.

        ``document_ids`` does double duty: it is the retrieval scope on the
        CONTENT route, and the ONLY source of a summarisation target on the
        other. A scope of exactly one document is an unambiguous target; every
        other shape (unscoped, or several documents) is not, and falls through
        to retrieval rather than picking one — see the module docstring.

        Errors from the summary route are NOT translated: an already-running
        build for the same key is a ``ConflictError`` and reaches the caller
        as one, exactly as it does on the REST route. Turning it into a
        friendly sentence is a rendering decision that belongs to whoever is
        rendering (recorded in the plan's §7).
        """
        intent = classify_intent(question)
        if intent is Intent.SUMMARIZE_DOC:
            target = _sole_document(document_ids)
            if target is not None:
                job = await self._summaries.start(
                    ctx,
                    document_id=target,
                    kind=_ROUTED_SUMMARY_KIND,
                    lang=_ROUTED_SUMMARY_LANG,
                )
                return RoutedAnswer(intent=intent, chunks=(), summary_job_id=job.id)
        result = await self._retrieval.execute(
            ctx,
            query=question,
            model=model,
            api_key=api_key,
            k=k,
            document_ids=document_ids,
            space_id=space_id,
        )
        return RoutedAnswer(intent=intent, chunks=tuple(result.chunks), summary_job_id=None)


def _sole_document(document_ids: Sequence[Uuid] | None) -> Uuid | None:
    """The one document a scope identifies, or ``None`` when it identifies
    zero or several.

    ``None`` (unscoped) and ``[]`` (a scope that resolved to nothing) stay as
    different downstream as they are everywhere else in this module — they
    just happen to answer this particular question the same way, because
    neither NAMES a document.
    """
    if document_ids is None or len(document_ids) != 1:
        return None
    return document_ids[0]
