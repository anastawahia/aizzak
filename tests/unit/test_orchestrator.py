"""Unit tests for the Orchestrator (``app/agents/orchestrator.py``, Phase
4.7-b): the agents layer's per-request coordinator.

Hermetic throughout — fake ``ProviderResolver``/registry/agents, no network,
no Docker. What these pin is the coordination itself: that the per-request
``AgentDependencies`` is assembled from process-wide singletons plus this
request's ``ExecutionContext``, that provider resolution is routed by agent
key, that an agent needing no LLM is not made to resolve one, and — the
load-bearing one for Phase 6 — that a PRE-FLIGHT failure RAISES while an
IN-FLIGHT failure arrives as an event.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import ClassVar

import pytest

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.framework.agent_runtime.base_agent import (
    AgentDependencies,
    AgentEvent,
    AgentRequest,
    BaseAgent,
)
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.plugin_loader import PluginLoader
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.modules.conversations.ports.inbound import AppendedMessage, StartedConversation
from app.modules.usage.ports.inbound import LimitDecision, UsageCharge
from tests.unit.support_access import build_authorization

_WORKSPACE = "018f0000-0000-7000-8000-000000000001"
# Spaces plan step 12 — a request that opens a FRESH thread has to name the
# space it opens it in; one that continues a thread inherits that thread's.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WORKSPACE,
        user_id="018f0000-0000-7000-8000-0000000000ff",
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset({"member"}),
        request_id="req-1",
    )


def _metadata(
    key: str, *, capabilities: frozenset[str], permissions: frozenset[str] = frozenset()
) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=key,
        version="1.0.0",
        description="test agent",
        capabilities=capabilities,
        required_permissions=permissions,
    )


class _FakeLLM:
    """A structural ``LLMProvider`` — never called here, only identity-checked."""

    provider = "fake"

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("not exercised")

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        raise AssertionError("not exercised")

    def supports(self, capability: str) -> bool:
        return True


class _FakeResolver:
    """Records every resolution so the routing key can be asserted."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.llm = _FakeLLM()
        self.calls: list[tuple[str, str | None]] = []
        self._raises = raises

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        self.calls.append((capability, model))
        if self._raises is not None:
            raise self._raises
        return self.llm, ResolvedProvider(provider="fake", model="fake-model", api_key="k-123")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


class _RecordingAgent(BaseAgent):
    """Captures the ``deps`` it was constructed with, then emits one event."""

    metadata = _metadata("recording", capabilities=frozenset({"chat"}))
    seen: ClassVar[list[AgentDependencies]] = []

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        _RecordingAgent.seen.append(self.deps)
        yield AgentEvent(type="final", data={"ok": True})


class _ExplodingAgent(BaseAgent):
    """Fails DURING the run — the in-flight failure channel."""

    metadata = _metadata("exploding", capabilities=frozenset({"chat"}))

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"text": "partial"})
        raise RuntimeError("boom")


def _orchestrator(
    *,
    agents: Sequence[tuple[AgentMetadata, type[BaseAgent]]],
    resolver: _FakeResolver | None = None,
    **seams: object,
) -> tuple[AgentOrchestrator, _FakeResolver]:
    registry = InMemoryAgentRegistry()
    for metadata, factory in agents:
        registry.register(metadata, factory)
    used = resolver if resolver is not None else _FakeResolver()
    # 6.4-ب: the authorization seam is wired by DEFAULT here, because the
    # production one always is — a bundle without it refuses any agent that
    # declares a permission (fail-closed). `setdefault` rather than a fixed
    # argument so a test can pass `authorization=None` and exercise exactly
    # that refusal.
    seams.setdefault("authorization", build_authorization())
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=used,  # type: ignore[arg-type]
        **seams,  # type: ignore[arg-type]
    )
    return AgentOrchestrator(deps), used


# --------------------------------------------------------------------------- #
# Dependency assembly — the orchestrator's core job                           #
# --------------------------------------------------------------------------- #
async def test_invoke_hands_the_agent_a_resolved_llm() -> None:
    _RecordingAgent.seen = []
    orchestrator, resolver = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]

    assert events == [AgentEvent(type="final", data={"ok": True})]
    (seen,) = _RecordingAgent.seen
    assert seen.llm is not None
    # 4.7-c-2 wraps the resolved provider in the metering decorator, so the
    # agent gets a TRANSPARENT stand-in rather than the raw adapter: same
    # `provider` identity string, same model, same key. Asserting the
    # delegated identity (not `is resolver.llm`) is the stronger claim — it
    # says metering did not change what the agent is talking to.
    assert seen.llm.provider.provider == resolver.llm.provider
    assert seen.llm.model == "fake-model"
    assert seen.llm.api_key == "k-123"


async def test_invoke_passes_the_injected_module_seams_through() -> None:
    """The process-wide seams reach the agent untouched — the orchestrator
    adds the per-request LLM, it does not filter what the root wired."""
    _RecordingAgent.seen = []
    sentinel_knowledge = object()
    sentinel_files = object()
    orchestrator, _ = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)],
        knowledge=sentinel_knowledge,
        files=sentinel_files,
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    (seen,) = _RecordingAgent.seen
    assert seen.knowledge is sentinel_knowledge
    assert seen.files is sentinel_files


async def test_unwired_seams_stay_none_rather_than_being_invented() -> None:
    _RecordingAgent.seen = []
    orchestrator, _ = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    (seen,) = _RecordingAgent.seen
    assert seen.knowledge is None
    assert seen.media is None
    assert seen.web_search is None


# --------------------------------------------------------------------------- #
# Provider routing — the agent key IS the routing key (D-16)                  #
# --------------------------------------------------------------------------- #
async def test_provider_is_resolved_with_the_agent_key_as_capability() -> None:
    """`.env.example` documents the llm namespace as "capability/agent ->
    provider + model", so an operator can pin ONE agent to one provider from
    configuration alone. Resolving under a fixed literal instead would make
    that documented capability unreachable."""
    _RecordingAgent.seen = []
    orchestrator, resolver = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert resolver.calls == [("recording", None)]


