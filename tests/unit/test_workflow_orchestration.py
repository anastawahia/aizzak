"""Unit tests for the workflow seam (Phase 4.7-e): the orchestrator's
``invoke_workflow``, the ``WorkflowRun`` handle, the D-12 conversation, the
engine's per-step ``AgentDepsProvider`` (4.7-e-1), and the per-step quota
enforcement and usage capture layered onto them (4.7-e-2).

Hermetic throughout — fake resolver/registry/agents/repository/ledger, no
network, no Docker. What these pin is the coordination 4.5 deliberately left
to 4.7: that a run gets its own conversation BEFORE any event escapes, that
``WorkflowResult`` is assembled from the ordered per-step ``final`` events and
is correct even for a run that was halted or abandoned, and — the change with
the sharpest teeth — that each step resolves ITS OWN agent key rather than
inheriting one bundle for the whole workflow.

The 4.7-e-2 section makes the same argument about money. Its central test runs
two steps that resolve DIFFERENT providers and asserts two charges naming them
individually: a single charge for the run cannot express that, which is why
the step is the billing unit. The rest guard the two boundaries billing
depends on — a step is charged when it ends (not when the run does), and the
last step is charged from the generator the CALLER holds (not from a wrapper
underneath it, which a client hanging up would never close).

Two tests deliberately run REAL collaborators rather than fakes: the
``ConversationService`` one, so the ``"workflow"`` string the agents layer
sends is proven against the actual ``ConversationKind`` enum instead of a fake
that would accept anything.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

import pytest

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies, WorkflowRun
from app.framework.agent_runtime.base_agent import (
    AgentDependencies,
    AgentEvent,
    AgentRequest,
    BaseAgent,
)
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.types import Uuid
from app.framework.workflows import (
    InMemoryWorkflowRegistry,
    SequentialWorkflowEngine,
    StaticAgentDeps,
    WorkflowDefinition,
    WorkflowStep,
)
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ConversationService,
    GetConversation,
    ListConversationFiles,
    StartConversation,
)
from app.modules.conversations.domain.entities import Conversation, Message
from app.modules.conversations.ports.inbound import AppendedMessage, StartedConversation
from app.modules.usage.ports.inbound import LimitDecision, UsageCharge
from tests.unit.support_access import build_authorization

_WORKSPACE = "018f0000-0000-7000-8000-000000000001"
_CONVERSATION = "018f0000-0000-7000-8000-0000000000aa"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WORKSPACE,
        user_id="018f0000-0000-7000-8000-0000000000ff",
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset({"member"}),
        request_id="req-1",
    )


def _metadata(key: str, *, capabilities: frozenset[str]) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=key,
        version="1.0.0",
        description="test agent",
        capabilities=capabilities,
        required_permissions=frozenset(),
    )


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
class _FakeLLM:
    """A structural ``LLMProvider`` — never called, only identity-checked."""

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
    """Records every resolution so the ROUTING KEY can be asserted per step."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.llm = _FakeLLM()
        self.calls: list[str] = []
        self._raises = raises

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        self.calls.append(capability)
        if self._raises is not None:
            raise self._raises
        return self.llm, ResolvedProvider(
            provider="fake", model=f"model-for-{capability}", api_key="k-123"
        )

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


class _FakeThreads:
    """A ``ConversationThreads`` that records how the thread was opened."""

    def __init__(
        self,
        *,
        raises: Exception | None = None,
        append_raises: Exception | None = None,
        fail_role: str | None = None,
    ) -> None:
        self.calls: list[tuple[ExecutionContext, str, str, str | None]] = []
        # 6.1-د-1: the run's transcript, as it was actually written.
        self.appended: list[tuple[str, str, str, tuple[str, ...], int | None]] = []
        self._raises = raises
        self._append_raises = append_raises
        self._fail_role = fail_role
        self._seq = 0

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        title: str | None = None,
    ) -> StartedConversation:
        self.calls.append((ctx, agent_key, kind, title))
        if self._raises is not None:
            raise self._raises
        return StartedConversation(id=_CONVERSATION, agent_key=agent_key, kind=kind)

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
        if self._append_raises is not None and self._fail_role in (None, role):
            raise self._append_raises
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


class _EchoAgent(BaseAgent):
    """Emits one ``final`` naming itself, and records the deps it was built
    with — the per-step resolution evidence."""

    metadata = _metadata("echo_a", capabilities=frozenset({"chat"}))
    seen: ClassVar[list[tuple[str, AgentDependencies]]] = []

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        _EchoAgent.seen.append((self.metadata.key, self.deps))
        yield AgentEvent(type="final", data={"who": self.metadata.key, "got": dict(req.input)})


