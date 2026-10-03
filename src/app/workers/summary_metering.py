"""Summary builds count against the workspace's quota and budget
(capacity-plan 6.5, owner decision 2026-10-04).

**The hole this closes.** 6.2 found it live: a full summary did not move the
usage ledger by one token (8,218 before and after), because the only caller of
``usage``'s ports was the chat orchestrator and the summarisation map-reduce —
the most expensive single job the platform runs — happens here, in the
knowledge worker. Free while ``summarize`` routes to the local model; an
unmetered invoice the day it routes to a cloud one. So a build now does what a
chat turn does, in the same order and through the same ports:

1. **Reserve before the first provider call** (``admit``), under the agent key
   ``summarize`` and the provider the route resolved. A workspace that is over
   its token quota or its cost budget is refused HERE, before a single chunk
   is sent anywhere, and the job fails with a sentence its thread can show.
2. **Meter every call the build makes** — the map, the reduce, a translation —
   through a provider decorator, kept current chunk by chunk so a build cut by
   its own deadline is still billed for what it spent.
3. **Commit the measured charge** (``settle``), priced on the route's model, or
   hand the slot back if nothing was spent.

**Why the worker and not the knowledge module.** ``usage``'s ports are
cross-module (modules may not import each other), and the knowledge module
does not need to know metering exists: wrapping the resolved summarizer's
provider is invisible to the pipeline, exactly as ``_MeteredLLM`` is invisible
to an agent. This file sits in the worker's composition layer, beside the
handler that calls it — INV-U4's "only the orchestrator" now reads "only the
two places that spend tokens": the orchestrator for turns, this for builds.

**The estimate rule is the orchestrator's.** A provider that reports no
counters on its terminal chunk is estimated from the text, and one estimated
call marks the whole charge ``estimated`` — the conservative direction.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import (
    LlmChunk,
    LlmMessage,
    LlmParams,
    LLMProvider,
    LlmResult,
)
from app.framework.providers.pricing import LlmPricing
from app.framework.types import Uuid
from app.infrastructure.ai_providers.llm.shared import estimate_tokens
from app.modules.knowledge.application.use_cases import (
    SUMMARY_BUDGET_EXCEEDED_REASON,
    SUMMARY_QUOTA_EXCEEDED_REASON,
    SummaryBuildPlan,
)
from app.modules.usage.ports.inbound import UsageCharge, UsageEnforcement

_logger = get_logger(__name__)

# The usage ledger's agent key for a summary build: the routing capability it
# resolves through (`SUMMARIZE_CAPABILITY`), so an operator reading
# `GET /usage` by agent, or setting an agent-scoped limit, names it the way the
# routing table already does.
SUMMARIZE_AGENT_KEY = "summarize"

# `LimitDecision.reason` for the spending budget (`usage`'s `DenyReason`
# value, crossing the port as a plain `str`). Every other denial -- the token
# quota, or a reason nobody has written yet -- is told as the quota: the
# conservative sentence, since both mean "wait for the period to turn over".
_BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(slots=True)
class _Call:
    """One provider call's running usage — current at every chunk."""

    prompt_text: str
    completion_parts: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def prompt_total(self) -> int:
        if self.prompt_tokens is not None:
            return self.prompt_tokens
        return estimate_tokens(self.prompt_text)

    @property
    def completion_total(self) -> int:
        if self.completion_tokens is not None:
            return self.completion_tokens
        return estimate_tokens("".join(self.completion_parts))

    @property
    def measured(self) -> bool:
        return self.prompt_tokens is not None and self.completion_tokens is not None


@dataclass(slots=True)
class SummaryMeter:
    """Every call one build made, summed (module docstring, step 2)."""

    calls: list[_Call] = field(default_factory=list)

    def begin(self, prompt_text: str) -> _Call:
        call = _Call(prompt_text=prompt_text)
        self.calls.append(call)
        return call

    @property
    def prompt_total(self) -> int:
        return sum(call.prompt_total for call in self.calls)

    @property
    def completion_total(self) -> int:
        return sum(call.completion_total for call in self.calls)

    @property
    def total(self) -> int:
        return self.prompt_total + self.completion_total

    @property
    def estimated(self) -> bool:
        return any(not call.measured for call in self.calls)


