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

**How SUMMARIZE_DOC finds its document (plan §4 rows 13/14, ``P-04``).** Two
sources, tried in that order and never blended:

1. the CALLER's pin, when it names exactly one document — an identification
   a human already made, which no algorithm can improve on;
2. otherwise ``domain/file_resolution.resolve_file`` over the workspace's
   own file names, which answers with one of three things.

Only ``ResolvedFile`` queues a build. ``AmbiguousFiles`` comes back as
``RoutedAnswer.clarification_options`` — the names to ask the user about, in
ordinary answer text (س-18 = أ) — and ``NoFileMatch`` falls through to
CONTENT retrieval with ``intent`` still reported honestly as SUMMARIZE_DOC.
There is no fourth branch that picks a best candidate: "summarising the
wrong file with confidence" is the worst failure available on this path
(plan §3.5), which is exactly why ``AmbiguousFiles`` exposes no ``.best``
for this module to reach for.

**Why acting on ``ResolvedFile`` belongs to row 14 and not to a later step.**
Row 14 owns the tie; but a clarification question is only worth asking if
ANSWERING it works. A user told «أيّ ملفّ تقصد؟» replies with the file's
name, that reply resolves EXACT next turn, and a router that still ignored
it would have asked a question it had no way of hearing. The two halves are
one behaviour.

**A file named in a CONTENT question (plan §4 row 15, ``P-25``) — STRICT.**
The same resolver, on the other route, doing one thing: when the question
names one of this workspace's files CONFIDENTLY, that document becomes the
retrieval scope (``_content_scope``), and the scope is handed to
``RetrieveContext`` as ``document_ids`` — the parameter BE-RAG-005's pin
already uses, which reaches Qdrant as a ``document_id`` condition inside the
same ``must`` as ``workspace_id`` (plan fact ح-13: ``_build_filter`` builds
``must`` + ``MatchValue``/``MatchAny``). So the narrowing happens INSIDE the
search engine, on the payload key the pin path has always filtered on — not
by fetching wide and discarding afterwards, which would make the scope a
recall cost and let a fetch limit silently starve it.

**Strict means the search is never widened again.** A named file that
returns nothing returns nothing: there is no retry without the scope, no
"try the rest of the corpus" branch, and no path that reaches
``RetrieveContext`` twice. Zero chunks flow into the honest-fallback gate
(plan §3.3/§4 row 5, ``P-33``) that already answers «لا أملك معلومات كافية»
with the corpus-awareness header (§3.6) — the one fallback, reused, never a
second one. Answering that question from a DIFFERENT file would be the
same failure row 13 refuses at resolution time, only later and with
citations that look authoritative.

**What an AMBIGUOUS reference does here, and why it differs from row 14.**
Nothing: the question is retrieved UNSCOPED, exactly as ``NoFileMatch`` is.
Only a confident single ``ResolvedFile`` narrows a content search.

*Not* because ambiguity matters less on this route, but because the two
routes pay opposite prices for the same uncertainty. On SUMMARIZE_DOC there
is no answer to give without a document — the route's entire input is one
file — so asking is strictly better than not answering. On CONTENT there IS
an answer: retrieval ranks the whole corpus and every chunk it returns is
labelled with the file it came from (§3.2/§3.3, rows 2 and 3), so the user
sees which file answered and can name it exactly next turn. Turning that
into a question would trade a cited answer for a round trip — and it would
do so OFTEN: ``_decide`` returns ``AmbiguousFiles`` for a LONE candidate
that merely clears ``_LOW``, which is what a content question's topical
words score against a file whose name shares one of them. The two
alternatives were rejected for the same reason strictness exists: scoping
to the top candidate is the guess §3.5 forbids, and scoping to ALL the tied
candidates hides the rest of the corpus on evidence too weak to justify it —
it could answer «لا يوجد» for a question the corpus answers elsewhere,
which is a worse answer than today's. Recorded in the plan's §7 as a
calibration decision that can be revisited with the evaluation set ``P-38``
waits on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.domain.entities import SummaryJob
from app.modules.knowledge.domain.file_resolution import (
    AmbiguousFiles,
    FileCandidate,
    ResolvedFile,
    resolve_file,
)
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