class _EchoB(_EchoAgent):
    metadata = _metadata("echo_b", capabilities=frozenset({"chat"}))


class _Mediaish(_EchoAgent):
    """Declares no ``chat`` capability — the D-04 media-agent shape."""

    metadata = _metadata("mediaish", capabilities=frozenset({"image"}))


class _FailingAgent(BaseAgent):
    metadata = _metadata("failing", capabilities=frozenset({"chat"}))

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "partial"})
        raise RuntimeError("boom")


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def _definition(
    *agent_keys: str, key: str = "wf", name: str = "Test Pipeline"
) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key,
        name=name,
        steps=tuple(WorkflowStep(agent_key=k, input_map={}) for k in agent_keys),
    )


def _orchestrator(
    *,
    agents: Sequence[type[BaseAgent]],
    definitions: Sequence[WorkflowDefinition] = (),
    resolver: _FakeResolver | None = None,
    threads: _FakeThreads | None = None,
    wire_workflows: bool = True,
    stream_max_duration_s: float | None = None,
) -> tuple[AgentOrchestrator, _FakeResolver, _FakeThreads]:
    registry = InMemoryAgentRegistry()
    for agent_cls in agents:
        registry.register(agent_cls.metadata, agent_cls)
    workflows = InMemoryWorkflowRegistry()
    for definition in definitions:
        workflows.register(definition)
    used_resolver = resolver if resolver is not None else _FakeResolver()
    used_threads = threads if threads is not None else _FakeThreads()
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=used_resolver,  # type: ignore[arg-type]
        workflows=workflows if wire_workflows else None,
        conversations=used_threads if wire_workflows else None,
        stream_max_duration_s=stream_max_duration_s,
        authorization=build_authorization(),
    )
    return AgentOrchestrator(deps), used_resolver, used_threads


async def _drain(run: WorkflowRun) -> list[AgentEvent]:
    return [event async for event in run.events()]


# --------------------------------------------------------------------------- #
# D-12 — the workflow's own conversation                                      #
# --------------------------------------------------------------------------- #
async def test_a_run_opens_its_own_conversation() -> None:
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})

    (call,) = threads.calls
    _ctx_seen, agent_key, kind, title = call
    # Threaded under the WORKFLOW key, not under any step's agent: D-12 makes
    # the run the subject of the thread, so a two-step workflow is one
    # conversation and not two.
    assert agent_key == "wf"
    assert kind == "workflow"
    assert title == "Test Pipeline"
    assert run.conversation_id == _CONVERSATION


async def test_the_conversation_exists_before_the_first_event() -> None:
    """The load-bearing ordering: ``WorkflowRunOut.conversation_id`` has to be
    reportable on a STREAMING response, so it cannot be back-filled at the end
    of the run."""
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})

    # Nothing has been iterated yet.
    assert threads.calls, "the thread must be opened during pre-flight"
    assert run.conversation_id == _CONVERSATION
    assert run.result.outputs == []


async def test_the_run_carries_the_callers_context_to_the_thread() -> None:
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")]
    )
    ctx = _ctx()

    await orchestrator.invoke_workflow(ctx, "wf", {})

    (seen_ctx, _key, _kind, _title) = threads.calls[0]
    assert seen_ctx is ctx


async def test_a_thread_that_cannot_be_opened_never_starts_the_run() -> None:
    """A run whose conversation could not be written is not a run that should
    have started — it raises pre-flight, and no agent is created."""
    _EchoAgent.seen = []
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent],
        definitions=[_definition("echo_a")],
        threads=_FakeThreads(raises=AppError("db down", code="common.internal")),
    )

    with pytest.raises(AppError):
        await orchestrator.invoke_workflow(_ctx(), "wf", {})

    assert _EchoAgent.seen == []


# --------------------------------------------------------------------------- #
# Pre-flight — unknown workflow, unwired seam                                 #
# --------------------------------------------------------------------------- #
async def test_an_unknown_workflow_raises_workflow_unknown() -> None:
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent])

    with pytest.raises(NotFoundError) as excinfo:
        await orchestrator.invoke_workflow(_ctx(), "nope", {})

    # 03-api-spec's own code, not the inherited generic `common.not_found`.
    assert excinfo.value.code == "workflow.unknown"
    assert excinfo.value.status == 404


