"""capacity-plan 6.4 — a chat turn whose cloud provider fails before answering
is answered by the local model, and the user is told (owner decision
2026-10-03).

The step's acceptance is "a fallback enabled in CONFIGURATION, with a test
proving the three conditions", and this file is that test:

1. **Functional equivalence** — a call carrying tools is never re-sent to a
   provider that cannot take them (``_FallbackLLM._eligible``).
2. **The tenant's data policy** — the fallback can only lead to a local
   provider: a cloud route refuses to construct the resolver, and a turn
   that is already local has nowhere to go.
3. **Idempotent / a pure read** — the switch only happens BEFORE the first
   chunk; after it, the turn is cut exactly as without a fallback.

Most of it runs through the REAL ``SettingsProviderResolver`` (both faces:
``ProviderResolver`` and ``LlmFallback``) and the real orchestrator, with
scripted adapters at the bottom — so the configuration decides, not a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

import pytest
from prometheus_client import REGISTRY

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.v1.sse import sse_stream
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.errors import AppError, RateLimitedError, ValidationError
from app.framework.observability.metrics import LLM_FALLBACK_METRIC
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LLMProvider, LlmResult
from app.framework.providers import LlmFallback, SettingsProviderResolver
from app.framework.settings.settings import ProviderGuardSettings
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.guard import GuardedLLM, ProviderGuard
from app.infrastructure.ai_providers.llm.shared import ProviderFailure
from tests.unit.support_access import build_authorization
from tests.unit.test_orchestrator import (
    _SPACE,
    _ctx,
    _FakeCapture,
    _FakeEnforcement,
    _FakeThreads,
    _metadata,
)
from tests.unit.test_provider_resolver import SpyKeyResolver

_CLOUD_MODEL = "gpt-cloud"
_LOCAL_MODEL = "gemma-local"
_ROUTING: Json = {
    "llm": {
        "default": {"provider": "openai", "model": _CLOUD_MODEL},
        "local": {"provider": "ollama", "model": _LOCAL_MODEL},
    }
}
_DOWN = ProviderFailure("openai is unreachable", transient=True, retryable=True)
_TIMED_OUT = ProviderFailure("openai timed out", transient=True)
_BAD_KEY = ProviderFailure("openai rejected the key")
_NOTICE_AR = "⚠️ تعذّر الوصولُ إلى النموذج السحابيّ، فأجاب النموذجُ المحلّيّ عن هذا السؤال."
_GREETING = "أهلاً"
_GREETING_TAIL = " بك"
_NOTICE_EN = "⚠️ The cloud model could not be reached, so the local model answered this question."


class _ScriptedLLM:
    """A structural ``LLMProvider`` that records every call and plays a script:
    raise before the first chunk, stream ``deltas``, then optionally raise."""

    def __init__(
        self,
        provider: str,
        deltas: Sequence[str] = (),
        *,
        fails_first: BaseException | None = None,
        fails_after: BaseException | None = None,
        tools: bool = True,
    ) -> None:
        self.provider = provider
        self._deltas = tuple(deltas)
        self.fails_first = fails_first
        self._fails_after = fails_after
        self._tools = tools
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        self.calls.append((params.model, api_key))
        if self.fails_first is not None:
            raise self.fails_first
        return LlmResult(
            content="".join(self._deltas),
            finish_reason="stop",
            prompt_tokens=3,
            completion_tokens=len(self._deltas),
        )

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        # Recorded on the first PULL, like a real adapter's request goes out:
        # `GuardedLLM.stream` builds the adapter's generator eagerly and may
        # then never iterate it (an open circuit).
        async def _gen() -> AsyncIterator[LlmChunk]:
            self.calls.append((params.model, api_key))
            if self.fails_first is not None:
                raise self.fails_first
            for delta in self._deltas:
                yield LlmChunk(delta=delta)
            if self._fails_after is not None:
                raise self._fails_after
            yield LlmChunk(delta="", finish_reason="stop", prompt_tokens=3, completion_tokens=2)

        return _gen()

    def supports(self, capability: str) -> bool:
        return capability == "streaming" or (capability == "tools" and self._tools)


class _ChatAgent(BaseAgent):
    """Streams one answer from its LLM, exactly as the shipped agents do."""

    metadata = _metadata("chat", capabilities=frozenset({"chat"}))
    tools: ClassVar[list[Json] | None] = None

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        binding = self.deps.llm
        assert binding is not None
        parts: list[str] = []
        async for chunk in binding.provider.stream(
            [LlmMessage(role="user", content="q")],
            LlmParams(model=binding.model, tools=type(self).tools),
            binding.api_key,
        ):
            if chunk.delta:
                parts.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(type="final", data={"text": "".join(parts)})


class _ToolAgent(_ChatAgent):
    metadata = _metadata("tooled", capabilities=frozenset({"chat"}))
    tools: ClassVar[list[Json] | None] = [{"name": "lookup", "parameters": {}}]


class _TwoCallAgent(BaseAgent):
    """A classify-then-answer agent: one ``complete``, then one ``stream`` —
    the RAG agent's shape, and the one that can mix two providers in a turn."""

    metadata = _metadata("two", capabilities=frozenset({"chat"}))

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        binding = self.deps.llm
        assert binding is not None
        messages = [LlmMessage(role="user", content="q")]
        params = LlmParams(model=binding.model)
        first = await binding.provider.complete(messages, params, binding.api_key)
        parts = [first.content]
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                parts.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(type="final", data={"text": "|".join(parts)})


