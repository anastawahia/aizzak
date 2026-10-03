"""capacity-plan 6.5 — a cost budget per workspace that actually spends.

Until this step every charge carried ``cost_micros = 0``, so the budget in
``USAGE_DEFAULT_LIMITS`` could never fire, and the knowledge worker's summary
builds were not metered at all. The step's acceptance is "a workspace that
reaches its cap answers 429 with a reason distinct from the rate limit", and
the owner decided 2026-10-04 that a spent budget stops EVERY turn (local
included) and that summaries count. This file proves each half:

1. **Pricing** — USD per million tokens, rounded up once per charge; an
   unpriced cloud route refuses to boot (``LlmPricing``).
2. **Admission** — the budget is checked before the call, and its last
   micro-dollar admits one turn, not a hundred (``ReserveQuota``).
3. **Chat** — a turn is priced on the model that served it, through the real
   resolver and the real orchestrator; a spent budget is ``usage.budget_
   exceeded``/429.
4. **Summaries** — a build reserves before its first provider call, is metered
   through the plan's provider, and is charged even when cut by its deadline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

import pytest

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ERROR_CATALOG, RateLimitedError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LLMProvider, LlmResult
from app.framework.providers import (
    ConfiguredProvider,
    LlmPricing,
    ModelPrice,
    ProviderRoute,
    SettingsProviderResolver,
)
from app.framework.types import Json
from app.modules.knowledge.application.use_cases import (
    SUMMARY_BUDGET_EXCEEDED_REASON,
    SUMMARY_QUOTA_EXCEEDED_REASON,
    SummaryBuildPlan,
    delivered_failure_text,
)
from app.modules.knowledge.ports.summarization import ResolvedSummarizer
from app.modules.usage.application.use_cases import CaptureUsage, UsageCaptureService
from app.modules.usage.domain.enforcement import LimitCheck, evaluate
from app.modules.usage.domain.entities import UsageLimit
from app.modules.usage.domain.value_objects import DenyReason, LimitScope, Metric, Period
from app.workers.bootstrap import build_knowledge_summary_handler
from app.workers.summary_metering import SUMMARIZE_AGENT_KEY, SummaryMetering
from tests.unit.support_access import build_authorization
from tests.unit.test_llm_fallback import (
    _DOWN,
    _ChatAgent,
    _ScriptedLLM,
    _TwoCallAgent,
)
from tests.unit.test_orchestrator import _SPACE, _FakeCapture, _FakeEnforcement, _FakeThreads
from tests.unit.test_orchestrator import _ctx as _turn_ctx
from tests.unit.test_provider_resolver import SpyKeyResolver
from tests.unit.test_usage_module import (
    _enforcement,
    _FakeUsageLedger,
    _usage_limit,
)
from tests.unit.test_workers_bootstrap import (
    _FakeLedger,
    _FakeOutbox,
    _FakeSummaryAttempt,
    _FakeUnitOfWork,
    _summary_envelope,
)

_CLOUD = "gpt-4.1-mini"
_LOCAL = "gemma3:1b"
# OpenAI's published price for gpt-4.1-mini, the way an operator writes it.
_PRICES: Json = {f"openai/{_CLOUD}": {"input": 0.40, "output": 1.60}}
_ROUTING: Json = {
    "llm": {
        "default": {"provider": "openai", "model": _CLOUD},
        "local": {"provider": "ollama", "model": _LOCAL},
    }
}


def _resolver(
    cloud: LLMProvider, local: LLMProvider, *, routing: Json | None = None
) -> SettingsProviderResolver:
    return SettingsProviderResolver(
        routing=_ROUTING if routing is None else routing,
        llm_providers={"openai": cloud, "ollama": local},
        embedding_providers={},
        image_providers={},
        key_resolver=SpyKeyResolver("sk-cloud"),
        keyless_providers=frozenset({"ollama"}),
        fallback_route="local" if routing is None else "",
    )


def _pricing(resolver: SettingsProviderResolver, prices: Json | None = None) -> LlmPricing:
    return LlmPricing.for_routes(
        _PRICES if prices is None else prices, resolver.configured_providers()
    )


def _llm_routes(provider: str, *models: str, keyless: bool = False) -> ConfiguredProvider:
    return ConfiguredProvider(
        provider=provider,
        keyless=keyless,
        probeable=True,
        routes=tuple(
            ProviderRoute(namespace="llm", capability=f"r{i}", model=model)
            for i, model in enumerate(models)
        ),
    )


# --------------------------------------------------------------------------- #
# 1) Pricing                                                                  #
# --------------------------------------------------------------------------- #
def test_a_price_is_dollars_per_million_tokens_and_is_rounded_up_once() -> None:
    """The vendor's number IS micro-dollars per token: 1,000 prompt tokens at
    $0.40/M and 500 completion tokens at $1.60/M are 400 + 800 micros. And a
    fraction is rounded UP, once — a charge is never smaller than its tokens."""
    pricing = LlmPricing.for_routes(_PRICES, [_llm_routes("openai", _CLOUD)])

    assert pricing.cost_micros("openai", _CLOUD, 1_000, 500) == 1_200
    assert pricing.cost_micros("openai", _CLOUD, 3, 2) == 5  # 1.2 + 3.2 = 4.4


def test_a_local_model_costs_nothing() -> None:
    pricing = LlmPricing.for_routes(
        _PRICES, [_llm_routes("openai", _CLOUD), _llm_routes("ollama", _LOCAL, keyless=True)]
    )

    assert pricing.cost_micros("ollama", _LOCAL, 10_000, 10_000) == 0
    assert pricing.cost_micros("openai", _CLOUD, 0, 0) == 0


def test_a_cloud_route_without_a_price_refuses_to_boot() -> None:
    """The hole this step closes: spend that is never counted must die at
    boot, not be discovered on an invoice."""
    with pytest.raises(ValidationError, match=r"openai/gpt-4\.1\b.*no price"):
        LlmPricing.for_routes(_PRICES, [_llm_routes("openai", _CLOUD, "gpt-4.1")])


def test_a_price_for_a_local_provider_refuses_to_boot() -> None:
    with pytest.raises(ValidationError, match="local provider"):
        LlmPricing.for_routes(
            {**_PRICES, f"ollama/{_LOCAL}": {"input": 0.1, "output": 0.1}},
            [_llm_routes("openai", _CLOUD), _llm_routes("ollama", _LOCAL, keyless=True)],
        )


def test_a_price_for_a_model_no_route_names_is_allowed() -> None:
    """How an operator stages a model switch: the price goes in first."""
    pricing = LlmPricing.for_routes(
        {**_PRICES, "openai/gpt-4.1": {"input": 2, "output": 8}}, [_llm_routes("openai", _CLOUD)]
    )

    assert pricing.cost_micros("openai", "gpt-4.1", 1_000, 1_000) == 10_000


def test_a_model_name_with_a_slash_keeps_it() -> None:
    """The FIRST slash splits: router model names carry their own."""
    pricing = LlmPricing.for_routes(
        {"openrouter/meta-llama/llama-3": {"input": 1, "output": 1}},
        [_llm_routes("openrouter", "meta-llama/llama-3")],
    )

    assert pricing.cost_micros("openrouter", "meta-llama/llama-3", 1, 1) == 2


@pytest.mark.parametrize(
    "prices",
    [
        [],
        {"gpt-4.1-mini": {"input": 1, "output": 1}},
        {"openai/": {"input": 1, "output": 1}},
        {"openai/gpt-4.1-mini": {"input": 1}},
        {"openai/gpt-4.1-mini": {"input": 1, "output": 1, "cached": 0.1}},
        {"openai/gpt-4.1-mini": {"input": -1, "output": 1}},
        {"openai/gpt-4.1-mini": {"input": True, "output": 1}},
        {"openai/gpt-4.1-mini": {"input": "lots", "output": 1}},
        {"openai/gpt-4.1-mini": {"input": float("inf"), "output": 1}},
    ],
)
def test_a_malformed_price_table_refuses_to_boot(prices: object) -> None:
    with pytest.raises(ValidationError, match="llm_prices"):
        LlmPricing.for_routes(prices, [_llm_routes("openai", _CLOUD)])


def test_an_unpriced_cloud_model_at_call_time_raises_rather_than_charging_zero() -> None:
    pricing = LlmPricing(
        {("openai", _CLOUD): ModelPrice(Decimal("0.4"), Decimal("1.6"))}, frozenset()
    )

    with pytest.raises(ValidationError, match="no price"):
        pricing.cost_micros("openai", "gpt-5", 1, 1)


# --------------------------------------------------------------------------- #
# 2) Admission — before the call, and one micro of headroom admits one turn   #
# --------------------------------------------------------------------------- #
def test_a_cost_estimate_that_would_overshoot_the_budget_is_denied() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.COST_MICROS, Period.MONTH, 1_000, 999)]

    assert evaluate(checks, estimated_cost_micros=1).decision.allowed is True
    denied = evaluate(checks, estimated_cost_micros=2).decision
    assert denied.allowed is False
    assert denied.reason is DenyReason.BUDGET_EXCEEDED


async def test_a_reservation_holds_one_micro_against_the_budget() -> None:
    ledger = _FakeUsageLedger()

    await _enforcement(ledger).reserve(_ws(), "rag_agent", "openai")

    assert [row.cost_micros for row in ledger.reservations.values()] == [1]


async def test_the_last_micro_of_a_budget_admits_one_turn_not_a_hundred() -> None:
    """Before 6.5 a cost reservation held 0, so every simultaneous turn read
    the same headroom and all were told yes."""
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [_budget(10)]
    ledger.seed_rollup("ws1", "*", "*", "month", cost_micros=9)
    service = _enforcement(ledger)

    decisions = [await service.reserve(_ws(), "rag_agent", "openai") for _ in range(100)]

    assert sum(d.allowed for d in decisions) == 1
    assert {d.reason for d in decisions if not d.allowed} == {DenyReason.BUDGET_EXCEEDED.value}


async def test_a_spent_budget_stops_a_local_turn_too() -> None:
    """Owner decision 2026-10-04: a spent budget stops every turn of the
    workspace until the month turns over, the free local model included."""
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [_budget(10)]
    ledger.seed_rollup("ws1", "*", "*", "month", cost_micros=10)

    decision = await _enforcement(ledger).reserve(_ws(), "rag_agent", "ollama")

    assert decision.allowed is False
    assert decision.reason == DenyReason.BUDGET_EXCEEDED.value
    assert decision.retry_after_s is not None and decision.retry_after_s > 0


# --------------------------------------------------------------------------- #
# 3) Chat — priced on the model that served it                                #
# --------------------------------------------------------------------------- #
def _orchestrator(
    cloud: LLMProvider,
    local: LLMProvider,
    *,
    agent: type[BaseAgent] = _ChatAgent,
    routing: Json | None = None,
) -> tuple[AgentOrchestrator, _FakeCapture]:
    registry = InMemoryAgentRegistry()
    registry.register(agent.metadata, agent)
    resolver = _resolver(cloud, local, routing=routing)
    enforcement = _FakeEnforcement()
    capture = _FakeCapture()
    enforcement.bind_capture(capture)
    deps = OrchestratorDependencies(
        agents=registry,
        executor=AgentLifecycleExecutor(),
        providers=resolver,
        llm_fallback=resolver,
        conversations=_FakeThreads(),
        usage_enforcement=enforcement,
        usage_capture=capture,
        authorization=build_authorization(),
        pricing=_pricing(resolver),
    )
    return AgentOrchestrator(deps), capture


async def _turn(orchestrator: AgentOrchestrator, key: str = "chat") -> list[AgentEvent]:
    req = AgentRequest(space_id=_SPACE, conversation_id=None, input={"text": "what is it?"})
    return [event async for event in await orchestrator.invoke(_turn_ctx(), key, req)]


async def test_a_cloud_turn_is_charged_at_its_models_price() -> None:
    orchestrator, capture = _orchestrator(_ScriptedLLM("openai", ["a"]), _ScriptedLLM("ollama"))

    await _turn(orchestrator)

    [charge] = capture.charges
    assert (charge.provider, charge.tokens) == ("openai", 5)
    assert charge.cost_micros == 5  # 3 x 0.40 + 2 x 1.60 = 4.4, rounded up


async def test_a_local_turn_is_charged_tokens_and_no_money() -> None:
    routing: Json = {"llm": {"default": {"provider": "ollama", "model": _LOCAL}}}
    orchestrator, capture = _orchestrator(
        _ScriptedLLM("openai"), _ScriptedLLM("ollama", ["a"]), routing=routing
    )

    await _turn(orchestrator)

    [charge] = capture.charges
    assert (charge.provider, charge.tokens, charge.cost_micros) == ("ollama", 5, 0)


async def test_a_turn_the_local_model_answered_for_a_failed_cloud_costs_nothing() -> None:
    orchestrator, capture = _orchestrator(
        _ScriptedLLM("openai", fails_first=_DOWN), _ScriptedLLM("ollama", ["l"])
    )

    await _turn(orchestrator)

    [charge] = capture.charges
    assert (charge.provider, charge.cost_micros) == ("ollama", 0)


async def test_a_turn_that_mixed_both_models_pays_the_cloud_price_for_every_token() -> None:
    """6.4's conservative direction carried into money: the charge names the
    cloud, so every token in it is priced as the cloud's."""

    class _FirstOnly(_ScriptedLLM):
        def stream(
            self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
        ) -> AsyncIterator[LlmChunk]:
            self.fails_first = _DOWN  # the classify call worked; the answer fails
            return super().stream(messages, params, api_key)

    orchestrator, capture = _orchestrator(
        _FirstOnly("openai", ["c"]), _ScriptedLLM("ollama", ["l"]), agent=_TwoCallAgent
    )

    await _turn(orchestrator, key="two")

    [charge] = capture.charges
    # complete (3 + 1) on the cloud, stream (3 + 2) on the local model:
    # 6 x 0.40 + 3 x 1.60 = 7.2 -> 8.
    assert (charge.provider, charge.tokens, charge.cost_micros) == ("openai", 9, 8)


