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

**How DEEP a routed summarisation reads (plan §3.9, ``F-8``).** ``OVERVIEW``
by default, ``FULL`` when the question says so — «لخّص هذا الملفّ كاملاً». The
default is a cost guard and the constants below carry its argument; what
matters HERE is that the depth is read off the QUESTION while the target is
not, so a pinned document and a resolved one get the same reading — the user
said the same thing in both. The reading itself is the domain's
(``intent.asks_for_full_summary``), which leaves this module the one decision
it actually owns: what to do when nothing was said.

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
Only a confident single ``ResolvedFile`` can narrow a content search — and
not every one of those does either (the paragraph after next).

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

**And a CONFIDENT reference is not always enough either — the name that
matched has to be worth narrowing on.** ``_is_exact`` (row 13) resolves when
a normalized file name appears ANYWHERE inside the normalized question,
which is the right test on the route it was written for: a summarisation
question is ABOUT a file, so the file's name is the only content-bearing
thing in it. A content question is about a SUBJECT, and there the same test
reads a subject word as a file reference. A corpus that happens to hold
«تقرير.pdf» turns «ما هو الحد الأقصى للإجازات حسب تقرير الأداء السنوي؟» into
``ResolvedFile(method=EXACT, score=1.0)`` — a full-confidence scope on a
document the question never meant — and the strictness above then guarantees
there is no second, wider search to recover from it. The user is told «لا
أملك معلومات كافية» about a question the corpus answers in another file, and
nothing in that answer names the file that swallowed it, so there is nothing
to correct on the next turn either. It is the failure §3.5 forbids — the
wrong file, with full confidence — in its silent form: no file is summarised
wrongly, the corpus is simply hidden. The shorter and commoner the name, the
likelier it is: «تقرير» · «سياسات» · «الميزانية» · ``report`` · ``notes``.

So this route asks for MORE before it NARROWS than row 13 asks for before it
RESOLVES: the match must be ``EXACT`` **and** the matched name must carry
more than one token (``_MIN_CONTENT_SCOPE_NAME_TOKENS``, measured with
``file_resolution.name_token_count`` — the resolver's own tokenizer, never a
second one). Anything else leaves the caller's pin exactly as it arrived,
which is the same "narrow nothing" the ambiguous case above lands on, for
the same reason. Requiring ``EXACT`` also retires the FUZZY narrowing this
route used to do: a 0.75 blended similarity is a fair guess at which file a
user MEANT when the whole question is a file reference, and a poor reason to
hide the rest of the corpus from a question that is not one.

**The bar is raised HERE, and the resolver is untouched.** ``_is_exact``
still behaves exactly as row 13 wrote it, because on the summarisation route
the substring test is what makes «لخّص تقرير» work at all — and one-token
names are precisely what a user types there. What differs is the price of
being wrong: a summarisation that picked the wrong file SHOWS it (the answer
is that file), while a content search narrowed to the wrong file shows
nothing at all.

**The number is provisional; the direction is not.** ``> 1 token`` shares
its input with everything else waiting on ``P-38``'s evaluation set (plan
§7), so it lives in ONE named constant below and is read nowhere else —
retuning it is a one-line edit, and no behaviour here depends on the raw
value. The direction needs no measurement: a wrong narrowing is worse than
no narrowing, which is what the tie case above already decided.

**Both resolutions happen INSIDE the space being searched.** ``space_id`` is
the second, independent narrowing axis (spaces plan step 8): it reaches
``RetrieveContext`` as the ``space`` condition on the vector search, and it
reaches ``_candidates`` as the space whose files a question is allowed to
NAME. The two have to be the same space, or this router can build a scope
that nothing in the corpus can satisfy — a question asked in space (أ) that
names a file living in space (ب) would resolve (a candidate walk that saw
every space finds it), and ``RetrieveContext`` would then be handed
``document_ids=[a document in (ب)]`` AND ``space=(أ)``, two conditions ANDed
inside the same ``must``. Zero chunks, about a file the workspace really
does hold, and nothing in the answer names what swallowed the question.
Resolving inside the space turns that into an ordinary ``NoFileMatch``
instead: the name belongs to no file HERE, so the question is retrieved
unscoped within its own space and the honest-fallback gate (§3.3) speaks
about a corpus the user can actually see.