async def test_an_unknown_workflow_leaves_no_orphan_conversation() -> None:
    """Ordering evidence: the catalog is consulted BEFORE the thread is
    opened, so a typo in a workflow key does not litter the workspace with
    empty conversations."""
    orchestrator, _, threads = _orchestrator(agents=[_EchoAgent])

    with pytest.raises(NotFoundError):
        await orchestrator.invoke_workflow(_ctx(), "nope", {})

    assert threads.calls == []


async def test_an_unwired_workflow_seam_refuses_rather_than_inventing() -> None:
    """``WorkflowResult.conversation_id`` is not optional: with no thread to
    point at, the only alternatives to refusing are inventing an id or lying
    about it."""
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")], wire_workflows=False
    )

    with pytest.raises(AppError) as excinfo:
        await orchestrator.invoke_workflow(_ctx(), "wf", {})

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# WorkflowResult assembly                                                     #
# --------------------------------------------------------------------------- #
async def test_result_collects_each_steps_final_in_order() -> None:
    _EchoAgent.seen = []
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {"seed": 1})
    await _drain(run)

    assert [output["who"] for output in run.result.outputs] == ["echo_a", "echo_b"]


async def test_result_carries_the_workflow_key_and_its_conversation() -> None:
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent], definitions=[_definition("echo_a")])

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    result = await run.collect()

    assert result.workflow_key == "wf"
    assert result.conversation_id == _CONVERSATION
    assert len(result.outputs) == 1


async def test_collect_drains_the_stream_and_returns_the_result() -> None:
    """The non-streaming (``stream=false``) call in one line."""
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    result = await run.collect()

    assert [output["who"] for output in result.outputs] == ["echo_a", "echo_b"]


async def test_result_is_correct_for_an_abandoned_run() -> None:
    """The ``_TokenMeter`` discipline applied to outputs: a consumer that walks
    away mid-stream still sees what really was produced.

    An end-of-stream tally would report an EMPTY list here — Python does not
    finalize an async generator merely because iteration stopped.
    """
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    async for event in run.events():
        if event.type == "final":
            break  # walk away after the FIRST step

    assert [output["who"] for output in run.result.outputs] == ["echo_a"]


async def test_result_outputs_cannot_be_mutated_through_the_handle() -> None:
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent], definitions=[_definition("echo_a")])

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await _drain(run)
    run.result.outputs.append({"forged": True})

    assert len(run.result.outputs) == 1


async def test_a_halted_run_keeps_the_outputs_of_the_steps_that_ran() -> None:
    _EchoAgent.seen = []
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent, _FailingAgent, _EchoB],
        definitions=[_definition("echo_a", "failing", "echo_b")],
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    events = await _drain(run)

    assert any(event.type == "error" for event in events)
    # The first step's real output survives the halt...
    assert [output["who"] for output in run.result.outputs] == ["echo_a"]
    # ...and `echo_b` — the step AFTER the failure — never ran at all, which is
    # what "halt" has to mean: chaining a step off an output that was never
    # produced would feed it whatever the blackboard happened to still hold.
    assert [key for key, _deps in _EchoAgent.seen] == ["echo_a"]


# --------------------------------------------------------------------------- #
# 6.1-د-1 — the run's transcript in its own thread, and its status            #
# --------------------------------------------------------------------------- #
def _roles(threads: _FakeThreads) -> list[str]:
    return [role for _cid, role, _text, _att, _tok in threads.appended]


async def test_the_initial_input_is_written_as_the_runs_opening_turn() -> None:
    """Until 6.1-د-1 a run opened its D-12 thread and left it EMPTY. The input
    is written PRE-FLIGHT — asserted here before a single event is pulled."""
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {"text": "summarise this"})

    assert threads.appended == [(_CONVERSATION, "user", "summarise this", (), None)]
    await _drain(run)


async def test_each_completed_step_is_appended_to_the_thread_in_order() -> None:
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {"text": "go"})
    await _drain(run)

    assert _roles(threads) == ["user", "assistant", "assistant"]
    # The transcript says WHICH step produced each turn, in step order. These
    # outputs carry no `text` key, so they are serialised losslessly rather
    # than dropped — the same choice `_turn_content` makes for a media reply.
    written = [json.loads(text)["who"] for _cid, role, text, _a, _t in threads.appended[1:]]
    assert written == ["echo_a", "echo_b"]