class FileCandidates(Protocol):
    """The corpus ``resolve_file`` matches against: every document in this
    workspace paired with the name of the file it was built from
    (``application/use_cases.py::ListFileCandidates``).

    A narrow Protocol for ``SummaryStarting``'s two reasons — the
    implementation lives in the module that imports this one, and a router
    should depend on "something that can name this workspace's files", not on
    the repository walk and the per-file lookup that produce them.

    **Every candidate, never a page of them.** The resolver's entire value is
    its refusal to answer from a partial view: matching a name against the
    newest N documents can return a confident ``ResolvedFile`` while the file
    the user actually meant sits at N+1, unseen. A cap here would be that
    failure, wearing the costume of a performance guard.
    """

    async def execute(self, ctx: ExecutionContext) -> Sequence[FileCandidate]: ...


class RouteQuestion:
    """Classify ``question`` with the pure domain classifier, then dispatch:
    SUMMARIZE_DOC to ``RequestSummary``, CONTENT to ``RetrieveContext``
    (plan §3.4).

    Stateless and injected, like every use-case here: one instance serves
    every workspace, and the tenant is the ``ExecutionContext`` each call
    carries.
    """

    def __init__(
        self,
        retrieval: RetrieveContext,
        summaries: SummaryStarting,
        files: FileCandidates,
    ) -> None:
        self._retrieval = retrieval
        self._summaries = summaries
        self._files = files

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        question: str,
        model: str,
        api_key: str,
        k: int | None = None,
        document_ids: Sequence[Uuid] | None = None,
        space_id: Uuid | None,
    ) -> RoutedAnswer:
        """Route one question. The arguments are ``RetrieveContext.execute``'s,
        because the CONTENT route is that use-case unchanged — this adds a
        decision in front of it, not a second retrieval path.

        ``k = None`` is that use-case's own "use the configured default"
        (retrieval plan §4 row 18, ``P-40``, س-24) and is passed straight
        through: resolving it here would put the deployment's `k` in two
        places, and this class owns no tuning of its own.

        ``document_ids`` does double duty: it is the retrieval scope on the
        CONTENT route, and the FIRST source of a summarisation target on the
        other — a scope of exactly one document is an unambiguous target. Any
        other shape sends the question to ``_summarisation_route``, which
        reads the target out of the question's own words (plan rows 13/14).

        On the CONTENT route that scope can be NARROWED further by the
        question's own words (row 15, ``P-25`` — ``_content_scope``), never
        widened: what reaches ``RetrieveContext`` is either the caller's pin
        or one document from inside it.

        Errors from the summary route are NOT translated: an already-running
        build for the same key is a ``ConflictError`` and reaches the caller
        as one, exactly as it does on the REST route. Turning it into a
        friendly sentence is a rendering decision that belongs to whoever is
        rendering (recorded in the plan's §7).
        """
        intent = classify_intent(question)
        scope = document_ids
        if intent is Intent.SUMMARIZE_DOC:
            summarisation = await self._summarisation_route(ctx, question, document_ids)
            if summarisation is not None:
                return summarisation
            # Falling through means `_summarisation_route` got `NoFileMatch`
            # from the very same resolver over the very same candidates, so
            # `_content_scope` could only reach the same verdict — at the
            # price of a second corpus walk. The pin is the scope, unchanged.
        else:
            scope = await self._content_scope(ctx, question, document_ids)
        result = await self._retrieval.execute(
            ctx,
            query=question,
            model=model,
            api_key=api_key,
            k=k,
            document_ids=scope,
            space_id=space_id,
        )
        return RoutedAnswer(
            intent=intent,
            chunks=tuple(result.chunks),
            summary_job_id=None,
            clarification_options=(),
        )

    async def _summarisation_route(
        self,
        ctx: ExecutionContext,
        question: str,
        document_ids: Sequence[Uuid] | None,
    ) -> RoutedAnswer | None:
        """The SUMMARIZE_DOC route, or ``None`` when it has nothing to act on
        and the question should fall through to CONTENT retrieval.

        Three outcomes, and the missing fourth is the point (plan §3.5):
        a queued build when the target is identified, a set of names to ask
        the user about when it is not, ``None`` when the question names
        nothing in this corpus at all — and never a best guess.
        """
        target = _sole_document(document_ids)
        if target is None:
            resolution = resolve_file(question, await self._candidates(ctx, document_ids))
            if isinstance(resolution, AmbiguousFiles):
                # Plan §4 row 14 / س-18 = أ. The names cross as data and the
                # caller renders the question — no new event type, no change
                # to the streaming contract (§7).
                return RoutedAnswer(
                    intent=Intent.SUMMARIZE_DOC,
                    chunks=(),
                    summary_job_id=None,
                    clarification_options=tuple(
                        candidate.file_name for candidate in resolution.candidates
                    ),
                )
            if not isinstance(resolution, ResolvedFile):
                return None
            target = resolution.document_id
        job = await self._summaries.start(
            ctx,
            document_id=target,
            kind=_ROUTED_SUMMARY_KIND,
            lang=_ROUTED_SUMMARY_LANG,
        )
        return RoutedAnswer(
            intent=Intent.SUMMARIZE_DOC,
            chunks=(),
            summary_job_id=job.id,
            clarification_options=(),
        )

    async def _content_scope(
        self,
        ctx: ExecutionContext,
        question: str,
        document_ids: Sequence[Uuid] | None,
    ) -> Sequence[Uuid] | None:
        """The scope a CONTENT question is retrieved under (plan §4 row 15,
        ``P-25``): the caller's pin, narrowed to ONE document when the
        question names one of this workspace's files confidently.

        ``resolve_file`` is row 13's resolver — the same cascade, the same
        candidates, the same refusal to guess — not a second name matcher
        written for this route. Only ``ResolvedFile`` narrows anything:
        ``AmbiguousFiles`` and ``NoFileMatch`` both return the pin untouched,
        for the reasons in this module's docstring.

        The returned scope is always a SUBSET of what came in, so this can
        only ever narrow. A pin already restricted the candidate list
        (``_candidates``), so the resolved document is inside it by
        construction — a question cannot name its way past a scope the
        conversation set, on this route any more than on the other one.

        **The short-circuit is not an optimisation of the answer, only of
        the walk.** A pin of at most one document is already as narrow as a
        name could make it: resolution over that single candidate can return
        it or fall through to it, and both end at the same filter. What it
        would cost is a full ``ListFileCandidates`` pass (one name lookup on
        the files seam per document — its own §7 entry) on every pinned
        content question, to re-derive a scope the caller already stated.
        """
        if document_ids is not None and len(document_ids) <= 1:
            return document_ids
        resolution = resolve_file(question, await self._candidates(ctx, document_ids))
        if isinstance(resolution, ResolvedFile):
            return [resolution.document_id]
        return document_ids

    async def _candidates(
        self, ctx: ExecutionContext, document_ids: Sequence[Uuid] | None
    ) -> Sequence[FileCandidate]:
        """The files this question is allowed to be about: the workspace's
        corpus, narrowed to the caller's pin when there is one. Shared by
        both routes' resolutions — the summarisation target (row 14) and the
        CONTENT scope (row 15) match against the same candidate list.

        A pin is a statement about which documents this conversation is
        working with, so resolving OUTSIDE it could summarise (or answer
        from) a file the caller had deliberately excluded — the pin's whole
        purpose, undone by the mechanism meant to honour the question.
        ``None`` (unscoped) means the whole corpus; a pin that resolved to
        nothing narrows to nothing and the resolver honestly finds no match,
        which is the same answer retrieval gives that scope.

        The semantic layer of the cascade is NOT run: it needs an embedding
        per candidate label, and embedding every file name on every question
        is a cost decision (and a caching design) this step does not own —
        recorded in the plan's §7. Without a ``query_vector`` the cascade
        ends after FUZZY, exactly as alpha's ``embed_model=None`` did. On the
        CONTENT route its absence keeps the same safe direction row 14
        recorded: a file the question DESCRIBES without naming is not
        resolved, so the search stays unscoped rather than being narrowed to
        a guess.
        """
        candidates = await self._files.execute(ctx)
        if document_ids is None:
            return candidates
        pinned = set(document_ids)
        return [candidate for candidate in candidates if candidate.document_id in pinned]


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