async def test_a_spent_budget_answers_its_own_429_not_the_rate_limit() -> None:
    """The acceptance line itself, through the REAL enforcement: the turn is
    priced into the ledger, the budget is then exactly spent, and the next
    turn — a local one, by the owner's decision — is refused before it starts
    with ``usage.budget_exceeded``, not ``common.rate_limited``."""
    ledger = _FakeUsageLedger()
    registry = InMemoryAgentRegistry()
    registry.register(_ChatAgent.metadata, _ChatAgent)
    cloud = _ScriptedLLM("openai", ["a"])
    resolver = _resolver(cloud, _ScriptedLLM("ollama", ["l"]))
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=resolver,
            conversations=_FakeThreads(),
            usage_enforcement=_enforcement(ledger),
            usage_capture=UsageCaptureService(CaptureUsage(ledger)),  # type: ignore[arg-type]
            authorization=build_authorization(),
            pricing=_pricing(resolver),
        )
    )
    ctx = _turn_ctx()

    await _turn(orchestrator)
    spent = (await ledger.rollup(ctx, "*", "*", "month")).cost_micros
    assert spent == 5
    ledger.limits[ctx.workspace_id] = [_budget(spent, workspace_id=ctx.workspace_id)]

    with pytest.raises(RateLimitedError) as caught:
        await _turn(orchestrator)

    assert caught.value.code == "usage.budget_exceeded"
    assert ERROR_CATALOG["usage.budget_exceeded"].status == 429
    assert caught.value.code != RateLimitedError.code
    assert caught.value.retry_after_s is not None
    assert len(cloud.calls) == 1  # the refused turn reached no provider


