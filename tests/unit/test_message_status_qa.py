"""th-2 (gap ث-2) -- QA's additions to the message status/fallback coverage.

Three holes the developers' tests left open:

* AC-1.4: the ``except Exception`` branch of the orchestrator loop (a failure of
  the machinery around the agent, not the agent's own raise) stores ``partial``.
* AC-1.8: a fixed apology answer with ``citations: []`` is ``complete`` and not
  a fallback.
* A fallback the REAL conversations module refuses to record (a name beyond the
  bound) costs one warning and a null, with the turn still answered -- the two
  halves (module drops it, orchestrator notices) tested together.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import StubActiveSpaces, build_conversations
from tests.unit.test_llm_fallback import (
    _DOWN,
    _LOCAL_MODEL,
    _ROUTING,
    _orchestrator,
    _resolver,
    _ScriptedLLM,
    _turn,
)
from tests.unit.test_orchestrator import (
    _SPACE,
    _ctx,
    _FakeResolver,
    _FakeThreads,
    _metadata,
)


class _ApologyAgent(BaseAgent):
    """What rag_agent answers when the confidence gate refuses: fixed text, no sources."""

    metadata = _metadata("apology", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type="final",
            data={"text": "لم أجد ما يكفي للإجابة.", "citations": [], "fallback": True},
        )


class _ExplodingExecutor(AgentLifecycleExecutor):
    """A failure of the machinery AROUND the agent: one token, then a bare raise."""

    async def drive(self, agent: BaseAgent, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "half an answer"})
        raise RuntimeError("machinery broke")


def _plain(agent: type[BaseAgent], threads: _FakeThreads, executor: AgentLifecycleExecutor):
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    return AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=executor,
            providers=_FakeResolver(),  # type: ignore[arg-type]
            conversations=threads,
            authorization=build_authorization(),
        )
    )


async def test_a_machinery_failure_after_tokens_is_stored_partial() -> None:
    """AC-1.4: the ``except Exception`` branch (reason=error) states ``partial``."""
    threads = _FakeThreads(known="conv-1")
    orchestrator = _plain(_ApologyAgent, threads, _ExplodingExecutor())
    stream = await orchestrator.invoke(
        _ctx(), "apology", AgentRequest(conversation_id="conv-1", input={"text": "hi"})
    )

    with pytest.raises(RuntimeError, match="machinery broke"):
        _ = [event async for event in stream]

    assert threads.statuses == [None, "partial"]
    assert threads.fallbacks == [None, None]
    assert threads.appended[-1][2].startswith("half an answer")


async def test_a_confidence_gate_apology_is_complete_and_not_a_fallback() -> None:
    """AC-1.8 and AC-2.5: the agent's own ``fallback: True`` is not a 6.4 switch."""
    threads = _FakeThreads(known="conv-1")
    orchestrator = _plain(_ApologyAgent, threads, AgentLifecycleExecutor())

    events = [
        e
        async for e in await orchestrator.invoke(
            _ctx(), "apology", AgentRequest(conversation_id="conv-1", input={"text": "hi"})
        )
    ]

    assert events[-1].type == "final"
    assert threads.statuses == [None, "complete"]
    assert threads.fallbacks == [None, None]
    assert threads.citations[-1] == ()  # the agent stated "no sources"


async def test_a_name_the_module_refuses_to_record_leaves_null_and_one_clean_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed fallback end to end: the REAL service drops a 300-character
    model name (bound 255), the orchestrator warns once, the answer is stored."""
    long_model = "m" * 300
    routing = {
        "llm": {
            "default": _ROUTING["llm"]["default"],  # type: ignore[index]
            "local": {"provider": "ollama", "model": long_model},
        }
    }
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", ["fine"])
    stack = build_conversations(spaces=StubActiveSpaces(live={_SPACE}))
    orchestrator, _fake, _capture = _orchestrator(cloud, local, routing=routing)
    # same orchestrator, real conversations module instead of the recording fake
    orchestrator._deps = OrchestratorDependencies(
        agents=orchestrator._deps.agents,
        executor=orchestrator._deps.executor,
        providers=_resolver(cloud, local, routing=routing),
        llm_fallback=_resolver(cloud, local, routing=routing),
        conversations=stack.service,
        authorization=build_authorization(),
    )

    with caplog.at_level("WARNING"):
        events = await _turn(orchestrator)

    assert events[-1].type == "final"
    assert "fallback" in events[-1].data  # the frame still tells the live reader
    (stored,) = stack.repository.messages.values()
    assistant = [m for m in stored if m.role.value == "assistant"][-1]
    assert assistant.status is not None and assistant.status.value == "complete"
    assert assistant.fallback is None
    assert assistant.content.text.startswith("⚠️")  # the notice sentence is still there
    warned = [r for r in caplog.records if r.getMessage() == "orchestrator.fallback_not_recorded"]
    assert len(warned) == 1
    rendered = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert long_model not in rendered
    assert _LOCAL_MODEL not in rendered