async def test_an_agent_without_the_chat_capability_resolves_no_llm() -> None:
    """The media agents (D-04) queue a job and never call an LLM. Resolving
    one for them performs a real credential lookup that would fail the request
    on a deployment carrying no LLM credential at all — for a provider the
    agent never touches."""

    class _Mediaish(BaseAgent):
        metadata = _metadata("mediaish", capabilities=frozenset({"image_generation"}))

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            _RecordingAgent.seen.append(self.deps)
            yield AgentEvent(type="final", data={})

    _RecordingAgent.seen = []
    orchestrator, resolver = _orchestrator(agents=[(_Mediaish.metadata, _Mediaish)])

    async for _ in await orchestrator.invoke(
        _ctx(), "mediaish", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert resolver.calls == []  # no credential lookup at all
    (seen,) = _RecordingAgent.seen
    assert seen.llm is None


# --------------------------------------------------------------------------- #
# Pre-flight RAISES vs in-flight EVENT — the Phase-6 status seam              #
# --------------------------------------------------------------------------- #
async def test_unknown_agent_raises_404_before_any_event() -> None:
    """The load-bearing one. If this became an error EVENT instead, the API
    could no longer answer 404: by the time the first event exists the HTTP
    status is already committed."""
    orchestrator, _ = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    with pytest.raises(NotFoundError) as excinfo:
        await orchestrator.invoke(
            _ctx(), "no-such-agent", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.status == 404


@pytest.mark.parametrize("bad_key", ["", "   "])
async def test_a_malformed_agent_key_raises_422_before_any_event(bad_key: str) -> None:
    orchestrator, _ = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    with pytest.raises(ValidationError) as excinfo:
        await orchestrator.invoke(
            _ctx(), bad_key, AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.status == 422


async def test_a_provider_resolution_failure_raises_before_any_event() -> None:
    """A missing credential / unroutable capability is a pre-flight fact, so
    it must reach the API as a status, not as a body that already claimed 200."""
    orchestrator, _ = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)],
        resolver=_FakeResolver(raises=NotFoundError("no credential for provider 'fake'")),
    )

    with pytest.raises(AppError):
        await orchestrator.invoke(
            _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )


async def test_an_in_flight_failure_becomes_a_terminal_error_event_not_a_raise() -> None:
    """Once streaming has begun the executor's B1 model owns failure: the
    partial output already sent is kept and the failure is delivered as the
    last event, never as an exception the caller cannot turn into a response."""
    orchestrator, _ = _orchestrator(agents=[(_ExplodingAgent.metadata, _ExplodingAgent)])

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "exploding", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]

    assert events[0].type == "token"
    assert events[-1].type == "error"
    assert events[-1].data["status"] == 500


# --------------------------------------------------------------------------- #
# The orchestrator is not a plugin                                            #
# --------------------------------------------------------------------------- #
def test_the_plugin_loader_never_registers_the_orchestrator_module() -> None:
    """``PluginLoader`` only descends into sub-PACKAGES of ``app.agents``, so
    this loose module is invisible to it. Pinned rather than trusted: a future
    loader that started scanning loose modules would otherwise try to register
    the orchestrator as an agent, and this test is the tripwire."""
    registry = InMemoryAgentRegistry()
    PluginLoader().load_into(registry)

    keys = {metadata.key for metadata in registry.list()}
    assert "orchestrator" not in keys
    # ...and the real five are all still there, so the scan genuinely ran.
    assert keys == {
        "rag_agent",
        "data_analysis_agent",
        "file_editing_agent",
        "image_agent",
        "video_agent",
    }


# --------------------------------------------------------------------------- #
# End to end over the REAL plugin tree — no agent doubles                     #
# --------------------------------------------------------------------------- #
class _StreamingLLM(_FakeLLM):
    """Streams two deltas + a terminal chunk carrying 4.7-a's new counters."""

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        async def _gen() -> AsyncIterator[LlmChunk]:
            yield LlmChunk(delta="Paris")
            yield LlmChunk(delta=" it is")
            yield LlmChunk(delta="", finish_reason="stop", prompt_tokens=31, completion_tokens=4)

        return _gen()


class _StreamingResolver(_FakeResolver):
    def __init__(self) -> None:
        super().__init__()
        self.llm = _StreamingLLM()


class _FakeChunk:
    def __init__(self, chunk_id: str, text: str) -> None:
        self.document_id = "doc-1"
        self.chunk_id = chunk_id
        self.text = text
        self.score = 0.9
        self.file_name: str | None = None
        self.page_number: int | None = None
        self.section: str | None = None


class _FakeDocumentNames:
    def __init__(self) -> None:
        self.names: tuple[str, ...] = ()
        self.total = 0


class _FakeRoutedAnswer:
    """Retrieval plan §3.4/§4 row 11 (`P-21`) — what the real seam hands the
    agent now that classification and dispatch live inside the module."""

    def __init__(self, chunks: Sequence[_FakeChunk]) -> None:
        self.intent = "content"
        self.chunks = chunks
        self.summary_job_id: str | None = None
        # Retrieval plan §4 row 14 (`P-04`) — nothing to clarify: this fake
        # always reports the CONTENT route, which never resolves a file name.
        self.clarification_options: tuple[str, ...] = ()
        # ب-7أ / ب-4ب / ب-8 — and for the same reason, no build to name, no
        # refusal to report and no stored summary read back. All three are
        # carried anyway rather than left off: this class exists to BE a
        # `RoutedAnswerView`, and a view missing a member the agent reads is a
        # fake that fails where the real seam would not.
        self.summary_target_name: str | None = None
        self.summary_blocked: str | None = None
        self.stored_summary_text: str | None = None


class _FakeKnowledge:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.scopes: list[tuple[str, ...] | None] = []
        # س-32 — every space the agent named, on every face of this seam.
        self.spaces: list[str] = []
        # Retrieval plan §3.6/§4 row 6 (`P-36`) -- the real `rag_agent` now
        # calls this on every request that has a knowledge seam at all, not
        # only the zero-chunk fallback below.
        self.name_limit_calls: list[int | None] = []

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str,
    ) -> Sequence[_FakeChunk]:
        self.queries.append(query)
        self.scopes.append(None if file_ids is None else tuple(file_ids))
        self.spaces.append(space_id)
        return [_FakeChunk("chunk-a", "The capital of France is Paris.")]

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[str] | None = None,
        *,
        space_id: str,
        conversation_id: str | None = None,
    ) -> _FakeRoutedAnswer:
        """The seam the real ``rag_agent`` calls (retrieval plan §3.4/§4 row
        11): ONE call, routed inside the module. It records into the SAME
        ``queries``/``scopes`` logs ``retrieve`` uses, so what this test
        proves — the question and the pinned scope reach the module intact —
        is unchanged by which face carries them."""
        chunks = await self.retrieve(ctx, question, k, file_ids, space_id=space_id)
        return _FakeRoutedAnswer(chunks)

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: str, limit: int | None = None
    ) -> _FakeDocumentNames:
        self.name_limit_calls.append(limit)
        # س-32 — the header is space-scoped too, and the space lands in the
        # same log the two retrieval faces write to.
        self.spaces.append(space_id)
        return _FakeDocumentNames()