# --------------------------------------------------------------------------- #
# 4) Summaries — reserved, metered and charged in the worker                  #
# --------------------------------------------------------------------------- #
class _SummaryLLM:
    """Streams a summary in two pieces, reporting (or omitting) counters."""

    def __init__(self, provider: str, *, prompt: int | None = 400, completion: int | None = 100):
        self.provider = provider
        self.calls = 0
        self._prompt = prompt
        self._completion = completion

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("summaries stream")

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        async def _gen() -> AsyncIterator[LlmChunk]:
            self.calls += 1
            yield LlmChunk(delta="part one, ")
            yield LlmChunk(
                delta="part two",
                finish_reason="stop",
                prompt_tokens=self._prompt,
                completion_tokens=self._completion,
            )

        return _gen()

    def supports(self, capability: str) -> bool:
        return capability == "streaming"


class _StreamingBuild:
    """``BuildSummary``'s claim/run/fail/finalize split, whose ``run`` really
    calls the plan's provider — so whatever the handler wrapped it in is what
    the build spends through. ``calls`` makes it a map with several calls;
    ``hang_s`` makes it a build that will not come back."""

    def __init__(self, llm: LLMProvider, model: str, *, calls: int = 1, hang_s: float = 0.0):
        self.plan = SummaryBuildPlan(
            job=object(),  # type: ignore[arg-type]
            chunks=(),
            summarizer=ResolvedSummarizer(provider=llm, model=model, api_key="sk"),
        )
        self.run_calls = 0
        self.failed: list[str] = []
        self._calls = calls
        self._hang_s = hang_s

    async def claim(self, ctx: ExecutionContext, *, job_id: str) -> SummaryBuildPlan:
        return self.plan

    async def run(
        self, ctx: ExecutionContext, plan: SummaryBuildPlan, *, on_heartbeat: object = None
    ) -> _FakeSummaryAttempt:
        self.run_calls += 1
        summarizer = plan.summarizer
        for _ in range(self._calls):
            async for _chunk in summarizer.provider.stream(
                [LlmMessage(role="user", content="x" * 400)],
                LlmParams(model=summarizer.model),
                summarizer.api_key,
            ):
                if self._hang_s:
                    await asyncio.sleep(self._hang_s)
        return _FakeSummaryAttempt()

    async def fail(self, ctx: ExecutionContext, *, job_id: str, reason: str) -> _FakeSummaryAttempt:
        self.failed.append(reason)
        return _FakeSummaryAttempt(error=reason)

    async def finalize(
        self, ctx: ExecutionContext, attempt: object, *, conversation_id: str | None = None
    ) -> tuple[object, tuple[object, ...]]:
        return object(), ()