async def test_a_halted_run_records_only_the_steps_that_produced_output() -> None:
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent, _FailingAgent, _EchoB],
        definitions=[_definition("echo_a", "failing", "echo_b")],
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await _drain(run)

    # One completed step ⇒ one assistant turn. The failed step wrote nothing
    # (it produced nothing), which is what makes the count readable later as
    # "how far this run got".
    assert _roles(threads) == ["user", "assistant"]
    assert run.status == "failed"


async def test_an_abandoned_run_keeps_the_turns_it_already_wrote() -> None:
    """The 4.7-c-2 lesson applied to persistence: writing from a wrapper AROUND
    ``events()`` would leave an abandoned run's completed steps unrecorded,
    because closing an outer generator does not cascade into the sub-iterator
    of an ``async for``. The steps were produced and billed; they are kept."""
    orchestrator, _, threads = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    async for event in run.events():
        if event.type == "final":
            break  # walk away after the FIRST step

    assert _roles(threads) == ["user", "assistant"]
    # And nobody may claim it finished: a consumer that walked away leaves no
    # way to tell, so the handle keeps saying `running` rather than guessing.
    assert run.status == "running"


async def test_a_step_write_failure_never_halts_the_run() -> None:
    """The ``_persist_reply`` trade, applied per step: the output has been
    produced and streamed, so a bookkeeping fault must not replace it with an
    error — nor stop the steps that come after it."""
    threads = _FakeThreads(append_raises=ConflictError("write lost a race"), fail_role="assistant")
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent, _EchoB],
        definitions=[_definition("echo_a", "echo_b")],
        threads=threads,
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    events = await _drain(run)

    assert [event.type for event in events].count("final") == 2
    assert run.status == "completed"
    assert _roles(threads) == ["user"]  # only the opening turn survived


async def test_an_opening_turn_that_cannot_be_written_never_starts_the_run() -> None:
    """Pre-flight means pre-flight: the failure surfaces as itself while the
    HTTP status is still open, and no agent is ever created."""
    _EchoAgent.seen = []
    threads = _FakeThreads(append_raises=ConflictError("thread is gone"), fail_role="user")
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")], threads=threads
    )

    with pytest.raises(ConflictError):
        await orchestrator.invoke_workflow(_ctx(), "wf", {})

    assert _EchoAgent.seen == []


async def test_run_id_is_the_conversation_id() -> None:
    """`03 §2` carries both fields; `01-data-model` stores no run row. Minting
    a second id would hand the client one that ``GET /workflows/runs/{id}``
    could never resolve."""
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent], definitions=[_definition("echo_a")])

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})

    assert run.run_id == run.conversation_id == _CONVERSATION
    await _drain(run)


async def test_status_is_running_before_the_stream_ends_and_completed_after() -> None:
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent], definitions=[_definition("echo_a")])

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    assert run.status == "running"

    await _drain(run)

    assert run.status == "completed"