async def test_orchestrator_drives_the_real_rag_agent_from_the_real_plugin_tree() -> None:
    """The one test here that uses NO agent double: the registry is populated
    by the real ``PluginLoader`` scanning ``app.agents``, and the agent driven
    is the shipped ``rag_agent``. Everything below the agent is faked (LLM,
    knowledge) because this is a hermetic unit suite — but the coordination
    path, the manifest, the lifecycle and the event contract are all real.

    This is what proves 4.7-b actually composes: the fakes above could all
    agree with each other while the real agent expected a different ``deps``
    shape entirely."""
    registry = InMemoryAgentRegistry()
    PluginLoader().load_into(registry)
    knowledge = _FakeKnowledge()
    resolver = _StreamingResolver()
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=resolver,  # type: ignore[arg-type]
            knowledge=knowledge,  # type: ignore[arg-type]
            # س-32 — WIRED here, where it used to be absent. The real agent
            # reads its space off the bundle now and retrieves nothing without
            # one, so an orchestrator with no thread seam cannot demonstrate
            # that the retrieval path composes: it would demonstrate the
            # degradation instead (which
            # `test_a_run_with_no_thread_seam_gives_the_agent_no_space` covers
            # on its own).
            conversations=_FakeThreads(),
            authorization=build_authorization(),
        )
    )

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(),
            "rag_agent",
            AgentRequest(
                space_id=_SPACE, conversation_id=None, input={"text": "capital of France?"}
            ),
        )
    ]

    # The agent key routed the provider lookup, and the retrieval seam was used.
    assert resolver.calls == [("rag_agent", None)]
    assert knowledge.queries == ["capital of France?"]
    # And it was used INSIDE the request's space, end to end through the real
    # agent: the answer's space and the corpus header's are the same one.
    assert knowledge.spaces == [_SPACE, _SPACE]
    # Token stream, then one final carrying the assembled answer + citations.
    assert [e.type for e in events] == ["token", "token", "final"]
    assert events[-1].data["text"] == "Paris it is"
    # Retrieval plan §3.2/§4 row 3, P-32 — a structured citation, not a bare
    # `chunk_id` UUID.
    assert events[-1].data["citations"] == [
        {"document_id": "doc-1", "file_name": None, "page": None, "chunk_id": "chunk-a"}
    ]


async def test_real_agent_input_validation_still_raises_in_flight_as_an_event() -> None:
    """A bad ``input`` is only discoverable INSIDE the agent's own run, so it
    is an error EVENT (422 payload), not a pre-flight raise — the boundary
    between the two channels, shown on a real agent rather than a double."""
    registry = InMemoryAgentRegistry()
    PluginLoader().load_into(registry)
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_StreamingResolver(),  # type: ignore[arg-type]
            authorization=build_authorization(),
        )
    )

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(),
            "rag_agent",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "  "}),
        )
    ]

    assert [e.type for e in events] == ["error"]
    assert events[0].data["status"] == 422


# --------------------------------------------------------------------------- #
# 4.7-c-2 -- quota enforcement (before) + usage capture (after)               #
# --------------------------------------------------------------------------- #
class _FakeEnforcement:
    """Records every check; answers with a canned decision."""

    def __init__(self, decision: LimitDecision | None = None) -> None:
        self.decision = decision or LimitDecision(allowed=True)
        self.calls: list[tuple[str, str, int | None]] = []

    async def check(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision:
        self.calls.append((agent, provider, estimated_tokens))
        return self.decision


class _FakeCapture:
    """Records every charge; can be made to fail."""

    def __init__(self, *, fails: bool = False) -> None:
        self.charges: list[UsageCharge] = []
        self._fails = fails

    async def record(self, ctx: ExecutionContext, charge: UsageCharge) -> None:
        if self._fails:
            raise RuntimeError("ledger unavailable")
        self.charges.append(charge)


class _CountingLLM(_FakeLLM):
    """Streams one delta plus a terminal chunk carrying (or omitting) counters."""

    def __init__(self, *, prompt: int | None, completion: int | None) -> None:
        self._prompt = prompt
        self._completion = completion

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        async def _gen() -> AsyncIterator[LlmChunk]:
            yield LlmChunk(delta="hello there")
            yield LlmChunk(
                delta="",
                finish_reason="stop",
                prompt_tokens=self._prompt,
                completion_tokens=self._completion,
            )

        return _gen()


class _StreamingAgent(BaseAgent):
    """Consumes its LLM exactly as a real agent does, so the metering
    decorator is exercised through the real call path."""

    metadata = _metadata("streamer", capabilities=frozenset({"chat"}))

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        binding = self.deps.llm
        assert binding is not None
        async for chunk in binding.provider.stream(
            [LlmMessage(role="user", content="hi there friend")],
            LlmParams(model=binding.model),
            binding.api_key,
        ):
            if chunk.delta:
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(type="final", data={})


def _metered_orchestrator(
    *,
    llm: _FakeLLM,
    enforcement: _FakeEnforcement | None = None,
    capture: _FakeCapture | None = None,
    agent: type[BaseAgent] = _StreamingAgent,
) -> tuple[AgentOrchestrator, _FakeEnforcement, _FakeCapture]:
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    resolver = _FakeResolver()
    resolver.llm = llm
    used_enforcement = enforcement or _FakeEnforcement()
    used_capture = capture or _FakeCapture()
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=resolver,  # type: ignore[arg-type]
        usage_enforcement=used_enforcement,
        usage_capture=used_capture,
        authorization=build_authorization(),
    )
    return AgentOrchestrator(deps), used_enforcement, used_capture