async def _summarise(
    build: _StreamingBuild,
    ledger: _FakeUsageLedger,
    *,
    max_duration_s: float | None = None,
    providers: Sequence[ConfiguredProvider] | None = None,
) -> None:
    pricing = LlmPricing.for_routes(
        _PRICES,
        providers
        if providers is not None
        else [_llm_routes("openai", _CLOUD), _llm_routes("ollama", _LOCAL, keyless=True)],
    )
    handler = build_knowledge_summary_handler(
        build,  # type: ignore[arg-type]
        _FakeOutbox(),
        _FakeUnitOfWork(),
        _FakeLedger(),
        max_duration_s=max_duration_s,
        metering=SummaryMetering(_enforcement(ledger, ttl_s=1_800), pricing),
    )
    await handler(_ws(), _summary_envelope(_ws()))


async def test_a_summary_is_metered_and_charged_at_its_models_price() -> None:
    """6.2's live finding, closed: a full build used to leave the ledger
    exactly where it was."""
    ledger = _FakeUsageLedger()
    llm = _SummaryLLM("openai")
    build = _StreamingBuild(llm, _CLOUD, calls=3)  # a three-call map-reduce

    await _summarise(build, ledger)

    totals = await ledger.rollup(_ws(), SUMMARIZE_AGENT_KEY, "*", "month")
    # 3 x (400 + 100) tokens; 3 x (400 x 0.40 + 100 x 1.60) = 960 micros.
    assert (totals.tokens, totals.cost_micros) == (1_500, 960)
    assert ledger.reservations == {}  # the slot became the charge
    [charge] = ledger.records.values()
    assert (charge.agent, charge.provider, charge.estimated) == ("summarize", "openai", False)