class MeteredSummarizerLLM:
    """A transparent ``LLMProvider`` decorator that tees a build's token counts
    into its ``SummaryMeter``. Forwards every call unchanged: metering can
    never change a summary."""

    def __init__(self, inner: LLMProvider, meter: SummaryMeter) -> None:
        self._inner = inner
        self._meter = meter
        self.provider = inner.provider

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        result = await self._inner.complete(messages, params, api_key)
        call = self._meter.begin(_joined(messages))
        call.prompt_tokens = result.prompt_tokens
        call.completion_tokens = result.completion_tokens
        return result

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        return self._metered(self._inner.stream(messages, params, api_key), _joined(messages))

    def supports(self, capability: str) -> bool:
        return self._inner.supports(capability)

    async def _metered(
        self, chunks: AsyncIterator[LlmChunk], prompt_text: str
    ) -> AsyncIterator[LlmChunk]:
        # Registered on the FIRST chunk: a stream never iterated sent nothing.
        call: _Call | None = None
        try:
            async for chunk in chunks:
                if call is None:
                    call = self._meter.begin(prompt_text)
                if chunk.delta:
                    call.completion_parts.append(chunk.delta)
                if chunk.finish_reason is not None:
                    call.prompt_tokens = chunk.prompt_tokens
                    call.completion_tokens = chunk.completion_tokens
                yield chunk
        finally:
            # The pipeline closes what it opened (`_close_quietly`), and that
            # close must reach the adapter's response, not stop here.
            close = getattr(chunks, "aclose", None)
            if close is not None:
                await close()


@dataclass(frozen=True, slots=True)
class SummaryRefusal:
    """A build the workspace's quota or budget refused, before any provider
    call. ``reason`` is the knowledge module's deliverable sentence — what the
    job's ``error`` column records and the asking thread is shown. No HTTP
    code: nobody is waiting on a response here, only on the thread."""

    reason: str


@dataclass(frozen=True, slots=True)
class SummaryAdmission:
    """A build admitted against its workspace's quota: the plan to run (its
    provider now metered), the meter, and the slot ``settle`` owes back."""

    plan: SummaryBuildPlan
    meter: SummaryMeter
    provider: str
    model: str
    reservation_id: Uuid | None


class SummaryMetering:
    """``admit`` before a build, ``settle`` after it (module docstring)."""

    def __init__(self, enforcement: UsageEnforcement, pricing: LlmPricing) -> None:
        self._enforcement = enforcement
        self._pricing = pricing

    async def admit(
        self, ctx: ExecutionContext, plan: SummaryBuildPlan
    ) -> SummaryAdmission | SummaryRefusal:
        """Reserve the workspace's quota for this build — or refuse it, with
        the sentence that says which limit, before any provider is called."""
        summarizer = plan.summarizer
        provider = summarizer.provider.provider
        decision = await self._enforcement.reserve(ctx, SUMMARIZE_AGENT_KEY, provider)
        if not decision.allowed:
            budget = decision.reason == _BUDGET_EXCEEDED
            return SummaryRefusal(
                SUMMARY_BUDGET_EXCEEDED_REASON if budget else SUMMARY_QUOTA_EXCEEDED_REASON
            )
        meter = SummaryMeter()
        metered = dataclasses.replace(
            summarizer, provider=MeteredSummarizerLLM(summarizer.provider, meter)
        )
        return SummaryAdmission(
            plan=dataclasses.replace(plan, summarizer=metered),
            meter=meter,
            provider=provider,
            model=summarizer.model,
            reservation_id=decision.reservation_id,
        )

    async def settle(self, ctx: ExecutionContext, admission: SummaryAdmission) -> None:
        """Replace the reservation with what the build spent, or give it back
        if it spent nothing. Never raises: the build's outcome is already
        decided, and a lost charge is logged for the operator rather than
        turned into a failed job (the orchestrator's ``_capture`` rule)."""
        meter = admission.meter
        reservation_id = admission.reservation_id
        try:
            if meter.total <= 0:
                if reservation_id is not None:
                    await self._enforcement.release(ctx, reservation_id)
                return
            charge = UsageCharge(
                agent=SUMMARIZE_AGENT_KEY,
                provider=admission.provider,
                tokens=meter.total,
                cost_micros=self._pricing.cost_micros(
                    admission.provider,
                    admission.model,
                    meter.prompt_total,
                    meter.completion_total,
                ),
                operation_id=new_uuid7(),
                estimated=meter.estimated,
            )
            if reservation_id is None:
                # `reserve` returned no slot only when nothing is enforced --
                # an unmetered deployment, where there is nothing to commit to.
                return
            await self._enforcement.commit(ctx, reservation_id, charge)
        except Exception as exc:  # never fail a finished build over its bill
            _logger.warning(
                "summary_usage_capture_failed",
                extra={"provider": admission.provider, "tokens": meter.total},
                exc_info=exc,
            )


def _joined(messages: Sequence[LlmMessage]) -> str:
    return "\n".join(m.content for m in messages)