def _resolver(
    cloud: LLMProvider,
    local: LLMProvider,
    *,
    fallback_route: str = "local",
    routing: Json | None = None,
) -> SettingsProviderResolver:
    return SettingsProviderResolver(
        routing=_ROUTING if routing is None else routing,
        llm_providers={"openai": cloud, "ollama": local},
        embedding_providers={},
        image_providers={},
        key_resolver=SpyKeyResolver("sk-cloud"),
        keyless_providers=frozenset({"ollama"}),
        fallback_route=fallback_route,
    )


def _orchestrator(
    cloud: LLMProvider,
    local: LLMProvider,
    *,
    agent: type[BaseAgent] = _ChatAgent,
    fallback: LlmFallback | str | None = "wired",
    routing: Json | None = None,
) -> tuple[AgentOrchestrator, _FakeThreads, _FakeCapture]:
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    resolver = _resolver(cloud, local, routing=routing)
    threads = _FakeThreads()
    enforcement = _FakeEnforcement()
    capture = _FakeCapture()
    enforcement.bind_capture(capture)
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=resolver,
        llm_fallback=resolver if fallback == "wired" else fallback,  # type: ignore[arg-type]
        conversations=threads,
        usage_enforcement=enforcement,
        usage_capture=capture,
        authorization=build_authorization(),
    )
    return AgentOrchestrator(deps), threads, capture


async def _turn(
    orchestrator: AgentOrchestrator, key: str = "chat", text: str = "ما هي السياسة؟"
) -> list[AgentEvent]:
    req = AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": text})
    return [event async for event in await orchestrator.invoke(_ctx(), key, req)]


def _stored_reply(threads: _FakeThreads) -> str:
    replies = [text for _cid, role, text, _att, _tok in threads.appended if role == "assistant"]
    assert len(replies) == 1
    return replies[0]


def _fallbacks(outcome: str) -> float:
    value = REGISTRY.get_sample_value(
        LLM_FALLBACK_METRIC,
        {"from_provider": "openai", "to_provider": "ollama", "outcome": outcome},
    )
    return value or 0.0


# --------------------------------------------------------------------------- #
# Configuration — the decision lives in settings, and condition 2 is checked  #
# where the table is parsed                                                   #
# --------------------------------------------------------------------------- #
async def test_an_empty_fallback_route_is_the_written_refusal() -> None:
    resolver = _resolver(_ScriptedLLM("openai"), _ScriptedLLM("ollama"), fallback_route="")
    assert await resolver.resolve_llm_fallback(_ctx(), primary="openai") is None


def test_a_fallback_route_missing_from_the_table_refuses_to_boot() -> None:
    with pytest.raises(ValidationError, match=r"'lokal' is not a configured llm route"):
        _resolver(_ScriptedLLM("openai"), _ScriptedLLM("ollama"), fallback_route="lokal")


