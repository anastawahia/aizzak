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
    """

    intent: Intent
    chunks: tuple[RetrievedChunk, ...]
    summary_job_id: Uuid | None
    clarification_options: tuple[str, ...]


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

    ``space_id`` (spaces plan §3.4, step 8) is the space to search inside, and
    is keyword-only WITHOUT a default while ``file_ids`` keeps one. Forgetting
    a pin narrows nothing; forgetting a space widens the answer across the
    axis this plan draws — so the caller has to say, and the two callers that
    still cannot say it (``POST /knowledge/search`` and the RAG agent) are
    visible in the source as the ones passing ``None`` on purpose.

    ⚠️ **Step 12 did NOT close those two, contrary to what this note used to
    predict.** It put the space on the wire for the routes §3.7 names, and
    both of these reach retrieval from somewhere else: the search body is not
    in that table, and the RAG agent reads its space from ``AgentDeps``, which
    the orchestrator does not fill yet. Until that lands (recorded in the
    plan's §7), a thread inside a space still retrieves across every space —
    which is decision 1 unenforced on the read path, and the reason the entry
    is written down rather than left to be noticed.
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid | None,
    ) -> list[RetrievedChunk]: ...

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid | None,
    ) -> RoutedAnswer:
        """Classify ``question`` and dispatch it to the route it belongs to
        (retrieval plan §3.4/§4 row 11, ``P-21``, س-16 = أ) — SUMMARIZE_DOC to
        the module's ``RequestSummary``, CONTENT to its ``RetrieveContext``.

        A THIRD method on the SAME seed, for the reason ``list_document_names``
        is a second one: the RAG agent calls exactly one thing (ح-11), and
        routing is the module's business rather than the agent's — every
        consumer of this port gets it, not only the agent that asked first.

        The arguments are ``retrieve``'s, unchanged, because the CONTENT route
        IS ``retrieve``: ``k``, the pinned ``file_ids`` scope and the
        keyword-only ``space_id`` mean here exactly what they mean there, and
        a second vocabulary for the same three narrowings would be a second
        place for them to drift.

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
        self, ctx: ExecutionContext, *, limit: int | None = None
    ) -> DocumentNames:
        """This workspace's corpus-awareness source (retrieval plan §3.6/§4
        row 6, ``P-36``): up to ``limit`` document file names plus the
        workspace's total document count.

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
