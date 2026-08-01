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

    def __init__(self, chunk_id: str, text: str, *, score: float = 0.9) -> None:
        self.document_id = "doc-1"
        self.chunk_id = chunk_id
        self.text = text
        self.score = score


class FakeKnowledge:
    """Structurally satisfies ``KnowledgeAccess``; records its calls."""

    def __init__(self, chunks: Sequence[FakeChunk]) -> None:
        self._chunks = chunks
        self.calls: list[tuple[str, int]] = []

    async def retrieve(self, ctx: ExecutionContext, query: str, k: int) -> Sequence[FakeChunk]:
        self.calls.append((query, k))
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
    *, deltas: Sequence[str] = ("ok",), chunks: Sequence[FakeChunk] | None = None
) -> tuple[AgentDependencies, FakeKnowledge, FakeLLM]:
    llm = FakeLLM(deltas)
    knowledge = FakeKnowledge(chunks if chunks is not None else [])
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=llm, model="fake-model", api_key="k"),
        knowledge=knowledge,
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
    assert final.data["citations"] == ["c1"]
    assert knowledge.calls == [("capital of France?", 5)]


async def test_retrieved_context_is_injected_into_the_system_prompt() -> None:
    deps, _knowledge, llm = make_deps(chunks=[FakeChunk("c1", "Paris is the capital.")])
    await drive_run(RagAgent(make_ctx(), deps), "q")

    system_message = llm.stream_calls[0][0][0]
    assert system_message.role == "system"
    assert "Paris is the capital." in system_message.content
    assert llm.stream_calls[0][1].model == "fake-model"
    assert llm.stream_calls[0][2] == "k"


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