def test_a_fallback_route_to_a_cloud_refuses_to_boot() -> None:
    """Condition 2: a fallback never moves a turn to a cloud — even one the
    table routes, even as the only other route there is."""
    routing: Json = {
        "llm": {
            "default": {"provider": "ollama", "model": _LOCAL_MODEL},
            "cloud": {"provider": "openai", "model": _CLOUD_MODEL},
        }
    }
    with pytest.raises(ValidationError, match="may only lead to a local provider"):
        _resolver(
            _ScriptedLLM("openai"), _ScriptedLLM("ollama"), fallback_route="cloud", routing=routing
        )


async def test_a_cloud_turn_falls_back_to_the_local_route_with_no_credential_lookup() -> None:
    local = _ScriptedLLM("ollama")
    resolver = _resolver(_ScriptedLLM("openai"), local)
    found = await resolver.resolve_llm_fallback(_ctx(), primary="openai")
    assert found is not None
    provider, resolved = found
    assert provider is local
    assert (resolved.provider, resolved.model, resolved.api_key) == ("ollama", _LOCAL_MODEL, "")


async def test_a_local_turn_has_nowhere_to_fall_back_to() -> None:
    """Condition 2 from the other side: local → cloud is never offered."""
    resolver = _resolver(_ScriptedLLM("openai"), _ScriptedLLM("ollama"))
    assert await resolver.resolve_llm_fallback(_ctx(), primary="ollama") is None


def test_a_circuit_open_refusal_reads_as_the_provider_being_down() -> None:
    guard = ProviderGuard(
        "openai", ProviderGuardSettings(circuit_failure_threshold=1), clock=lambda: 0.0
    )
    guard.record(_TIMED_OUT)
    with pytest.raises(ProviderFailure) as refused:
        guard.admit()
    assert (refused.value.transient, refused.value.retryable) == (True, False)


# --------------------------------------------------------------------------- #
# The switch, and what the user is told                                       #
# --------------------------------------------------------------------------- #
async def test_a_cloud_failure_before_the_first_chunk_is_answered_by_the_local_model() -> None:
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", [_GREETING, _GREETING_TAIL])
    orchestrator, threads, capture = _orchestrator(cloud, local)
    served_before = _fallbacks("served")

    events = await _turn(orchestrator)

    # The notice comes FIRST, before a single word of the local answer.
    assert [e.type for e in events] == ["notice", "token", "token", "final"]
    notice = events[0].data
    assert notice["kind"] == "llm_fallback"
    assert notice["detail"] == _NOTICE_AR
    assert notice["from"] == {"provider": "openai", "model": _CLOUD_MODEL}
    assert notice["to"] == {"provider": "ollama", "model": _LOCAL_MODEL}
    assert events[-1].data["fallback"] == {"from": notice["from"], "to": notice["to"]}
    # The local model got the SAME call with its own model and no key.
    assert local.calls == [(_LOCAL_MODEL, "")]
    assert cloud.calls == [(_CLOUD_MODEL, "sk-cloud")]
    # A reader returning to the thread sees why, at the head of the reply.
    assert _stored_reply(threads) == f"{_NOTICE_AR}\n\n{_GREETING}{_GREETING_TAIL}"
    assert events[-1].data["content"]["text"] == _stored_reply(threads)
    # Billed to the model that actually answered.
    assert [c.provider for c in capture.charges] == ["ollama"]
    assert _fallbacks("served") == served_before + 1


async def test_the_notice_follows_the_language_of_the_question() -> None:
    orchestrator, threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_TIMED_OUT), _ScriptedLLM("ollama", ["Hi"])
    )
    events = await _turn(orchestrator, text="What is the policy?")
    assert events[0].data["detail"] == _NOTICE_EN
    assert _stored_reply(threads).startswith(_NOTICE_EN)


async def test_a_healthy_cloud_turn_is_untouched() -> None:
    cloud = _ScriptedLLM("openai", ["fine"])
    local = _ScriptedLLM("ollama", ["never"])
    orchestrator, threads, capture = _orchestrator(cloud, local)

    events = await _turn(orchestrator)

    assert [e.type for e in events] == ["token", "final"]
    assert "fallback" not in events[-1].data
    assert local.calls == []
    assert _stored_reply(threads) == "fine"
    assert [c.provider for c in capture.charges] == ["openai"]