**That day arrived with س-32** (owner decision 2026-08-26). The paragraph
above used to close by noting the failure was latent because every caller
said ``space_id=None`` and ``None`` agreed with itself on both axes. There is
no ``None`` on this path any more: ``space_id`` is a required, non-nullable
``Uuid`` from ``POST /knowledge/search`` and from ``AgentDependencies.space_id``
down through here, and ``require_space_scope`` refuses anything else before a
candidate is walked or an embedding computed. The two axes still have to
agree, and now they agree on a REAL space rather than on a shared absence.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.knowledge.application.retrieval import RetrieveContext, require_space_scope
from app.modules.knowledge.domain.entities import SummaryJob
from app.modules.knowledge.domain.file_resolution import (
    AmbiguousFiles,
    FileCandidate,
    ResolutionMethod,
    ResolvedFile,
    name_token_count,
    resolve_file,
)
from app.modules.knowledge.domain.intent import (
    Intent,
    asks_for_full_summary,
    classify_intent,
)
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.inbound import RoutedAnswer

# What a routed summarisation asks for. Neither is a `Settings` knob (س-24
# keeps runtime tuning in `Settings`, but these are contract choices, not
# tuning). The LANGUAGE is fixed outright; the KIND has exactly one, narrow
# way of being overridden.
#
# `OVERVIEW` is the DEFAULT and stays it (`F-8`, plan §3.9), because this
# path can be entered by a REGEX false positive (plan §6 risk 4 — every false
# SUMMARIZE_DOC damages a legitimate content question). `OVERVIEW` is bounded
# to the document's opening chunks, `FULL` is a map-reduce over all of them,
# so a misfire on a 500-page document costs one bounded call instead of a
# corpus-sized bill.
#
# `FULL` became reachable from chat when — and only when — the question SAYS
# so (`_routed_summary_kind`). That does not spend the guard above, it
# doubles it: a misroute and an explicit depth phrase are independent
# readings of the sentence, so the expensive call now needs BOTH to be wrong
# at once, and the second is one a user typed on purpose. What it ends is the
# silence — «لخّص هذا الملفّ كاملاً» used to be answered with eight chunks
# and nothing in the answer said that only the opening had been read. A
# caller who wants the map-reduce WITHOUT saying so still has `POST
# /documents/{id}/summary`, where a human named both the document and the
# depth.
#
# `AUTO`, because it is a real member and not a missing value (see
# `SummaryLanguage`): "answer in whatever language the document is written
# in" is the honest instruction when nobody stated a language — the question
# is not the document, and a question asked in English about an Arabic report
# is not a request for a translation. The DEPTH is read off the question and
# the LANGUAGE deliberately is not, which is the same distinction: depth is a
# two-valued property of the REQUEST, language is a property of the DOCUMENT,
# and only one of those the asker is the authority on.
_ROUTED_SUMMARY_DEFAULT_KIND = SummaryKind.OVERVIEW
_ROUTED_SUMMARY_EXPLICIT_KIND = SummaryKind.FULL
_ROUTED_SUMMARY_LANG = SummaryLanguage.AUTO