async def test_a_local_summary_counts_its_tokens_against_the_quota() -> None:
    ledger = _FakeUsageLedger()

    await _summarise(_StreamingBuild(_SummaryLLM("ollama"), _LOCAL), ledger)

    totals = await ledger.rollup(_ws(), "*", "ollama", "month")
    assert (totals.tokens, totals.cost_micros) == (500, 0)


async def test_a_workspace_out_of_budget_gets_no_summary_and_no_provider_call() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [_budget(100)]
    ledger.seed_rollup("ws1", "*", "*", "month", cost_micros=100)
    llm = _SummaryLLM("openai")
    build = _StreamingBuild(llm, _CLOUD)

    await _summarise(build, ledger)

    assert build.failed == [SUMMARY_BUDGET_EXCEEDED_REASON]
    assert (build.run_calls, llm.calls) == (0, 0)
    assert ledger.reservations == {}


async def test_a_workspace_out_of_tokens_is_told_which_limit() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=10)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=10)
    build = _StreamingBuild(_SummaryLLM("ollama"), _LOCAL)

    await _summarise(build, ledger)

    assert build.failed == [SUMMARY_QUOTA_EXCEEDED_REASON]


def test_the_refusal_reaches_the_thread_as_written() -> None:
    """Both sentences are deliverable reasons, so the thread is told WHICH
    limit — not the generic "ask again", which is the wrong advice here."""
    for reason in (SUMMARY_BUDGET_EXCEEDED_REASON, SUMMARY_QUOTA_EXCEEDED_REASON):
        text = delivered_failure_text(reason, "report.pdf")
        assert reason in text
        assert "ask for it again" not in text