async def test_the_notice_reaches_the_wire_as_its_own_sse_frame() -> None:
    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_DOWN), _ScriptedLLM("ollama", ["x"])
    )
    req = AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "q"})
    frames = [f async for f in sse_stream(await orchestrator.invoke(_ctx(), "chat", req))]
    assert frames[0].startswith(b"event: notice\ndata: {")
    assert frames[1].startswith(b"event: token\n")


async def test_the_real_guard_with_an_open_circuit_falls_back_without_calling_the_cloud() -> None:
    """The chain as deployed: the cloud adapter behind its ``GuardedLLM``,
    whose circuit is already open — the turn is answered locally at once."""
    inner = _ScriptedLLM("openai", ["never"])
    guard = ProviderGuard(
        "openai", ProviderGuardSettings(circuit_failure_threshold=1), clock=lambda: 0.0
    )
    guard.record(_TIMED_OUT)
    local = _ScriptedLLM("ollama", ["local"])
    orchestrator, _threads, _capture = _orchestrator(GuardedLLM(inner, guard), local)

    events = await _turn(orchestrator)

    assert [e.type for e in events] == ["notice", "token", "final"]
    assert inner.calls == []  # the open circuit refused before the adapter


# --------------------------------------------------------------------------- #
# When it must NOT switch                                                     #
# --------------------------------------------------------------------------- #
async def test_a_rejected_key_is_not_an_outage_and_never_falls_back() -> None:
    """A configuration error to fix: a working local answer would hide it."""
    local = _ScriptedLLM("ollama", ["never"])
    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_BAD_KEY), local
    )
    events = await _turn(orchestrator)
    assert [e.type for e in events] == ["error"]
    assert events[0].data["detail"] == "openai rejected the key"
    assert local.calls == []


async def test_the_guards_own_saturation_is_not_a_failure_to_fall_back_from() -> None:
    """ق-6: a full ceiling answers 429 with `Retry-After`, signed as such."""
    local = _ScriptedLLM("ollama", ["never"])
    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=RateLimitedError("full", retry_after_s=5)), local
    )
    events = await _turn(orchestrator)
    assert [e.type for e in events] == ["error"]
    assert events[0].data["status"] == 429
    assert local.calls == []


async def test_a_failure_after_the_first_chunk_is_cut_not_answered_twice() -> None:
    """Condition 3: once the user is reading an answer, a second answer from
    another model would be a different answer — the turn is cut (ب-10)."""
    local = _ScriptedLLM("ollama", ["never"])
    orchestrator, threads, _capture = _orchestrator(
        _ScriptedLLM("openai", ["half"], fails_after=_TIMED_OUT), local
    )
    events = await _turn(orchestrator)
    assert [e.type for e in events] == ["token", "error"]
    assert local.calls == []
    assert not _stored_reply(threads).startswith("⚠️ تعذّر")


async def test_a_call_with_tools_is_never_sent_to_a_model_that_cannot_take_them() -> None:
    """Condition 1: functional equivalence, checked per call."""
    local = _ScriptedLLM("ollama", ["never"], tools=False)
    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_DOWN), local, agent=_ToolAgent
    )
    events = await _turn(orchestrator, key="tooled")
    assert [e.type for e in events] == ["error"]
    assert local.calls == []


async def test_without_the_seam_a_cloud_failure_is_the_answer_as_before() -> None:
    local = _ScriptedLLM("ollama", ["never"])
    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_DOWN), local, fallback=None
    )
    events = await _turn(orchestrator)
    assert [e.type for e in events] == ["error"]
    assert local.calls == []