# --------------------------------------------------------------------------- #
# Per-step dependency resolution — the 4.7-e-1 correction                     #
# --------------------------------------------------------------------------- #
async def test_each_step_resolves_its_own_agent_key() -> None:
    """The bug a single shared bundle caused: an operator pinning each agent to
    its own provider/model (D-16, FR-73) had that configuration silently
    discarded for every step of a workflow."""
    _EchoAgent.seen = []
    orchestrator, resolver, _ = _orchestrator(
        agents=[_EchoAgent, _EchoB], definitions=[_definition("echo_a", "echo_b")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await _drain(run)

    # Routed by the STEP's agent key — never once by the workflow key.
    assert resolver.calls == ["echo_a", "echo_b"]
    assert "wf" not in resolver.calls
    models = [deps.llm.model for _key, deps in _EchoAgent.seen if deps.llm is not None]
    assert models == ["model-for-echo_a", "model-for-echo_b"]


async def test_a_step_needing_no_llm_resolves_none() -> None:
    """D-04 preserved inside a workflow: an image → video pipeline must not
    demand a credential for a provider no step ever calls."""
    _EchoAgent.seen = []
    orchestrator, resolver, _ = _orchestrator(
        agents=[_Mediaish], definitions=[_definition("mediaish")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await _drain(run)

    assert resolver.calls == []
    ((_key, deps),) = _EchoAgent.seen
    assert deps.llm is None


async def test_a_step_agent_gets_a_metered_llm_like_a_direct_invocation() -> None:
    """A step and a direct invocation must hand the agent the same thing — an
    agent that behaved differently depending on how it was reached would be the
    exact bug the shared ``dependencies_for`` path exists to prevent."""
    _EchoAgent.seen = []
    orchestrator, resolver, _ = _orchestrator(
        agents=[_EchoAgent], definitions=[_definition("echo_a")]
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await _drain(run)

    ((_key, deps),) = _EchoAgent.seen
    assert deps.llm is not None
    # The transparent metering decorator, not the raw adapter (4.7-c-2).
    assert deps.llm.provider is not resolver.llm
    assert deps.llm.provider.provider == resolver.llm.provider
    assert deps.llm.api_key == "k-123"


async def test_a_resolution_failure_becomes_a_terminal_event_not_a_raise() -> None:
    """Resolution is real I/O performed INSIDE the run, so it must fail like a
    step, not detonate out of the generator past the caller's handling."""
    orchestrator, _, _ = _orchestrator(
        agents=[_EchoAgent],
        definitions=[_definition("echo_a")],
        resolver=_FakeResolver(raises=NotFoundError("no credential")),
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    events = await _drain(run)

    assert [event.type for event in events] == ["error"]
    assert events[0].data["status"] == 404
    assert run.result.outputs == []


# --------------------------------------------------------------------------- #
# The engine's own contract                                                   #
# --------------------------------------------------------------------------- #
async def test_the_engine_asks_the_provider_once_per_step() -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def for_agent(self, agent_key: str) -> AgentDependencies:
            self.asked.append(agent_key)
            return AgentDependencies()

    registry = InMemoryAgentRegistry()
    registry.register(_EchoAgent.metadata, _EchoAgent)
    registry.register(_EchoB.metadata, _EchoB)
    recorder = _Recorder()
    engine = SequentialWorkflowEngine(registry, AgentLifecycleExecutor(), recorder)

    async for _event in engine.run(_ctx(), _definition("echo_a", "echo_b"), {}):
        pass

    assert recorder.asked == ["echo_a", "echo_b"]


async def test_static_deps_hands_every_step_the_same_bundle() -> None:
    bundle = AgentDependencies()
    provider = StaticAgentDeps(bundle)

    assert await provider.for_agent("a") is bundle
    assert await provider.for_agent("b") is bundle


# --------------------------------------------------------------------------- #
# The real ConversationService under the real orchestrator                    #
# --------------------------------------------------------------------------- #
class _FakeConversationRepo:
    """An in-memory ``ConversationRepository`` — enough for ``StartConversation``."""

    def __init__(self) -> None:
        self.added: list[Conversation] = []
        self.messages: list[Message] = []

    async def get(self, ctx: ExecutionContext, conversation_id: Uuid) -> Conversation | None:
        return next((c for c in self.added if c.id == conversation_id), None)

    async def add(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        self.added.append(conversation)

    async def save(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        raise AssertionError("not exercised")

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None = None
    ) -> object:
        raise AssertionError("not exercised")

    async def append_message(self, ctx: ExecutionContext, message: Message) -> None:
        # 6.1-د-1: a workflow run writes its turns through the same port, so
        # this is exercised now — by the REAL `AppendMessage` use-case.
        self.messages.append(message)

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: Uuid, *, limit: int, cursor: str | None = None
    ) -> object:
        raise AssertionError("not exercised")


def _conversation_service(repo: _FakeConversationRepo) -> ConversationService:
    """The REAL service over a fake repository — `start` + `append` both wired,
    since the orchestrator now writes turns through the same port, plus `get`
    for the BE-RAG-003 route read and `list_files` for the BE-RAG-005 scope
    read (a workflow run pins neither, but the port is one protocol and a
    partial construction would not satisfy it)."""
    return ConversationService(  # type: ignore[arg-type]
        StartConversation(repo),
        AppendMessage(repo),
        GetConversation(repo),
        ListConversationFiles(repo),  # type: ignore[arg-type]
    )


async def test_the_workflow_kind_string_is_the_real_domain_enum() -> None:
    """The whole point of running the REAL service here: the agents layer sends
    ``kind`` as a plain string, and a fake would accept any string at all. Only
    the real ``ConversationKind`` can prove ``"workflow"`` is the right one.
    """
    repo = _FakeConversationRepo()
    service = _conversation_service(repo)
    registry = InMemoryAgentRegistry()
    registry.register(_EchoAgent.metadata, _EchoAgent)
    workflows = InMemoryWorkflowRegistry()
    workflows.register(_definition("echo_a"))
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),  # type: ignore[arg-type]
            workflows=workflows,
            conversations=service,
            authorization=build_authorization(),
        )
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    result = await run.collect()

    (conversation,) = repo.added
    assert conversation.kind.value == "workflow"
    assert conversation.agent_key.value == "wf"
    assert conversation.title == "Test Pipeline"
    assert conversation.workspace_id == _WORKSPACE
    # The id the caller is handed is the id that was persisted.
    assert result.conversation_id == conversation.id


async def test_an_invalid_conversation_kind_is_a_422_not_a_500() -> None:
    """``ConversationKind("nope")`` raises a bare ``ValueError`` — a 500 at the
    API edge for what is plainly a caller mistake."""
    service = _conversation_service(_FakeConversationRepo())

    with pytest.raises(ValidationError) as excinfo:
        await service.start(_ctx(), agent_key="wf", kind="nope")

    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# 4.7-e-2 — quota enforcement + usage capture across workflow steps           #
# --------------------------------------------------------------------------- #
class _CountingLLM:
    """Streams one delta plus a terminal chunk carrying real counters, under a
    provider NAME of its own — so a ledger row can be traced back to the step
    that produced it."""

    def __init__(self, provider: str, *, prompt: int, completion: int) -> None:
        self.provider = provider
        self._prompt = prompt
        self._completion = completion

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("not exercised")

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

    def supports(self, capability: str) -> bool:
        return True


class _CountingResolver:
    """Gives every agent key its OWN provider — the per-agent routing an
    operator configures (D-16/FR-73), made visible in the ledger."""

    def __init__(self, *, prompt: int = 10, completion: int = 5) -> None:
        self.calls: list[str] = []
        self._prompt = prompt
        self._completion = completion

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_CountingLLM, ResolvedProvider]:
        self.calls.append(capability)
        llm = _CountingLLM(f"prov-{capability}", prompt=self._prompt, completion=self._completion)
        return llm, ResolvedProvider(
            provider=llm.provider, model=f"model-for-{capability}", api_key="k-123"
        )

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


class _FakeEnforcement:
    """Records every check; allows the first ``allow_first`` and denies after."""

    def __init__(self, *, allow_first: int | None = None, reason: str = "quota_exceeded") -> None:
        self.calls: list[tuple[str, str, int | None]] = []
        self._allow_first = allow_first
        self._reason = reason

    async def check(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision:
        self.calls.append((agent, provider, estimated_tokens))
        if self._allow_first is None or len(self.calls) <= self._allow_first:
            return LimitDecision(allowed=True)
        return LimitDecision(allowed=False, reason=self._reason, remaining=0)


class _FakeCapture:
    """Records every charge; can be made to fail like a ledger outage."""

    def __init__(self, *, fails: bool = False) -> None:
        self.charges: list[UsageCharge] = []
        self._fails = fails

    async def record(self, ctx: ExecutionContext, charge: UsageCharge) -> None:
        if self._fails:
            raise RuntimeError("ledger unavailable")
        self.charges.append(charge)


class _ConsumingAgent(BaseAgent):
    """Consumes its LLM exactly as a real agent does, so the metering decorator
    is exercised through the real call path."""

    metadata = _metadata("consume_a", capabilities=frozenset({"chat"}))

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
        yield AgentEvent(type="final", data={"who": self.metadata.key})


class _ConsumingB(_ConsumingAgent):
    metadata = _metadata("consume_b", capabilities=frozenset({"chat"}))


def _billed_orchestrator(
    *,
    agents: Sequence[type[BaseAgent]],
    definition: WorkflowDefinition,
    enforcement: _FakeEnforcement | None = None,
    capture: _FakeCapture | None = None,
    stream_max_duration_s: float | None = None,
) -> tuple[AgentOrchestrator, _FakeEnforcement, _FakeCapture, _FakeThreads]:
    registry = InMemoryAgentRegistry()
    for agent_cls in agents:
        registry.register(agent_cls.metadata, agent_cls)
    workflows = InMemoryWorkflowRegistry()
    workflows.register(definition)
    used_enforcement = enforcement if enforcement is not None else _FakeEnforcement()
    used_capture = capture if capture is not None else _FakeCapture()
    used_threads = _FakeThreads()
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=_CountingResolver(),  # type: ignore[arg-type]
        workflows=workflows,
        conversations=used_threads,
        usage_enforcement=used_enforcement,
        usage_capture=used_capture,
        stream_max_duration_s=stream_max_duration_s,
        authorization=build_authorization(),
    )
    return AgentOrchestrator(deps), used_enforcement, used_capture, used_threads


async def test_each_step_is_charged_under_its_own_agent_and_provider() -> None:
    """The shape 4.7-e-1 could not produce, and the whole reason the step is the
    billing unit: the two steps resolve DIFFERENT providers, while
    ``UsageCharge`` names one agent and one provider — so a single charge for
    the run would have to misname at least one of them."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
    )

    await (await orchestrator.invoke_workflow(_ctx(), "wf", {})).collect()

    assert [(c.agent, c.provider, c.tokens) for c in capture.charges] == [
        ("consume_a", "prov-consume_a", 15),
        ("consume_b", "prov-consume_b", 15),
    ]
    # Distinct idempotency keys: one operation per step, not one per run.
    assert len({c.operation_id for c in capture.charges}) == 2
    assert not any(c.estimated for c in capture.charges)


async def test_a_step_is_billed_when_it_ends_not_when_the_run_ends() -> None:
    """The eager-billing half. Counting charges as each event passes shows step
    1's row landing BEFORE step 2 emits anything — deferring every charge to
    end-of-stream would leave this list all zeros."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    seen = [(event.type, len(capture.charges)) async for event in run.events()]

    assert seen == [("token", 0), ("final", 0), ("token", 1), ("final", 1)]
    assert len(capture.charges) == 2


async def test_an_abandoned_run_still_bills_the_step_it_was_walked_away_from() -> None:
    """4.7-c-2's lesson one layer up: closing an async generator does not
    cascade into the sub-iterator of its ``async for``, so the ``finally`` has
    to live in the generator the CALLER holds. Move it into a wrapper below
    ``WorkflowRun.events`` and a client hanging up mid-step bills nothing."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    stream = run.events()
    await stream.__anext__()  # mid-step-1: one token delivered
    await stream.aclose()  # type: ignore[attr-defined]  # the client hangs up

    (charge,) = capture.charges
    assert charge.agent == "consume_a"
    assert charge.tokens > 0
    # The terminal chunk never arrived, so the counters never did either.
    assert charge.estimated is True


async def test_the_run_is_gated_by_quota_before_its_conversation_exists() -> None:
    """FR-132's "a denial must cost nothing", applied to a run: pre-flight, so
    Phase 6 answers a real 429, and no orphan thread is left behind."""
    orchestrator, enforcement, capture, threads = _billed_orchestrator(
        agents=[_ConsumingAgent],
        definition=_definition("consume_a"),
        enforcement=_FakeEnforcement(allow_first=0),
    )

    with pytest.raises(RateLimitedError) as excinfo:
        await orchestrator.invoke_workflow(_ctx(), "wf", {})

    assert excinfo.value.code == "usage.quota_exceeded"
    assert excinfo.value.status == 429
    assert threads.calls == []
    assert capture.charges == []
    # Checked under the WORKFLOW key, with no step resolved yet to name.
    assert enforcement.calls == [("wf", "none", None)]


async def test_an_unknown_workflow_is_404_even_for_a_workspace_over_quota() -> None:
    """The catalog lookup precedes the quota check on purpose: it is a free
    in-memory read, and a bogus key deserves its own error rather than a 429
    about a workflow that does not exist."""
    orchestrator, enforcement, _, threads = _billed_orchestrator(
        agents=[_ConsumingAgent],
        definition=_definition("consume_a"),
        enforcement=_FakeEnforcement(allow_first=0),
    )

    with pytest.raises(NotFoundError) as excinfo:
        await orchestrator.invoke_workflow(_ctx(), "nope", {})

    assert excinfo.value.code == "workflow.unknown"
    assert enforcement.calls == []
    assert threads.calls == []


async def test_every_step_is_checked_under_its_own_agent_and_provider() -> None:
    """Same order as ``invoke`` — resolve, then enforce — so an agent-scoped
    and a provider-scoped limit both get their say on every step."""
    orchestrator, enforcement, _, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
    )

    await (await orchestrator.invoke_workflow(_ctx(), "wf", {})).collect()

    assert enforcement.calls == [
        ("wf", "none", None),
        ("consume_a", "prov-consume_a", None),
        ("consume_b", "prov-consume_b", None),
    ]


async def test_a_mid_run_denial_halts_the_workflow_and_keeps_what_ran() -> None:
    """A workspace that exhausts its quota mid-workflow keeps the steps it
    already paid for and stops there — it neither finishes on credit nor loses
    the work it had already bought. The code survives intact because
    ``RateLimitedError`` is an ``AppError``."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
        enforcement=_FakeEnforcement(allow_first=2),  # the run, then step 1
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    events = [event async for event in run.events()]

    assert events[-1].type == "error"
    assert events[-1].data["code"] == "usage.quota_exceeded"
    assert events[-1].data["status"] == 429
    # Step 1 ran and is billed; step 2 never ran and is never billed.
    assert [c.agent for c in capture.charges] == ["consume_a"]
    assert len(run.result.outputs) == 1


async def test_a_step_that_consumes_nothing_leaves_no_ledger_row() -> None:
    """A media step inside a pipeline (D-04) resolves no LLM and spends no
    tokens; a zero-token row per step would be pure noise in an append-only
    table. It is still quota-checked, under the same placeholder provider a
    charge would have carried."""
    orchestrator, enforcement, capture, _ = _billed_orchestrator(
        agents=[_Mediaish, _ConsumingB],
        definition=_definition("mediaish", "consume_b"),
    )

    await (await orchestrator.invoke_workflow(_ctx(), "wf", {})).collect()

    assert [c.agent for c in capture.charges] == ["consume_b"]
    assert ("mediaish", "none", None) in enforcement.calls


async def test_a_ledger_outage_never_halts_the_run() -> None:
    """Capture now runs at a step BOUNDARY, inside the engine's failure window
    — so a raising ledger would become a terminal error event and kill a
    workflow that was answering perfectly well. The swallow is what keeps a
    billing outage from becoming an availability outage."""
    orchestrator, _, _, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
        capture=_FakeCapture(fails=True),
    )

    result = await (await orchestrator.invoke_workflow(_ctx(), "wf", {})).collect()

    assert [event["who"] for event in result.outputs] == ["consume_a", "consume_b"]


async def test_draining_the_run_twice_does_not_double_bill() -> None:
    """``close_step`` clears before it awaits: the last step is reachable from
    both the step boundary and the end of the stream, and charging it twice
    would bill a workspace for tokens it spent once."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _ConsumingB],
        definition=_definition("consume_a", "consume_b"),
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    await run.collect()
    await run.collect()

    assert [c.agent for c in capture.charges] == ["consume_a", "consume_b"]


async def test_an_unmetered_deployment_still_runs_workflows() -> None:
    """Both usage seams stay optional (the uniform rule): absent, nothing is
    enforced and nothing is captured, and the run itself is unaffected."""
    orchestrator, _, _ = _orchestrator(agents=[_EchoAgent], definitions=[_definition("echo_a")])

    result = await (await orchestrator.invoke_workflow(_ctx(), "wf", {})).collect()

    assert len(result.outputs) == 1


# --------------------------------------------------------------------------- #
# 5.3-أ — the total stream deadline applies to a workflow run too             #
# --------------------------------------------------------------------------- #
class _StallingB(_ConsumingAgent):
    """Consumes its LLM (so the cut step has real tokens to bill), then stalls
    forever — the run-level shape the deadline exists for."""

    metadata = _metadata("stall_b", capabilities=frozenset({"chat"}))

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        async for event in super().run(req):
            if event.type == "final":
                await asyncio.sleep(3)  # cancelled by the run deadline
            yield event


async def test_an_overrunning_run_halts_with_the_terminal_error_event() -> None:
    """One deadline bounds the WHOLE run, not each step: step 1 completes,
    step 2 overruns, and the stream ends with B1's terminal ``error`` — while
    ``outputs`` keeps exactly the steps that really finished (the timeout
    event is not a step ``final`` and must never be collected as one)."""
    orchestrator, _, capture, _ = _billed_orchestrator(
        agents=[_ConsumingAgent, _StallingB],
        definition=_definition("consume_a", "stall_b"),
        stream_max_duration_s=0.05,
    )

    run = await orchestrator.invoke_workflow(_ctx(), "wf", {})
    events = await _drain(run)

    assert events[-1].type == "error"
    assert events[-1].data["code"] == "agent.failed"
    assert events[-1].data["status"] == 502
    assert [o.get("who") for o in run.result.outputs] == ["consume_a"]
    # A run the cap cut is `failed`, never `completed` (6.1-د-1): the timeout
    # path bypasses the in-band `error` check, so it sets the status itself.
    assert run.status == "failed"
    # BOTH steps are billed: step 1 at its boundary, and the cut step from
    # `on_finish` in the handle's `finally` — its meter was live (eager), so
    # the tokens it streamed before stalling are real and charged.
    assert [(c.agent, c.tokens) for c in capture.charges] == [
        ("consume_a", 15),
        ("stall_b", 15),
    ]
