"""Unit tests for ``app.agents.rag_agent`` (Phase 4.6-a — FR-20.1, 11 §9).

Purely hermetic: the agent is driven against FAKE ports (a ``KnowledgeAccess``
and an ``LLMProvider``), exactly as 11 §9 prescribes — no service, no
``live_*`` marker. Covers the streamed event sequence + citations, the
retrieval-context injection into the system prompt, the R6 query guard (422),
the unbound-LLM guard (500), knowledge-optional degradation, discovery via the
real ``PluginLoader``, and one full drive through the 4.2 lifecycle executor.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import pytest

from app.agents.orchestrator import _turn_content
from app.agents.rag_agent import agent as agent_module
from app.agents.rag_agent.agent import RagAgent
from app.framework.agent_runtime import (
    AgentDependencies,
    AgentLifecycleExecutor,
    AgentRequest,
    BaseAgent,
    InMemoryAgentRegistry,
    PluginLoader,
    ResolvedLLM,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.observability.logging import JsonFormatter
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.modules.knowledge.application.retrieval import RetrievalResult
from app.modules.knowledge.domain.intent import Intent
from app.modules.knowledge.domain.value_objects import SummaryBlocked
from app.modules.knowledge.ports.retrieval import RetrievedChunk

# --------------------------------------------------------------------------- #
# Fakes + builders                                                            #
# --------------------------------------------------------------------------- #


# س-32 (owner decision 2026-08-26) — the space every fixture below runs in.
# One constant rather than a literal per test: the agent now refuses to
# retrieve without one, so "which space" is a fact about the harness, and a
# test that cares about the value says so by naming a different one.
SPACE = "space-alpha"


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


class FakeChunk:
    """Structurally satisfies ``RetrievedChunkView``."""

    def __init__(
        self,
        chunk_id: str,
        text: str,
        *,
        score: float = 0.9,
        file_name: str | None = None,
        page_number: int | None = None,
        section: str | None = None,
    ) -> None:
        self.document_id = "doc-1"
        self.chunk_id = chunk_id
        self.text = text
        self.score = score
        self.file_name = file_name
        self.page_number = page_number
        self.section = section


class FakeDocumentNames:
    """Structurally satisfies ``DocumentNamesView``."""

    def __init__(self, names: Sequence[str], total: int | None = None) -> None:
        self.names = tuple(names)
        # Defaults to `len(names)` — the common "no overflow" shape — so a
        # test that only cares about the listed names does not also have to
        # spell out a total that agrees with them.
        self.total = len(names) if total is None else total


class FakeRoutedAnswer:
    """Structurally satisfies ``RoutedAnswerView`` (retrieval plan §3.4/§4 row
    11, `P-21`)."""

    def __init__(
        self,
        intent: str,
        chunks: Sequence[FakeChunk],
        summary_job_id: str | None,
        clarification_options: Sequence[str] = (),
        summary_target_name: str | None = None,
        summary_blocked: str | None = None,
        stored_summary_text: str | None = None,
        best_dense_score: float | None = None,
        best_bm25_score: float | None = None,
    ) -> None:
        self.intent = intent
        self.chunks = chunks
        self.summary_job_id = summary_job_id
        # Retrieval plan §3.5/§4 row 14 (`P-04`, س-18 = أ) — the file names
        # the module refused to choose between. Defaulted to empty: every
        # pre-row-14 construction above means "nothing to clarify", and
        # spelling that out at each of them would say less than it costs.
        self.clarification_options = clarification_options
        # ب-7أ (خطة السيناريوهات §4، ف-2) — the name of the file a queued
        # build is about. Defaulted to `None` for the reason above and
        # one more: `None` is what the REAL module sends whenever it
        # queued nothing, and whenever it queued a build it could not
        # name, so a construction that says nothing here is saying a
        # thing the module actually says.
        self.summary_target_name = summary_target_name
        # ب-4ب (خطة السيناريوهات §5، ف-7) — the module's classification of a
        # REFUSED build, as the string the seam carries it as. Defaulted to
        # `None` for `clarification_options`' reason: nothing was refused on
        # any construction that does not say so.
        self.summary_blocked = summary_blocked
        # ب-8 (خطة السيناريوهات §6، ف-3) — an ALREADY-BUILT summary, arriving
        # as the module delivers it: through `delivered_summary_text`, so the
        # header and any truncation notice are already inside the string. The
        # agent is expected to emit it and add nothing, which is why this fake
        # carries a whole text rather than a flag.
        self.stored_summary_text = stored_summary_text
        # ب-11 (خطة السيناريوهات section 8، ف-6) — the retrieval's own raw
        # confidence. Defaulted to `None` because that is what the REAL module
        # sends on every outcome that ran no query, which is every outcome but
        # one: a construction that says nothing here is saying something the
        # module actually says.
        self.best_dense_score = best_dense_score
        self.best_bm25_score = best_bm25_score


class FakeKnowledge:
    """Structurally satisfies ``KnowledgeAccess``; records its calls.

    ``summary_job_id`` is how a test picks which ROUTE the module took
    (retrieval plan §3.4): ``None`` is the CONTENT route and returns the
    canned chunks, a value is the SUMMARIZE_DOC route having queued a build.
    ``clarification_options`` is that route's THIRD outcome (row 14): the
    module classified a summarisation but refused to choose between these
    files. Which questions take which route is the knowledge module's
    decision and is tested there — what the agent owes is the right behaviour
    once the module has decided.
    """

    def __init__(
        self,
        chunks: Sequence[FakeChunk],
        *,
        document_names: Sequence[str] = (),
        document_total: int | None = None,
        summary_job_id: str | None = None,
        summary_target_name: str | None = None,
        summary_blocked: str | None = None,
        stored_summary_text: str | None = None,
        clarification_options: Sequence[str] = (),
        routed_intent: str | None = None,
        answer_error: Exception | None = None,
        names_error: Exception | None = None,
        best_dense_score: float | None = None,
        best_bm25_score: float | None = None,
    ) -> None:
        self._chunks = chunks
        self._document_names = FakeDocumentNames(document_names, document_total)
        self._summary_job_id = summary_job_id
        # ب-7أ — the name the module resolved the build's target to.
        # Only ever read on the branch that returns a job id: a name
        # without a build is a state the real module cannot produce.
        self._summary_target_name = summary_target_name
        # ب-4ب — the refusal reason the module reports. It is the ONE input
        # that produces a routed answer with a `summarize_doc` intent, no job
        # and no candidates and yet is not the targetless case: a target WAS
        # resolved, and the build was refused. Set it and this fake takes the
        # refusal branch; leave it and nothing about the fake changes.
        self._summary_blocked = summary_blocked
        # ب-8 — the stored summary the module read back instead of queueing a
        # build. Set it and this fake takes the cached branch, which is the
        # FIRST of the routed outcomes: the module reads before it starts, and
        # a fake that ordered them the other way would test an agent the
        # module can never produce input for.
        self._stored_summary_text = stored_summary_text
        self._clarification_options = clarification_options
        # ب-3 (خطة الفجوات §3، ف-5) — the intent the module REPORTS on the
        # route that returned no job and no candidates. `None` keeps the
        # pre-existing `"content"`, so every construction above still means
        # what it did; `"summarize_doc"` is the case the fifth branch exists
        # for — a summarisation whose target the module could not identify,
        # which used to fall silently through to the content route.
        self._routed_intent = routed_intent
        # ب-11 (خطة السيناريوهات section 8، ف-6) — the retrieval's own raw
        # confidence, reported ONLY on the branch that ran a query. The real
        # module sends `None` on every summarisation outcome because none of
        # them searched, and a fake that leaked a number onto those branches
        # would let an agent test pass on input the module cannot produce.
        self._best_dense_score = best_dense_score
        self._best_bm25_score = best_bm25_score
        # ب-4أ / ب-2 / ب-5 — what each of the two seam calls does INSTEAD of
        # answering. A raising dependency is the entire subject of those three
        # items, and the difference between them is which call fails and
        # whether the turn survives it: `answer` raising `ConflictError` is a
        # sentence (ب-4أ), `answer` raising anything else is still a failed
        # turn (the containing guard), and `list_document_names` raising is
        # never allowed to sink the answer at all (ب-2).
        self._answer_error = answer_error
        self._names_error = names_error
        # `k` is `int | None` since retrieval plan §4 row 18 (`P-40`): the
        # agent stopped naming one, so what this records is a `None` that
        # means "the deployment's configured `k`". Recording it rather than
        # dropping it is what makes that a visible assertion.
        self.calls: list[tuple[str, int | None, tuple[str, ...] | None]] = []
        # Every DIRECT `retrieve` — which the agent must never make, now that
        # routing happens inside the module (`answer`). Its own log, so
        # "the agent asked for retrieval instead of an answer" is a visible
        # assertion rather than an absence nobody checks.
        self.retrieve_calls: list[tuple[str, int | None]] = []
        # Every space the agent named (spaces plan step 8) — its own log, so
        # the existing `calls` assertions keep their shape.
        self.spaces: list[str] = []
        # Retrieval plan §3.6/§4 row 6 (`P-36`) — every `limit` the agent
        # asked for, `None` included: since the display cap moved to the
        # module side (review §8) `None` is what the agent passes, and
        # recording it rather than dropping it is what makes "the agent names
        # no cap" a visible assertion instead of an absence.
        self.name_limit_calls: list[int | None] = []
        # `F-7` — every thread the agent named, `None` included. Its own log
        # for `spaces`' reason, and `None` is recorded rather than dropped so
        # that "a turn with no thread still says so" is a visible assertion.
        self.threads: list[str | None] = []
        # ب-9 (ف-1أ) — every pending-clarification list the agent forwarded,
        # `()` included. Its own log for the same reason: what the agent owes
        # here is carrying the names through UNTOUCHED, and a fake that
        # dropped the argument could not tell that from an agent that never
        # sent it.
        self.pending: list[tuple[str, ...]] = []

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str,
    ) -> Sequence[FakeChunk]:
        self.retrieve_calls.append((query, k))
        self.spaces.append(space_id)
        return self._chunks

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str,
        conversation_id: str | None = None,
        pending_candidates: Sequence[str] = (),
    ) -> FakeRoutedAnswer:
        # The scope is RECORDED, not honoured: this fake is the agent's
        # counterpart, and what the agent owes is passing the scope through
        # untouched — resolving it to documents is the knowledge module's job
        # and is tested there.
        self.calls.append((question, k, None if file_ids is None else tuple(file_ids)))
        self.spaces.append(space_id)
        self.threads.append(conversation_id)
        self.pending.append(tuple(pending_candidates))
        # Recorded BEFORE it raises: the call was made, and a test about a
        # failing seam still wants to see that the agent asked.
        if self._answer_error is not None:
            raise self._answer_error
        if self._stored_summary_text is not None:
            # ب-8 — the FIFTH outcome and the only one carrying an ANSWER: the
            # summary existed, so nothing was queued and there is no job id to
            # report. `summary_target_name` rides along because the real module
            # keeps it — the read is about a document it named, and the name is
            # already inside the delivered text as its header.
            return FakeRoutedAnswer(
                "summarize_doc",
                (),
                None,
                summary_target_name=self._summary_target_name,
                stored_summary_text=self._stored_summary_text,
            )
        if self._summary_job_id is not None:
            return FakeRoutedAnswer(
                "summarize_doc",
                (),
                self._summary_job_id,
                summary_target_name=self._summary_target_name,
            )
        if self._summary_blocked is not None:
            # ب-4ب — the FOURTH outcome: a resolved target whose build the
            # module refused. `summary_target_name` rides along because the
            # real module keeps it here — the refusal is about a document it
            # named.
            return FakeRoutedAnswer(
                "summarize_doc",
                (),
                None,
                summary_target_name=self._summary_target_name,
                summary_blocked=self._summary_blocked,
            )
        if self._clarification_options:
            # Retrieval plan §4 row 14 — the honest "I did not decide"
            # answer: the intent is reported as the summarisation it was, no
            # job was queued, and no chunks came back either.
            return FakeRoutedAnswer("summarize_doc", (), None, self._clarification_options)
        return FakeRoutedAnswer(
            self._routed_intent or "content",
            self._chunks,
            None,
            best_dense_score=self._best_dense_score,
            best_bm25_score=self._best_bm25_score,
        )

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: str, limit: int | None = None
    ) -> FakeDocumentNames:
        self.name_limit_calls.append(limit)
        # س-32 — the header is space-scoped too, and it lands in the SAME log
        # the two retrieval faces write to: the decision is that one turn reads
        # one space, so a header taken from a different space than the answer
        # would be the leak in its other costume.
        self.spaces.append(space_id)
        # Recorded first, for `answer`'s reason: ب-2's whole claim is that the
        # listing was ATTEMPTED and its failure absorbed, which an assertion
        # can only see if the attempt is logged before it raises.
        if self._names_error is not None:
            raise self._names_error
        return self._document_names


class FakeLLM:
    """Structurally satisfies ``LLMProvider``; streams the configured deltas."""

    provider = "fake"

    def __init__(self, deltas: Sequence[str]) -> None:
        self._deltas = deltas
        self.stream_calls: list[tuple[list[LlmMessage], LlmParams, str]] = []

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:  # pragma: no cover - RAG v1 streams, never completes
        raise NotImplementedError

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        self.stream_calls.append((list(messages), params, api_key))

        async def gen() -> AsyncIterator[LlmChunk]:
            for delta in self._deltas:
                yield LlmChunk(delta=delta)
            yield LlmChunk(delta="", finish_reason="stop")

        return gen()

    def supports(self, capability: str) -> bool:
        return capability == "streaming"


def make_deps(
    *,
    deltas: Sequence[str] = ("ok",),
    chunks: Sequence[FakeChunk] | None = None,
    scope: tuple[str, ...] = (),
    document_names: Sequence[str] = (),
    document_total: int | None = None,
    summary_job_id: str | None = None,
    summary_target_name: str | None = None,
    summary_blocked: str | None = None,
    stored_summary_text: str | None = None,
    clarification_options: Sequence[str] = (),
    space_id: str | None = SPACE,
    routed_intent: str | None = None,
    answer_error: Exception | None = None,
    names_error: Exception | None = None,
    pending: tuple[str, ...] = (),
    best_dense_score: float | None = None,
    best_bm25_score: float | None = None,
) -> tuple[AgentDependencies, FakeKnowledge, FakeLLM]:
    llm = FakeLLM(deltas)
    knowledge = FakeKnowledge(
        chunks if chunks is not None else [],
        document_names=document_names,
        document_total=document_total,
        summary_job_id=summary_job_id,
        summary_target_name=summary_target_name,
        summary_blocked=summary_blocked,
        stored_summary_text=stored_summary_text,
        clarification_options=clarification_options,
        routed_intent=routed_intent,
        answer_error=answer_error,
        names_error=names_error,
        best_dense_score=best_dense_score,
        best_bm25_score=best_bm25_score,
    )
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"),
        knowledge=knowledge,
        knowledge_scope=scope,
        space_id=space_id,
        # ب-9 — what the orchestrator read off the thread. `()` on almost
        # every turn, which is why it defaults to it: every test written
        # before this item keeps meaning exactly what it meant.
        pending_clarification=pending,
    )
    return deps, knowledge, llm


async def drive_run(agent: RagAgent, text: str, *, conversation_id: str | None = None) -> list:
    return [
        event
        async for event in agent.run(
            AgentRequest(conversation_id=conversation_id, input={"text": text})
        )
    ]


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


async def test_streams_tokens_then_final_with_citations() -> None:
    deps, knowledge, _llm = make_deps(
        deltas=["Paris", " is", " the capital."],
        chunks=[FakeChunk("c1", "Paris is the capital of France.")],
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert [e.data["delta"] for e in events if e.type == "token"] == [
        "Paris",
        " is",
        " the capital.",
    ]
    final = events[-1]
    assert final.type == "final"
    assert final.data["text"] == "Paris is the capital."
    # Retrieval plan §3.2/§4 row 3, P-32 — a structured citation, not a bare
    # `chunk_id` UUID: `document_id`/`file_name`/`page`/`chunk_id`, reusing
    # step 1's fields verbatim (`file_name`/`page` both `None` here because
    # this `FakeChunk` set neither) — plus ب-12's one-based `rank`.
    assert final.data["citations"] == [
        {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "c1", "rank": 1}
    ]
    # `None`, not `()`: an agent with no pinned scope must ask for the WHOLE
    # workspace corpus. Forwarding the bundle's empty tuple would arrive one
    # layer down as "a scope that resolved to no documents", which retrieves
    # nothing (BE-RAG-005).
    assert knowledge.calls == [("capital of France?", None, None)]


async def test_a_pinned_scope_is_forwarded_to_retrieval_untouched() -> None:
    """BE-RAG-005: the agent passes the bundle's scope straight through.

    It does NOT resolve, filter or re-order it — the file⇒document translation
    belongs to the knowledge module, and an agent that pre-processed the scope
    would be a second place where a pin could silently stop applying.
    """
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")], scope=("file-a", "file-b"))
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.calls == [("q", None, ("file-a", "file-b"))]


async def test_the_agent_names_the_thread_its_turn_belongs_to() -> None:
    """`F-7`: the thread travels from ``AgentRequest`` onto the one knowledge
    call this agent makes, and the agent does nothing else with it.

    It is the only layer that can: the module builds summaries asynchronously
    and has no idea which conversation asked, and the worker that finishes one
    is further from the question still. Passing it through is the whole of the
    agent's part — the same shape as ``knowledge_scope``, which it also
    forwards untouched."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q", conversation_id="conv-7")

    assert knowledge.threads == ["conv-7"]