async def _drain(orchestrator: AgentOrchestrator, key: str = "streamer") -> list[AgentEvent]:
    return [
        e
        async for e in await orchestrator.invoke(
            _ctx(), key, AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]


async def test_quota_is_checked_before_the_run_and_denial_raises_429() -> None:
    """FR-132 + 11 §8.1: a denial must cost NOTHING — no agent is created, no
    provider is called — and it must raise pre-flight so Phase 6 can answer a
    real 429 instead of a body that already claimed 200."""
    orchestrator, enforcement, capture = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5),
        enforcement=_FakeEnforcement(
            LimitDecision(allowed=False, reason="quota_exceeded", remaining=0)
        ),
    )

    with pytest.raises(RateLimitedError) as excinfo:
        await orchestrator.invoke(
            _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.status == 429
    # The reason becomes the CODE (11 §8.2), not a generic rate-limit code:
    # an operator must be able to see WHICH limit stopped the request.
    assert excinfo.value.code == "usage.quota_exceeded"
    assert enforcement.calls == [("streamer", "fake", None)]
    assert capture.charges == []  # nothing ran, nothing billed


async def test_budget_denial_maps_to_its_own_code() -> None:
    orchestrator, _, _ = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5),
        enforcement=_FakeEnforcement(LimitDecision(allowed=False, reason="budget_exceeded")),
    )

    with pytest.raises(RateLimitedError) as excinfo:
        await orchestrator.invoke(
            _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.code == "usage.budget_exceeded"


async def test_the_denials_retry_hint_is_carried_onto_the_error() -> None:
    """3.79: the orchestrator is the 429's producer but not the reset's — it
    forwards ``LimitDecision.retry_after_s`` unexamined, because only the
    enforcement adapter knows which period bound the decision. This is what
    lets ``api/main.py`` emit a real ``Retry-After``."""
    orchestrator, _, _ = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5),
        enforcement=_FakeEnforcement(
            LimitDecision(allowed=False, reason="quota_exceeded", remaining=0, retry_after_s=3600)
        ),
    )

    with pytest.raises(RateLimitedError) as excinfo:
        await orchestrator.invoke(
            _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.retry_after_s == 3600


async def test_a_denial_without_a_reset_carries_no_retry_hint() -> None:
    """``None`` in, ``None`` out — no header rather than a plausible number
    nobody computed (03 §4's whole reason for withholding it in v1)."""
    orchestrator, _, _ = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5),
        enforcement=_FakeEnforcement(LimitDecision(allowed=False, reason="quota_exceeded")),
    )

    with pytest.raises(RateLimitedError) as excinfo:
        await orchestrator.invoke(
            _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )

    assert excinfo.value.retry_after_s is None


async def test_enforcement_supplies_no_estimated_tokens() -> None:
    """Nothing can know a request's token cost before running it; inventing a
    number here would silently shrink every workspace's headroom by that
    guess. The port makes the parameter optional for exactly this reason."""
    orchestrator, enforcement, _ = _metered_orchestrator(llm=_CountingLLM(prompt=10, completion=5))

    await _drain(orchestrator)

    assert enforcement.calls == [("streamer", "fake", None)]


async def test_reported_counters_are_captured_as_measured() -> None:
    """The 4.7-a payoff: a streamed turn whose provider reported real counters
    bills the EXACT sum, flagged as measured."""
    orchestrator, _, capture = _metered_orchestrator(llm=_CountingLLM(prompt=31, completion=4))

    await _drain(orchestrator)

    (charge,) = capture.charges
    assert charge.tokens == 35
    assert charge.estimated is False
    assert charge.agent == "streamer"
    assert charge.provider == "fake"
    assert charge.cost_micros == 0  # v1: no pricing source exists


async def test_unreported_counters_fall_back_to_a_marked_estimate() -> None:
    """A provider that reports nothing must still be billed — but the row has
    to say the number was guessed, which is the whole point of 4.7-c-1."""
    orchestrator, _, capture = _metered_orchestrator(llm=_CountingLLM(prompt=None, completion=None))

    await _drain(orchestrator)

    (charge,) = capture.charges
    assert charge.tokens > 0
    assert charge.estimated is True


async def test_a_partially_unreported_request_is_marked_estimated() -> None:
    """Conservative direction: a charge is called "measured" only if ALL of it
    genuinely was. One unreported call taints the total."""
    orchestrator, _, capture = _metered_orchestrator(llm=_CountingLLM(prompt=10, completion=None))

    await _drain(orchestrator)

    (charge,) = capture.charges
    assert charge.estimated is True


async def test_every_charge_gets_a_distinct_operation_id() -> None:
    """`operation_id` is the idempotency key (FR-134): two genuine runs must
    NOT collide, or the second would be silently discarded as a replay."""
    orchestrator, _, capture = _metered_orchestrator(llm=_CountingLLM(prompt=10, completion=5))

    await _drain(orchestrator)
    await _drain(orchestrator)

    assert len({c.operation_id for c in capture.charges}) == 2


async def test_an_agent_that_never_calls_an_llm_records_nothing() -> None:
    """The media agents (D-04) queue a job and call no provider. Recording a
    zero-token row per request would be pure noise in an append-only ledger;
    the Phase-5 worker meters the generation it actually performs."""

    class _Quiet(BaseAgent):
        metadata = _metadata("quiet", capabilities=frozenset({"image_generation"}))

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(type="final", data={})

    orchestrator, enforcement, capture = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5), agent=_Quiet
    )

    await _drain(orchestrator, key="quiet")

    assert capture.charges == []
    # Quota is still ENFORCED for it -- only the capture is skipped.
    assert enforcement.calls == [("quiet", "none", None)]


async def test_an_abandoned_stream_is_still_billed() -> None:
    """A consumer that walks away mid-stream still consumed real provider
    tokens; not billing them would make abandonment a free tier."""
    orchestrator, _, capture = _metered_orchestrator(llm=_CountingLLM(prompt=10, completion=5))

    events = await orchestrator.invoke(
        _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    )
    agen = events.__aiter__()
    await agen.__anext__()  # take one token, then abandon
    await agen.aclose()

    assert len(capture.charges) == 1


async def test_a_capture_failure_never_breaks_a_delivered_answer() -> None:
    """The answer has already been streamed in full by the time capture runs;
    raising here would turn a successful response into an error the client
    cannot act on. Swallowed + logged (the executor's `dispose` precedent)."""
    orchestrator, _, _ = _metered_orchestrator(
        llm=_CountingLLM(prompt=10, completion=5), capture=_FakeCapture(fails=True)
    )

    events = await _drain(orchestrator)

    assert [e.type for e in events] == ["token", "final"]


async def test_usage_ports_absent_means_unmetered_not_broken() -> None:
    """An unmetered deployment must still boot and run: both ports optional."""
    registry = InMemoryAgentRegistry()
    registry.register(_StreamingAgent.metadata, _StreamingAgent)
    resolver = _FakeResolver()
    resolver.llm = _CountingLLM(prompt=10, completion=5)
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=resolver,  # type: ignore[arg-type]
            authorization=build_authorization(),
        )
    )

    events = await _drain(orchestrator)

    assert [e.type for e in events] == ["token", "final"]


async def test_the_metering_wrapper_forwards_supports_verbatim() -> None:
    """A decorator that answered capability questions for itself would let
    metering change ROUTING — the one thing this wrapper must never do."""
    _RecordingAgent.seen = []

    registry = InMemoryAgentRegistry()
    registry.register(_RecordingAgent.metadata, _RecordingAgent)
    resolver = _FakeResolver()
    inner = _CountingLLM(prompt=1, completion=1)
    resolver.llm = inner
    wrapped = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=resolver,  # type: ignore[arg-type]
            authorization=build_authorization(),
        )
    )
    await _drain(wrapped, key="recording")

    (seen,) = _RecordingAgent.seen
    assert seen.llm is not None
    assert seen.llm.provider.supports("tools") is inner.supports("tools")


