"""Knowledge inbound port (02-port-contracts §2).

``KnowledgeRetrieval`` is injected into the agent layer so it can retrieve
context chunks without importing the knowledge module directly (ARC-07/08) —
the ``files.FilesQuery`` precedent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.knowledge.domain.intent import Intent
from app.modules.knowledge.domain.value_objects import SummaryBlocked
from app.modules.knowledge.ports.retrieval import RetrievedChunk


@dataclass(frozen=True, slots=True)
class DocumentNames:
    """This workspace's document file names, newest first — up to the
    caller's ``limit``, or the deployment's configured cap when the caller
    names none — plus ``total``, the workspace's FULL document count
    (retrieval plan §3.6/§4 row 6, ``P-36``, decision س-23 = ج).

    ``total`` rides along so a caller can render an honest "and N more
    files" tail without a second round trip: ``list_document_names`` already
    has to walk the whole corpus to count it (``ListDocumentNames``), so
    handing the number back is free once that walk has happened.

    ``names`` is capped by the PRODUCER, never by a caller slicing afterwards
    — the same "the module decides, the seam just reads" shape
    ``RetrievedChunk`` already has, and the reason a ``limit`` the caller
    omits can be resolved inside the module at all.
    """

    names: tuple[str, ...]
    total: int


@dataclass(frozen=True, slots=True)
class RoutedAnswer:
    """What ``answer`` gives back: the ``intent`` the question was classified
    as, plus whichever of the two routes' outcomes actually ran (retrieval
    plan §3.4/§4 row 11, ``P-21``, س-16 = أ).

    ``chunks`` carries the CONTENT route's retrieval — the same list
    ``retrieve`` returns — and ``summary_job_id`` the SUMMARIZE_DOC route's
    queued build. Exactly one of them is ever populated, but they are two
    fields rather than a union because a caller renders them differently and
    a union would make "which one is it?" an ``isinstance`` question at the
    seam instead of a read.

    ``intent`` is reported HONESTLY even when the route it names could not
    run: a summarisation question whose target document cannot be identified
    comes back ``SUMMARIZE_DOC`` with ``summary_job_id=None`` and CONTENT
    chunks in hand (see ``RouteQuestion``). That is what lets plan step 14
    (the clarification question, س-18 = أ) recognise the case at all — a
    result that lied and said CONTENT would have erased the evidence.

    ``clarification_options`` (retrieval plan §3.5/§4 row 14, ``P-04``, س-18
    = أ) is the THIRD outcome, and the only one that is a question rather
    than an answer: the file names of the documents the question could have
    meant, when ``resolve_file`` refused to choose between them. Non-empty
    means "ask the user which of these" — capped by the resolver at five,
    never re-capped here — and ``()`` means there is nothing to ask.

    **File NAMES, not candidate objects, and no rendered sentence either.**
    س-18 = أ made the clarification ORDINARY ANSWER TEXT on the existing
    stream, so the wording and its language are the answering caller's
    (``RagAgent`` already picks one voice per query for its fallback,
    receipt and corpus header); the module owes it the facts and no more. A
    structured ``clarification`` EVENT — which would carry ids and let a UI
    render buttons — is recorded in the plan's §7 as out of scope: it
    changes the streaming contract, which this field pointedly does not.

    ``summary_target_name`` (scenarios plan §4, ب-7أ, gap ف-2) is the
    file name of the document a queued build is ABOUT — the fifth
    field, and the one that makes ``summary_job_id`` legible to the
    person who asked. It is populated exactly when a build was queued,
    and it names the document THIS module resolved rather than
    whatever the question happened to say.

    **The rule it looks like it breaks, it does not.** "Never echo a
    name you did not resolve" is the RAG agent's rule
    (``_summary_queued_answer``) and it is right: an agent that repeats
    the user's phrasing back as a filename is asserting a resolution it
    never performed. That argument has no purchase HERE, because this
    module IS the resolver — ``resolve_file`` ran, chose one document
    and refused every alternative, and this field carries that choice's
    own ``ResolvedFile.file_name``. The agent still names nothing it
    was not told; it is simply told now.

    **Why it earns its place on the seam:** a FUZZY resolution at 0.78
    against a 0.75 threshold is a guess that looked confident, and
    until this field existed the user learned which file it picked
    only minutes later, when a summary of the wrong document arrived
    — or, on the pinned path (س-21), never. Named in the receipt, a
    mis-resolution is visible in the same breath as the acceptance.

    ``None`` is honest and stays reachable: a document whose file is
    no longer readable has no name to give (``ListFileCandidates``
    drops exactly those), and the caller falls back to its unnamed
    wording rather than to a blank. It is also ``None`` on every
    outcome that queued nothing — CONTENT, clarification, no match —
    because there is no build for a name to be about.

    **No default**, the ``Document.space_id`` rule applied to a field
    that is read out loud: a route that queues a build and forgets the
    name would inherit ``None`` silently and answer in the unnamed
    wording forever, which is the exact failure ف-2 is — and it would
    look like the honest "the module could not name it" case. Every
    construction site says which one it means.

    ``summary_blocked`` (scenarios plan section 5, ب-4ب, gap ف-7) is the
    FOURTH outcome of the summarisation route and the sixth field: the
    module resolved a target, asked for a build, and was refused — and
    this says WHICH refusal it was.

    **It exists because the caller cannot work it out.** Both refusals
    are ``ConflictError`` and both carry ``common.conflict``, so a caller
    catching the exception knows only that something conflicted. It then
    has one sentence to write for two opposite facts: a summary that IS
    coming, and a document that has no text and never will. The neutral
    wording that covers both (ب-4أ) was the honest answer to that, and
    this field is what retires it.

    **A REASON, never a sentence.** The module classifies because only
    the module can; it does not word, because س-18's rule stands — the
    caller owns the voice, its language and its phrasing. That is the
    same division ``clarification_options`` draws, one field over.

    ``summary_job_id`` and ``summary_blocked`` are never both set: one
    says a build was accepted, the other that it was refused. But
    ``summary_target_name`` survives a refusal and is meant to — the
    refusal is ABOUT a document, and naming it is what turns «تعذّر
    البدء» into a sentence a user can act on.

    **No default**, for ``summary_target_name``'s reason exactly: a
    refusal that forgot to report itself would inherit ``None`` and be
    indistinguishable from an ordinary answer, which is ف-7 restored.

    ``stored_summary_text`` (scenarios plan section 6, ب-8, gap ف-3) is the
    FIFTH outcome and the seventh field: the summarisation this question
    asked for was ALREADY BUILT, and this is its text — answered in the turn
    that asked, with nothing queued.

    **It is text and not an id because there is no job.** Every other
    outcome of that route reports a build: accepted (``summary_job_id``),
    refused (``summary_blocked``), or not yet targeted. This one reports an
    ANSWER, and the only honest carrier for an answer is the answer.

    **The one field on this record that is not a fact but a rendering**, and
    the exception is argued rather than assumed. س-18 gives the caller the
    voice, and it still has it: this text is not the module's wording of
    anything — it is the summary the model wrote, framed exactly as the
    worker frames the same artefact when it delivers one to a thread
    (``delivered_summary_text``: the truncation notice, the file-name
    header). Handing over a ``Summary`` instead would move that framing to
    the caller and give the same stored row two different shapes depending on
    whether a worker or a reader delivered it — which is the failure, not the
    principle.

    ``None`` on every other outcome, including the one that queues a build:
    nothing was stored, which is precisely why a build was queued. It is
    never set together with ``summary_job_id`` or ``summary_blocked`` — a
    request is answered from the store, or accepted, or refused.

    **No default**, for the reason above it: a path that reads a stored
    summary and forgets to carry it would queue a rebuild that the read had
    just proven unnecessary, silently, which is ف-3 restored.

    ``best_dense_score``/``best_bm25_score`` (scenarios plan section 8,
    ب-11, gap ف-6) are the eighth and ninth fields, and the only two that
    describe the SEARCH rather than the answer: the maximum raw
    ``VectorHit.score`` each leg returned, over every hit it returned —
    before relevance filtering, before diversity, before truncation to ``k``.
    They are ``RetrievalResult``'s own two signals, passed on unchanged.

    **They cross to be MEASURED, never to be gated**, and the distinction is
    the whole of the item. ق-2 keeps a numeric threshold out of the agent,
    and ت-1 is why it can stay out: thresholds exist here, they are
    calibrated, and they are applied one layer down. What was missing was not
    a knob but a VIEW — no record anywhere paired a turn's OUTCOME with the
    confidence retrieval had in it, so the score distribution over turns that
    ended in an apology, against turns that ended in an answer, could only be
    guessed at. Carried across, it is collected from production for free, on
    a real corpus instead of a fifteen-question set, and it is the material
    any RE-calibration would be derived from.

    ``None`` is an honestly absent signal and never ``0.0``: that leg
    returned no hits at all. Both are ``None`` on every outcome of the
    summarisation route, which ran no query — a receipt, a refusal, a stored
    summary and a clarification question are facts about documents, not
    results of a search, and reporting ``0.0`` for them would put a fabricated
    zero into the very distribution this field exists to make readable.

    **No default**, for ``stored_summary_text``'s reason exactly: a route that
    searched and forgot to report its confidence would be indistinguishable
    from one that never searched, which is the blindness ف-6 names.
    """

    intent: Intent
    chunks: tuple[RetrievedChunk, ...]
    summary_job_id: Uuid | None
    clarification_options: tuple[str, ...]
    summary_target_name: str | None
    summary_blocked: SummaryBlocked | None
    stored_summary_text: str | None
    best_dense_score: float | None
    best_bm25_score: float | None


class KnowledgeRetrieval(Protocol):
    """Injected into agents; retrieves the top ``k`` relevant chunks for
    ``query`` within the caller's workspace (02 §2).

    ``k`` is OPTIONAL since retrieval plan §4 row 18 (``P-40``, س-24 = أ):
    omitting it asks for however many chunks the deployment is configured to
    return (``Settings.retrieval.default_k``, resolved inside the module by
    ``RetrieveContext``). That is what let the RAG agent drop its own
    ``_TOP_K = 5`` — the agent imports nothing and reads no configuration
    (ح-11), so the only place a deployment's ``k`` could reach it is a
    default on this seam. Naming a ``k`` is still allowed and still means
    exactly what it did: ``POST /knowledge/search`` names one, because a
    request's result-set SIZE is part of that published contract (03 §2) and
    is not one of the tuning knobs س-24 confined to ``Settings``.

    ``file_ids`` (BE-RAG-005) narrows that workspace-wide search to the
    documents built from those files — the retrieval scope a conversation
    pins. It crosses as FILE ids, not document ids, so callers keep speaking
    about what they uploaded; the translation is the module's own
    (``KnowledgeRetrievalService``).

    Defaulted to ``None`` = unscoped, which keeps every existing caller —
    including ``POST /knowledge/search`` — exactly as it was.

    ``space_id`` (spaces plan §3.4, step 8) is the space to search inside. It
    is keyword-only, without a default, and **not nullable** — while
    ``file_ids`` keeps its default and its ``None``. Forgetting a pin narrows
    nothing; there is no forgetting a space, because there is nothing to
    forget it to.

    ✅ **س-32, owner decision 2026-08-26 — closed.** Both callers that used to
    pass ``None`` on purpose now name a space: ``POST /knowledge/search`` takes
    a REQUIRED ``space_id`` on ``KnowledgeSearchIn``, and the RAG agent reads
    the thread's space off ``AgentDependencies.space_id``, which the
    orchestrator fills from the conversation it is answering in. The rule the
    decision states is that spaces are isolated completely — files, index and
    rows — so a search spans one space or it does not run; the enforcement is
    ``retrieval.require_space_scope``, which refuses the call before an
    embedding is computed. What used to be "decision 1 unenforced on the read
    path" is now enforced by the type on this line and by that guard behind it.

    ``list_document_names`` takes the same required ``space_id`` for the same
    reason: a corpus header naming files from a space the asker cannot open is
    the identical leak, wearing the costume of a helpful answer.
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
    ) -> list[RetrievedChunk]: ...

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
        conversation_id: Uuid | None = None,
        pending_candidates: Sequence[str] = (),
    ) -> RoutedAnswer:
        """Classify ``question`` and dispatch it to the route it belongs to
        (retrieval plan §3.4/§4 row 11, ``P-21``, س-16 = أ) — SUMMARIZE_DOC to
        the module's ``RequestSummary``, CONTENT to its ``RetrieveContext``.

        A THIRD method on the SAME seed, for the reason ``list_document_names``
        is a second one: the RAG agent calls exactly one thing (ح-11), and
        routing is the module's business rather than the agent's — every
        consumer of this port gets it, not only the agent that asked first.

        The RETRIEVAL arguments are ``retrieve``'s, unchanged, because the
        CONTENT route IS ``retrieve``: ``k``, the pinned ``file_ids`` scope
        and the keyword-only ``space_id`` mean here exactly what they mean
        there, and a second vocabulary for the same three narrowings would be
        a second place for them to drift.

        ``pending_candidates`` (ب-9, gap ف-1أ) is the SECOND argument that is
        not retrieval's, and the only one that can answer a question before it
        is classified: the file names the caller's LAST turn asked the user to
        choose between, in the order they were shown. A question that chooses
        one of them is a summarisation of that document, and is answered as
        one.

        It closes the far end of a path this port already had. When the
        resolver refuses to choose, ``RoutedAnswer.clarification_options``
        comes back and the caller asks the user — and until ب-9 nothing on
        this port could receive the reply, so every one of those questions
        ended in a content search. The names cross OUT here and now they cross
        back IN.

        NAMES cross, not document ids, and the same reason holds on both
        directions of the trip: what was displayed is what is being answered,
        and a position («the second one») is only readable against the list
        that was actually shown. The caller stores what it displayed and hands
        it back; this module does the resolving, exactly as ق-3 has it — the
        caller never matches a name itself.

        Defaulted to ``()``, so a caller with no notion of a previous turn
        (``POST /knowledge/search``) keeps calling this as it did, and an
        empty value costs nothing at all: nothing is walked and nothing is
        matched.

        ``conversation_id`` is the one argument that is NOT retrieval's
        (`F-7`), which is why it lives on this method and not on ``retrieve``.
        The SUMMARIZE_DOC route queues work that finishes minutes after the
        call returns, so it has to be told where the answer is owed; the
        module stamps the id on the build and a subscriber posts the finished
        text into that thread. CONTENT ignores it entirely — that answer is
        written by the caller, inside the turn. Defaulted to ``None``, which
        means "nowhere to deliver" and is the honest state of every caller
        that has no thread, ``POST /knowledge/search`` included.

        **One behaviour is not ``retrieve``'s** (retrieval plan §4 row 15,
        ``P-25``): a question that NAMES one of this workspace's files is
        retrieved inside that file only, and STRICTLY — if the named file
        holds nothing, ``chunks`` comes back empty and the search is never
        re-run across the rest of the corpus, because an answer drawn from a
        file the user did not ask about is worse than an honest "not in that
        file" (which the caller renders through the same fallback that
        already handles an empty result). ``retrieve`` keeps no such
        behaviour: it is the literal search ``POST /knowledge/search`` means.

        ``retrieve`` stays, and stays public: ``POST /knowledge/search`` asks
        for chunks and means chunks — routing a REST search through a
        classifier would let it queue a summary job nobody requested.
        """
        ...

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: Uuid, limit: int | None = None
    ) -> DocumentNames:
        """ONE SPACE's corpus-awareness source (retrieval plan §3.6/§4 row 6,
        ``P-36``): up to ``limit`` document file names plus that space's total
        document count.

        ⚠️ **It was the WORKSPACE's until س-32** (owner decision 2026-08-26),
        on decision س-23 = ج's argument that a header describing a slice would
        misreport the corpus as smaller than it is. That argument is still
        sound about a workspace and no longer the question: the decision
        isolates spaces completely — files, index and rows — so the corpus a
        thread HAS is its space's, and a header naming files from a space the
        asker cannot open does not describe their corpus, it describes someone
        else's. The undercount س-23 feared is now the honest count.

        ``space_id`` is therefore required and non-nullable here exactly as it
        is on ``retrieve``/``answer``: the header and the answer describe the
        same corpus, or the header is a list of files no follow-up question
        can be answered from.

        A SECOND method on the SAME seed rather than a second injected port
        — the RAG agent still calls exactly one thing (``self.deps.knowledge``,
        fact ح-11) for both retrieval and corpus awareness.

        ``limit`` is OPTIONAL for ``k``'s exact reason, and it is the same
        shape (plan row 18, ``P-40``, س-24 = أ): omitting it asks for however
        many names the deployment is configured to show
        (``Settings.retrieval.max_corpus_names``, resolved inside the module by
        ``ListDocumentNames``). That is what let the RAG agent drop its own
        ``_MAX_CORPUS_NAMES = 50`` — the display cap was the LAST tuning number
        left in an agent, and an agent reads no configuration and imports
        nothing (ح-11), so a default on this seam is the only place a
        deployment's number could reach it. Naming a ``limit`` is still allowed
        and still means what it did: a caller asking for a result-set SIZE, not
        overriding a deployment knob. Either way no ``Settings``/``os.getenv``
        is read inside this port or its implementation (س-24).
        """
        ...