async def test_a_turn_that_opens_a_thread_reports_no_thread_rather_than_omitting_it() -> None:
    """A turn with no ``conversation_id`` yet is a real state, not a missing
    argument: the orchestrator opens the thread AFTER the agent is built for
    invocations that start one. ``None`` reaches the module as "nowhere to
    deliver", and a summary asked for on such a turn is still built and still
    readable through the summary routes."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.threads == [None]


async def test_the_agent_names_the_space_its_turn_belongs_to() -> None:
    """س-32 (owner decision 2026-08-26): ``AgentDeps`` carries the space now —
    the orchestrator reads it off the turn's thread — and the agent names it on
    every knowledge call it makes. It was ``None`` on both until this decision,
    which is what let a thread inside one space answer from all of them."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    # Both faces, one space: the answer and the corpus header describe the
    # same corpus or the header names files the answer may not use.
    assert knowledge.spaces == [SPACE, SPACE]


async def test_without_a_space_the_agent_retrieves_nothing_rather_than_everything() -> None:
    """س-32's degradation, and the direction is the whole point.

    A bundle with no space is "this turn's space is unknown" — reachable only
    where the orchestrator has no conversations seam to read a thread from. The
    agent answers from the model alone, exactly as it does with no knowledge
    seam wired at all: no retrieval call, no corpus header, no citations. The
    behaviour it replaces was the opposite one — search every space — which is
    the hole the decision closes.
    """
    # A name nothing else in this file (or in `SYSTEM_PROMPT`, which cites a
    # `criteria.pdf` of its own) could contribute to the prompt.
    deps, knowledge, llm = make_deps(
        chunks=[FakeChunk("c1", "text")], document_names=("ledger-q3.xlsx",), space_id=None
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.calls == []
    assert knowledge.spaces == []
    assert knowledge.name_limit_calls == []
    final = next(event for event in events if event.type == "final")
    assert final.data["citations"] == []
    # Not the trust-gate fallback either: nothing was searched, so there is no
    # zero-result to be honest about — the model answered, as it does when no
    # knowledge seam is wired.
    assert "ledger-q3.xlsx" not in llm.stream_calls[0][0][0].content
    assert final.data["text"] == "ok"


async def test_retrieved_context_is_injected_into_the_system_prompt() -> None:
    deps, _knowledge, llm = make_deps(chunks=[FakeChunk("c1", "Paris is the capital.")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert system_message.role == "system"
    assert "Paris is the capital." in system_message.content
    assert llm.stream_calls[0][1].model == "fake-model"
    assert llm.stream_calls[0][2] == "k"


# --------------------------------------------------------------------------- #
# Structured citations (retrieval plan §3.2, §4 row ٣ — P-32)                #
# --------------------------------------------------------------------------- #


async def test_a_citation_carries_the_full_structured_shape() -> None:
    """`{document_id, file_name, page, chunk_id}` — `page` reuses step 1's
    `page_number` field verbatim under the shorter wire name the plan names
    for this shape; nothing is re-derived."""
    deps, _knowledge, _llm = make_deps(
        chunks=[
            FakeChunk(
                "c1",
                "Paris is the capital.",
                file_name="maintenance.pdf",
                page_number=12,
                section="المسؤوليات",
            )
        ]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert events[-1].data["citations"] == [
        {
            "document_id": "doc-1",
            "file_name": "maintenance.pdf",
            "page": 12,
            "chunk_id": "c1",
            "rank": 1,
        }
    ]


async def test_a_citation_represents_missing_file_name_and_page_as_explicit_none() -> None:
    """A missing `file_name`/`page` is an explicit `None` (⇒ JSON `null`) on
    an always-present key — never an omitted key, never a placeholder
    string — the same rule `RetrievedChunkOut` already follows on the wire."""
    deps, _knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "chunk body")])
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    citation = events[-1].data["citations"][0]
    assert citation == {
        "document_id": "doc-1",
        "file_name": None,
        "page": None,
        "chunk_id": "c1",
        "rank": 1,
    }
    assert "file_name" in citation
    assert "page" in citation


async def test_multiple_chunks_each_get_their_own_citation_in_order() -> None:
    deps, _knowledge, _llm = make_deps(
        chunks=[
            FakeChunk("c1", "first", file_name="a.pdf", page_number=1),
            FakeChunk("c2", "second", file_name="b.pdf", page_number=None),
        ]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert events[-1].data["citations"] == [
        {"document_id": "doc-1", "file_name": "a.pdf", "page": 1, "chunk_id": "c1", "rank": 1},
        {"document_id": "doc-1", "file_name": "b.pdf", "page": None, "chunk_id": "c2", "rank": 2},
    ]


# --------------------------------------------------------------------------- #
# Source labeling (retrieval plan §3.2, row ٢ — P-31)                        #
# --------------------------------------------------------------------------- #


async def test_context_carries_the_full_source_label_above_the_chunk_text() -> None:
    deps, _knowledge, llm = make_deps(
        chunks=[
            FakeChunk(
                "c1",
                "Paris is the capital.",
                file_name="maintenance.pdf",
                page_number=12,
                section="المسؤوليات",
            )
        ]
    )
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert (
        "[maintenance.pdf p.12 | section: المسؤوليات]\nParis is the capital."
        in system_message.content
    )


@pytest.mark.parametrize(
    ("file_name", "page_number", "section", "expected_label"),
    [
        ("maintenance.pdf", 12, "المسؤوليات", "[maintenance.pdf p.12 | section: المسؤوليات]"),
        ("maintenance.pdf", None, "المسؤوليات", "[maintenance.pdf | section: المسؤوليات]"),
        ("maintenance.pdf", 12, None, "[maintenance.pdf p.12]"),
        ("maintenance.pdf", None, None, "[maintenance.pdf]"),
        (None, 12, "المسؤوليات", "[unknown p.12 | section: المسؤوليات]"),
        (None, None, None, "[unknown]"),
    ],
)
async def test_the_label_degrades_deterministically_per_missing_field(
    file_name: str | None,
    page_number: int | None,
    section: str | None,
    expected_label: str,
) -> None:
    deps, _knowledge, llm = make_deps(
        chunks=[
            FakeChunk(
                "c1",
                "chunk body",
                file_name=file_name,
                page_number=page_number,
                section=section,
            )
        ]
    )
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert f"{expected_label}\nchunk body" in system_message.content


# --------------------------------------------------------------------------- #
# Header instructions (retrieval plan §4 row 7 — P-37)                       #
# --------------------------------------------------------------------------- #


async def test_the_composed_system_message_carries_all_four_header_instructions() -> None:
    """§4 row 7 (`P-37`) names four instructions the header must give the
    model: gather from ALL sections, include EVERY list item, cite the file
    and section, and don't narrate its reasoning. This drives the agent
    end-to-end (fake LLM, real chunks) and reads the ACTUAL system message
    `_messages` composed — not `SYSTEM_PROMPT` the constant — so a future
    refactor of message composition cannot silently drop one of them."""
    deps, _knowledge, llm = make_deps(
        chunks=[FakeChunk("c1", "Paris is the capital.", file_name="a.pdf", page_number=1)]
    )
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert system_message.role == "system"
    content = system_message.content
    # 1 — gather from ALL sections, not just the first relevant one.
    assert "ALL the passages" in content
    # 2 — a list answer must include EVERY item, never truncated/sampled.
    assert "EVERY item" in content
    # 3 — cite the source by naming its FIELDS (file · page · section), in
    # the model's own sentences. It deliberately does NOT ask for the
    # `[file p.N | section: S]` label `format_labeled_chunk` puts above each
    # passage: instructing a small model to reproduce a shape the context
    # literally contains made it copy the block instead of answering (see
    # `test_the_prompt_never_asks_the_model_to_reproduce_the_source_label`).
    assert "name the file, page and section" in content
    assert "[file p.N | section: S]" not in content
    # 4 — answer, don't narrate the reasoning.
    assert "do not narrate your reasoning" in content


# --------------------------------------------------------------------------- #
# Corpus awareness (retrieval plan §3.6/§4 row 6 — P-36, س-23 = ج)            #
# --------------------------------------------------------------------------- #


async def test_corpus_header_is_prepended_to_the_system_prompt_on_the_normal_path() -> None:
    """س-23 = ج's "always" on the NORMAL synthesis path: the header lands in
    the system prompt (invisible to the user, read by the model), never in
    what the model streams back — `final.data["text"]` stays exactly what
    the (fake) LLM emitted."""
    deps, _knowledge, llm = make_deps(
        deltas=["Paris"],
        chunks=[FakeChunk("c1", "Paris is the capital of France.")],
        document_names=["a.pdf", "b.docx"],
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    system_message = llm.stream_calls[0][0][0]
    assert "Files in this space: a.pdf, b.docx." in system_message.content
    assert events[-1].data["text"] == "Paris"


async def test_corpus_header_is_prepended_to_the_fallback_text() -> None:
    """س-23 = ج's "always" on the FALLBACK path: since the LLM is never
    called on this branch, the header is prepended straight onto the
    user-visible text — the only place it can reach the user at all — and
    both the streamed `delta` and the `final.text` carry it identically."""
    deps, _knowledge, llm = make_deps(chunks=[], document_names=["a.pdf", "b.docx"])
    events = await drive_run(RagAgent(make_ctx(), deps), "how many files do you have?")

    assert llm.stream_calls == []  # the trust gate still never calls the LLM
    final = events[-1]
    assert "enough information" in final.data["text"]
    assert "Files in this space: a.pdf, b.docx." in final.data["text"]
    assert events[0].data["delta"] == final.data["text"]


async def test_corpus_header_caps_the_listed_names_with_an_overflow_tail() -> None:
    """§3.6's declared shape: names up to the cap, then a literal "and N more
    files" tail computed from `total - len(names)` — `ListDocumentNames`
    itself (not this agent) is what actually enforces the 50-name cap; this
    fake simply hands back what a capped module response would look like."""
    deps, _knowledge, llm = make_deps(
        chunks=[FakeChunk("c1", "text")],
        document_names=["a.pdf", "b.pdf"],
        document_total=52,
    )
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert "Files in this space: a.pdf, b.pdf, and 50 more files." in system_message.content


@pytest.mark.parametrize(
    ("remaining", "expected_tail"),
    [
        (1, "، وملفّ آخر."),
        (2, "، وملفّان آخران."),
        (3, "، و 3 ملفّات أخرى."),
        (10, "، و 10 ملفّات أخرى."),
        (11, "، و 11 ملفًّا آخر."),
        (50, "، و 50 ملفًّا آخر."),
    ],
)
async def test_the_arabic_overflow_tail_agrees_with_the_number_it_names(
    remaining: int, expected_tail: str
) -> None:
    """Arabic number agreement (تمييز العدد) has FOUR forms, and this tail used
    to render one of them for every count — «و 5 ملفًا آخر», which is correct
    from 11 to 99 and nowhere else. All four are pinned here, plus BOTH sides
    of the 10/11 boundary, because that boundary is the only one the arithmetic
    can get wrong silently.

    Asserted on the header the model/user actually receives (the whole tail,
    its leading «، » and its full stop included) rather than on the private
    helper, so a caller that stopped calling it would fail this too. The word
    «ملفّ» carries its shadda in all four, the convention `_CORPUS_LABEL_AR`
    in the SAME sentence already keeps."""
    deps, _knowledge, llm = make_deps(
        chunks=[FakeChunk("c1", "نصّ")],
        document_names=["a.pdf"],
        document_total=1 + remaining,
    )
    await drive_run(RagAgent(make_ctx(), deps), "كم ملفًا لديك؟")

    system_message = llm.stream_calls[0][0][0]
    assert f"ملفّات هذا الفضاء: a.pdf{expected_tail}" in system_message.content


async def test_the_agreeing_tail_reaches_the_user_on_the_fallback_path() -> None:
    """Where review §9 says the wording matters most: on the fallback the LLM
    is never called, so the header is prepended straight onto the sentence the
    user READS — and that sentence is already an apology. Same builder as the
    system-prompt path above, asserted on `final.text` so the agreement is
    proven where a human actually sees it."""
    deps, _knowledge, llm = make_deps(chunks=[], document_names=["a.pdf"], document_total=4)
    events = await drive_run(RagAgent(make_ctx(), deps), "كم ملفًا لديك؟")

    assert llm.stream_calls == []
    final = events[-1]
    assert "لا أملك معلومات كافية" in final.data["text"]
    assert "ملفّات هذا الفضاء: a.pdf، و 3 ملفّات أخرى." in final.data["text"]
    assert events[0].data["delta"] == final.data["text"]


async def test_the_arabic_header_ends_in_a_full_stop_when_nothing_remains() -> None:
    """The zero case is NOT a fifth form: `total == len(names)` means there is
    nothing more to name, so the sentence closes on the last file name. Pinned
    beside the four so a future agreement rule cannot start rendering «و 0
    ملفّات أخرى» for an entirely listed corpus."""
    deps, _knowledge, llm = make_deps(
        chunks=[FakeChunk("c1", "نصّ")],
        document_names=["a.pdf", "b.pdf"],
        document_total=2,
    )
    await drive_run(RagAgent(make_ctx(), deps), "كم ملفًا لديك؟")

    system_message = llm.stream_calls[0][0][0]
    assert "ملفّات هذا الفضاء: a.pdf، b.pdf." in system_message.content


async def test_corpus_header_reports_no_files_for_an_empty_workspace() -> None:
    deps, _knowledge, llm = make_deps(chunks=[FakeChunk("c1", "text")], document_names=[])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert "There are no files in this space yet." in system_message.content


async def test_corpus_header_follows_the_query_language_like_the_fallback_does() -> None:
    """One shared language mechanism (`_ARABIC_CHAR_RE`) drives both the
    fallback sentence AND the corpus header — no second i18n mechanism."""
    deps, _knowledge, llm = make_deps(chunks=[], document_names=["a.pdf"])
    events = await drive_run(RagAgent(make_ctx(), deps), "كم ملفًا لديك؟")

    final = events[-1]
    assert "لا أملك معلومات كافية" in final.data["text"]
    assert "ملفّات هذا الفضاء: a.pdf." in final.data["text"]
    assert llm.stream_calls == []


async def test_the_agent_names_no_display_cap_and_holds_no_corpus_number() -> None:
    """The 50-name display cap (§3.6) used to be `_MAX_CORPUS_NAMES = 50`, a
    plain module constant in this agent, passed as an argument to the seam.
    Review §8: whatever it tunes, it was a tuning number held by an AGENT —
    the one thing ح-11 says this agent does not do, and the one place
    `Settings` cannot reach, since an agent reads no configuration and imports
    nothing. So it moved to the module side in `_TOP_K`'s exact shape (plan
    row 18, `P-40`): the agent names no `limit` at all and
    `ListDocumentNames` resolves `Settings.retrieval.max_corpus_names`.

    Asserted BOTH ways round, like the `_TOP_K` test below. The call site
    proves the behaviour; the absent module attribute proves the constant was
    actually REMOVED rather than merely left unused — the failure a call-site
    assertion cannot see. That the omitted `limit` yields 50 is the module's
    own fact and is pinned there, over the real `ListDocumentNames`."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.name_limit_calls == [None]
    assert not hasattr(agent_module, "_MAX_CORPUS_NAMES")


async def test_without_knowledge_still_answers_with_no_citations() -> None:
    """No knowledge seam wired at all (`deps.knowledge is None`) is NOT the
    trust gate's "zero chunks" case below — no retrieval was even attempted,
    so there is no retrieval result to gate on, and this stays a plain LLM
    answer (retrieval plan §3.3/§4 row 5, `P-33`, module docstring). Nor is
    there a corpus to describe — retrieval plan §3.6's "always" only ever
    means "whenever retrieval was attempted at all", so no header text
    appears here either."""
    llm = FakeLLM(["hi"])
    deps = AgentDependencies(llm=ResolvedLLM(provider=llm, model="m", api_key="k"))
    events = await drive_run(RagAgent(make_ctx(), deps), "hello")

    assert [e.data["delta"] for e in events if e.type == "token"] == ["hi"]
    assert events[-1].data["citations"] == []
    assert "Files in this space:" not in llm.stream_calls[0][0][0].content


# --------------------------------------------------------------------------- #
# Trust gate + honest fallback (retrieval plan §3.3/§4 row 5 — P-33)          #
# --------------------------------------------------------------------------- #


async def test_zero_chunks_with_knowledge_wired_falls_back_without_calling_the_llm() -> None:
    """The gate's whole point: a knowledge seam IS wired, retrieval genuinely
    returns nothing, and the LLM is NEVER called — the fallback text is a
    fixed local string, not anything the model improvised. Before this step
    the path fell through to bare `SYSTEM_PROMPT` and the model answered from
    its own knowledge as though it were sourced from the user's documents."""
    deps, knowledge, llm = make_deps(chunks=[])
    events = await drive_run(RagAgent(make_ctx(), deps), "what is the capital of France?")

    assert knowledge.calls == [("what is the capital of France?", None, None)]
    assert llm.stream_calls == []  # the LLM provider is never reached
    assert [e.type for e in events] == ["token", "final"]
    final = events[-1]
    assert final.data["citations"] == []
    assert "enough information" in final.data["text"]
    assert events[0].data["delta"] == final.data["text"]


async def test_the_fallback_answers_in_arabic_for_an_arabic_query() -> None:
    """The fallback text matches the query's own language (the same
    convention `SYSTEM_PROMPT` states for the LLM), picked by a plain
    Arabic-script presence check — no new i18n mechanism."""
    deps, _knowledge, llm = make_deps(chunks=[])
    events = await drive_run(RagAgent(make_ctx(), deps), "ما هي عاصمة فرنسا؟")

    assert llm.stream_calls == []
    final = events[-1]
    assert final.data["citations"] == []
    assert "لا أملك معلومات كافية" in final.data["text"]


async def test_a_non_empty_result_takes_the_normal_synthesis_path_not_the_fallback() -> None:
    """A non-empty retrieval result never trips the gate — the pre-existing
    synthesis path (LLM called, real citations) is unaffected."""
    deps, _knowledge, llm = make_deps(
        deltas=["Paris"], chunks=[FakeChunk("c1", "Paris is the capital of France.")]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert len(llm.stream_calls) == 1
    assert events[-1].data["text"] == "Paris"
    assert events[-1].data["citations"] != []


# --------------------------------------------------------------------------- #
# Intent routing (retrieval plan §3.4/§4 row 11 — P-21, س-16 = أ)             #
# --------------------------------------------------------------------------- #


async def test_the_agent_asks_the_module_to_answer_and_never_retrieves_directly() -> None:
    """ح-11 and س-16 = أ, as an assertion: ONE seed, and the call on it is
    `answer`. Classifying here would mean importing the classifier and then
    holding a second seam for whichever route it picked — the two things this
    agent's declared convention rules out."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.calls == [("q", None, None)]
    assert knowledge.retrieve_calls == []


async def test_the_agent_names_no_k_and_holds_no_retrieval_number() -> None:
    """Retrieval plan §4 row 18 (`P-40`, س-24 = أ): the `k` this agent used to
    hold as `_TOP_K = 5` is now the DEPLOYMENT's
    (`Settings.retrieval.default_k`), so the agent passes none at all and the
    number is gone from this module's source.

    Asserted BOTH ways round on purpose. The call site proves the behaviour;
    the absent module attribute proves the constant was actually removed
    rather than merely left unused, which is the failure mode a call-site
    assertion cannot see. An agent reads no configuration and imports nothing
    (ح-11), so "omit it and let the module's configured default apply" is the
    only shape a Settings-owned `k` could ever take here."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert [call[1] for call in knowledge.calls] == [None]
    assert not hasattr(agent_module, "_TOP_K")
    # ...and this IS the module under test rather than an empty stand-in, so
    # `not hasattr` is a proof about `_TOP_K` and not about the import. There
    # is nothing else to name here any more: `_MAX_CORPUS_NAMES`, the one
    # number that used to legitimately stay, followed `_TOP_K` out (review
    # §8), and this agent now holds no tuning number at all.
    assert agent_module.RagAgent is RagAgent


async def test_a_routed_summary_yields_a_receipt_and_never_calls_the_llm() -> None:
    """The SUMMARIZE_DOC route: the module queued a build, so there is nothing
    to synthesise and nothing to cite. The LLM is not reached at all — the
    receipt is a fixed local string, exactly like the fallback sentence."""
    deps, _knowledge, llm = make_deps(summary_job_id="job-1")
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize this file")

    assert llm.stream_calls == []
    assert [e.type for e in events] == ["token", "final"]
    final = events[-1]
    assert final.data["citations"] == []
    assert "being prepared" in final.data["text"]
    assert events[0].data["delta"] == final.data["text"]


async def test_the_summary_receipt_answers_in_arabic_for_an_arabic_query() -> None:
    """One language mechanism in this agent, reused: the same Arabic-script
    presence check the fallback sentences and the corpus header use."""
    deps, _knowledge, llm = make_deps(summary_job_id="job-1")
    events = await drive_run(RagAgent(make_ctx(), deps), "لخص لي هذا الملف")

    assert llm.stream_calls == []
    assert "جارٍ إعداد ملخّص" in events[-1].data["text"]


async def test_the_receipt_never_invents_a_name_the_module_did_not_send() -> None:
    """**The containing guard for ب-7أ.** The rule «لا تردّد اسمًا لم
    تحلَّه» is the agent's and it survives the name crossing the
    seam: what changed is that the module now SENDS one, not that
    the agent started deriving one.

    So with no name sent, nothing is named — not the corpus this
    workspace holds, not the file the QUERY mentions, not the job
    id. An agent that filled the gap from any of those would be
    asserting a resolution nobody performed, which is the failure
    §3.5/س-18 exists to prevent.

    And the corpus header is still absent AND unfetched: س-23 = ج
    puts it on the two ANSWERING paths, and this branch is a
    receipt for an action on a document the module already
    identified.
    """
    deps, knowledge, _llm = make_deps(
        summary_job_id="job-1", document_names=["a.pdf", "b.pdf"], scope=("file-a",)
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize budget-2025.xlsx")

    text = events[-1].data["text"]
    assert "a.pdf" not in text
    # The query named a file. The module resolved nothing, so neither
    # does the answer — the whole distinction ب-7أ rests on.
    assert "budget-2025.xlsx" not in text
    assert "job-1" not in text
    assert knowledge.name_limit_calls == []


async def test_the_receipt_names_the_file_it_queued() -> None:
    """ب-7أ (ف-2): the module resolved the target and said which
    file it is, so the receipt says it too.

    Before this, three sound decisions composed into a user who
    could not tell WHICH file was being summarised — and a FUZZY
    match at 0.78 over a 0.75 threshold announced itself only
    minutes later, when a summary of the wrong document arrived.
    """
    deps, _knowledge, llm = make_deps(
        summary_job_id="job-1", summary_target_name="التقرير الشمالي.pdf"
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "لخص لي هذا الملف")

    # Still no LLM: naming the file is a template fill, not synthesis.
    assert llm.stream_calls == []
    text = events[-1].data["text"]
    assert "التقرير الشمالي.pdf" in text
    assert "جارٍ إعداد ملخّص" in text
    # And still one emit site: token then final, the same text.
    assert [e.type for e in events] == ["token", "final"]
    assert events[0].data["delta"] == text


async def test_the_named_receipt_answers_in_the_querys_language() -> None:
    """One language mechanism, and the name does not change it: the
    file is named in both sentences, and which sentence is used is
    still the `_ARABIC_CHAR_RE` presence check every other fixed
    sentence in this agent uses.

    The name itself is copied verbatim whatever script it is in —
    an Arabic file name inside the English sentence is what the
    file is actually called."""
    deps, _knowledge, _llm = make_deps(
        summary_job_id="job-1", summary_target_name="التقرير الشمالي.pdf"
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize this file")

    text = events[-1].data["text"]
    assert "being prepared" in text
    assert "التقرير الشمالي.pdf" in text


async def test_the_receipt_falls_back_to_its_unnamed_wording_without_a_name() -> None:
    """A target the module could not name (`None`) delivers the
    sentence exactly as it read before ب-7أ — never that sentence
    with a blank where the file should be.

    A blank-looking name is the same case for the same reason: a
    receipt reading «ملخّص «»» tells a user their file is called
    nothing, which is worse than one that names no file at all."""
    for name in (None, "   "):
        deps, _knowledge, _llm = make_deps(summary_job_id="job-1", summary_target_name=name)
        events = await drive_run(RagAgent(make_ctx(), deps), "لخص لي هذا الملف")

        text = events[-1].data["text"]
        assert text == "جارٍ إعداد ملخّص المستند المطلوب، وسيكون متاحًا بعد قليل."


# --------------------------------------------------------------------------- #
# The clarification question (retrieval plan §3.5/§4 row ١٤ — P-04, س-18 = أ) #
# --------------------------------------------------------------------------- #


async def test_an_undecided_file_asks_the_user_which_one_in_ordinary_text() -> None:
    """س-18 = أ: the module refused to choose between two files, so the agent
    ASKS — «أيّ ملفّ تقصد؟» and the names — and the LLM is never called,
    because every word of the question comes from the module's own list."""
    deps, _knowledge, llm = make_deps(
        clarification_options=["الميزانية 2024.pdf", "الميزانية 2025.pdf"]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "لخص لي ملف الميزانية")

    assert llm.stream_calls == []
    text = events[-1].data["text"]
    assert text == "أيّ ملفّ تقصد؟\n- الميزانية 2024.pdf\n- الميزانية 2025.pdf"


async def test_the_clarification_travels_on_the_unchanged_streaming_contract() -> None:
    """The heart of س-18 = أ, and the reason the structured `clarification`
    event stayed in §7: this is an ORDINARY answer. The same `token` + `final`
    pair as every other reply, `citations` empty because a question cites
    nothing — and no event type a client has to have heard of.

    ب-9 added ONE key to the `final` payload and no event type, which is
    exactly the shape ق-4 allows: `_persist_reply` already keeps an agent's own
    terminal keys, and the orchestrator strips this one before the frame is
    sent (`test_a_clarification_key_never_reaches_the_client`). What a client
    sees is unchanged; what the platform learns is which files were offered.
    """
    deps, _knowledge, _llm = make_deps(clarification_options=["a.pdf", "b.pdf"])
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize the budget file")

    assert [e.type for e in events] == ["token", "final"]
    assert events[0].data == {"delta": events[-1].data["text"]}
    assert set(events[-1].data) == {"text", "citations", "pending_clarification"}
    assert events[-1].data["citations"] == []


async def test_the_clarification_answers_in_english_for_an_english_query() -> None:
    """One language mechanism in this agent, reused a fourth time — the same
    Arabic-script presence check the fallback, receipt and corpus header use.
    The FILE NAMES are never translated or transliterated: they are the
    strings the user has to recognise."""
    deps, _knowledge, _llm = make_deps(clarification_options=["الميزانية.pdf", "budget.pdf"])
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize the budget file")

    text = events[-1].data["text"]
    assert text.startswith("Which file do you mean?")
    assert "الميزانية.pdf" in text


async def test_the_clarification_lists_every_candidate_and_never_narrows_them() -> None:
    """The agent echoes the module's list verbatim — no trimming, no
    re-ordering, no de-duplication. Dropping a candidate would be the agent
    narrowing a choice it is not the one making, which is exactly the
    "confident wrong file" failure §3.5 exists to prevent, moved one layer
    up. The cap is the resolver's (five candidates), not this one's."""
    options = ["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"]
    deps, _knowledge, _llm = make_deps(clarification_options=options)
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize it")

    lines = events[-1].data["text"].splitlines()
    assert lines[1:] == [f"- {name}" for name in options]


async def test_the_clarification_carries_no_corpus_header_and_fetches_none() -> None:
    """س-23 = ج puts the corpus header on the two ANSWERING paths. This one
    asks a question about files it has already named, so listing the whole
    workspace underneath would bury the very choice it wants made — and the
    listing is not even fetched."""
    deps, knowledge, _llm = make_deps(
        clarification_options=["a.pdf", "b.pdf"], document_names=["a.pdf", "b.pdf", "z.pdf"]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize it")

    assert "z.pdf" not in events[-1].data["text"]
    assert knowledge.name_limit_calls == []


async def test_a_content_route_answer_takes_the_normal_synthesis_path() -> None:
    """The other route is the pre-existing behaviour, unchanged: routed chunks
    feed the same prompt, the same stream and the same citations `retrieve`'s
    did."""
    deps, _knowledge, llm = make_deps(
        deltas=["Paris"], chunks=[FakeChunk("c1", "Paris is the capital of France.")]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert len(llm.stream_calls) == 1
    assert "Paris is the capital of France." in llm.stream_calls[0][0][0].content
    assert events[-1].data["citations"] == [
        {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "c1", "rank": 1}
    ]


# --------------------------------------------------------------------------- #
# The structured answer measurements (plan §3.11/§4 row 17, `P-29`, س-25 = أ) #
# --------------------------------------------------------------------------- #
def _answer_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    """The ONE ``rag_agent.answer`` record a turn emits -- the count is part
    of the assertion: a second record would mean two exits ran, and none
    would mean a path measures nothing."""
    records = [record for record in caplog.records if record.getMessage() == "rag_agent.answer"]
    assert len(records) == 1
    return records[0]


async def test_the_synthesis_path_measures_the_llm_and_the_whole_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retrieval plan §3.11's four measurements, on the path that has all of
    them: ``llm_ms`` is a real duration (the provider stream ran),
    ``context_nodes`` is what the model was actually given, and ``total_ms``
    contains ``llm_ms`` because the turn contains the stream."""
    deps, _knowledge, _llm = make_deps(
        deltas=["Paris", " is", " the capital."],
        chunks=[FakeChunk("c1", "Paris is the capital of France.")],
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    record = _answer_record(caplog)
    assert record.path == "synthesis"
    assert record.retrieval_attempted is True
    assert record.context_nodes == 1
    assert record.fallback is False
    assert isinstance(record.llm_ms, int)
    assert record.llm_ms >= 0
    assert record.total_ms >= record.llm_ms


async def test_the_trust_gate_is_the_one_path_that_logs_fallback_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The plan's ``fallback`` measurement means the trust gate fired (plan
    §3.3/§4 row 5, ``P-33``) -- and its companion is a ``null`` ``llm_ms``,
    because that branch never calls the model. A `0` there would claim an
    instantaneous provider instead of an absent one."""
    deps, _knowledge, llm = make_deps(chunks=[])
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "anything at all?")

    record = _answer_record(caplog)
    assert record.path == "fallback"
    assert record.fallback is True
    assert record.retrieval_attempted is True
    assert record.context_nodes == 0
    assert record.llm_ms is None
    assert llm.stream_calls == []  # the `null` is the truth, not an omission


async def test_a_turn_with_no_knowledge_seam_logs_an_unattempted_retrieval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``retrieval_attempted`` separates the two ways a turn can carry no
    context: nothing was ever asked (the optional-degrading mode), versus a
    real retrieval that came back empty. Both show ``context_nodes == 0``, and
    only the second is a ``fallback``."""
    llm = FakeLLM(["ok"])
    deps = AgentDependencies(llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"))
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "hi")

    record = _answer_record(caplog)
    assert record.path == "synthesis"
    assert record.retrieval_attempted is False
    assert record.fallback is False
    assert record.context_nodes == 0


@pytest.mark.parametrize(
    ("kwargs", "expected_path"),
    [
        ({"summary_job_id": "job-1"}, "summary_receipt"),
        ({"clarification_options": ("a.pdf", "b.pdf")}, "clarification"),
    ],
)
async def test_the_two_no_llm_routes_name_themselves_and_report_a_null_llm_ms(
    kwargs: dict[str, object], expected_path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Three of ``run``'s four exits never call the model, so ``path`` is what
    makes their ``null`` ``llm_ms`` readable -- without it a queued summary
    and a fallback would be indistinguishable in the log."""
    deps, _knowledge, llm = make_deps(**kwargs)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "لخّص الملف")

    record = _answer_record(caplog)
    assert record.path == expected_path
    assert record.llm_ms is None
    assert record.fallback is False
    assert llm.stream_calls == []


async def test_the_answer_record_carries_no_question_answer_or_document_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """10-code-standards §10: no sensitive user content in logs. Asserted on
    the RENDERED line (the exact bytes a log sink receives), not on the
    fields, so a future field cannot smuggle content past this test."""
    question = "what did the quarterly report say about the northern region?"
    answer_delta = "Revenue climbed 12% in the north."
    chunk_text = "Northern region revenue for Q3 was 4.1 million."
    deps, _knowledge, _llm = make_deps(
        deltas=[answer_delta],
        chunks=[FakeChunk("c1", chunk_text, file_name="quarterly-report.pdf")],
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), question)

    rendered = JsonFormatter().format(_answer_record(caplog))
    assert question not in rendered
    assert answer_delta not in rendered
    assert chunk_text not in rendered
    assert "quarterly-report.pdf" not in rendered


# --------------------------------------------------------------------------- #
# Guards                                                                       #
# --------------------------------------------------------------------------- #


async def test_unbound_llm_raises_500() -> None:
    deps = AgentDependencies(knowledge=FakeKnowledge([]))  # no llm
    with pytest.raises(AppError) as excinfo:
        await drive_run(RagAgent(make_ctx(), deps), "hi")
    assert excinfo.value.status == 500


@pytest.mark.parametrize("bad_text", ["", "   ", None, 123, ["x"]])
async def test_blank_or_non_string_query_is_422(bad_text: object) -> None:
    deps, _knowledge, _llm = make_deps()
    agent = RagAgent(make_ctx(), deps)
    with pytest.raises(ValidationError):
        [
            event
            async for event in agent.run(
                AgentRequest(conversation_id=None, input={"text": bad_text})
            )
        ]  # type: ignore[dict-item]


# --------------------------------------------------------------------------- #
# Discovery + lifecycle integration                                           #
# --------------------------------------------------------------------------- #


async def test_plugin_loader_registers_and_creates_rag_agent() -> None:
    registry = InMemoryAgentRegistry()
    report = PluginLoader().load_into(registry)
    assert "rag_agent" in report.loaded

    deps, _knowledge, _llm = make_deps()
    agent = registry.create("rag_agent", make_ctx(), deps)
    assert isinstance(agent, BaseAgent)
    assert agent.metadata.key == "rag_agent"


async def test_drives_cleanly_through_the_lifecycle_executor() -> None:
    deps, _knowledge, _llm = make_deps(deltas=["ok"])
    agent = RagAgent(make_ctx(), deps)
    events = [
        event
        async for event in AgentLifecycleExecutor().drive(
            agent, AgentRequest(conversation_id=None, input={"text": "hi"})
        )
    ]
    assert any(e.type == "token" for e in events)
    assert events[-1].type == "final"
    assert not any(e.type == "error" for e in events)


# --------------------------------------------------------------------------- #
# One format, two paths (retrieval plan §3.2, rows ٢ + ١٩ — `P-31`/`P-39`)    #
# --------------------------------------------------------------------------- #
# §3.2 asks for "وحدة تنسيق واحدة يتقاسمها مسار التوليف ومسار `context_text`
# الداخليّ — لا صيغتان تنحرفان". The tests below are the enforcement: they put
# the SAME chunk data through the agent's synthesis path and through the
# knowledge module's internal `context_text`, and demand the identical string.
_SHARED_CHUNK_DATA = (
    # (chunk_id, text, file_name, page_number, section)
    ("c1", "Paris is the capital of France.", "atlas.pdf", 12, "Capitals"),
    ("c2", "Lyon is the third largest city.", "atlas.pdf", None, "Cities"),
    ("c3", "An older point carries no citation fields at all.", None, None, None),
)


async def test_the_prompt_context_and_the_modules_context_text_are_the_same_string() -> None:
    """The drift guard. The agent renders its ``Context:`` block and
    ``RetrievalResult.context_text`` renders the internal one — from the same
    chunks, through the same ``format_context_block``. If either side ever
    grew a formatter, a separator or a label of its own, these two strings
    would stop matching and this test would fail. That is the whole of what
    §3.2's "one source of truth" buys, made checkable.

    Both sides are built from ``_SHARED_CHUNK_DATA``, so the inputs cannot
    disagree either — only the RENDERINGS are under test."""
    deps, _knowledge, llm = make_deps(
        chunks=[
            FakeChunk(chunk_id, text, file_name=file_name, page_number=page, section=section)
            for chunk_id, text, file_name, page, section in _SHARED_CHUNK_DATA
        ]
    )
    internal = RetrievalResult(
        chunks=[
            RetrievedChunk(
                document_id="doc-1",
                chunk_id=chunk_id,
                text=text,
                score=0.9,
                file_name=file_name,
                page_number=page,
                section=section,
            )
            for chunk_id, text, file_name, page, section in _SHARED_CHUNK_DATA
        ],
        best_dense_score=None,
        best_bm25_score=None,
    )

    await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    system_content = llm.stream_calls[0][0][0].content
    _, heading, prompt_context = system_content.partition("\n\nContext:\n")

    assert heading  # the synthesis path really did compose a context block
    assert prompt_context == internal.context_text
    # Not vacuous: the §3.2 label shape, including its two degradations, is
    # actually present in the string both sides produced.
    assert "[atlas.pdf p.12 | section: Capitals]" in prompt_context
    assert "[atlas.pdf | section: Cities]" in prompt_context
    assert "[unknown]" in prompt_context
    # §3.7 — descending then truncate: the top-ranked chunk opens the context
    # a model reads. `LongContextReorder` would put it last; it is a rejected
    # design note (§3.7, §7), never code.
    assert prompt_context.startswith("[atlas.pdf p.12 | section: Capitals]\nParis is the capital")


async def test_the_assembled_context_never_crosses_the_streaming_contract() -> None:
    """س-25 = أ: ``context_text`` is INTERNAL. The agent may send the block to
    the MODEL (system prompt), and must never emit it — or a field named for
    it — on the ``token``/``final`` stream a client reads. §7 records why: an
    assembled context exposes index structure and would need permission
    scoping of its own."""
    deps, _knowledge, _llm = make_deps(
        deltas=["Paris"],
        chunks=[FakeChunk("c1", "Paris is the capital of France.", file_name="atlas.pdf")],
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert events[-1].data["text"] == "Paris"  # only what the LLM streamed
    for event in events:
        assert "context_text" not in event.data
        rendered = str(event.data)
        assert "[atlas.pdf]" not in rendered
        assert "Paris is the capital of France." not in rendered


async def test_the_prompt_never_asks_the_model_to_reproduce_the_source_label() -> None:
    """⚠️ Regression, found live: `SYSTEM_PROMPT` must not instruct the model
    to emit the `[file p.N | section: S]` label it is SHOWN.

    `format_labeled_chunk` renders that label as its own LINE above each
    passage. An instruction to reproduce it verbatim therefore reads, to a
    small local model, as an OUTPUT TEMPLATE rather than a citation rule —
    and `gemma3:1b` answered by copying the block instead of the question,
    returning a label plus one heading line (61 characters) as a whole
    answer. Measured on the live model against the exact context that
    produced it: 24 of 40 samples copied a label under the old wording, 2 of
    40 under this one.

    The guard is deliberately about the INSTRUCTION, not the label: §3.2's
    format (`P-31`) is untouched and `format_labeled_chunk` still owns it.
    What the prompt may not do is tell the model to echo it back."""
    prompt = agent_module.SYSTEM_PROMPT.lower()

    # The bracketed label shape must not appear as something to reproduce.
    assert "[file p.n" not in prompt
    assert "label already shown" not in prompt
    # Row 7 (`P-37`) still stands: the source is still cited, by FIELD name.
    assert "file" in prompt
    assert "page" in prompt
    assert "section" in prompt
    # And the failure itself is refused outright.
    assert "never reply with a source label alone" in prompt


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-1 — the empty-completion guard (خطة الفجوات §3، ف-4، س-8)       #
# --------------------------------------------------------------------------- #


async def test_an_empty_completion_falls_back_instead_of_emitting_empty_text() -> None:
    """The bug the user SEES: the provider streams nothing, the `final` carries
    `text: ""`, and the orchestrator — finding neither text nor attachment —
    serialises the whole payload into the thread as raw JSON.

    An empty reply is not a different shape of answer; it is the absence of
    one. It is treated exactly as "no chunks": the same honest sentence the
    trust gate would have used."""
    deps, _knowledge, llm = make_deps(
        deltas=[], chunks=[FakeChunk("c1", "Paris is the capital of France.")]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert len(llm.stream_calls) == 1  # the model WAS called; it said nothing
    assert [e.type for e in events] == ["token", "final"]
    final = events[-1]
    assert final.data["text"].strip()
    assert "enough information" in final.data["text"]
    assert events[0].data["delta"] == final.data["text"]


async def test_an_empty_completion_carries_no_citations() -> None:
    """An answer that was never written rests on nothing. Showing the five
    sources the retrieval found beneath an apology tells the user the apology
    is sourced — which is the trust failure this agent's whole citation
    surface exists to prevent, arriving from the other end."""
    deps, _knowledge, _llm = make_deps(
        deltas=[], chunks=[FakeChunk("c1", "text", file_name="a.pdf", page_number=3)]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert events[-1].data["citations"] == []


async def test_a_whitespace_only_completion_is_treated_as_empty() -> None:
    """`strip()`, not `if not text`: a reply of blanks reaches the
    orchestrator's JSON fallback by exactly the route an empty one does, and
    reads to a human as exactly the same nothing."""
    deps, _knowledge, _llm = make_deps(
        deltas=["   ", "\n "], chunks=[FakeChunk("c1", "Paris is the capital.")]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    final = events[-1]
    assert "enough information" in final.data["text"]
    assert final.data["citations"] == []


async def test_an_empty_completion_logs_its_own_path_with_a_measured_llm_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its own `path`, and the ONE combination no other exit produces: a real
    `llm_ms` on a turn that answered nothing. The model was called and did
    spend that time — which is exactly what makes this case countable instead
    of hiding inside `synthesis`.

    `fallback` stays `False`: that flag measures the trust gate firing on zero
    chunks, and this turn HAD chunks. The path name is what separates them."""
    deps, _knowledge, _llm = make_deps(deltas=[], chunks=[FakeChunk("c1", "text")])
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    record = _answer_record(caplog)
    assert record.path == "empty_completion"
    assert isinstance(record.llm_ms, int)
    assert record.llm_ms >= 0
    assert record.fallback is False
    assert record.context_nodes == 1


async def test_an_empty_completion_never_reaches_the_orchestrators_json_fallback() -> None:
    """The acceptance criterion, asserted against the actual code that produced
    the symptom: `_turn_content` is what writes a conversation message out of a
    `final` payload, and its JSON branch is correct and deliberate for the
    MEDIA agents (whose `final` is structured and carries no text at all).

    This agent's payload is textual, so reaching that branch is the fault. No
    input may take it there."""
    deps, _knowledge, _llm = make_deps(deltas=[], chunks=[FakeChunk("c1", "text")])
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    text, attachments = _turn_content(events[-1].data)
    assert attachments == ()
    assert text == events[-1].data["text"]
    assert not text.lstrip().startswith("{")
    assert "citations" not in text


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-2 — the corpus-listing guard (خطة الفجوات §3، ف-10، س-27)       #
# --------------------------------------------------------------------------- #


async def test_a_failing_corpus_listing_does_not_sink_a_good_answer() -> None:
    """The listing runs BEFORE synthesis and had no guard, so a transient fault
    in the file seam threw away a retrieval that had already succeeded and
    already been paid for — for the sake of a header whose only job is to
    phrase the answer better."""
    deps, knowledge, llm = make_deps(
        deltas=["Paris"],
        chunks=[FakeChunk("c1", "Paris is the capital of France.")],
        document_names=["a.pdf", "b.pdf"],
        names_error=RuntimeError("document store unreachable"),
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert knowledge.name_limit_calls == [None]  # it was attempted...
    assert len(llm.stream_calls) == 1  # ...and the turn carried on regardless
    assert events[-1].data["text"] == "Paris"
    assert events[-1].data["citations"] != []


async def test_a_failing_corpus_listing_degrades_the_header_to_absent() -> None:
    """`corpus_header = None` is a state both call sites already knew, so the
    failure degrades to what this turn looked like BEFORE the header existed —
    not to some new third shape. Asserted on the composed system message: the
    prompt resumes at `Context:` with nothing spliced between."""
    deps, _knowledge, llm = make_deps(
        deltas=["Paris"],
        chunks=[FakeChunk("c1", "Paris is the capital of France.")],
        # A name `SYSTEM_PROMPT` itself cannot contain: its citation example
        # mentions `criteria.pdf`, and asserting on `a.pdf` would have been an
        # assertion about that example rather than about the header.
        document_names=["budget-2025.xlsx"],
        names_error=RuntimeError("document store unreachable"),
    )
    await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    system = llm.stream_calls[0][0][0].content
    assert system.startswith(f"{agent_module.SYSTEM_PROMPT}\n\nContext:\n")
    assert "Files in this space:" not in system
    assert "budget-2025.xlsx" not in system


async def test_a_failing_corpus_listing_on_the_fallback_path_still_apologises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other call site. Zero chunks AND a failing listing is the worst
    combination available, and it still ends in the honest sentence — bare,
    without the header it could not build — rather than in an error event."""
    deps, _knowledge, llm = make_deps(
        chunks=[], document_names=["a.pdf"], names_error=RuntimeError("store down")
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "what is in the report?")

    assert llm.stream_calls == []
    assert [e.type for e in events] == ["token", "final"]
    assert "enough information" in events[-1].data["text"]
    assert "Files in this space:" not in events[-1].data["text"]
    assert _answer_record(caplog).path == "fallback"


async def test_a_failing_retrieval_still_fails_the_turn() -> None:
    """**The containing guard.** The wrap is on the corpus listing and NOWHERE
    else. `answer` stays bare deliberately (س-28): with no context there is no
    answer, and absorbing a retrieval failure here would produce a confident
    reply from the model's own parametric knowledge with no citations —
    precisely what the trust gate was built to prevent."""
    boom = RuntimeError("vector store unreachable")
    deps, _knowledge, llm = make_deps(deltas=["Paris"], answer_error=boom)

    with pytest.raises(RuntimeError) as excinfo:
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert excinfo.value is boom
    assert llm.stream_calls == []


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-3 — summarisation with no known target (§3، ف-5، س-15)         #
# --------------------------------------------------------------------------- #


async def test_a_targetless_summarisation_asks_which_file_instead_of_apologising() -> None:
    """«لخّص لي هذا» — classified as a summarisation correctly, matching no
    file name — used to fall SILENTLY through to the content route, retrieve
    nothing and end in «لا أملك معلومات كافية». The most natural phrasing a
    user can reach for produced the least useful answer the agent has.

    The honest reply is the QUESTION. The agent knows exactly what was asked
    and is missing exactly one name."""
    deps, _knowledge, _llm = make_deps(
        chunks=[], routed_intent="summarize_doc", document_names=["الميزانية.pdf"]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي هذا")

    text = events[-1].data["text"]
    assert text.startswith("أيّ ملفّ تريد تلخيصه؟")
    assert "لا أملك معلومات كافية" not in text
    assert events[-1].data["citations"] == []


async def test_a_targetless_summarisation_lists_the_space_corpus() -> None:
    """The header IS attached here, unlike on the receipt and clarification
    branches, and their reason for withholding it does not apply: those two
    already name files, this one names none, and the space's listing is the
    only material that turns the question into something the user can answer.
    It is the menu, not decoration."""
    deps, knowledge, _llm = make_deps(
        chunks=[], routed_intent="summarize_doc", document_names=["a.pdf", "b.pdf"]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarise this for me")

    text = events[-1].data["text"]
    assert text.startswith("Which file would you like me to summarise?")
    assert "Files in this space: a.pdf, b.pdf." in text
    assert knowledge.name_limit_calls == [None]


async def test_a_targetless_summarisation_never_calls_the_llm() -> None:
    """Every word of the reply is a fixed sentence plus the module's own list,
    so there is nothing for a model to improvise — and a model asked to phrase
    it could drop, merge or invent a file name, which is the failure this
    branch's neighbours already exist to prevent."""
    deps, _knowledge, llm = make_deps(chunks=[], routed_intent="summarize_doc")
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي هذا")

    assert llm.stream_calls == []
    assert [e.type for e in events] == ["token", "final"]
    assert events[0].data["delta"] == events[-1].data["text"]


async def test_a_targetless_summarisation_logs_its_own_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its own name in the log, for the reason the other no-LLM exits have
    theirs: folded into `fallback` this case would be invisible, and its whole
    problem was that it was invisible — an apology that looked like every other
    apology."""
    deps, _knowledge, _llm = make_deps(chunks=[], routed_intent="summarize_doc")
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "لخّص لي هذا")

    record = _answer_record(caplog)
    assert record.path == "summary_target_unknown"
    assert record.llm_ms is None
    assert record.fallback is False
    assert record.context_nodes == 0


async def test_a_content_intent_with_zero_chunks_still_apologises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """**The containing guard.** The fifth branch keys on the INTENT, not on
    "zero chunks", so the trust-gate fallback — the path every ordinary
    unanswerable question takes — is untouched by it."""
    deps, _knowledge, llm = make_deps(chunks=[])
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "what is the capital of France?")

    assert llm.stream_calls == []
    assert "enough information" in events[-1].data["text"]
    assert "Which file" not in events[-1].data["text"]
    assert _answer_record(caplog).path == "fallback"


def test_the_agents_intent_literal_matches_the_modules_enum() -> None:
    """**The drift test.** ق-1 forbids this agent from importing `Intent`, so
    the value is copied as a string literal — which is exactly the widening
    `RoutedAnswerView` was documented for ("`intent` is `str` here and a
    `StrEnum` there").

    A copy across an architectural boundary is only safe while something
    compares the two, and the TEST layer is the one layer allowed to import
    both sides. Renaming the enum's value without this would leave the fifth
    branch silently unreachable — the same silence ب-3 was written to end."""
    assert Intent.SUMMARIZE_DOC.value == agent_module._INTENT_SUMMARIZE_DOC


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-4أ — a refused summary build (§3، ف-7، س-17)                   #
# --------------------------------------------------------------------------- #


async def test_a_summary_conflict_becomes_a_sentence_not_an_error_event() -> None:
    """`RequestSummary` refuses a second build for a key one is already
    running. That refusal used to travel up untranslated and reach the user as
    a technical error event — for asking twice.

    ⚠️ The sentence is deliberately NEUTRAL. The module raises
    `common.conflict` from TWO places with two meanings (an active build, and a
    document that was never indexed and holds no text at all) under ONE code,
    and this agent cannot tell them apart. "Your summary is still being
    prepared" would be a plain lie on the second.

    ب-4ب has since classified the reason inside the module, and this sentence
    is no longer what a refused build normally says — the two exact ones are.
    What this test now pins is the RESIDUE: a `ConflictError` the module did
    NOT classify still reaches the agent, and the neutral wording is still the
    only honest answer to a conflict nobody named. The `answer_error` here is
    a bare `ConflictError` precisely for that reason."""
    conflict = ConflictError("summary already running for document 0198-…")
    deps, _knowledge, _llm = make_deps(answer_error=conflict)
    events = await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    assert [e.type for e in events] == ["token", "final"]
    text = events[-1].data["text"]
    assert text.startswith("I couldn't start a summary of that file just now.")
    assert "still being prepared" not in text
    assert events[-1].data["citations"] == []


async def test_a_summary_conflict_never_calls_the_llm() -> None:
    """A fixed sentence, like every other no-LLM exit — and picked by the same
    Arabic-script presence check the fallback, the receipt, the clarification
    and the corpus header all use. One language mechanism in this agent."""
    deps, _knowledge, llm = make_deps(answer_error=ConflictError("in progress"))
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي ملف الميزانية")

    assert llm.stream_calls == []
    assert events[-1].data["text"].startswith("تعذّر بدءُ تلخيص هذا الملفّ الآن.")


async def test_a_non_conflict_failure_still_fails_the_turn() -> None:
    """**The containing guard.** `ConflictError` ALONE, never the general
    `AppError`: a broken store is not a message for a user, and a
    `NotFoundError` on a deleted document is a different state deserving a
    different sentence — not this one, and not silence."""
    boom = AppError(detail="store exploded", code="common.internal", status=500)
    deps, _knowledge, _llm = make_deps(answer_error=boom)

    with pytest.raises(AppError) as excinfo:
        await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    assert excinfo.value is boom


async def test_a_summary_conflict_logs_its_own_path(caplog: pytest.LogCaptureFixture) -> None:
    """Without its own path the case would vanish from the measurements
    entirely — it is neither an error any more nor any of the four answers."""
    deps, _knowledge, _llm = make_deps(answer_error=ConflictError("in progress"))
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    record = _answer_record(caplog)
    assert record.path == "summary_conflict"
    assert record.llm_ms is None
    assert record.error_type is None  # it is an ANSWER now, not a failure


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-5 — the error path measures itself (§3، ف-14، س-33)            #
# --------------------------------------------------------------------------- #


async def test_a_failing_turn_still_emits_one_answer_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`rag_agent.answer` was written from the answering exits only, so a
    failed turn produced no record at all: the failure rate had to be inferred
    from the ABSENCE of lines rather than read from their presence, and the
    reason was nowhere."""
    deps, _knowledge, _llm = make_deps(answer_error=RuntimeError("vector store unreachable"))
    with (
        caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"),
        pytest.raises(RuntimeError),
    ):
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    record = _answer_record(caplog)
    assert record.path == "error"
    assert record.retrieval_attempted is True
    assert record.llm_ms is None
    assert record.total_ms >= 0


async def test_the_error_record_names_the_exception_class_and_no_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ق-5 — the CLASS name, never the message. `ConflictError`'s own message
    carries a document id, and other exceptions carry fragments of the input,
    so a record that logged `str(exc)` would be a content leak wearing a
    diagnostics costume. Asserted on the RENDERED line, so a future field
    cannot smuggle it past."""
    deps, _knowledge, _llm = make_deps(
        answer_error=RuntimeError("document 'quarterly-report.pdf' is corrupt")
    )
    with (
        caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"),
        pytest.raises(RuntimeError),
    ):
        await drive_run(RagAgent(make_ctx(), deps), "what did the report say?")

    record = _answer_record(caplog)
    assert record.error_type == "RuntimeError"
    rendered = JsonFormatter().format(record)
    assert "quarterly-report.pdf" not in rendered
    assert "what did the report say?" not in rendered


async def test_an_abandoned_stream_logs_no_record(caplog: pytest.LogCaptureFixture) -> None:
    """**The whole reason this is `except Exception` and not `finally`.** A
    reader that walks away mid-answer closes the generator, which raises
    `GeneratorExit` — a `BaseException` — at the suspended `yield`. A `finally`
    would have logged a turn that never finished, at whatever moment the event
    loop happened to close it; `except Exception` does not catch it, so the
    guarantee `_log_answer` documents survives verbatim."""
    deps, _knowledge, _llm = make_deps(
        deltas=["Paris", " is", " the capital."], chunks=[FakeChunk("c1", "text")]
    )
    agent = RagAgent(make_ctx(), deps)
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = agent.run(AgentRequest(conversation_id=None, input={"text": "capital?"}))
        first = await anext(events)
        assert first.type == "token"
        await events.aclose()

    assert [r for r in caplog.records if r.getMessage() == "rag_agent.answer"] == []


async def test_a_failing_turn_re_raises_for_the_lifecycle_executor() -> None:
    """The item is MEASUREMENT, not handling: the record is a side effect and
    the exception continues on its way to the 4.2 executor, which owns the
    error event. Swallowing it would turn a fault into a silence — the very
    thing this item exists to end."""
    boom = RuntimeError("vector store unreachable")
    deps, _knowledge, _llm = make_deps(answer_error=boom)

    with pytest.raises(RuntimeError) as excinfo:
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert excinfo.value is boom


async def test_a_blank_query_is_measured_too(caplog: pytest.LogCaptureFixture) -> None:
    """A turn that fails BEFORE it can ask anything still gets its record, and
    `retrieval_attempted` reports `False` because that is the truth — nothing
    was searched — not because the field was never reached."""
    deps, _knowledge, _llm = make_deps()
    with (
        caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"),
        pytest.raises(ValidationError),
    ):
        await drive_run(RagAgent(make_ctx(), deps), "   ")

    record = _answer_record(caplog)
    assert record.path == "error"
    assert record.error_type == "ValidationError"
    assert record.retrieval_attempted is False


# --------------------------------------------------------------------------- #
# الموجة 1 · ب-6 — a turn with no space is lit up (§3، ف-9، س-26)             #
# --------------------------------------------------------------------------- #


def _unscoped_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "rag_agent.unscoped_turn"]


async def test_a_turn_without_a_space_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    """The degraded path is SAFE (ق-7: an unknown boundary is stayed inside,
    never widened) and it was also completely silent — the model answers alone,
    with no retrieval, no citations and no header, which is the shape of answer
    the trust gate exists to prevent arriving through a different door.

    A warning, not a refusal: ق-أ records why. Refusing is a visible behaviour
    change on a path nobody has measured, so the order is illuminate → measure
    → decide."""
    deps, knowledge, llm = make_deps(space_id=None, deltas=["Paris"])
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    assert len(_unscoped_warnings(caplog)) == 1
    assert knowledge.calls == []  # ق-7 holds: nothing was searched
    assert len(llm.stream_calls) == 1  # and the turn still answered
    assert events[-1].data["citations"] == []


async def test_a_turn_without_a_knowledge_seam_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The distinction the warning exists to draw. `retrieval_attempted=False`
    covers BOTH "no seam wired at all" and "a seam wired but no space known",
    and they are two different diagnoses: the first is a deployment answering
    from the model on purpose, the second is a turn that lost its thread."""
    llm = FakeLLM(["ok"])
    deps = AgentDependencies(llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"))
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "hi")

    assert _unscoped_warnings(caplog) == []


async def test_the_unscoped_warning_carries_no_question_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ق-5 applies to this line as much as to the answer record: the
    `conversation_id` is an IDENTIFIER, which is what makes one warning
    traceable to one thread without putting a single word of the question into
    a log."""
    question = "what did the quarterly report say about the northern region?"
    thread = new_uuid7()
    deps, _knowledge, _llm = make_deps(space_id=None, deltas=["ok"])
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), question, conversation_id=thread)

    rendered = JsonFormatter().format(_unscoped_warnings(caplog)[0])
    assert question not in rendered
    assert thread in rendered


# --------------------------------------------------------------------------- #
# الموجة 3 · ب-4ب — the refusal, classified (§5، ف-7، ت-3)                    #
# --------------------------------------------------------------------------- #


async def test_an_active_build_is_reported_as_in_progress_not_as_an_error() -> None:
    """The benign refusal, said benignly: the summary IS coming, and the
    sentence says where it will arrive.

    This is what the neutral ب-4أ wording could not commit to, because it had
    to stay true of the other refusal as well."""
    deps, _knowledge, _llm = make_deps(summary_blocked="in_progress")
    events = await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    assert [e.type for e in events] == ["token", "final"]
    text = events[-1].data["text"]
    assert text == (
        "A summary of that file is still being prepared. It will reach you in "
        "this conversation when it is ready."
    )
    assert events[-1].data["citations"] == []


async def test_an_unindexed_document_is_not_reported_as_in_progress() -> None:
    """**The guard this item was born for** (ت-3), on the agent's side.

    The study's original advice was to catch the conflict here and say «ما زال
    قيد الإعداد». That is a flat lie about a document with no indexed text:
    nothing is being prepared, nothing will be, and a user told to wait waits
    forever. The assertions are negative as well as positive — the sentence
    must be the right one AND must not contain the promise the other one
    makes."""
    deps, _knowledge, _llm = make_deps(summary_blocked="not_indexed")
    events = await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    text = events[-1].data["text"]
    assert text == (
        "That file has not finished being indexed yet, so there is no text in it to summarise."
    )
    assert "still being prepared" not in text
    assert "will reach you" not in text


@pytest.mark.parametrize(
    ("reason", "query", "expected"),
    [
        (
            "in_progress",
            "summarise the budget file",
            "A summary of that file is still being prepared.",
        ),
        ("in_progress", "لخّص لي ملف الميزانية", "ما زال ملخّص هذا الملفّ قيد الإعداد."),
        (
            "not_indexed",
            "summarise the budget file",
            "That file has not finished being indexed yet,",
        ),
        ("not_indexed", "لخّص لي ملف الميزانية", "هذا الملفّ لم تكتمل فهرستُه بعد،"),
    ],
)
async def test_the_agent_renders_each_blocked_reason_in_both_languages(
    reason: str, query: str, expected: str
) -> None:
    """Four sentences, picked by the SAME `_ARABIC_CHAR_RE` presence check the
    fallback, the receipt, the clarification and the corpus header all use.
    One language mechanism in this agent, never a second one."""
    deps, _knowledge, llm = make_deps(summary_blocked=reason)
    events = await drive_run(RagAgent(make_ctx(), deps), query)

    assert events[-1].data["text"].startswith(expected)
    assert llm.stream_calls == []


async def test_a_blocked_summary_names_the_file_the_module_named() -> None:
    """ب-4ب meets ب-7أ. The name arrives across the seam already resolved, so
    uttering it repeats the module's decision rather than asserting one — the
    same licence the receipt has.

    A refusal is where the name earns the most: «تعذّر البدء» about no file in
    particular leaves a user who pinned one document and asked about another
    with no way to see which one was refused."""
    name = "التقرير الشمالي.pdf"
    deps, _knowledge, _llm = make_deps(summary_blocked="in_progress", summary_target_name=name)
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    text = events[-1].data["text"]
    assert text == f"ما زال ملخّص «{name}» قيد الإعداد. سيصلك في هذه المحادثة عند اكتماله."


async def test_the_blocked_sentence_never_invents_a_name_the_module_did_not_send() -> None:
    """**The containing guard**, and the receipt's guard restated for this
    branch. The rule is the agent's: never echo a name you did not resolve.

    The question names a file outright; the module sends no name. The answer
    must fall back to the unnamed wording rather than lift the filename off
    the query — an agent that repeats the user's phrasing as a resolved
    filename is asserting a resolution nobody performed."""
    deps, _knowledge, _llm = make_deps(summary_blocked="not_indexed")
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize budget-2025.xlsx")

    text = events[-1].data["text"]
    assert "budget-2025.xlsx" not in text
    assert text.startswith("That file has not finished being indexed yet,")


async def test_a_blocked_summary_never_calls_the_llm() -> None:
    """A fact about a build, not an answer to synthesise — the receipt
    branch's reason exactly. And no corpus header either: this answer already
    concerns one named file, and the space's listing would answer a question
    nobody asked."""
    deps, knowledge, llm = make_deps(
        summary_blocked="in_progress", document_names=["a.pdf", "b.pdf"]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    assert llm.stream_calls == []
    assert knowledge.name_limit_calls == []
    assert "a.pdf" not in events[-1].data["text"]


@pytest.mark.parametrize(
    ("reason", "path"),
    [
        ("in_progress", "summary_blocked_in_progress"),
        ("not_indexed", "summary_blocked_not_indexed"),
        # ب-10 (خطة السيناريوهات §7، ف-7) — the third refusal gets a third
        # path for the reason the first two got separate ones: folding "this
        # workspace is at its ceiling" in with "this file is already being
        # built" would leave the number unable to tell one tenant asking too
        # fast from one document being busy.
        ("workspace_busy", "summary_blocked_workspace_busy"),
    ],
)
async def test_a_blocked_summary_logs_its_reason_in_the_answer_record(
    reason: str, path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Two paths, not one path with a flag — and here the argument is
    unusually literal.

    `summary_conflict` counting both refusals under one name IS gap ف-7
    reproduced in the measurement (open item م-2). Folding the classified
    cases back into it would have fixed the sentence a user reads and left the
    number that says how often each case happens exactly as blind as it was.
    """
    deps, _knowledge, _llm = make_deps(summary_blocked=reason)
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    record = _answer_record(caplog)
    assert record.path == path
    assert record.llm_ms is None
    assert record.error_type is None  # an ANSWER, not a failure


async def test_a_blocked_summary_is_not_answered_as_a_targetless_one() -> None:
    """**The containing guard** against ب-3 swallowing this branch.

    A refused answer looks exactly like a targetless one from the outside —
    intent `summarize_doc`, no job id, no candidates — so before the blocked
    branch existed this turn would have been answered «Which file would you
    like me to summarise?» about the one file the module had just named."""
    deps, knowledge, _llm = make_deps(
        summary_blocked="not_indexed",
        summary_target_name="التقرير الشمالي.pdf",
        document_names=["a.pdf"],
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    text = events[-1].data["text"]
    assert "أيّ ملفّ تريد تلخيصه؟" not in text
    assert text.startswith("الملفّ «التقرير الشمالي.pdf» لم تكتمل فهرستُه بعد،")
    # The corpus listing is the targetless branch's answer, and it is not
    # fetched here at all.
    assert knowledge.name_limit_calls == []


async def test_an_unrecognised_block_reason_falls_back_to_the_neutral_sentence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The forward-compatibility guard, and the reason `summary_blocked`
    crosses the seam as a `str` rather than as an imported enum.

    A module that grows a THIRD refusal must not break an agent that predates
    it. The unknown reason degrades to the ب-4أ sentence — true of every
    conflict, including ones this file has never heard of — rather than
    raising or rendering a blank."""
    deps, _knowledge, llm = make_deps(summary_blocked="some_future_reason")
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "summarise the budget file")

    assert llm.stream_calls == []
    assert events[-1].data["text"].startswith("I couldn't start a summary of that file just now.")
    # And it lands on `summary_conflict` — the SAME path an unclassified
    # `ConflictError` lands on. The two ways a refusal can arrive unnamed
    # count as one thing, which is what makes that number readable.
    #
    # ⚠️ It must NOT reach the trust-gate fallback. That was this method's
    # first shape and it answered «I don't have enough information» to a
    # refused summary while logging `fallback=True` — inflating §3.11's
    # measurement of the gate firing with turns where retrieval never ran.
    record = _answer_record(caplog)
    assert record.path == "summary_conflict"
    assert record.fallback is False


async def test_an_unrecognised_reason_still_names_the_file_when_the_module_did() -> None:
    """The name and the reason cross the seam SEPARATELY, so one being
    unreadable says nothing about the other.

    Withholding a perfectly good name because the reason beside it was
    unrecognised would lose the one thing that makes «تعذّر البدء» actionable,
    for no reason but the shape of the fallback."""
    deps, _knowledge, _llm = make_deps(
        summary_blocked="some_future_reason", summary_target_name="التقرير الشمالي.pdf"
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["text"] == (
        "تعذّر بدءُ تلخيص «التقرير الشمالي.pdf» الآن."
        " إن كان تلخيصٌ له قيد الإعداد فسيصلك في هذه المحادثة عند اكتماله."
    )


def test_the_agents_blocked_literals_match_the_modules_enum() -> None:
    """**The drift test**, on `_INTENT_SUMMARIZE_DOC`'s exact model. ق-1
    forbids this agent from importing `SummaryBlocked`, so the values are
    copied as string literals — and a copy across an architectural boundary is
    only safe while something compares the two.

    Both directions matter. A renamed enum value would leave the branch
    silently unreachable; a member with no entry in the agent's table would
    fall to the neutral sentence forever, which is ف-7 quietly restored."""
    assert {reason.value for reason in SummaryBlocked} == set(agent_module._SUMMARY_BLOCKED_REASONS)
    assert SummaryBlocked.IN_PROGRESS.value == agent_module._BLOCKED_IN_PROGRESS
    assert SummaryBlocked.NOT_INDEXED.value == agent_module._BLOCKED_NOT_INDEXED


# --------------------------------------------------------------------------- #
# ب-8 — الملخّصُ المخزَّن يُقرأ في مسار الدردشة (خطة السيناريوهات §6، ف-3)        #
# --------------------------------------------------------------------------- #

# What the module hands over on that branch: a whole summary, already framed by
# `delivered_summary_text` — header, body, and any truncation notice — because
# the composition happened on the module side and this agent adds nothing.
_STORED_SUMMARY_AR = "\n\n".join(
    ("ملخّص الملفّ «التقرير الشمالي.pdf»:", "الإيرادات ارتفعت بنسبة ١٢٪.")
)


async def test_a_stored_summary_is_answered_in_the_turn_that_asked_for_it() -> None:
    """**The item** (ف-3). The summary exists, so the answer IS the summary.

    Before this branch the same turn produced «بدأت العمل عليه» — a receipt for
    a map-reduce over a document that had already been summarised. The user
    waited minutes, and the workspace paid twice, for text that was sitting in
    the store.
    """
    deps, _knowledge, _llm = make_deps(stored_summary_text=_STORED_SUMMARY_AR)

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["text"] == _STORED_SUMMARY_AR
    # Not the receipt. That sentence promises an arrival, and nothing is
    # arriving — this turn already delivered.
    assert "بدأت العمل" not in events[-1].data["text"]


async def test_a_stored_summary_is_emitted_exactly_as_the_module_delivered_it() -> None:
    """The text arrives ALREADY framed and leaves untouched.

    `delivered_summary_text` composed the header and the truncation notice on
    the module side, on purpose: a summary read out of the store must reach a
    thread looking exactly like one a worker just finished putting there. An
    agent that prefixed, suffixed or re-wrapped it would be the second of two
    deliveries of one artefact, and the two would drift.

    Byte for byte, and asserted as equality rather than containment — the
    difference is the whole claim.
    """
    truncated = "\n\n".join((_STORED_SUMMARY_AR, "⚠️ هذا الملخّص يغطّي جزءاً من المستند."))
    deps, _knowledge, _llm = make_deps(stored_summary_text=truncated)

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["text"] == truncated


async def test_a_stored_summary_never_calls_the_llm() -> None:
    """The strongest instance of the "no model on a fixed reply" rule, because
    here a model would have had something to say.

    The summary is a finished artefact; asking one to re-word it would spend
    tokens to make it less faithful — and would put prose no summariser wrote
    into an answer the user will read as the summary.
    """
    deps, _knowledge, llm = make_deps(stored_summary_text=_STORED_SUMMARY_AR)

    await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert llm.stream_calls == []


async def test_a_stored_summary_cites_nothing() -> None:
    """Decision 4, first half. This text came from a stored summary, not from
    retrieved chunks — there are no chunk ids behind it to point at, and
    citations invented for it would attribute a paragraph to a passage nobody
    retrieved."""
    deps, _knowledge, _llm = make_deps(
        stored_summary_text=_STORED_SUMMARY_AR, chunks=[FakeChunk("c1", "text")]
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["citations"] == []


async def test_a_stored_summary_logs_its_own_path(caplog: pytest.LogCaptureFixture) -> None:
    """Decision 4, second half — and the item's own case for itself.

    Every turn on `summary_cached` is a map-reduce that did NOT run. Folded
    into `summary_receipt` the saving would be unmeasurable: the two look alike
    from outside (a summarisation, no synthesis, no citations) and mean
    opposite things — one says the work is starting, this one says the work was
    already done.
    """
    deps, _knowledge, _llm = make_deps(stored_summary_text=_STORED_SUMMARY_AR)
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    record = _answer_record(caplog)
    assert record.path == "summary_cached"
    # No model was called, so there is no duration to report — and the path is
    # what makes that `None` readable rather than a provider that answered
    # instantaneously.
    assert record.llm_ms is None
    # An ANSWER, and the most complete one this agent gives without a model.
    assert record.error_type is None
    assert record.fallback is False


async def test_a_stored_summary_carries_no_corpus_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """س-23 = ج puts the header on the two ANSWERING paths, and this is not one
    of them in the sense that rule means: the header exists so «I don't know»
    is not uninformative, and this turn knows a great deal.

    Appending the workspace listing under a full summary would read as though
    the summary had not been enough. The listing is not even fetched.
    """
    deps, knowledge, _llm = make_deps(
        stored_summary_text=_STORED_SUMMARY_AR, document_names=["a.pdf", "b.pdf"]
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["text"] == _STORED_SUMMARY_AR
    assert knowledge.name_limit_calls == []


async def test_a_stored_summary_is_not_answered_as_a_targetless_one() -> None:
    """**The containing guard** against ب-3 swallowing this branch, the twin of
    ب-4ب's.

    A stored summary comes back with intent `summarize_doc`, no job id and no
    candidates — the exact shape `_is_targetless_summary` recognises. Asking
    «أيّ ملفّ تريد تلخيصه؟» under a summary of that very file would be this
    item's own failure, restored one branch later.
    """
    deps, _knowledge, _llm = make_deps(
        stored_summary_text=_STORED_SUMMARY_AR, document_names=["a.pdf"]
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert "أيّ ملفّ تريد تلخيصه؟" not in events[-1].data["text"]
    assert events[-1].data["text"] == _STORED_SUMMARY_AR


def test_a_stored_summary_excludes_the_targetless_case_structurally() -> None:
    """And the exclusion is a property of the PREDICATE, not of where the
    branches happen to sit.

    Order alone would be enough today. It stops being enough the moment
    somebody moves a branch, which is exactly the kind of edit that looks safe
    — so `_is_targetless_summary` is asked directly, with no agent around it.
    """
    routed = FakeRoutedAnswer("summarize_doc", (), None, stored_summary_text=_STORED_SUMMARY_AR)

    assert agent_module.RagAgent._is_targetless_summary(routed) is False


async def test_an_english_summary_is_delivered_in_its_own_language(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one branch with NO language decision to make, and that is the point.

    Every other fixed reply picks Arabic or English off the query
    (`_ARABIC_CHAR_RE`). This one picks nothing: the summary is written in the
    document's language (`SummaryLanguage.AUTO`), which is neither the
    question's language nor a choice this agent gets to second-guess. An
    English question about an Arabic report is not a request for a translation.
    """
    english = "Summary of «north-report.pdf»:\n\nRevenue rose 12%."
    deps, _knowledge, _llm = make_deps(stored_summary_text=english)
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي هذا الملف")

    assert events[-1].data["text"] == english
    assert _answer_record(caplog).path == "summary_cached"


async def test_a_stored_summary_still_streams_the_two_events_every_answer_owes() -> None:
    """ق-4: the streaming contract is untouched. A seventh fixed reply is a
    seventh `_FixedReply`, sharing the one emit site — not a new event type and
    not a new shape on `final`."""
    deps, _knowledge, _llm = make_deps(stored_summary_text=_STORED_SUMMARY_AR)

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert [event.type for event in events] == ["token", "final"]
    assert events[0].data["delta"] == _STORED_SUMMARY_AR


# --------------------------------------------------------------------------- #
# ب-9 (خطة السيناريوهات §7، ف-1أ) — the clarification is remembered            #
# --------------------------------------------------------------------------- #
async def test_a_clarification_final_carries_its_candidates_for_the_platform() -> None:
    """The near end of the fix: the agent declares what it just asked about.

    It is a message to ONE layer — the orchestrator, which alone knows which
    thread this turn belongs to and alone holds a seam to write on it. The
    names are the module's own list, verbatim and in order, because the order
    is what «الثاني» will index on the next turn.
    """
    options = ["الميزانية 2024.pdf", "الميزانية 2025.pdf"]
    deps, _knowledge, _llm = make_deps(clarification_options=options)

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي الميزانية")

    assert events[-1].data["pending_clarification"] == options


async def test_the_clarification_event_types_are_unchanged() -> None:
    """⚠️ The containing guard. ق-4 fixes this agent's contract at `token`
    then `final` and nothing else, and ب-9 could very easily have been a third
    event type — «here are your choices, structured» is the obvious shape and
    the wrong one (س-18 put it out of scope in the retrieval plan, and §7 of
    this plan keeps it there).

    So the signal rides the payload of a frame that already exists. Both ends
    of the round trip are checked here: the turn that asks, and the turn that
    answers what it asked.
    """
    options = ["الميزانية 2024.pdf", "الميزانية 2025.pdf"]
    asking, _k1, _l1 = make_deps(clarification_options=options)
    answering, _k2, _l2 = make_deps(summary_job_id="job-1", pending=tuple(options))

    asked = await drive_run(RagAgent(make_ctx(), asking), "لخّص لي الميزانية")
    answered = await drive_run(RagAgent(make_ctx(), answering), "الثاني")

    assert [e.type for e in asked] == ["token", "final"]
    assert [e.type for e in answered] == ["token", "final"]


async def test_the_workspace_ceiling_gets_a_sentence_of_its_own() -> None:
    """**ب-10** (خطة السيناريوهات §7، ف-7) — and it is not the optional
    polish the item took it for.

    The item reasons that this agent degrades neutrally for a
    `SummaryBlocked` member it has never heard of, and it does: at RUNTIME,
    `_blocked_reason` falls to `_BLOCKED_UNCLASSIFIED`. But
    `test_the_agents_blocked_literals_match_the_modules_enum` asserts set
    equality in both directions, and says why in its own words — a member
    with no row here "would fall to the neutral sentence forever, which is
    ف-7 quietly restored". The fifth gate makes the third sentence mandatory,
    which is also what makes it right.

    Its sentence is the only one of the three that is not about the FILE:
    «قيد الإعداد» would promise this document an arrival nothing is
    preparing, and «لا نصَّ فيه» would blame a file whose text is fine.
    """
    deps, _knowledge, llm = make_deps(summary_blocked="workspace_busy")

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    text = events[-1].data["text"]
    assert text == agent_module._BLOCKED_WORKSPACE_BUSY_AR
    # Distinct from all three sentences it could have degraded into. That is
    # the property, not the wording: routing this reason to any of them says
    # something false about the file.
    assert text not in {
        agent_module._BLOCKED_IN_PROGRESS_AR,
        agent_module._BLOCKED_NOT_INDEXED_AR,
        agent_module._SUMMARY_BLOCKED_AR,
    }
    # And it names no number: three is a deployment's setting (ق-د), so a
    # sentence quoting it would be wrong wherever it was raised.
    assert "٣" not in text and "3" not in text
    assert llm.stream_calls == []


async def test_an_agent_that_does_not_know_the_third_reason_degrades_neutrally() -> None:
    """⚠️ **The containing guard** for ب-10, and it is the half of the item's
    decision 1 that IS true.

    The gate forces this repository's agent to know `workspace_busy`. It does
    not force a DEPLOYED agent that predates the module's next member to know
    that one — and the fallback is what keeps such a pair working: an
    unrecognised reason is answered with ب-4أ's neutral sentence, which is
    true of every conflict, and counted on `summary_conflict`, which is the
    number that says how often this happens.
    """
    deps, _knowledge, _llm = make_deps(summary_blocked="a_fourth_reason_not_yet_written")

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["text"] == agent_module._SUMMARY_BLOCKED_AR


async def test_the_receipt_declares_its_job_id_on_the_final_event() -> None:
    """**ب-11ج** (خطة السيناريوهات §7، ف-3). The id already crossed the seam
    and was already read here — as a BOOLEAN. `summary_job_id is not None`
    selects this branch and the value was dropped, so a turn that said «بدأت
    العمل عليه» named no build and nothing downstream could refer to the one
    it had just started.

    ق-هـ = أ, inherited without argument: the orchestrator writes it onto the
    thread and strips it before the frame is sent, so this is a message to one
    layer and not a change to what a client receives. Stopping a build is
    `POST /knowledge/summary-jobs/{id}/cancel`, which exists and works.
    """
    deps, _knowledge, _llm = make_deps(summary_job_id="job-42")

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert events[-1].data["summary_job_id"] == "job-42"
    # The prose is untouched: a user reads a sentence, and this key is for the
    # layer that stores the turn.
    assert "job-42" not in events[-1].data["text"]


async def test_no_other_reply_declares_a_job_id() -> None:
    """The key appears on exactly one of the seven fixed replies — the
    receipt. The other six have no build behind them to name, and an absent
    key says that without putting an internal name on six frames."""
    for deps, _knowledge, _llm in (
        make_deps(summary_blocked="in_progress"),
        make_deps(stored_summary_text="ملخّصٌ مخزَّن."),
        make_deps(clarification_options=["a.pdf", "b.pdf"]),
    ):
        events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي الميزانية")

        assert "summary_job_id" not in events[-1].data


async def test_the_agent_event_types_are_unchanged() -> None:
    """⚠️ **The containing guard for ب-11ج.** ق-4 fixes this agent's contract
    at `token` then `final`, and "the receipt should carry its job id" is
    exactly the shape that grows a third event type. It rides the payload of
    a frame that already exists instead, so the streaming contract is what it
    was."""
    deps, _knowledge, _llm = make_deps(summary_job_id="job-42")

    events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي التقرير الشمالي")

    assert [event.type for event in events] == ["token", "final"]


async def test_no_other_reply_declares_a_pending_clarification() -> None:
    """The key appears on exactly one of the seven fixed replies. The other
    six are ANSWERS, and an answer leaves nothing outstanding — so the absence
    of the key is what erases whatever the last turn asked."""
    for kwargs in (
        {"summary_job_id": "job-1"},
        {"stored_summary_text": "خلاصة."},
        {"summary_blocked": "in_progress"},
        {"routed_intent": "summarize_doc"},
        {"chunks": []},
    ):
        deps, _knowledge, _llm = make_deps(**kwargs)  # type: ignore[arg-type]

        events = await drive_run(RagAgent(make_ctx(), deps), "لخّص لي الميزانية")

        assert "pending_clarification" not in events[-1].data, kwargs


async def test_a_synthesised_answer_declares_no_pending_clarification() -> None:
    """And neither does the streaming path — the one reply that is not a
    `_FixedReply` at all."""
    deps, _knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])

    events = await drive_run(RagAgent(make_ctx(), deps), "what is the revenue?")

    assert "pending_clarification" not in events[-1].data


async def test_the_pending_candidates_reach_the_module_untouched() -> None:
    """The far end: what the thread remembered is carried to the module as it
    was stored. This agent does not read it, does not trim it and does not
    match against it — resolving a file name is the module's job (ق-3), and
    an agent that decided for itself which candidate a reply meant would be
    doing the one thing this whole path exists to prevent."""
    options = ("الميزانية 2024.pdf", "الميزانية 2025.pdf")
    deps, knowledge, _llm = make_deps(summary_job_id="job-1", pending=options)

    await drive_run(RagAgent(make_ctx(), deps), "الثاني")

    assert knowledge.pending == [options]


async def test_a_turn_with_nothing_pending_says_so_rather_than_omitting_it() -> None:
    """`()` is a real value on this seam, not a forgotten one — the same rule
    `space_id` and `conversation_id` state at their own call sites."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])

    await drive_run(RagAgent(make_ctx(), deps), "what is the revenue?")

    assert knowledge.pending == [()]


async def test_an_answered_clarification_is_an_ordinary_receipt() -> None:
    """What the user reads on the answering turn. Once the module has resolved
    the reply to a document there is nothing special about this turn at all:
    it is the receipt (ب-7أ), naming the file the module chose — which is what
    makes the exchange self-correcting if the wrong one was picked."""
    deps, _knowledge, _llm = make_deps(
        summary_job_id="job-1",
        summary_target_name="الميزانية 2025.pdf",
        pending=("الميزانية 2024.pdf", "الميزانية 2025.pdf"),
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "الثاني")

    assert "الميزانية 2025.pdf" in events[-1].data["text"]
    assert "pending_clarification" not in events[-1].data


# --------------------------------------------------------------------------- #
# ب-11 (خطة السيناريوهات §8، ف-6) — the retrieval confidence enters the record #
# --------------------------------------------------------------------------- #
# ت-1 corrected the review here before a line was written: the evaluation set
# EXISTS and the thresholds are calibrated and applied one layer down. So this
# item invents no threshold. What was missing was a VIEW — no record anywhere
# paired a turn's OUTCOME with the confidence retrieval had in it, so the score
# distribution over turns that ended in an apology, against turns that ended in
# an answer, could only be guessed at.
async def test_the_answer_record_carries_the_retrieval_confidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The acceptance test of §12's tracking table. Both signals land on the
    turn's own record, beside the path that says how the turn ended."""
    deps, _knowledge, _llm = make_deps(
        chunks=[FakeChunk("c1", "Paris is the capital.")],
        best_dense_score=0.81,
        best_bm25_score=12.5,
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "capital of France?")

    record = _answer_record(caplog)
    assert record.best_dense_score == 0.81
    assert record.best_bm25_score == 12.5
    assert record.path == "synthesis"


async def test_the_confidence_is_a_number_and_never_a_chunk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ق-5's guard, applied to the two fields this wave adds. A float that
    ranks a match carries no fragment of what was searched or found; the
    chunk text, the file names and the question stay out of this record as
    they always have."""
    deps, _knowledge, _llm = make_deps(
        chunks=[FakeChunk("c1", "الميزانية بلغت مليونًا", file_name="الميزانية 2025.pdf")],
        best_dense_score=0.77,
        best_bm25_score=3.0,
    )
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "كم بلغت الميزانية؟")

    record = _answer_record(caplog)
    assert isinstance(record.best_dense_score, float)
    assert isinstance(record.best_bm25_score, float)
    written = " ".join(str(value) for value in vars(record).values())
    assert "الميزانية" not in written
    assert "c1" not in written


async def test_the_fallback_records_the_confidence_it_apologised_over(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """⚠️ The most valuable row this field has. `path=fallback` with a real
    `best_dense_score` beside it is a turn that searched, found something, and
    apologised anyway — which is the pairing any re-calibration would be read
    off, and which no record could state before this wave."""
    deps, _knowledge, llm = make_deps(chunks=[], best_dense_score=0.62, best_bm25_score=8.0)
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "anything at all?")

    record = _answer_record(caplog)
    assert record.path == "fallback"
    assert record.fallback is True
    assert record.best_dense_score == 0.62
    # And the model was never asked: the gate is unchanged, only measured.
    assert llm.stream_calls == []


async def test_a_strong_retrieval_with_no_chunks_still_falls_back() -> None:
    """⚠️ The CONTAINING guard, and the one that says ب-11 added no threshold.
    A very high score with zero chunks must reach the SAME honest apology it
    reached before this wave: the gate reads `chunks`, and only `chunks`
    (ق-2). If a number ever starts deciding here, this test is what fails."""
    deps, _knowledge, llm = make_deps(chunks=[], best_dense_score=0.99, best_bm25_score=99.0)

    events = await drive_run(RagAgent(make_ctx(), deps), "anything at all?")

    assert llm.stream_calls == []
    assert events[-1].data["citations"] == []


async def test_a_summarisation_outcome_reports_no_confidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`None` and never `0.0`. A receipt is a fact about a document, not the
    result of a search — a fabricated zero here would land inside the very
    distribution the field exists to make readable."""
    deps, _knowledge, _llm = make_deps(summary_job_id="job-1")
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "لخّص تقرير الأداء")

    record = _answer_record(caplog)
    assert record.path == "summary_receipt"
    assert record.best_dense_score is None
    assert record.best_bm25_score is None


async def test_a_failed_turn_still_reports_what_retrieval_had_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reason the two scores live on `_TurnRecord` rather than on a local
    in `_answer`: ب-5's error record is emitted one frame OUT, and a turn that
    retrieved well and then broke in the model is a different fault from one
    that broke before it could ask."""

    class _ExplodingLLM(FakeLLM):
        """Retrieval succeeded; the model is what died."""

        def stream(
            self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
        ) -> AsyncIterator[LlmChunk]:
            async def gen() -> AsyncIterator[LlmChunk]:
                raise RuntimeError("the provider went away")
                yield LlmChunk(delta="")  # pragma: no cover - unreachable

            return gen()

    knowledge = FakeKnowledge([FakeChunk("c1", "body")], best_dense_score=0.9, best_bm25_score=4.0)
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=_ExplodingLLM([]), model="m", api_key="k"),
        knowledge=knowledge,
        space_id=SPACE,
    )
    with (
        caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"),
        pytest.raises(RuntimeError),
    ):
        await drive_run(RagAgent(make_ctx(), deps), "q")

    record = _answer_record(caplog)
    assert record.path == "error"
    assert record.best_dense_score == 0.9


async def test_a_turn_that_never_retrieved_reports_no_confidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No seam wired at all — the optional-degrading mode. Nothing searched,
    so there is nothing to report, and `retrieval_attempted` is what already
    tells that apart from a search that came back empty."""
    deps = AgentDependencies(llm=ResolvedLLM(provider=FakeLLM(["hi"]), model="m", api_key="k"))
    with caplog.at_level(logging.INFO, logger="app.agents.rag_agent.agent"):
        await drive_run(RagAgent(make_ctx(), deps), "q")

    record = _answer_record(caplog)
    assert record.retrieval_attempted is False
    assert record.best_dense_score is None
    assert record.best_bm25_score is None


async def test_the_confidence_never_reaches_the_streaming_contract() -> None:
    """س-25 = أ, restated for this wave: a measurement is a measurement. The
    two events a turn owes carry exactly what they carried."""
    deps, _knowledge, _llm = make_deps(
        chunks=[FakeChunk("c1", "body")], best_dense_score=0.81, best_bm25_score=12.5
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert [e.type for e in events] == ["token", "final"]
    assert set(events[-1].data) == {"text", "citations"}


# --------------------------------------------------------------------------- #
# ب-12 (خطة السيناريوهات §8، ف-12) — the first source is nameable             #
# --------------------------------------------------------------------------- #
# The citations were ALREADY ordered correctly and this agent already did not
# re-order them — both true before ف-12 and both pinned by tests older than
# it. So this item is display, and its one backend half is that the order
# stops being the only place the meaning lives.
async def test_every_citation_carries_its_rank() -> None:
    """One-based, because it is a rank a person reads and not an index a
    program dereferences."""
    deps, _knowledge, _llm = make_deps(
        chunks=[
            FakeChunk("c1", "first", file_name="a.pdf"),
            FakeChunk("c2", "second", file_name="b.pdf"),
            FakeChunk("c3", "third", file_name="c.pdf"),
        ]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert [c["rank"] for c in events[-1].data["citations"]] == [1, 2, 3]


async def test_the_rank_follows_the_module_order_and_never_a_sort() -> None:
    """The rank ENUMERATES what arrived; it does not impose an order. The
    module hands these over in descending relevance and this agent's not
    re-ordering them is pinned separately — so a rank that disagreed with the
    array's position would mean something here had started sorting."""
    deps, _knowledge, _llm = make_deps(
        chunks=[
            FakeChunk("c-weak", "weakest", file_name="z.pdf"),
            FakeChunk("c-strong", "strongest", file_name="a.pdf"),
        ]
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    citations = events[-1].data["citations"]
    assert [c["chunk_id"] for c in citations] == ["c-weak", "c-strong"]
    assert [c["rank"] for c in citations] == [1, 2]


async def test_a_reply_that_cites_nothing_carries_no_ranks() -> None:
    """The seven model-free replies cite nothing, so there is nothing to rank
    — and neither does the empty completion (ب-1), whose citations were
    dropped precisely because an answer that was never written rests on
    nothing."""
    deps, _knowledge, _llm = make_deps(
        deltas=[""], chunks=[FakeChunk("c1", "body", file_name="a.pdf")]
    )

    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    assert events[-1].data["citations"] == []
