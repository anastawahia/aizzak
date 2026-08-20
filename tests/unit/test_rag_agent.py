"""Unit tests for ``app.agents.rag_agent`` (Phase 4.6-a — FR-20.1, 11 §9).

Purely hermetic: the agent is driven against FAKE ports (a ``KnowledgeAccess``
and an ``LLMProvider``), exactly as 11 §9 prescribes — no service, no
``live_*`` marker. Covers the streamed event sequence + citations, the
retrieval-context injection into the system prompt, the R6 query guard (422),
the unbound-LLM guard (500), knowledge-optional degradation, discovery via the
real ``PluginLoader``, and one full drive through the 4.2 lifecycle executor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

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
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult

# --------------------------------------------------------------------------- #
# Fakes + builders                                                            #
# --------------------------------------------------------------------------- #


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


class FakeKnowledge:
    """Structurally satisfies ``KnowledgeAccess``; records its calls."""

    def __init__(self, chunks: Sequence[FakeChunk]) -> None:
        self._chunks = chunks
        self.calls: list[tuple[str, int, tuple[str, ...] | None]] = []
        # Every space the agent named (spaces plan step 8) — its own log, so
        # the existing `calls` assertions keep their shape.
        self.spaces: list[str | None] = []

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str | None,
    ) -> Sequence[FakeChunk]:
        # The scope is RECORDED, not honoured: this fake is the agent's
        # counterpart, and what the agent owes is passing the scope through
        # untouched — resolving it to documents is the knowledge module's job
        # and is tested there.
        self.calls.append((query, k, None if file_ids is None else tuple(file_ids)))
        self.spaces.append(space_id)
        return self._chunks


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
) -> tuple[AgentDependencies, FakeKnowledge, FakeLLM]:
    llm = FakeLLM(deltas)
    knowledge = FakeKnowledge(chunks if chunks is not None else [])
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"),
        knowledge=knowledge,
        knowledge_scope=scope,
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
    assert knowledge.calls == [("capital of France?", 5, None)]


async def test_a_pinned_scope_is_forwarded_to_retrieval_untouched() -> None:
    """BE-RAG-005: the agent passes the bundle's scope straight through.

    It does NOT resolve, filter or re-order it — the file⇒document translation
    belongs to the knowledge module, and an agent that pre-processed the scope
    would be a second place where a pin could silently stop applying.
    """
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")], scope=("file-a", "file-b"))
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.calls == [("q", 5, ("file-a", "file-b"))]


async def test_the_agent_names_its_space_and_has_none_to_name_yet() -> None:
    """Spaces plan step 8/12: ``AgentDeps`` carries no space until the
    invocation does, so the agent passes ``None`` — deliberately, and the port
    forces it to say so rather than let the omission read as an oversight. An
    agent that invented a space would answer from a corpus nobody chose."""
    deps, knowledge, _llm = make_deps(chunks=[FakeChunk("c1", "text")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    assert knowledge.spaces == [None]


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


async def test_without_knowledge_still_answers_with_no_citations() -> None:
    llm = FakeLLM(["hi"])
    deps = AgentDependencies(llm=ResolvedLLM(provider=llm, model="m", api_key="k"))
    events = await drive_run(RagAgent(make_ctx(), deps), "hello")

    assert [e.data["delta"] for e in events if e.type == "token"] == ["hi"]
    assert events[-1].data["citations"] == []


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