async def test_a_local_turn_that_fails_stays_failed() -> None:
    routing: Json = {"llm": {"default": {"provider": "ollama", "model": _LOCAL_MODEL}}}
    cloud = _ScriptedLLM("openai", ["never"])
    local = _ScriptedLLM("ollama", fails_first=_DOWN)
    registry = InMemoryAgentRegistry()
    registry.register(_ChatAgent.metadata, _ChatAgent)
    resolver = SettingsProviderResolver(
        routing=routing,
        llm_providers={"openai": cloud, "ollama": local},
        embedding_providers={},
        image_providers={},
        key_resolver=SpyKeyResolver(),
        keyless_providers=frozenset({"ollama"}),
        fallback_route="default",
    )
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=resolver,
            llm_fallback=resolver,
            authorization=build_authorization(),
        )
    )
    events = await _turn(orchestrator)
    assert [e.type for e in events] == ["error"]
    assert cloud.calls == []


async def test_when_the_local_model_fails_too_the_user_gets_the_clouds_error() -> None:
    local = _ScriptedLLM("ollama", fails_first=RateLimitedError("ollama full", retry_after_s=2))
    orchestrator, threads, _capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_DOWN), local
    )
    failed_before = _fallbacks("failed")

    events = await _turn(orchestrator)

    # No notice: nothing was answered. The error is the one the user's own
    # choice produced, not a model they never picked.
    assert [e.type for e in events] == ["error"]
    assert events[0].data["detail"] == "openai is unreachable"
    assert events[0].data["status"] == 502
    assert local.calls == [(_LOCAL_MODEL, "")]
    assert [role for _c, role, *_ in threads.appended] == ["user"]
    assert _fallbacks("failed") == failed_before + 1


# --------------------------------------------------------------------------- #
# Inside one turn                                                             #
# --------------------------------------------------------------------------- #
async def test_the_switch_is_sticky_for_the_rest_of_the_turn() -> None:
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", ["l"])
    orchestrator, threads, capture = _orchestrator(cloud, local, agent=_TwoCallAgent)

    events = await _turn(orchestrator, key="two")

    assert len(cloud.calls) == 1  # the second call never went back to it
    assert local.calls == [(_LOCAL_MODEL, ""), (_LOCAL_MODEL, "")]
    assert [e.type for e in events] == ["notice", "token", "final"]
    assert _stored_reply(threads) == f"{_NOTICE_AR}\n\nl|l"
    assert [c.provider for c in capture.charges] == ["ollama"]


async def test_a_turn_that_mixed_both_models_is_charged_to_the_cloud() -> None:
    """The conservative direction: a charge names one provider, and naming
    the local one would hide cloud tokens from 6.5's cost budget."""

    class _FirstOnly(_ScriptedLLM):
        def stream(
            self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
        ) -> AsyncIterator[LlmChunk]:
            self.fails_first = _DOWN  # the classify call worked; the answer fails
            return super().stream(messages, params, api_key)

    cloud = _FirstOnly("openai", ["c"])
    local = _ScriptedLLM("ollama", ["l"])
    orchestrator, threads, capture = _orchestrator(cloud, local, agent=_TwoCallAgent)

    events = await _turn(orchestrator, key="two")

    assert [e.type for e in events] == ["notice", "token", "final"]
    assert _stored_reply(threads) == f"{_NOTICE_AR}\n\nc|l"
    assert [c.provider for c in capture.charges] == ["openai"]


async def test_the_agent_still_sees_the_models_it_asked_for() -> None:
    """`supports` answers for the primary: an outage must not change what an
    agent believes it may ask for."""
    seen: list[bool] = []

    class _Asking(_ChatAgent):
        metadata = _metadata("asking", capabilities=frozenset({"chat"}))

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            assert self.deps.llm is not None
            seen.append(self.deps.llm.provider.supports("tools"))
            async for event in super().run(req):
                yield event

    orchestrator, _threads, _capture = _orchestrator(
        _ScriptedLLM("openai", ["x"], tools=True),
        _ScriptedLLM("ollama", ["y"], tools=False),
        agent=_Asking,
    )
    await _turn(orchestrator, key="asking")
    assert seen == [True]


def test_a_provider_failure_is_still_the_catalogued_502() -> None:
    """The tenant-facing shape is unchanged by 6.4 — the switch reads
    `transient`, and nothing else about the error moved."""
    assert isinstance(_DOWN, AppError)
    assert (_DOWN.code, _DOWN.status) == ("agent.failed", 502)
