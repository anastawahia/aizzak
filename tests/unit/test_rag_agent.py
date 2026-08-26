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
from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.observability.logging import JsonFormatter
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.modules.knowledge.application.retrieval import RetrievalResult
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
    ) -> None:
        self.intent = intent
        self.chunks = chunks
        self.summary_job_id = summary_job_id
        # Retrieval plan §3.5/§4 row 14 (`P-04`, س-18 = أ) — the file names
        # the module refused to choose between. Defaulted to empty: every
        # pre-row-14 construction above means "nothing to clarify", and
        # spelling that out at each of them would say less than it costs.
        self.clarification_options = clarification_options


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
        clarification_options: Sequence[str] = (),
    ) -> None:
        self._chunks = chunks
        self._document_names = FakeDocumentNames(document_names, document_total)
        self._summary_job_id = summary_job_id
        self._clarification_options = clarification_options
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
    ) -> FakeRoutedAnswer:
        # The scope is RECORDED, not honoured: this fake is the agent's
        # counterpart, and what the agent owes is passing the scope through
        # untouched — resolving it to documents is the knowledge module's job
        # and is tested there.
        self.calls.append((question, k, None if file_ids is None else tuple(file_ids)))
        self.spaces.append(space_id)
        if self._summary_job_id is not None:
            return FakeRoutedAnswer("summarize_doc", (), self._summary_job_id)
        if self._clarification_options:
            # Retrieval plan §4 row 14 — the honest "I did not decide"
            # answer: the intent is reported as the summarisation it was, no
            # job was queued, and no chunks came back either.
            return FakeRoutedAnswer("summarize_doc", (), None, self._clarification_options)
        return FakeRoutedAnswer("content", self._chunks, None)

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: str, limit: int | None = None
    ) -> FakeDocumentNames:
        self.name_limit_calls.append(limit)
        # س-32 — the header is space-scoped too, and it lands in the SAME log
        # the two retrieval faces write to: the decision is that one turn reads
        # one space, so a header taken from a different space than the answer
        # would be the leak in its other costume.
        self.spaces.append(space_id)
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
    clarification_options: Sequence[str] = (),
    space_id: str | None = SPACE,
) -> tuple[AgentDependencies, FakeKnowledge, FakeLLM]:
    llm = FakeLLM(deltas)
    knowledge = FakeKnowledge(
        chunks if chunks is not None else [],
        document_names=document_names,
        document_total=document_total,
        summary_job_id=summary_job_id,
        clarification_options=clarification_options,
    )
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"),
        knowledge=knowledge,
        knowledge_scope=scope,
        space_id=space_id,
    )
    return deps, knowledge, llm


async def drive_run(agent: RagAgent, text: str) -> list:
    return [
        event async for event in agent.run(AgentRequest(conversation_id=None, input={"text": text}))
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
    # this `FakeChunk` set neither).
    assert final.data["citations"] == [
        {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "c1"}
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
        }
    ]


async def test_a_citation_represents_missing_file_name_and_page_as_explicit_none() -> None:
    """A missing `file_name`/`page` is an explicit `None` (⇒ JSON `null`) on
    an always-present key — never an omitted key, never a placeholder
    string — the same rule `RetrievedChunkOut` already follows on the wire."""
    deps, _knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "chunk body")])
    events = await drive_run(RagAgent(make_ctx(), deps), "q")

    citation = events[-1].data["citations"][0]
    assert citation == {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "c1"}
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
        {"document_id": "doc-1", "file_name": "a.pdf", "page": 1, "chunk_id": "c1"},
        {"document_id": "doc-1", "file_name": "b.pdf", "page": None, "chunk_id": "c2"},
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


async def test_the_summary_receipt_names_no_document_and_lists_no_corpus() -> None:
    """Two things it must NOT do. It never echoes a file name: the agent knows
    a job id and nothing else, and naming a document it did not resolve is how
    an agent starts describing the wrong file with confidence (§3.5/س-18). And
    it does not carry the corpus header — س-23 = ج puts that on the two
    ANSWERING paths, and this branch is a receipt for an action on a document
    the caller already named, so the header is never even fetched."""
    deps, knowledge, _llm = make_deps(
        summary_job_id="job-1", document_names=["a.pdf", "b.pdf"], scope=("file-a",)
    )
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize this file")

    text = events[-1].data["text"]
    assert "a.pdf" not in text
    assert "job-1" not in text
    assert knowledge.name_limit_calls == []


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
    pair as every other reply, the same keys on each, `citations` empty
    because a question cites nothing — and no event type a client has to have
    heard of."""
    deps, _knowledge, _llm = make_deps(clarification_options=["a.pdf", "b.pdf"])
    events = await drive_run(RagAgent(make_ctx(), deps), "summarize the budget file")

    assert [e.type for e in events] == ["token", "final"]
    assert events[0].data == {"delta": events[-1].data["text"]}
    assert set(events[-1].data) == {"text", "citations"}
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
        {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "c1"}
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