# --------------------------------------------------------------------------- #
# 5.3-أ — the total stream duration cap (the §3.23(ز) debt)                    #
# --------------------------------------------------------------------------- #
class _StallingAgent(BaseAgent):
    """Streams its LLM to completion, then stalls forever — the shape the cap
    exists for: a stream that keeps the connection open without ending."""

    metadata = _metadata("staller", capabilities=frozenset({"chat"}))
    disposed: ClassVar[int] = 0

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        binding = self.deps.llm
        assert binding is not None
        async for chunk in binding.provider.stream(
            [LlmMessage(role="user", content="hi there friend")],
            LlmParams(model=binding.model),
            binding.api_key,
        ):
            if chunk.delta:
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        await asyncio.sleep(3)  # far past any test cap; cancelled by it
        yield AgentEvent(type="final", data={})

    async def dispose(self) -> None:
        _StallingAgent.disposed += 1


def _capped_orchestrator(
    *,
    cap_s: float | None,
    capture: _FakeCapture | None = None,
    agent: type[BaseAgent] = _StallingAgent,
) -> tuple[AgentOrchestrator, _FakeCapture]:
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    resolver = _FakeResolver()
    resolver.llm = _CountingLLM(prompt=10, completion=5)
    used_capture = capture or _FakeCapture()
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=resolver,  # type: ignore[arg-type]
        usage_capture=used_capture,
        stream_max_duration_s=cap_s,
        authorization=build_authorization(),
    )
    return AgentOrchestrator(deps), used_capture


async def test_an_overrunning_stream_ends_with_the_b1_terminal_error_event() -> None:
    """The cap fires IN-BAND: by then events have escaped and the HTTP status
    is committed, so the only honest channel is decision B1's terminal
    ``error`` event — code/status from the closed 03 §4 catalog."""
    orchestrator, _ = _capped_orchestrator(cap_s=0.05)

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "staller", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]

    assert [e.type for e in events] == ["token", "error"]
    assert events[-1].data["code"] == "agent.failed"
    assert events[-1].data["status"] == 502
    detail = events[-1].data["detail"]
    assert isinstance(detail, str) and "duration cap" in detail


async def test_the_cap_disposes_the_cut_agent() -> None:
    """AC-06 survives the deadline: cancelling the producer must still run the
    executor's dispose-always leg, not leave the agent for the GC."""
    _StallingAgent.disposed = 0
    orchestrator, _ = _capped_orchestrator(cap_s=0.05)

    async for _ in await orchestrator.invoke(
        _ctx(), "staller", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert _StallingAgent.disposed == 1


async def test_the_cut_run_is_still_billed_for_what_it_consumed() -> None:
    """The meter is live (eager, not end-of-stream), so the tokens streamed
    BEFORE the deadline are charged even though the run was cut mid-stall."""
    orchestrator, capture = _capped_orchestrator(cap_s=0.05)

    async for _ in await orchestrator.invoke(
        _ctx(), "staller", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    (charge,) = capture.charges
    assert charge.tokens == 15  # 10 prompt + 5 completion, measured
    assert charge.estimated is False


async def test_no_cap_means_no_deadline_at_all() -> None:
    """``None`` (the bare-bundle default) must mean UNCAPPED — not a zero
    budget: a deployment that configures no cap keeps today's behaviour."""

    class _Brief(BaseAgent):
        metadata = _metadata("brief", capabilities=frozenset({"chat"}))

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            await asyncio.sleep(0.02)  # nonzero wall time under no deadline
            yield AgentEvent(type="final", data={})

    registry = InMemoryAgentRegistry()
    registry.register(_Brief.metadata, _Brief)
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),  # type: ignore[arg-type]
            authorization=build_authorization(),
        )
    )

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "brief", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]

    assert [e.type for e in events] == ["final"]


async def test_a_generous_cap_never_touches_a_healthy_stream() -> None:
    """The deadline is a ceiling, not a schedule: a stream that ends inside
    its budget must be byte-for-byte what an uncapped one produces."""
    orchestrator, capture = _capped_orchestrator(cap_s=30.0, agent=_StreamingAgent)

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "streamer", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]

    assert [e.type for e in events] == ["token", "final"]
    (charge,) = capture.charges
    assert charge.tokens == 15


async def test_a_budget_spent_between_pulls_still_disposes_the_producer() -> None:
    """The deadline can expire while the producer sits PARKED at a yield (a
    dawdling consumer). No cancellation ever reaches the producer on that
    path, so the explicit close in the timeout branch is the only thing that
    runs its ``finally`` chain now — dispose (AC-06) must not wait for GC."""
    _StallingAgent.disposed = 0
    orchestrator, _ = _capped_orchestrator(cap_s=0.05)

    agen = await orchestrator.invoke(
        _ctx(), "staller", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    )
    first = await agen.__anext__()
    await asyncio.sleep(0.08)  # budget expires while the producer is parked
    second = await agen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()

    assert first.type == "token"
    assert second.type == "error"
    assert _StallingAgent.disposed == 1


# --------------------------------------------------------------------------- #
# 6.1-ج-3 — the single-agent turn: thread + both messages + the usage split   #
# --------------------------------------------------------------------------- #
# س-32 — the space `_FakeThreads` reports for an existing thread. A constant
# so a test that cares which space reached the agent can compare against it
# rather than against a literal repeated at both ends.
_THREAD_SPACE = "space-of-the-thread"