async def test_a_build_cut_by_its_deadline_is_still_billed_for_what_it_spent() -> None:
    """The meter is current at every chunk: a build stopped mid-stream has
    already been sent its prompt, and the provider bills for it."""
    ledger = _FakeUsageLedger()
    build = _StreamingBuild(_SummaryLLM("openai"), _CLOUD, hang_s=30.0)

    await _summarise(build, ledger, max_duration_s=0.05)

    assert build.failed == ["building this summary exceeded the 0.05s limit and was stopped"]
    [charge] = ledger.records.values()
    assert charge.estimated is True  # the terminal counters never arrived
    assert charge.tokens > 0 and charge.cost_micros > 0
    assert ledger.reservations == {}


async def test_a_build_that_spent_nothing_gives_its_slot_back() -> None:
    ledger = _FakeUsageLedger()
    build = _StreamingBuild(_SummaryLLM("openai"), _CLOUD, calls=0)

    await _summarise(build, ledger)

    assert ledger.records == {}
    assert ledger.reservations == {}


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _ws() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="ws1", user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _budget(micros: int, *, workspace_id: str = "ws1") -> UsageLimit:
    return _usage_limit(
        workspace_id=workspace_id,
        scope=LimitScope.WORKSPACE,
        metric=Metric.COST_MICROS,
        limit_value=micros,
    )