# How many tokens the matched file name must carry before an EXACT match is
# allowed to NARROW a content search (`_narrows_content_scope`; see the
# module docstring's last three paragraphs for the failure it prevents).
#
# THE one place this is tuned. It is a provisional calibration number, not a
# contract choice like the three above, and it is deliberately not a `Settings`
# knob: س-24 puts runtime tuning in `Settings`, but this has no reason to
# differ per deployment — it is one measurement, on the evaluation set `P-38`
# is waiting for, that should then hold for everybody. Should that
# measurement ever produce a per-deployment number, this constant is the
# single seam it has to be injected through.
_MIN_CONTENT_SCOPE_NAME_TOKENS = 2


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

    ``conversation_id`` (`F-7`) widens the Protocol by one defaulted keyword,
    and the router still holds no opinion about it: it hands over the thread
    it was called from so the build can say where its text is owed, and never
    reads it back. A default keeps every caller that has no thread — the REST
    route among them — calling this exactly as before.
    """

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
        conversation_id: Uuid | None = None,
    ) -> SummaryJob: ...


class FileCandidates(Protocol):
    """The corpus ``resolve_file`` matches against: every document in the
    space being searched, paired with the name of the file it was built from
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

    **``space_id`` is the one narrowing that IS honoured** (the module
    docstring's last two paragraphs), and since س-32 it is a required keyword
    with no default AND no ``None``: a question may only name files that live
    in the space it was asked in. "Every space" is not a value this signature
    can express any more, which is the point — it was expressible, and that is
    how a name from another space could ever have been resolved.
    """

    async def execute(
        self, ctx: ExecutionContext, *, space_id: Uuid
    ) -> Sequence[FileCandidate]: ...


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
        space_id: Uuid,
        conversation_id: Uuid | None = None,
    ) -> RoutedAnswer:
        """Route one question. The RETRIEVAL arguments are
        ``RetrieveContext.execute``'s, because the CONTENT route is that use-case
        unchanged — this adds a decision in front of it, not a second retrieval
        path. ``conversation_id`` is the one argument that use-case has never had,
        and the last paragraph says what it is for.

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

        ``space_id`` reaches BOTH halves of that sentence: the search it
        filters and the candidate list the name is resolved against (see the
        module docstring). It is passed on, never interpreted here — this
        class holds no opinion about which space a question belongs to — but
        it is CHECKED here before either half runs (س-32): the candidate walk
        happens before ``RetrieveContext.execute`` does, so leaving the guard
        to the search alone would let an unscoped call resolve a file name
        across every space first and be refused second.

        Errors from the summary route are NOT translated: an already-running
        build for the same key is a ``ConflictError`` and reaches the caller
        as one, exactly as it does on the REST route. Turning it into a
        friendly sentence is a rendering decision that belongs to whoever is
        rendering (recorded in the plan's §7).

        ``conversation_id`` reaches ONE of the two routes (`F-7`). It is the
        thread this question was asked in, and SUMMARIZE_DOC stamps it on the
        build so the finished summary can be posted back there — the whole
        point of that route being asynchronous. CONTENT never reads it: that
        answer is written by the caller, inside the turn, and a thread id
        would be a value it has no use for.
        """
        space_id = require_space_scope(space_id)
        intent = classify_intent(question)
        scope = document_ids
        if intent is Intent.SUMMARIZE_DOC:
            summarisation = await self._summarisation_route(
                ctx, question, document_ids, space_id=space_id, conversation_id=conversation_id
            )
            if summarisation is not None:
                return summarisation
            # Falling through means `_summarisation_route` got `NoFileMatch`
            # from the very same resolver over the very same candidates, so
            # `_content_scope` could only reach the same verdict — at the
            # price of a second corpus walk. The pin is the scope, unchanged.
        else:
            scope = await self._content_scope(ctx, question, document_ids, space_id=space_id)
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
            # ب-7أ — no build was queued, so there is no target to name.
            # A CONTENT question that NARROWED to one document
            # (`_content_scope`) still names nothing: that scope is what
            # the answer was retrieved under, and the citations already
            # say which file every sentence came from.
            summary_target_name=None,
        )

    async def _summarisation_route(
        self,
        ctx: ExecutionContext,
        question: str,
        document_ids: Sequence[Uuid] | None,
        *,
        space_id: Uuid,
        conversation_id: Uuid | None = None,
    ) -> RoutedAnswer | None:
        """The SUMMARIZE_DOC route, or ``None`` when it has nothing to act on
        and the question should fall through to CONTENT retrieval.

        Three outcomes, and the missing fourth is the point (plan §3.5):
        a queued build when the target is identified, a set of names to ask
        the user about when it is not, ``None`` when the question names
        nothing in this corpus at all — and never a best guess.

        The DEPTH comes from ``question`` on every one of those outcomes that
        queues anything (`F-8`): the pinned path and the resolved path share
        the one call below, so «كاملاً» means the same thing whether the
        caller named the document or the question did.
        """
        target = _sole_document(document_ids)
        target_name: str | None = None
        if target is not None:
            # ب-7ب (scenarios plan §4) — the pinned path BUYS the name,
            # with the corpus walk `_content_scope`'s short-circuit
            # exists to avoid.
            #
            # The short-circuit is right where it was written and
            # wrong here, and the difference is what the walk is
            # bought FOR. There it sits on EVERY pinned CONTENT
            # question, to re-derive a scope the caller already
            # stated — a walk that changes no answer. Here it sits on
            # a pinned SUMMARISATION request only, a turn that queues
            # a map-reduce over a whole document behind it: one name
            # lookup is noise beside that, and what it buys is س-21,
            # the worst shape of ف-2 — a thread pinned to one file,
            # a user asking about another, and the pinned one
            # summarised without a word. Named in the receipt, that
            # turn is self-correcting in the same breath.
            target_name = await self._pinned_name(ctx, target, space_id=space_id)
        else:
            resolution = resolve_file(
                question, await self._candidates(ctx, document_ids, space_id=space_id)
            )
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
                    # No build was queued, so there is nothing to name —
                    # and the names that matter this turn are already
                    # crossing as `clarification_options`.
                    summary_target_name=None,
                )
            if not isinstance(resolution, ResolvedFile):
                return None
            target = resolution.document_id
            # ب-7أ — free (ت-2): the resolver already carries the name
            # of the document it chose, so this is a read, not a
            # second lookup and not a derivation.
            target_name = resolution.file_name
        job = await self._summaries.start(
            ctx,
            document_id=target,
            # `F-8` — the default depth unless the question asked for the
            # whole document. Read from the QUESTION, never from the target:
            # what the user said does not change with how the file was found.
            kind=_routed_summary_kind(question),
            lang=_ROUTED_SUMMARY_LANG,
            # `F-7` — the thread that asked, so the build can post its text
            # back here rather than into a route nobody is watching. The
            # AMBIGUOUS branch above deliberately passes nothing: it queues no
            # build, and the question it returns is answered in this same turn.
            conversation_id=conversation_id,
        )
        return RoutedAnswer(
            intent=Intent.SUMMARIZE_DOC,
            chunks=(),
            summary_job_id=job.id,
            clarification_options=(),
            # ف-2 — the one outcome that HAS a target, so the one
            # that names it. `None` here means the module queued a
            # build it could not name (an unreadable file), never
            # that it did not look.
            summary_target_name=target_name,
        )

    async def _pinned_name(
        self, ctx: ExecutionContext, target: Uuid, *, space_id: Uuid
    ) -> str | None:
        """The file name of a PINNED summarisation target (ب-7ب), or
        ``None`` when this corpus cannot name it.

        Matched against ``_candidates`` rather than read through a
        one-document lookup, so the name a receipt utters comes from
        exactly the list a RESOLVED target's name comes from: the same
        walk, the same space narrowing, the same "a document whose file
        is no longer readable is not a candidate" rule
        (``ListFileCandidates``). A second reader would be a second
        answer to "what is this document called".

        ``None`` on a miss, and it is not an error: a pin names a
        document, and a document whose file was deleted or quarantined
        since it was indexed is still summarisable from the chunks
        already stored. The build is queued either way — the receipt
        simply falls back to its unnamed wording, which is what it
        said for every pinned request before this method existed.
        """
        for candidate in await self._candidates(ctx, [target], space_id=space_id):
            if candidate.document_id == target:
                return candidate.file_name
        return None

    async def _content_scope(
        self,
        ctx: ExecutionContext,
        question: str,
        document_ids: Sequence[Uuid] | None,
        *,
        space_id: Uuid,
    ) -> Sequence[Uuid] | None:
        """The scope a CONTENT question is retrieved under (plan §4 row 15,
        ``P-25``): the caller's pin, narrowed to ONE document when the
        question names one of the searched SPACE's files confidently.

        ``resolve_file`` is row 13's resolver — the same cascade, the same
        candidates, the same refusal to guess — not a second name matcher
        written for this route. But not every resolution narrows: this route
        applies ``_narrows_content_scope`` on top, so an EXACT match on a
        one-token name (and every FUZZY match) leaves the pin untouched,
        exactly as ``AmbiguousFiles`` and ``NoFileMatch`` do. The module
        docstring has the failure that bar exists to stop.

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
        resolution = resolve_file(
            question, await self._candidates(ctx, document_ids, space_id=space_id)
        )
        if isinstance(resolution, ResolvedFile) and _narrows_content_scope(resolution):
            return [resolution.document_id]
        return document_ids

    async def _candidates(
        self,
        ctx: ExecutionContext,
        document_ids: Sequence[Uuid] | None,
        *,
        space_id: Uuid,
    ) -> Sequence[FileCandidate]:
        """The files this question is allowed to be about: the corpus of the
        space being searched, narrowed further to the caller's pin when there
        is one. Shared by both routes' resolutions — the summarisation target
        (row 14) and the CONTENT scope (row 15) match against the same
        candidate list.

        **The space comes first, and it is the SAME space the answer will be
        retrieved from** (the module docstring's last two paragraphs). Two
        narrowings, applied in the order they were decided in: the space is
        where this conversation lives, the pin is what it is working with
        inside it. Since س-32 there is no third possibility: ``space_id`` is a
        real space or the call never got here (``require_space_scope``), so
        the corpus a name resolves against and the corpus the answer is drawn
        from are the same set by construction rather than by agreement.

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
        candidates = await self._files.execute(ctx, space_id=space_id)
        if document_ids is None:
            return candidates
        pinned = set(document_ids)
        return [candidate for candidate in candidates if candidate.document_id in pinned]


def _routed_summary_kind(question: str) -> SummaryKind:
    """How much of the document a routed summarisation should read: the
    default, unless ``question`` explicitly asked for the deep one (`F-8`,
    plan §3.9).

    Two layers, split where the argument for each of them lives. READING the
    phrase is the domain's (``asks_for_full_summary`` — the same normalizer
    and the same substring discipline as the classifier that sent the
    question here, in the one module that owns both); the DEFAULT is this
    module's, because the cost argument for it is about this route and no
    other. A domain function that returned a ``SummaryKind`` would have had
    to carry that default, and then the REST route's depth and the chat
    route's would be decided in the same place for opposite reasons.

    Pure and outside ``RouteQuestion`` for ``_narrows_content_scope``'s
    reason: what it answers is a property of the question, not of the
    router's state.
    """
    if asks_for_full_summary(question):
        return _ROUTED_SUMMARY_EXPLICIT_KIND
    return _ROUTED_SUMMARY_DEFAULT_KIND


def _narrows_content_scope(resolution: ResolvedFile) -> bool:
    """Whether ``resolution`` is a strong enough identification to hide the
    rest of the corpus from a CONTENT question (the module docstring's last
    three paragraphs).

    Two conditions, and the second is what the first cannot express: the
    layer that matched must be ``EXACT``, and the name it matched on must
    discriminate — a single token can be a subject word that merely happens
    to be somebody's file name («تقرير» inside «تقرير الأداء السنوي»), and
    the EXACT layer reports that with the same ``score=1.0`` it reports a
    whole name typed on purpose with.

    Pure, and deliberately outside ``RouteQuestion``: what it answers is a
    property of the resolution, not of the router's state, so it is decided
    the same way whatever else a call is doing. Nothing about the
    SUMMARIZE_DOC route consults it.
    """
    return (
        resolution.method is ResolutionMethod.EXACT
        and name_token_count(resolution.file_name) >= _MIN_CONTENT_SCOPE_NAME_TOKENS
    )


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