class _FakeThreads:
    """A recording ``ConversationThreads``: it is the whole point of these
    tests that the orchestrator writes REAL turns, in order, through the port."""

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        known: str | None = None,
        pinned: str | None = None,
        pinned_files: tuple[str, ...] = (),
        space: str | None = _THREAD_SPACE,
    ) -> None:
        self.started: list[tuple[str, str]] = []
        self.spaces: list[str | None] = []
        self.appended: list[tuple[str, str, str, tuple[str, ...], int | None]] = []
        self.route_reads: list[str] = []
        self.scope_reads: list[str] = []
        # س-32 — every read of an EXISTING thread's space, recorded like the
        # other two pre-flight reads so a test can pin that a request opening a
        # fresh thread never performs one.
        self.space_reads: list[str] = []
        self._fail_on = fail_on
        self._known = known
        self._pinned = pinned
        self._pinned_files = pinned_files
        self._space = space
        self._seq = 0

    async def space_of(self, ctx: ExecutionContext, conversation_id: str) -> str | None:
        """س-32. The thread's own space — what the orchestrator puts on
        ``AgentDependencies.space_id`` for every turn that CONTINUES a thread,
        where ``AgentRequest.space_id`` is ignored by design."""
        self.space_reads.append(conversation_id)
        return self._space

    async def pinned_files(self, ctx: ExecutionContext, conversation_id: str) -> tuple[str, ...]:
        """BE-RAG-005. Recorded for the same reason ``routed_model`` is: a
        request opening a FRESH thread must not ask a thread that does not
        exist yet what it pinned."""
        self.scope_reads.append(conversation_id)
        return self._pinned_files

    async def routed_model(self, ctx: ExecutionContext, conversation_id: str) -> str | None:
        """BE-RAG-003. Records the read so a test can pin that a request
        opening a FRESH thread never asks — there is nothing to ask about."""
        self.route_reads.append(conversation_id)
        return self._pinned

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        space_id: str | None,
        agent_key: str,
        kind: str,
        title: str | None = None,
    ) -> StartedConversation:
        # The space is RECORDED, not ignored: since step 12 the orchestrator
        # passes the one `AgentInvokeIn` carried, and a fake that swallowed
        # the argument could not tell that from passing nothing.
        self.spaces.append(space_id)
        self.started.append((agent_key, kind))
        return StartedConversation(id="conv-new", agent_key=agent_key, kind=kind)

    async def append(
        self,
        ctx: ExecutionContext,
        conversation_id: str,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
    ) -> AppendedMessage:
        if self._known is not None and conversation_id != self._known:
            raise NotFoundError("conversation not found")
        if role == self._fail_on:
            raise ConflictError("write lost a race")
        self.appended.append((conversation_id, role, text, attachments, token_count))
        self._seq += 1
        return AppendedMessage(
            id=f"msg-{self._seq}",
            conversation_id=conversation_id,
            role=role,
            text=text,
            attachments=attachments,
            token_count=token_count,
            seq=self._seq,
            created_at=utc_now(),
        )


class _JobAgent(BaseAgent):
    """A media-shaped agent: no LLM, and a structured `final` with NO text."""

    metadata = _metadata("job", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="final", data={"job_id": "j1", "status": "queued", "kind": "image"})


class _FileAgent(BaseAgent):
    """A `final` whose result is a file reference."""

    metadata = _metadata("filer", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="final", data={"text": "done", "file_id": "f-1"})


class _FailingAgent(BaseAgent):
    """Fails IN-FLIGHT: the executor turns the raise into a terminal event."""

    metadata = _metadata("boom", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "x"})
        raise ValidationError("bad input for this agent")


def _turn_orchestrator(agent: type[BaseAgent], threads: _FakeThreads | None) -> AgentOrchestrator:
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    return AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),  # type: ignore[arg-type]
            conversations=threads,
            authorization=build_authorization(),
        )
    )


async def test_invoke_opens_a_thread_and_writes_both_turns() -> None:
    threads = _FakeThreads()
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(),
            "filer",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
        )
    ]

    assert threads.started == [("filer", "agent")]
    # Spaces plan step 12: the space the REQUEST named reaches the thread —
    # never one invented from whatever this workspace happens to own, which
    # would file the thread and its whole retrieval scope where nobody put it.
    assert threads.spaces == [_SPACE]
    assert [(row[1], row[2]) for row in threads.appended] == [
        ("user", "hi"),
        ("assistant", "done"),
    ]
    # The reply's file reference became the message's attachment (INV-CV2).
    assert threads.appended[1][3] == ("f-1",)
    # 03 §3.1: the final frame gained message_id/content/usage, kept its own.
    final = events[-1]
    assert final.data["message_id"] == "msg-2"
    assert final.data["file_id"] == "f-1"
    assert final.data["content"] == {"text": "done", "attachments": ["f-1"]}
    assert final.data["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}


async def test_invoke_reuses_the_thread_it_is_given() -> None:
    threads = _FakeThreads(known="conv-1")
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    async for _ in await orchestrator.invoke(
        _ctx(), "filer", AgentRequest(conversation_id="conv-1", input={"text": "hi"})
    ):
        pass

    assert threads.started == []
    assert {row[0] for row in threads.appended} == {"conv-1"}


async def test_a_request_naming_neither_a_thread_nor_a_space_is_refused_pre_flight() -> None:
    """Spaces plan step 12 — the one combination that would file a thread
    nowhere.

    ``space_id`` is optional on ``AgentRequest`` because a request that
    CONTINUES a thread inherits that thread's space and must not restate it.
    That makes "neither" reachable, and it is the shape row 8-b's
    ``SET NOT NULL`` would meet as a ``23502`` from the database instead of a
    422 from the request. It is refused pre-flight — before the agent is
    created and before a single row is written — so nothing is left behind.
    """
    threads = _FakeThreads()
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    with pytest.raises(ValidationError):
        await orchestrator.invoke(
            _ctx(), "filer", AgentRequest(conversation_id=None, space_id=None, input={"text": "hi"})
        )

    assert threads.started == []
    assert threads.appended == []


async def test_a_request_continuing_a_thread_needs_no_space_of_its_own() -> None:
    """The other side of the same rule: the thread already has a space, so a
    continuation that names none is perfectly well-formed — and nothing is
    opened for it to be filed under."""
    threads = _FakeThreads(known="conv-1")
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    async for _ in await orchestrator.invoke(
        _ctx(), "filer", AgentRequest(conversation_id="conv-1", space_id=None, input={"text": "hi"})
    ):
        pass

    assert threads.started == []
    assert threads.spaces == []


async def test_an_unknown_thread_raises_pre_flight_before_the_agent_runs() -> None:
    threads = _FakeThreads(known="conv-1")
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    with pytest.raises(NotFoundError):
        await orchestrator.invoke(
            _ctx(), "filer", AgentRequest(conversation_id="ghost", input={"text": "hi"})
        )
    assert threads.appended == []


async def test_a_textless_final_is_recorded_as_its_own_json() -> None:
    """A media job's `final` has no text and `MessageContent` rejects an empty
    message — so the payload is serialised verbatim rather than invented or
    dropped."""
    threads = _FakeThreads()
    orchestrator = _turn_orchestrator(_JobAgent, threads)

    async for _ in await orchestrator.invoke(
        _ctx(),
        "job",
        AgentRequest(space_id=_SPACE, conversation_id=None, input={"prompt": "a cat"}),
    ):
        pass

    user_text, assistant_text = threads.appended[0][2], threads.appended[1][2]
    assert json.loads(user_text) == {"prompt": "a cat"}
    assert json.loads(assistant_text) == {"job_id": "j1", "status": "queued", "kind": "image"}


async def test_an_unwired_conversations_seam_streams_but_persists_nothing() -> None:
    orchestrator = _turn_orchestrator(_FileAgent, None)

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(),
            "filer",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
        )
    ]

    assert [e.type for e in events] == ["final"]
    assert "message_id" not in events[-1].data  # nothing was written to name


async def test_invoke_once_refuses_to_invent_a_turn_it_never_persisted() -> None:
    orchestrator = _turn_orchestrator(_FileAgent, None)

    with pytest.raises(AppError) as excinfo:
        await orchestrator.invoke_once(
            _ctx(),
            "filer",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
        )

    assert excinfo.value.status == 500
    assert excinfo.value.code == "common.internal"


async def test_invoke_once_returns_the_persisted_turn() -> None:
    threads = _FakeThreads()
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    turn = await orchestrator.invoke_once(
        _ctx(), "filer", AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"})
    )

    assert turn.conversation_id == "conv-new"
    assert turn.message.role == "assistant"
    assert turn.message.seq == 2
    assert turn.final["file_id"] == "f-1"


async def test_invoke_once_raises_an_in_flight_failure_as_the_problem_it_is() -> None:
    """Nothing has been written to the wire yet, so a terminal `error` event
    must become a real 4xx/5xx rather than a 200 wrapping a failure."""
    threads = _FakeThreads()
    orchestrator = _turn_orchestrator(_FailingAgent, threads)

    with pytest.raises(AppError) as excinfo:
        await orchestrator.invoke_once(
            _ctx(),
            "boom",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
        )

    assert excinfo.value.status == 422
    assert excinfo.value.code == "common.validation_error"


async def test_a_failed_reply_write_never_breaks_a_produced_answer() -> None:
    threads = _FakeThreads(fail_on="assistant")
    orchestrator = _turn_orchestrator(_FileAgent, threads)

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(),
            "filer",
            AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
        )
    ]

    assert events[-1].type == "final"
    assert events[-1].data == {"text": "done", "file_id": "f-1"}  # un-enriched


async def test_the_turn_reports_the_prompt_completion_split() -> None:
    orchestrator, _, _ = _metered_orchestrator(llm=_CountingLLM(prompt=812, completion=140))
    # Rewire the one seam this test is about, leaving the metering wiring the
    # helper built untouched.
    orchestrator._deps = replace(orchestrator._deps, conversations=_FakeThreads())

    turn = await orchestrator.invoke_once(
        _ctx(),
        "streamer",
        AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "hi"}),
    )

    assert turn.prompt_tokens == 812
    assert turn.completion_tokens == 140
    # The stored reply is charged its OWN half, not the turn total.
    assert turn.message.token_count == 140


# --------------------------------------------------------------------------- #
# The agent's own permissions (6.4-ب)                                         #
# --------------------------------------------------------------------------- #
class _PrivilegedAgent(BaseAgent):
    """Declares what it needs, exactly as every real manifest does (02 §3.2)."""

    metadata = _metadata(
        "privileged",
        capabilities=frozenset({"chat"}),
        permissions=frozenset({"agents:invoke", "credentials:manage"}),
    )

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="final", data={"ok": True})


async def test_an_agents_declared_permissions_are_enforced_before_it_runs() -> None:
    """``required_permissions`` carried the comment "checked by RBAC before any
    run" since 4.1 and was read by nothing until now.

    A member holds ``agents:invoke`` and not ``credentials:manage`` (05 §1.3),
    so this agent is refused — the per-agent layer of authorization, which no
    route guard can perform because only this layer resolves the manifest.
    """
    orchestrator, _ = _orchestrator(agents=[(_PrivilegedAgent.metadata, _PrivilegedAgent)])

    with pytest.raises(ForbiddenError) as raised:
        await orchestrator.invoke(
            _ctx(), "privileged", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    assert raised.value.code == "authz.forbidden"
    assert "credentials:manage" in str(raised.value)


async def test_a_refused_agent_costs_no_credential_lookup_and_no_quota_call() -> None:
    """Authorization runs FIRST — ahead of the provider resolution that reads a
    workspace's stored key and ahead of the quota check. A forbidden request
    must not appear in the usage ledger of a workspace it was never allowed to
    spend, and must not touch Vault on the way to being refused."""
    resolver = _FakeResolver()
    enforcement = _FakeEnforcement(LimitDecision(allowed=True, reason=None))
    orchestrator, _ = _orchestrator(
        agents=[(_PrivilegedAgent.metadata, _PrivilegedAgent)],
        resolver=resolver,
        usage_enforcement=enforcement,
    )

    with pytest.raises(ForbiddenError):
        await orchestrator.invoke(
            _ctx(), "privileged", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    assert resolver.calls == []
    assert enforcement.calls == []


async def test_an_owner_reaches_the_same_agent() -> None:
    """The other direction, over the real catalog: ``owner`` holds every
    in-workspace permission, so nothing about the guard is unconditional."""
    orchestrator, _ = _orchestrator(agents=[(_PrivilegedAgent.metadata, _PrivilegedAgent)])
    ctx = replace(_ctx(), roles=frozenset({"owner"}))

    events = [
        e
        async for e in await orchestrator.invoke(
            ctx, "privileged", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]
    assert events[-1].type == "final"


async def test_an_unwired_authorization_seam_refuses_instead_of_allowing() -> None:
    """The ONE seam that fails closed. An unwired quota seam means "not
    metered"; an unwired authorization seam must never come to mean "not
    authorized, therefore allowed"."""
    orchestrator, _ = _orchestrator(
        agents=[(_PrivilegedAgent.metadata, _PrivilegedAgent)], authorization=None
    )

    with pytest.raises(AppError) as raised:
        await orchestrator.invoke(
            _ctx(), "privileged", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    assert raised.value.code == "common.internal"


async def test_an_agent_that_declares_nothing_needs_no_decision() -> None:
    """No permissions means no question to answer, so an unwired seam is not a
    refusal — the fail-closed rule applies to decisions we cannot make, not to
    decisions nobody asked for."""
    orchestrator, _ = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], authorization=None
    )
    _RecordingAgent.seen = []

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )
    ]
    assert events[-1].type == "final"


async def test_an_unknown_agent_is_still_a_404_not_a_403() -> None:
    """``registry.create`` owns the unknown-agent answer; a second judgement
    here would let two call sites disagree about what "unknown" means."""
    orchestrator, _ = _orchestrator(agents=[(_PrivilegedAgent.metadata, _PrivilegedAgent)])

    with pytest.raises(NotFoundError):
        await orchestrator.invoke(
            _ctx(), "no_such_agent", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
        )


# --------------------------------------------------------------------------- #
# Per-conversation model pin (BE-RAG-003)                                     #
# --------------------------------------------------------------------------- #
async def test_a_pinned_route_replaces_the_agent_key_as_the_routing_capability() -> None:
    """The pin is the whole feature at this layer. It becomes the CAPABILITY,
    never a ``model=`` override: overriding the model inside whatever provider
    the agent routes to would send one vendor's model name to another the
    moment the two routes disagree."""
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1", pinned="fast-local")
    orchestrator, resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert resolver.calls == [("fast-local", None)]


async def test_an_unpinned_thread_still_routes_by_agent_key() -> None:
    """Null pin ⇒ the behaviour every thread had before the column existed."""
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1")
    orchestrator, resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert resolver.calls == [("recording", None)]


async def test_a_request_opening_a_fresh_thread_never_reads_a_pin() -> None:
    """There is nothing to read: the thread does not exist yet. Asking anyway
    would be a query per invocation for an answer that is always null."""
    _RecordingAgent.seen = []
    threads = _FakeThreads()
    orchestrator, resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert threads.route_reads == []
    assert resolver.calls == [("recording", None)]


async def test_an_unwired_conversations_seam_resolves_unpinned() -> None:
    """Degrade, do not refuse — the same choice ``_open_turn`` makes when the
    seam is absent."""
    _RecordingAgent.seen = []
    orchestrator, resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=None
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert resolver.calls == [("recording", None)]


async def test_the_pin_is_read_after_authorization_never_before() -> None:
    """A forbidden caller must not cause a read of somebody's conversation —
    the same rule that puts ``_authorize`` ahead of the credential lookup."""

    class _Guarded(BaseAgent):
        metadata = _metadata(
            "guarded", capabilities=frozenset({"chat"}), permissions=frozenset({"files:delete"})
        )

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            raise AssertionError("a refused caller must never reach the agent")
            yield  # pragma: no cover - unreachable, keeps this an async generator

    threads = _FakeThreads(known="conv-1", pinned="fast-local")
    orchestrator, resolver = _orchestrator(
        agents=[(_Guarded.metadata, _Guarded)],
        conversations=threads,
        authorization=None,
    )

    with pytest.raises(AppError):
        await orchestrator.invoke(
            _ctx(), "guarded", AgentRequest(conversation_id="conv-1", input={})
        )
    assert threads.route_reads == []
    # And no credential lookup either — the refusal cost nothing at all.
    assert resolver.calls == []


async def test_the_threads_pinned_files_become_the_runs_knowledge_scope() -> None:
    """BE-RAG-005 at this layer: the orchestrator is the only place that knows
    which thread a run belongs to, so it is where the pin becomes a scope the
    agent can act on."""
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1", pinned_files=("file-a", "file-b"))
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert _RecordingAgent.seen[0].knowledge_scope == ("file-a", "file-b")
    assert threads.scope_reads == ["conv-1"]


async def test_an_unpinned_thread_gives_the_run_an_empty_scope() -> None:
    """Empty means UNSCOPED — the whole workspace corpus, which is what every
    thread did before the table existed."""
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1")
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert _RecordingAgent.seen[0].knowledge_scope == ()


async def test_the_threads_own_space_becomes_the_runs_space() -> None:
    """س-32 (owner decision 2026-08-26) at this layer, and it is the same
    argument BE-RAG-005 makes one test up: the orchestrator is the only place
    that knows which thread a turn belongs to, so it is the only place the
    thread's space can become the boundary the agent reads and retrieves
    inside."""
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1")
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert _RecordingAgent.seen[0].space_id == _THREAD_SPACE
    assert threads.space_reads == ["conv-1"]


async def test_a_request_may_not_move_an_existing_thread_into_another_space() -> None:
    """The THREAD's space wins, and the request's is not consulted at all.

    ``_open_turn`` already ignores ``AgentRequest.space_id`` when a
    ``conversation_id`` is given, so believing it here would hand the agent a
    scope that disagrees with the row every message is written into — and would
    let a caller retrieve from another space by naming one on a thread that
    lives elsewhere. That is the isolation being decided by the request instead
    of by the data, which is precisely what س-32 forbids.
    """
    _RecordingAgent.seen = []
    threads = _FakeThreads(known="conv-1")
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(),
        "recording",
        AgentRequest(space_id="space-somewhere-else", conversation_id="conv-1", input={}),
    ):
        pass

    assert _RecordingAgent.seen[0].space_id == _THREAD_SPACE


async def test_a_fresh_thread_takes_its_space_from_the_request() -> None:
    """The other half: a turn that OPENS a thread has nothing to inherit from,
    so the request's space is the only honest source — and it is the same value
    ``_open_turn`` files the thread under, so the bundle and the row agree from
    the first turn."""
    _RecordingAgent.seen = []
    threads = _FakeThreads()
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert _RecordingAgent.seen[0].space_id == _SPACE
    assert threads.spaces == [_SPACE]
    # And no thread was read for it: there was none yet.
    assert threads.space_reads == []


async def test_a_run_with_no_thread_seam_gives_the_agent_no_space() -> None:
    """The degraded shape, and the direction it degrades in.

    An orchestrator with no conversations seam cannot know which space a turn
    belongs to. ``None`` reaches the bundle, and every consumer reads that as
    "read nothing" — the RAG agent does not retrieve, and the corpus header is
    not fetched. Before س-32 the same unknown silently read across every space,
    so this is strictly the safer failure.
    """
    _RecordingAgent.seen = []
    orchestrator, _resolver = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert _RecordingAgent.seen[0].space_id is None


async def test_a_request_that_opens_a_fresh_thread_never_asks_it_what_it_pinned() -> None:
    """There is nothing to ask: the thread does not exist until `_open_turn`
    creates it, and a read against ``None`` would be a fabricated id."""
    _RecordingAgent.seen = []
    threads = _FakeThreads()
    orchestrator, _resolver = _orchestrator(
        agents=[(_RecordingAgent.metadata, _RecordingAgent)], conversations=threads
    )

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(space_id=_SPACE, conversation_id=None, input={})
    ):
        pass

    assert threads.scope_reads == []
    assert _RecordingAgent.seen[0].knowledge_scope == ()


async def test_an_orchestrator_with_no_conversations_seam_runs_unscoped() -> None:
    """The "not wired" and the "not pinned" answers coincide deliberately: an
    orchestrator built without the seam must degrade to searching everything,
    never to an empty knowledge base."""
    _RecordingAgent.seen = []
    orchestrator, _resolver = _orchestrator(agents=[(_RecordingAgent.metadata, _RecordingAgent)])

    async for _ in await orchestrator.invoke(
        _ctx(), "recording", AgentRequest(conversation_id="conv-1", input={})
    ):
        pass

    assert _RecordingAgent.seen[0].knowledge_scope == ()
