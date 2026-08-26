"""The Orchestrator — the agents layer's request coordinator (11 §8.1/§8.2).

This is the component the API layer (Phase 6) calls to run one agent for one
request. It owns exactly the coordination `11 §8.1` assigns it and that no
agent is allowed to do for itself: resolve the provider/model/credential for
this workspace (D-16), assemble the per-request ``AgentDependencies``, create
the agent through the registry (FR-10/11: one instance per request), and drive
it through the FR-15 lifecycle. Later 4.7 sub-steps added quota enforcement
and usage capture around this spine (§8.1, 4.7-c) and the media seam (4.7-d).

**4.7-e-1 adds the second entry point: ``invoke_workflow``.** One agent for
one request stays ``invoke``; a WORKFLOW for one request is its sibling, and
the two share every per-request decision (provider resolution, quota
enforcement, dependency assembly) through ``begin_step``. The engine is built
per request because its dependency provider is bound to one
``ExecutionContext``, and the run gets its own conversation (D-12) before any
event escapes. **4.7-e-2 put the workflow path on the same billing footing as
the single-agent one**: the run is gated by the quota before its conversation
is written, and every step is enforced and charged individually — see
``_MeteredSteps`` for why the step is the billing unit and where its boundary
comes from.

**Why it lives in ``app.agents`` and not ``app.framework``.** `11 §8.1` names
the orchestrator as *the agents layer* ("المُنسِّق — طبقة الوكلاء"), and the
placement is load-bearing rather than cosmetic: the ``layers`` contract lets
``app.agents`` import ``app.modules``, so this file may depend on module
inbound ports **nominally** — a plain import of ``UsageEnforcement`` and
friends when 4.7-c lands. The framework kernel cannot (contract 7,
``framework-kernel``), which is precisely why ``AgentDependencies`` had to
reach its module-facing seams through the structural DIP Protocols in
``agent_runtime/deps_ports.py`` (4.6-a). The orchestrator sits on the side of
the boundary where that indirection is unnecessary, so it does not pay for it.

It is NOT a plugin: ``PluginLoader`` only descends into sub-*packages* of
``app.agents`` (``info.ispkg``), so a loose module like this one is never
scanned — pinned by a test rather than left to trust. It also imports no
sibling agent, so ``agents-independent`` is untouched.

**Pre-flight raises; in-flight becomes an event.** ``invoke`` is an ``async
def`` returning an ``AsyncIterator`` rather than an async generator itself —
the same shape as ``LLMProvider.stream`` and for the same reason, adapted:
everything that can fail BEFORE the first event (provider resolution, an
unknown agent key, a bad request shape) happens while the caller is still
awaiting, so it surfaces as a raised ``AppError`` that Phase 6 can map onto an
RFC 9457 response with a real status — 404 for an unknown agent, 422 for a
caller mistake. Once the first event is yielded the HTTP status is already
committed and no error response is possible any more, so from that point the
executor's terminal-``error``-event model (decision B1) is the only honest
failure channel. Had ``invoke`` been a plain async generator, the 404 would
have detonated inside the response body instead of becoming one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from app.framework.agent_runtime.base_agent import (
    AgentDependencies,
    AgentEvent,
    AgentRequest,
)
from app.framework.agent_runtime.deps_ports import (
    FilesAccess,
    KnowledgeAccess,
    MediaRequesting,
    ResolvedLLM,
)
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import AgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ForbiddenError, RateLimitedError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import (
    LlmChunk,
    LlmMessage,
    LlmParams,
    LLMProvider,
    LlmResult,
)
from app.framework.ports.storage_provider import StorageProvider
from app.framework.ports.web_search_provider import WebSearchProvider
from app.framework.providers.resolver import ProviderResolver
from app.framework.types import Json, Uuid
from app.framework.workflows.engine import SequentialWorkflowEngine, WorkflowResult
from app.framework.workflows.registry import WorkflowRegistry
from app.modules.access.ports.inbound import AuthorizationService
from app.modules.conversations.ports.inbound import AppendedMessage, ConversationThreads
from app.modules.usage.ports.inbound import UsageCapture, UsageCharge, UsageEnforcement

_logger = get_logger(__name__)

# The routing key this orchestrator asks `ProviderResolver` for. `.env.example`
# documents the llm namespace as "capability/agent -> provider + model", so the
# agent's own key IS a first-class routing key: an operator can pin one agent
# to one provider/model purely from configuration (D-16, FR-73), and every
# agent without such an entry falls through to the reserved "default" route by
# the resolver's own lookup. Nothing new is invented here.
#
# Media agents are the exception, below.
_LLM_CAPABILITY = "chat"

# Mirrors `infrastructure/.../llm/shared.py::estimate_tokens`. Duplicated
# ON PURPOSE rather than imported: `agents-no-api-no-infra` forbids this layer
# from importing `app.infrastructure` at all, and promoting a 2-line heuristic
# into the framework kernel to dodge that would put a billing fudge-factor in
# the kernel. The duplication is safe precisely because it is a FALLBACK — the
# number it produces is always marked `estimated=True`, so it can never be
# mistaken for a measured count (see `_TokenMeter`).
_CHARS_PER_TOKEN = 4

# v1 records no monetary cost: there is no pricing source anywhere in the
# project (no rate table in Requirements or `design/`), and plan §0.6 puts
# billing/payment explicitly out of v1. `tokens` is therefore the only live
# enforced metric, and the configured cost limit never fires — a documented
# consequence, not an oversight. The ledger column stays for the extension.
_V1_COST_MICROS = 0

# The `provider` recorded for an agent that resolves no LLM (the media agents,
# D-04). A non-empty placeholder, because `CaptureUsage` rejects a blank
# provider and `usage`'s rollup buckets are keyed by this string — "" would
# either fail validation or silently collide with a real provider's bucket.
_NO_PROVIDER = "none"

# `LimitDecision.reason` → 03 §4 error code (6.2). The keys are `usage`'s
# `DenyReason` values, which cross the inbound port as plain `str` (the same
# boundary convention as `_WORKFLOW_KIND` below), so this is the one place the
# agents layer names them. A CLOSED map, not an f-string: see `_enforce`.
_DENIAL_CODES = {
    "quota_exceeded": "usage.quota_exceeded",
    "budget_exceeded": "usage.budget_exceeded",
}
_DEFAULT_DENIAL = "quota_exceeded"

# `ConversationKind.WORKFLOW` as a plain string. The value crosses the
# `ConversationThreads` port as a `str` on purpose (see that port's docstring),
# so the enum itself stays inside `conversations`; this constant is the one
# place the agents layer names it, and `ConversationService.start` validates it
# against the real enum on the way in — a typo here is a 422 at the boundary,
# not a bad row.
_WORKFLOW_KIND = "workflow"

# `ConversationKind.AGENT` / `MessageRole.*` as plain strings — same boundary
# reasoning as `_WORKFLOW_KIND`: the vocabulary crosses the inbound port as
# text and is validated against the real enum on the way in.
_AGENT_KIND = "agent"
_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"

# `WorkflowRunOut.status` (`03 §2`) as this layer can honestly report it
# (6.1-د-1). The spec names the field but enumerates no values, and nothing in
# `01-data-model` stores a run's state — the only persistent trace of a run is
# its D-12 conversation — so these three are exactly what a LIVE handle knows
# about the stream it is holding: it has not ended yet, it ended on its own, or
# it ended on a terminal `error` (decision B1) / the total-duration cap
# (5.3-أ). A run read back LATER cannot be classified this finely from storage
# alone; the API layer says so in its own words rather than reusing these.
_RUN_RUNNING = "running"
_RUN_COMPLETED = "completed"
_RUN_FAILED = "failed"


@dataclass(slots=True)
class _CallMeter:
    """One provider call's running usage.

    Kept CURRENT as chunks arrive rather than totalled at the end, which is
    what makes an abandoned stream billable: nothing here depends on the
    generator ever being finalized (see ``_TokenMeter``).
    """

    prompt_text: str
    completion_parts: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def measured(self) -> bool:
        """True only once the provider actually reported BOTH counters."""
        return self.prompt_tokens is not None and self.completion_tokens is not None

    @property
    def prompt_total(self) -> int:
        """Reported prompt tokens, or the fallback estimate."""
        if self.prompt_tokens is not None:
            return self.prompt_tokens
        return _estimate_tokens(self.prompt_text)

    @property
    def completion_total(self) -> int:
        """Reported completion tokens, or the fallback estimate."""
        if self.completion_tokens is not None:
            return self.completion_tokens
        return _estimate_tokens("".join(self.completion_parts))

    @property
    def total(self) -> int:
        return self.prompt_total + self.completion_total


@dataclass(slots=True)
class _TokenMeter:
    """Accumulates one request's token usage across every LLM call the agent
    makes (4.7-c-2).

    **Eager, not end-of-stream.** An earlier draft totalled each call in the
    stream wrapper's ``finally`` — and a test proved that silently made
    abandonment FREE: Python does not finalize an async generator merely
    because its consumer stopped iterating, and closing the outer stream does
    not cascade down to the agent's inner ones, so at capture time the meter
    was still empty. Keeping every call's usage current as chunks arrive
    removes that dependency entirely: ``total`` is correct at any instant,
    whether the stream completed, failed, or was walked away from.

    **Why the orchestrator meters instead of the agent reporting.** `11 §8.1`
    says "the agent need only return the token counter in its output for the
    orchestrator to aggregate". Metering here achieves the same billing with
    strictly better properties, so this is a documented deviation (doc-sync
    recommendation on `11 §8.1`) rather than an oversight:

    * **Nothing to forget.** A plugin author cannot omit metering, because
      the agent is never asked to do it. Under the doc's literal reading, an
      agent that forgets to report its usage bills nothing at all — a silent
      revenue hole opened by a plugin, which is exactly the class of mistake
      the plugin architecture must not permit.
    * **Multi-call turns are correct by construction.** A tool loop (now
      expressible thanks to 4.7-a) makes SEVERAL provider calls in one
      request. This sums them; an agent self-reporting one number would have
      to remember to aggregate, and would silently under-bill if it did not.
    * **It reads the counters at the only place they exist** — the terminal
      ``LlmChunk``/``LlmResult`` the 4.7-a amendment added them to.

    A charge is called "measured" only if EVERY call in it reported real
    counters; one unreported call taints the whole request as an estimate,
    which is the conservative direction.
    """

    calls: list[_CallMeter] = field(default_factory=list)

    def begin(self, prompt_text: str) -> _CallMeter:
        """Register a call and hand back its live handle."""
        call = _CallMeter(prompt_text=prompt_text)
        self.calls.append(call)
        return call

    @property
    def prompt_total(self) -> int:
        """`03 §2`'s ``Usage.prompt_tokens`` — summed across the turn's calls."""
        return sum(call.prompt_total for call in self.calls)

    @property
    def completion_total(self) -> int:
        """`03 §2`'s ``Usage.completion_tokens`` — summed across the turn's calls."""
        return sum(call.completion_total for call in self.calls)

    @property
    def total(self) -> int:
        return self.prompt_total + self.completion_total

    @property
    def estimated(self) -> bool:
        """``UsageCharge.estimated``: any unreported call taints the total."""
        return any(not call.measured for call in self.calls)


def _joined(messages: Sequence[LlmMessage]) -> str:
    """The prompt text an estimate falls back to (mirrors each adapter's own
    ``"\\n".join(m.content ...)``)."""
    return "\n".join(m.content for m in messages)


def _estimate_tokens(text: str) -> int:
    """The per-character fallback; at least 1 so an empty turn never bills
    zero. Only ever reached on the ``estimated=True`` path."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _file_refs(data: Json) -> tuple[str, ...]:
    """The ``file_id`` references a payload carries, as message attachments
    (INV-CV2: attachments are file references, never bytes).

    Two shapes are honoured because both already exist in the build: an
    ``attachments`` list (what a client posts) and a single ``file_id`` (what
    the data-analysis / file-editing agents put in their ``final``).
    """
    raw = data.get("attachments")
    if isinstance(raw, list):
        refs = tuple(item for item in raw if isinstance(item, str) and item.strip())
        if refs:
            return refs
    file_id = data.get("file_id")
    return (file_id,) if isinstance(file_id, str) and file_id.strip() else ()


def _turn_content(data: Json) -> tuple[str, tuple[str, ...]]:
    """One payload (a request ``input`` or a terminal ``final``) → the
    ``(text, attachments)`` a conversation message is made of.

    ``text`` when the payload has one — the convention every text-producing
    agent already follows — plus any file references. **The JSON fallback is
    the interesting branch:** the media agents' ``final`` is
    ``{job_id,status,kind}`` with no text at all, and ``MessageContent``
    rejects a message that is neither text nor attachment. Serialising the
    payload records what the agent ACTUALLY said, losslessly, instead of
    either inventing a sentence for it or silently dropping the turn. It reads
    poorly in a chat UI, and that is the honest signal of the real gap:
    ``MessageContent`` has no structured ``data`` field (06 §4 / 01 §2.4), so
    a structured reply has nowhere else to go. Recorded as a doc-sync
    recommendation rather than papered over.
    """
    raw_text = data.get("text")
    attachments = _file_refs(data)
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text, attachments
    if attachments:
        return "", attachments
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str), attachments


def _deadline(cap_s: float | None) -> float | None:
    """The loop-clock instant this stream must end by, or ``None`` = uncapped.

    ONE deadline for the whole stream (5.3-أ, the §3.23(ز) debt): the adapters'
    httpx timeout is between-chunk only — deliberately, a whole-call cap in the
    adapter would sever a healthy long stream mid-chunk — so the TOTAL bound
    belongs to the orchestrator, the one place that sees a response end to end.
    """
    if cap_s is None:
        return None
    return asyncio.get_running_loop().time() + cap_s


async def _next_before(events: AsyncIterator[AgentEvent], deadline: float | None) -> AgentEvent:
    """``anext(events)``, but never past ``deadline``.

    ``asyncio.timeout`` wraps ONE await at a time with the REMAINING budget,
    on purpose: a single ``timeout`` spanning the generator's ``yield`` points
    would keep ticking while the frame is suspended and cancel whatever the
    CONSUMER happened to be awaiting between pulls — the cancellation would
    land in code that never opted into it. Re-arming per pull from one shared
    deadline gives the same total bound without ever cancelling foreign code.

    Raises ``TimeoutError`` when the budget is spent (including a budget that
    expired between pulls) and lets ``StopAsyncIteration`` fly for a stream
    that ends in time.
    """
    if deadline is None:
        return await anext(events)
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    async with asyncio.timeout(remaining):
        return await anext(events)


async def _close_quietly(events: AsyncIterator[AgentEvent]) -> None:
    """Best-effort ``aclose`` after a timeout, so the producer's ``finally``
    chain (executor dispose, engine unwind) runs NOW rather than at GC.

    Usually a no-op: the cancellation ``asyncio.timeout`` threw into the
    producer's suspended await has already unwound it. Tolerant of both a
    plain ``AsyncIterator`` without ``aclose`` and a close that itself fails —
    cleanup must never mask the timeout event the caller is about to yield.
    """
    aclose = getattr(events, "aclose", None)
    if aclose is None:
        return
    # Cleanup only — a close that fails must never mask the timeout event.
    with contextlib.suppress(Exception):
        await aclose()


def _timeout_event(cap_s: float) -> AgentEvent:
    """The terminal event for a stream that overran its total budget.

    Decision B1's shape exactly (``{code, status, detail}``): by the time the
    cap can fire, events have already escaped — the HTTP status is committed —
    so an in-band terminal ``error`` is the only honest channel, identical to
    every other in-flight failure. ``agent.failed``/502 because the 03 §4
    catalog is closed and this IS an execution failure: the platform killed
    the run for overrunning, and inventing an uncataloged code would hand
    Phase 6 an error its contract never named.
    """
    return AgentEvent(
        type="error",
        data={
            "code": "agent.failed",
            "status": 502,
            "detail": f"stream exceeded the total duration cap ({cap_s:g}s)",
        },
    )


class _MeteredLLM:
    """A transparent ``LLMProvider`` decorator that tees token counts into a
    ``_TokenMeter`` (4.7-c-2).

    Structural match, no inheritance — this codebase's house rule for ports
    since 2.3. It forwards both calls unchanged and never alters what the
    agent sees: an agent cannot tell it is being metered, and metering cannot
    change an answer.
    """

    def __init__(self, inner: LLMProvider, meter: _TokenMeter) -> None:
        self._inner = inner
        self._meter = meter
        self.provider = inner.provider

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        result = await self._inner.complete(messages, params, api_key)
        # `LlmResult` counters are non-optional and already fall back to an
        # estimate inside the adapter (`shared.token_count`), so a completed
        # call is treated as measured — the adapter, not this layer, is where
        # that distinction is knowable for the non-streaming path.
        call = self._meter.begin(_joined(messages))
        call.prompt_tokens = result.prompt_tokens
        call.completion_tokens = result.completion_tokens
        return result

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        # Plain `def` returning the generator, mirroring the port exactly, so
        # the adapter's own call-time guards still fire at call time.
        return self._metered_stream(
            self._inner.stream(messages, params, api_key), _joined(messages)
        )

    def supports(self, capability: str) -> bool:
        """Forwarded verbatim. A decorator that answered for itself would let
        metering change routing behaviour — the one thing this wrapper must
        never do (and mypy catches its absence, since the port requires it)."""
        return self._inner.supports(capability)

    async def _metered_stream(
        self, chunks: AsyncIterator[LlmChunk], prompt_text: str
    ) -> AsyncIterator[LlmChunk]:
        # The call is registered on the FIRST chunk, not at `stream()` time: a
        # stream that is created but never iterated consumed nothing, and
        # billing it would charge for a call that never left the process.
        call: _CallMeter | None = None
        async for chunk in chunks:
            if call is None:
                call = self._meter.begin(prompt_text)
            if chunk.delta:
                call.completion_parts.append(chunk.delta)
            if chunk.finish_reason is not None:
                # The terminal chunk is where 4.7-a put the real counters;
                # `None` here leaves the call on its estimate.
                call.prompt_tokens = chunk.prompt_tokens
                call.completion_tokens = chunk.completion_tokens
            yield chunk


@dataclass(frozen=True, slots=True)
class OrchestratorDependencies:
    """The PROCESS-WIDE collaborators, built once by the Composition Root.

    Deliberately distinct from ``AgentDependencies``, which is PER-REQUEST:
    turning these singletons plus one ``ExecutionContext`` into that
    per-request bundle is the orchestrator's core job, because the pieces that
    vary per request (which provider, which model, whose API key) are resolved
    from the caller's workspace, not fixed at boot.

    Every module-facing field is typed by a framework DIP Protocol and is
    OPTIONAL: an unwired seam yields a clean ``common.internal``/500 from the
    agent that needed it (the uniform 4.4/4.6 guard) instead of a crash, and
    keeps this constructible in tests with only what a case exercises.
    """

    agents: AgentRegistry
    executor: AgentLifecycleExecutor
    providers: ProviderResolver
    knowledge: KnowledgeAccess | None = None
    files: FilesAccess | None = None
    storage: StorageProvider | None = None
    media: MediaRequesting | None = None
    web_search: WebSearchProvider | None = None
    # 4.7-c-2 — the usage inbound ports (FR-131/132), imported NOMINALLY:
    # this is the agents layer, so no DIP mirror is needed (module docstring).
    # INV-U4: the orchestrator is the ONLY caller of these, never an agent.
    # Both optional so an unmetered deployment still boots; when absent,
    # quota is not enforced and nothing is captured (logged, see `_capture`).
    usage_enforcement: UsageEnforcement | None = None
    usage_capture: UsageCapture | None = None
    # 4.7-e-1 — the workflow seam. `workflows` is the static D-09 catalog;
    # `conversations` is the inbound port D-12 needs, since a workflow run
    # gets its OWN conversation and only this layer knows a run happened.
    # Optional like every other seam, but with a sharper consequence:
    # `invoke_workflow` REFUSES to run without them (see its guard) rather
    # than inventing a conversation id for a thread that does not exist.
    workflows: WorkflowRegistry | None = None
    conversations: ConversationThreads | None = None
    # 5.3-أ — the TOTAL duration cap for one streamed response (the §3.23(ز)
    # debt; `Limits.stream_max_duration_s` wired by the Composition Root).
    # `None` = uncapped, so a bare test bundle keeps today's behaviour; the
    # root always passes the configured number.
    stream_max_duration_s: float | None = None
    # 6.4-ب — the RBAC decision seam, imported NOMINALLY like the usage ports
    # above (this is the agents layer; no DIP mirror needed). It enforces the
    # agent MANIFEST's own `required_permissions` (02 §3.2: "checked by RBAC
    # before any run") — the per-agent half of authorization, which no route
    # guard can perform because only this layer resolves the manifest.
    #
    # Optional like every other seam, but the ONLY one whose absence fails
    # CLOSED: an unwired quota seam means "not metered", while an unwired
    # authorization seam would mean "not authorized, therefore allowed". A
    # bundle without it can still run agents that require NOTHING — there is no
    # decision to make — and refuses any agent that declares a permission.
    authorization: AuthorizationService | None = None


@dataclass(slots=True)
class _OpenStep:
    """The workflow step currently running: what it is, and what it has
    consumed so far.

    ``meter`` is LIVE — correct at any instant, never only at the end (the
    ``_TokenMeter`` discipline), which is what lets an abandoned run still bill
    the step it was walked away from.
    """

    agent_key: str
    provider: str
    meter: _TokenMeter


class _MeteredSteps:
    """The engine's ``AgentDepsProvider`` for one workflow run (4.7-e-1) — and
    the run's billing boundary (4.7-e-2).

    It resolves each step's agent through EXACTLY the same path a single-agent
    request takes — ``_resolve_llm`` → ``_enforce`` → ``_build_dependencies``,
    in that order — so a step inside a workflow and the same agent invoked
    directly get identical dependencies AND identical quota treatment.
    Anything else would mean an agent behaves differently depending on how it
    was reached, which is precisely the bug class the per-step provider was
    introduced to remove.

    ``ctx`` rides the constructor because the engine's port hands ``for_agent``
    only an ``agent_key``: the context belongs to the RUN, not to the step.

    **The step boundary was already here.** 4.7-e-1 named "a step boundary the
    engine does not signal" as what blocked per-step billing — but the engine
    calls ``for_agent`` exactly ONCE per step, immediately before it creates
    that step's agent, so arriving here means the PREVIOUS step is over. That
    partitions the run precisely and completely without adding a ``step`` event
    to the public stream (which would commit the yet-unbuilt Phase-6 SSE
    contract to a shape no requirement asks for) and without teaching the
    engine what a provider or a charge is. The last step has no successor, so
    the orchestrator closes it when the run's stream ends
    (``_metered_workflow``).

    **One charge per step, not one per run.** A workflow's steps may each
    resolve a DIFFERENT provider, while ``UsageCharge`` names a single
    ``agent`` and a single ``provider`` — so a single charge for the whole run
    could only be a lie about at least one step. N charges, each naming the
    agent that actually ran and the provider it actually used, is the only
    shape that stays true, and it is also the shape the ledger's rollup
    buckets (keyed by agent and by provider) already expect.

    **Billed as they finish, not accumulated to the end.** The same rule that
    made ``_TokenMeter`` eager: anything deferred to end-of-stream is simply
    absent for a run nobody drained. A ten-step run abandoned during step 8
    has already billed steps 1 through 7, each at the moment it ended.
    """

    def __init__(
        self, orchestrator: AgentOrchestrator, ctx: ExecutionContext, space_id: Uuid
    ) -> None:
        self._orchestrator = orchestrator
        self._ctx = ctx
        # س-32 — the run's space, on the constructor for `ctx`'s exact reason:
        # it belongs to the RUN, not to the step, and the engine's `for_agent`
        # port hands this object nothing but an `agent_key`. A workflow run
        # always has one (`invoke_workflow` requires it — the run's own D-12
        # thread is opened inside it), so unlike the single-agent path there is
        # no `None` case to describe here.
        self._space_id = space_id
        self._open: _OpenStep | None = None

    async def for_agent(self, agent_key: str) -> AgentDependencies:
        # Close the previous step BEFORE anything about this one can fail, so
        # a quota denial (or a resolution failure) on step 3 still bills the
        # tokens steps 1 and 2 really consumed.
        await self.close_step()
        meter = _TokenMeter()
        deps, provider = await self._orchestrator.begin_step(
            self._ctx, agent_key, meter, space_id=self._space_id
        )
        # Recorded only AFTER `begin_step` succeeded: a step the quota denied,
        # or whose provider could not be resolved, never ran and must never
        # produce a charge.
        self._open = _OpenStep(agent_key=agent_key, provider=provider, meter=meter)
        return deps

    async def close_step(self) -> None:
        """Bill the open step, if any. Idempotent — safe to call at a step
        boundary and again when the run ends."""
        step = self._open
        if step is None:
            return
        # Cleared BEFORE the await: a run that ends immediately after a step
        # boundary reaches this from both sides, and billing the same step
        # twice would double-charge a workspace for tokens it spent once.
        self._open = None
        await self._orchestrator.finish_step(self._ctx, step.agent_key, step.provider, step.meter)


class WorkflowRun:
    """A started workflow: identity known up front, outcome accruing as the
    stream drains (4.7-e-1).

    **Why a handle and not just an iterator.** `03-api-spec` gives the workflow
    endpoint two shapes from one operation — ``WorkflowRunIn.stream`` picks
    between them — and ``WorkflowRunOut`` requires ``conversation_id`` in
    BOTH. A bare ``AsyncIterator[AgentEvent]`` can carry neither the
    conversation nor the assembled result, so streaming callers would have had
    no way to report the thread they had just written to. This handle answers
    both: ``events()`` is the SSE stream, ``collect()`` is the non-streaming
    call, and ``conversation_id`` is readable before either.

    **``result`` is correct at any instant — the ``_TokenMeter`` discipline.**
    Outputs are appended as each ``final`` event passes through, never totalled
    in a ``finally``. 4.7-c-2 learned this the hard way: Python does not
    finalize an async generator merely because its consumer stopped iterating,
    so anything computed at end-of-stream is simply absent for an abandoned
    run. Reading ``result`` after a partial or halted run therefore yields the
    outputs that really were produced, rather than an empty list.

    **6.1-د-1 gave the run a body.** Every step's ``final`` is now written into
    the run's own D-12 thread as it passes, and the handle tracks whether the
    stream is still going, ended cleanly, or ended on a failure (``status``).
    Together with the opening turn ``invoke_workflow`` writes, that turns the
    conversation into the run's transcript — which is the only record of a run
    this build has, since `01-data-model` stores no run row (see ``run_id``).

    **One pass.** ``events()`` drains the underlying stream; calling it twice
    yields nothing the second time (the generator is exhausted). That is the
    honest behaviour for a live run — there is no replay buffer here. It is
    also what keeps 4.7-e-2's billing single: a second pass drives no step, so
    it opens no step to charge, and ``_MeteredSteps.close_step`` has already
    cleared the last one.
    """

    def __init__(
        self,
        workflow_key: str,
        conversation_id: Uuid,
        events: AsyncIterator[AgentEvent],
        on_finish: Callable[[], Awaitable[None]],
        stream_max_duration_s: float | None = None,
        on_step_output: Callable[[Json], Awaitable[None]] | None = None,
    ) -> None:
        self._workflow_key = workflow_key
        self._conversation_id = conversation_id
        self._events = events
        self._on_finish = on_finish
        self._stream_max_duration_s = stream_max_duration_s
        self._on_step_output = on_step_output
        self._outputs: list[Json] = []
        self._status = _RUN_RUNNING

    @property
    def workflow_key(self) -> str:
        return self._workflow_key

    @property
    def conversation_id(self) -> Uuid:
        """The workflow's OWN conversation (D-12) — known before the first
        event, which is what lets a streaming response report it."""
        return self._conversation_id

    @property
    def run_id(self) -> Uuid:
        """This run's identifier — **the conversation's id, deliberately.**

        `03 §2`'s ``WorkflowRunOut`` carries ``run_id`` AND ``conversation_id``,
        but `01-data-model` has no runs table: the only row a run ever writes
        is its D-12 conversation (``kind='workflow'``, ``agent_key`` = the
        workflow key). Minting a separate uuid here would hand the client an
        identifier that ``GET /workflows/runs/{id}`` could never resolve —
        a fabricated identity, which is the one thing this build refuses to
        ship. In v1 the two fields are therefore two views of one id; the wire
        contract keeps both so a future runs table can make them diverge
        without breaking a client (doc-sync recommendation to `01 §2.4`).
        """
        return self._conversation_id

    @property
    def status(self) -> str:
        """What this handle knows about its own stream, right now (6.1-د-1).

        ``running`` until the stream ends — which is also what an ABANDONED run
        reports, honestly: a consumer that walked away leaves no way to tell
        the run finished, and claiming otherwise would be a guess. ``failed``
        the moment a terminal ``error`` passes (B1) or the 5.3-أ cap cuts the
        run; ``completed`` only when the underlying stream ends of its own
        accord without either.
        """
        return self._status

    @property
    def result(self) -> WorkflowResult:
        """The 02 §3.3 carrier, assembled from the ordered per-step ``final``
        events. ``outputs`` is copied out so a caller cannot mutate the run."""
        return WorkflowResult(
            workflow_key=self._workflow_key,
            conversation_id=self._conversation_id,
            outputs=list(self._outputs),
        )

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Stream every event the run produces, collecting step outputs on the
        way past.

        Each step's ``final`` is what 02 §3.3 means by an entry in
        ``outputs`` — the engine emits exactly one per completed step, in
        order, so appending here preserves step order without the orchestrator
        having to count steps or know the definition.

        **``on_finish`` runs in THIS generator's ``finally``, deliberately.**
        4.7-e-2 bills the last step there, and it only works from here: closing
        an async generator does not cascade into the sub-iterator of an ``async
        for``, so a ``finally`` one layer down — in a wrapper the caller does
        not hold — would simply never run for a caller that walked away. That
        is the 4.7-c-2 lesson applied one level up, and it is why ``invoke``
        hands its metered generator back directly rather than wrapping it.

        **The total stream deadline applies here too (5.3-أ)** — same manual
        pull loop as ``_metered`` and for the same no-wrapper reason. A run
        that overruns is halted with the B1 terminal ``error`` event; the
        timeout event is NOT a step ``final``, so ``outputs`` keeps exactly
        the steps that really completed, and ``finally`` still bills the step
        that was cut (its meter is live, the ``_TokenMeter`` discipline).
        """
        cap_s = self._stream_max_duration_s
        deadline = _deadline(cap_s)
        try:
            while True:
                try:
                    event = await _next_before(self._events, deadline)
                except StopAsyncIteration:
                    # The only path that may claim completion: the engine ran
                    # out of steps on its own. A `failed` set earlier stands —
                    # the engine ends its stream after a terminal error too.
                    if self._status == _RUN_RUNNING:
                        self._status = _RUN_COMPLETED
                    break
                except TimeoutError:
                    assert cap_s is not None  # None cap ⇒ None deadline ⇒ no timeout
                    await _close_quietly(self._events)
                    _logger.warning(
                        "orchestrator.stream_deadline_exceeded",
                        extra={"workflow_key": self._workflow_key, "cap_s": cap_s},
                    )
                    self._status = _RUN_FAILED
                    yield _timeout_event(cap_s)
                    break
                if event.type == "final":
                    self._outputs.append(event.data)
                    # The step's output is written into the run's own thread
                    # HERE, inside the generator the caller holds — the
                    # 4.7-c-2/e-2 lesson that `on_finish` already obeys.
                    # Persisting from a wrapper would simply never run for an
                    # abandoned run, and a run that is abandoned mid-pipeline
                    # is exactly the one whose completed steps are worth
                    # keeping: they were produced, and they were billed.
                    if self._on_step_output is not None:
                        await self._on_step_output(event.data)
                elif event.type == "error":
                    self._status = _RUN_FAILED
                yield event
        finally:
            await self._on_finish()

    async def collect(self) -> WorkflowResult:
        """Drain the run and return its result — the non-streaming
        (``stream=false``) call in one line."""
        async for _event in self.events():
            pass
        return self.result


@dataclass(slots=True)
class _TurnRecord:
    """The mutable bookkeeping of ONE single-agent turn.

    It exists because ``invoke`` hands back an iterator while the facts the
    non-streaming caller needs (which thread the turn landed in, which message
    the reply became, what it consumed) are only complete once that iterator
    has been drained. Handing the record to the streaming generator and
    reading it afterwards keeps ``invoke_once`` typed, instead of re-parsing
    the ``final`` frame's own JSON to recover values this class just wrote.
    """

    meter: _TokenMeter
    conversation_id: Uuid | None = None
    message: AppendedMessage | None = None


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """One completed, non-streamed agent turn — what `03 §2`'s
    ``AgentInvokeOut``/``MessageOut`` are rendered from (6.1-ج-3)."""

    conversation_id: Uuid
    message: AppendedMessage
    prompt_tokens: int
    completion_tokens: int
    final: Json


def _in_band_error(event: AgentEvent) -> AppError:
    """A terminal ``error`` event → the ``AppError`` it stands for.

    Only the NON-streaming path uses this. Decision B1 makes an in-flight
    failure an event because the HTTP status is already committed once frames
    have escaped — but a caller that collects the whole turn before answering
    has written nothing yet, so the honest reply is the problem response the
    event describes, not a 200 wrapping an error payload. The three B1 fields
    are read defensively: an event that does not carry them still becomes a
    truthful ``agent.failed`` rather than a ``KeyError``.
    """
    code = event.data.get("code")
    status = event.data.get("status")
    detail = event.data.get("detail")
    return AppError(
        detail if isinstance(detail, str) else "the agent run failed",
        code=code if isinstance(code, str) else "agent.failed",
        status=status if isinstance(status, int) else 502,
    )


class AgentOrchestrator:
    """Runs ONE agent for ONE request (11 §8.1).

    Stateless and safe to share across requests: it holds only the injected
    singletons, and every per-request value lives in a local. The same
    reasoning as the workflow engine's decision D.
    """

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self._deps = deps

    async def invoke(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest
    ) -> AsyncIterator[AgentEvent]:
        """Pre-flight, then hand back the agent's event stream.

        Raises (never yields) on a pre-flight failure — see the module
        docstring on why that distinction is the API layer's whole ability to
        answer 404/422 instead of a broken body.
        """
        _record, events = await self._begin_turn(ctx, agent_key, req)
        return events

    async def invoke_once(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest
    ) -> AgentTurn:
        """Run one agent turn to completion and return it whole (6.1-ج-3).

        The non-streaming half of `03 §2`'s invoke contract: same pre-flight,
        same persistence, same billing — only the delivery differs. It drains
        the stream itself instead of handing it out, so:

        * an in-flight ``error`` event becomes a RAISED ``AppError`` (nothing
          has been written to the wire, so a real problem response is still
          possible — see ``_in_band_error``);
        * the stream is closed explicitly on every path, because leaving a
          half-consumed generator to be finalized at GC is exactly how billing
          was once lost (the 4.7-c-2 lesson).

        A turn that persisted no message cannot be rendered as
        ``AgentInvokeOut`` — that happens only where the conversations seam is
        unwired, so it is a deployment fault (500), not a caller's.
        """
        record, events = await self._begin_turn(ctx, agent_key, req)
        final: Json = {}
        try:
            async for event in events:
                if event.type == "error":
                    raise _in_band_error(event)
                if event.type == "final":
                    final = event.data
        finally:
            await _close_quietly(events)
        if record.conversation_id is None or record.message is None:
            raise AppError(
                "conversation persistence is not wired on this deployment",
                code="common.internal",
            )
        return AgentTurn(
            conversation_id=record.conversation_id,
            message=record.message,
            prompt_tokens=record.meter.prompt_total,
            completion_tokens=record.meter.completion_total,
            final=final,
        )

    async def _begin_turn(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest
    ) -> tuple[_TurnRecord, AsyncIterator[AgentEvent]]:
        """The shared spine of ``invoke``/``invoke_once``: pre-flight, open the
        turn, and build the metered+persisting stream.

        The ORDER is load-bearing. Quota precedes agent creation (a denial must
        cost nothing, FR-132), and agent creation precedes the conversation
        write — an unknown agent key must not leave an orphan thread and a user
        message behind for a run that never started.
        """
        record = _TurnRecord(meter=_TokenMeter())
        # Authorization FIRST — before the credential lookup `_resolve_llm`
        # performs, before quota, before anything. A refusal must cost nothing
        # (FR-132's rule, and a forbidden caller deserves it even more than a
        # throttled one), and an unauthorized request must not appear in the
        # usage ledger of a workspace it was never allowed to spend.
        self._authorize(ctx, agent_key)
        # BE-RAG-003 — the thread's pin is read BEFORE resolution, because the
        # thing it changes is which route gets resolved. It sits after
        # `_authorize` for the same reason everything does: a forbidden caller
        # must not cause a read of somebody's conversation.
        route = await self._pinned_route(ctx, req.conversation_id)
        # BE-RAG-005 — the thread's retrieval scope, read alongside its route
        # and for the same reason: both are what the thread was configured to
        # answer with, and both must be known before the agent exists.
        scope = await self._pinned_scope(ctx, req.conversation_id)
        # س-32 — the turn's SPACE, read in the same pre-flight breath and for a
        # stronger reason than either of the two above: they are preferences,
        # this is the isolation boundary. It must be known before the agent
        # exists, because the agent's every read of files and knowledge is
        # scoped by it.
        space_id = await self._turn_space(ctx, req)
        binding = await self._resolve_llm(ctx, agent_key, route=route)
        provider_name = binding.provider.provider if binding is not None else _NO_PROVIDER

        # Quota BEFORE the run (FR-132, 11 §8.1): a denial must cost nothing,
        # so this precedes agent creation, and it raises rather than yielding
        # -- still pre-flight, so Phase 6 answers a real 429.
        await self._enforce(ctx, agent_key, provider_name)

        deps = self._build_dependencies(binding, record.meter, scope, space_id=space_id)
        # Registry raises NotFoundError(404) for an unknown key and
        # ValidationError(422) for a malformed one -- both pass straight
        # through, still pre-flight, still mappable to a response.
        agent = self._deps.agents.create(agent_key, ctx, deps)
        record.conversation_id = await self._open_turn(ctx, agent_key, req)
        return record, self._metered(
            ctx, agent_key, provider_name, record, self._deps.executor.drive(agent, req)
        )

    async def _open_turn(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest
    ) -> Uuid | None:
        """Resolve this turn's thread and write the user's message into it
        (D-12 for the single agent) — still pre-flight.

        Pre-flight is the point: an unknown ``conversation_id`` surfaces as the
        append's own 404 and a deleted one as its 409, both while the status is
        still open, instead of detonating mid-stream. A request that names no
        thread gets a fresh one, because `03 §2`'s ``AgentInvokeOut`` carries a
        REQUIRED ``conversation_id`` — every invoke belongs to a thread.

        **An unwired seam degrades instead of refusing** (``None`` ⇒ persist
        nothing, stream normally). This is the opposite of ``invoke_workflow``,
        which refuses: there, ``WorkflowResult.conversation_id`` is
        non-optional, so carrying on would mean inventing an id; here the
        streamed answer is complete and correct without persistence, and it is
        ``invoke_once`` — the one caller that cannot do without a message —
        that raises.
        """
        threads = self._deps.conversations
        if threads is None:
            return None
        conversation_id = req.conversation_id
        if conversation_id is None:
            if req.space_id is None:
                # `space_id` is optional on `AgentRequest` because a request
                # that CONTINUES a thread inherits that thread's space; a
                # request that opens one has nothing to inherit from, and
                # there is no space this layer could honestly invent -- filing
                # the thread anywhere would file its whole retrieval scope
                # there too (decision 1). Raised pre-flight, like every other
                # failure in this method, so the caller meets a 422 while the
                # HTTP status is still open.
                raise ValidationError(
                    "space_id is required when no conversation_id is given",
                )
            started = await threads.start(
                ctx,
                space_id=req.space_id,
                agent_key=agent_key,
                kind=_AGENT_KIND,
            )
            conversation_id = started.id
        text, attachments = _turn_content(req.input)
        await threads.append(
            ctx, conversation_id, role=_ROLE_USER, text=text, attachments=attachments
        )
        return conversation_id

    async def invoke_workflow(
        self, ctx: ExecutionContext, workflow_key: str, initial_input: Json, *, space_id: Uuid
    ) -> WorkflowRun:
        """Start a workflow run: open its conversation (D-12), build a
        per-request engine, and hand back the run handle (4.7-e-1).

        Same pre-flight contract as ``invoke``: everything that can fail before
        the first event raises while the caller is still awaiting — an unknown
        workflow is a ``workflow.unknown`` 404 straight from the registry, and
        a conversation that cannot be opened surfaces as itself. Once the
        handle is returned the stream owns all failures (the engine's terminal
        ``error`` event).

        **The conversation comes BEFORE the engine, deliberately.** D-12 makes
        the thread part of the run's identity, and ``WorkflowRunOut`` carries
        ``conversation_id`` on the streaming response too — so it has to exist
        before a single event escapes, not be back-filled once the run ends. A
        run whose conversation could not be written is not a run that should
        have started.

        **Metered on two levels (4.7-e-2).** The RUN is gated here, before its
        conversation is written; each STEP is enforced and charged
        individually as the engine reaches it (``_MeteredSteps``). One charge
        per run was never available — steps may resolve different providers
        while ``UsageCharge`` names one — so the step is the billing unit.
        """
        registry = self._deps.workflows
        threads = self._deps.conversations
        if registry is None or threads is None:
            # The uniform unwired-seam guard (4.4/4.6), applied to a seam that
            # cannot degrade: `WorkflowResult.conversation_id` is not optional,
            # and the only ways to "carry on" would be to invent an id for a
            # thread nobody wrote or to hand back a result that lies about
            # which conversation holds the run. Refusing is the honest option.
            raise AppError(
                "workflow execution is not wired on this deployment",
                code="common.internal",
            )
        definition = registry.get(workflow_key)
        # Quota BEFORE the conversation is written — FR-132's "a denial must
        # cost nothing", applied to the run: a workspace already over its limit
        # gets a real 429 pre-flight with no orphan thread left behind, exactly
        # as `invoke` gets one before any agent is created.
        #
        # The catalog lookup precedes it deliberately: it is a free in-memory
        # dict read, and a bogus key deserves its `workflow.unknown` 404 rather
        # than a 429 about a workflow that does not exist.
        #
        # `_NO_PROVIDER` because no step has resolved yet and there is nothing
        # to guess — inventing one step's provider for a run-level check would
        # apply that provider's limits to every other step's tokens. This is
        # the RUN-level gate (workspace-wide rules, plus any rule an operator
        # scoped to the workflow key); every step is checked again under its
        # own agent key and its real provider in `begin_step`.
        await self._enforce(ctx, workflow_key, _NO_PROVIDER)
        conversation = await threads.start(
            ctx,
            # REQUIRED here, where `_open_turn` accepts `None` — and the
            # difference is that a run has no thread to inherit from. Every
            # workflow run opens its own D-12 thread (that is what makes the
            # run identifiable at all), so there is never a case where the
            # space is already known, and an optional parameter would only
            # describe a call that cannot legitimately happen.
            space_id=space_id,
            agent_key=workflow_key,
            kind=_WORKFLOW_KIND,
            # The definition's human name, so the thread is legible in a
            # conversation list without the reader having to resolve the key.
            title=definition.name,
        )
        # The run's OPENING turn, written pre-flight exactly as `_open_turn`
        # writes the single agent's (6.1-د-1). Until now a workflow opened its
        # D-12 thread and left it EMPTY — the thread existed but recorded
        # nothing, so the run had no readable trace at all and `GET
        # /workflows/runs/{id}` had nothing beneath it. A failure here raises
        # while the status is still open, before any event escapes.
        #
        # It leaves an empty conversation behind, and that is the honest
        # trade: `start` and `append` are two calls on the module's inbound
        # port with no transaction spanning them, and an orphan thread with no
        # messages is a far better outcome than a run whose input was never
        # recorded.
        text, attachments = _turn_content(initial_input)
        await threads.append(
            ctx, conversation.id, role=_ROLE_USER, text=text, attachments=attachments
        )
        _logger.info(
            "orchestrator.workflow_started",
            extra={
                "workflow_key": workflow_key,
                "conversation_id": conversation.id,
                "steps": len(definition.steps),
            },
        )
        # One engine per request (11 §8.1's per-request rule, applied to the
        # engine exactly as it applies to an agent) — it must be, since its
        # dependency provider is bound to THIS context. The concrete engine is
        # named directly rather than injected: D-09 fixes v1 workflows as
        # linear with no alternative driver to choose between, so a factory
        # would be a seam with one implementation and no second caller.
        steps = _MeteredSteps(self, ctx, space_id)
        engine = SequentialWorkflowEngine(self._deps.agents, self._deps.executor, steps)

        async def write_step_output(data: Json) -> None:
            await self._persist_step_output(ctx, conversation.id, data)

        return WorkflowRun(
            workflow_key=workflow_key,
            conversation_id=conversation.id,
            events=engine.run(ctx, definition, initial_input),
            # Every step but the last is billed by the NEXT step's `for_agent`;
            # the last one has no successor, so the handle closes it when its
            # stream ends — completed, failed, or abandoned. It consumed real
            # provider tokens on any of those three paths.
            on_finish=steps.close_step,
            stream_max_duration_s=self._deps.stream_max_duration_s,
            on_step_output=write_step_output,
        )

    async def _persist_step_output(
        self, ctx: ExecutionContext, conversation_id: Uuid, data: Json
    ) -> None:
        """Write one completed step's output into the run's thread (6.1-د-1).

        **One message per step, in order.** The engine emits exactly one
        ``final`` per completed step, so the thread ends up as the run's
        transcript: the initial input, then one assistant turn per step that
        really finished. Nothing is written for a step that failed — there is
        no output to record — which is precisely what makes the message count
        readable later as "how far this run got".

        ``token_count`` is left unset, unlike the single agent's reply. The
        meter that knows a step's cost is the STEP's (``_MeteredSteps``), it is
        billed and closed on the step boundary, and attributing a per-step
        completion count from here would mean reaching into another object's
        live meter to guess at it. The ledger already carries the real numbers.

        **A failed write degrades to a warning** — the same trade as
        ``_persist_reply``: the step's output has been produced and streamed,
        and raising here would replace a delivered answer with an error the
        client cannot act on, and would halt the remaining steps on a
        bookkeeping fault.
        """
        threads = self._deps.conversations
        if threads is None:  # pragma: no cover - `invoke_workflow` refuses first
            return
        text, attachments = _turn_content(data)
        try:
            await threads.append(
                ctx, conversation_id, role=_ROLE_ASSISTANT, text=text, attachments=attachments
            )
        except Exception as exc:  # never mask a produced output
            _logger.warning(
                "orchestrator.step_output_persist_failed",
                extra={"conversation_id": conversation_id},
                exc_info=exc,
            )

    async def begin_step(
        self, ctx: ExecutionContext, agent_key: str, meter: _TokenMeter, *, space_id: Uuid
    ) -> tuple[AgentDependencies, str]:
        """Start one workflow step: resolve its provider, enforce the quota,
        assemble its bundle — returning the bundle and the provider name the
        eventual charge must carry (4.7-e-1; enforcement added 4.7-e-2).

        Public, with ``finish_step``, because ``_MeteredSteps`` is their only
        caller and this IS the orchestrator's contract with the workflow
        engine; reaching into a private method from a collaborator would hide
        a real seam.

        Deliberately the SAME three moves in the SAME order as ``invoke``
        (resolve → enforce → build), with the run's ``space_id`` (س-32) riding
        into the third. The order is load-bearing in both: the
        quota is checked once the provider is known, so an agent-scoped and a
        provider-scoped limit both get their say, and it is checked before the
        agent exists, so a denial costs nothing.

        A failure here RAISES, and the engine converts it into its terminal
        ``error`` event — a quota denial arrives as ``usage.quota_exceeded``/429
        intact, since ``RateLimitedError`` is an ``AppError`` — after which the
        run halts and no later step runs. That is the honest shape: a workspace
        that exhausts its quota mid-workflow keeps the steps it already paid
        for and stops there, rather than either finishing on credit or losing
        the work it had already bought.
        """
        binding = await self._resolve_llm(ctx, agent_key)
        provider = binding.provider.provider if binding is not None else _NO_PROVIDER
        await self._enforce(ctx, agent_key, provider)
        # س-32 — the RUN's space reaches every step of it. A step's agent is the
        # same agent a user could invoke directly, so it gets the same isolation
        # by the same field; without this a workflow would be the one way to
        # reach an unscoped read, which is precisely the hole the decision
        # closes on the direct path.
        return self._build_dependencies(binding, meter, space_id=space_id), provider

    async def finish_step(
        self, ctx: ExecutionContext, agent_key: str, provider: str, meter: _TokenMeter
    ) -> None:
        """Bill one finished workflow step — the same capture, and the same two
        deliberate silences, as a single-agent request (see ``_capture``).

        In particular a step that consumed nothing writes nothing: a media step
        inside a pipeline (D-04) leaves no zero-token row, exactly as the same
        agent invoked directly leaves none.
        """
        await self._capture(ctx, agent_key, provider, meter)

    def _build_dependencies(
        self,
        binding: ResolvedLLM | None,
        meter: _TokenMeter,
        scope: tuple[Uuid, ...] = (),
        *,
        space_id: Uuid | None,
    ) -> AgentDependencies:
        """Assemble the per-request bundle handed to the agent.

        The LLM handed over is WRAPPED in ``_MeteredLLM`` — the agent sees an
        ordinary ``LLMProvider`` and cannot tell, which is the point: metering
        is not something a plugin can forget or opt out of.

        ``scope`` (BE-RAG-005) defaults to ``()`` — unscoped — which is what
        ``begin_step`` passes: a workflow step runs inside the run's OWN
        conversation (D-12), created by that run, so there is nothing pinned to
        it and no user who could have pinned anything.

        ``space_id`` (س-32) is the run's isolation boundary. Keyword-only and
        with NO default, unlike ``scope`` beside it, and the asymmetry is the
        decision restated at this layer: a forgotten pin narrows nothing, a
        forgotten space widened across spaces — so both callers have to say.
        ``_prepare`` names the turn's thread's space, ``begin_step`` the run's.

        Nullable, because there is one shape where no space can honestly be
        known — an orchestrator with no conversations seam wired. ``None``
        there means "no space known", never "every space": an agent handed
        ``None`` reads no file and retrieves nothing. That is strictly safer
        than the pre-decision behaviour, where the same unknown silently read
        across all of them.
        """
        metered = (
            ResolvedLLM(
                provider=_MeteredLLM(binding.provider, meter),
                model=binding.model,
                api_key=binding.api_key,
            )
            if binding is not None
            else None
        )
        return AgentDependencies(
            llm=metered,
            knowledge=self._deps.knowledge,
            files=self._deps.files,
            storage=self._deps.storage,
            media=self._deps.media,
            web_search=self._deps.web_search,
            knowledge_scope=scope,
            space_id=space_id,
        )

    async def _enforce(self, ctx: ExecutionContext, agent_key: str, provider: str) -> None:
        """Check the workspace's quota; raise 429 when denied (FR-132).

        The decision OBJECT is what the port returns (never a bare bool), and
        its ``reason`` selects the error code — ``usage.quota_exceeded`` /
        ``usage.budget_exceeded``, exactly the codes ``11 §8.2`` names, rather
        than the generic ``common.rate_limited`` that would tell an operator
        nothing about WHICH limit stopped the request.

        SELECTS, not interpolates (6.2). ``LimitDecision.reason`` is a plain
        ``str | None`` on the port, so an ``f"usage.{reason}"`` would let any
        enforcement adapter — including a future one nobody has written —
        mint a code outside 03 §4's catalog, straight onto the wire, with no
        way to notice. The mapping is closed and its fallback is the
        catalogued generic: a denial with an unrecognised reason is still a
        truthful 429, just a less specific one.

        ``estimated_tokens`` is deliberately not supplied: nothing can know a
        request's token cost before running it, and inventing a number here
        would silently shrink every workspace's headroom by that guess. The
        port makes it optional for exactly this reason.

        ``retry_after_s`` is CARRIED, not computed (3.79). This method is the
        natural producer of the 429 but the wrong place to reason about when a
        quota resets: the orchestrator does not know which period bound the
        decision, and the enforcement adapter — which does — already put the
        number on the decision object. Passing it through unexamined is what
        keeps the header honest; ``None`` simply means no header.
        """
        enforcement = self._deps.usage_enforcement
        if enforcement is None:
            return
        decision = await enforcement.check(ctx, agent_key, provider)
        if decision.allowed:
            return
        reason = decision.reason or _DEFAULT_DENIAL
        raise RateLimitedError(
            f"usage limit reached for this workspace ({reason})",
            code=_DENIAL_CODES.get(reason, "common.rate_limited"),
            retry_after_s=decision.retry_after_s,
        )

    async def _metered(
        self,
        ctx: ExecutionContext,
        agent_key: str,
        provider: str,
        record: _TurnRecord,
        events: AsyncIterator[AgentEvent],
    ) -> AsyncIterator[AgentEvent]:
        """Forward the agent's events under the total stream deadline (5.3-أ),
        persist the terminal turn, then capture what the run consumed.

        **Persistence lives INSIDE this generator, not in a wrapper around it**
        — the same 4.7-c-2/e-2 lesson the deadline obeys: closing an outer
        generator does not cascade into the sub-iterator of an ``async for``,
        so a separate persisting layer would have re-opened the abandonment
        hole this method exists to keep closed.

        The deadline lives INSIDE this generator rather than in a wrapper
        around it — the 4.7-c-2/e-2 lesson again: closing an outer generator
        does not cascade into the sub-iterator of an ``async for``, so a
        separate capping layer would have re-opened the very abandonment hole
        (`finally` never runs) that keeping ``invoke`` wrapper-free closed.
        A manual pull loop is the same iteration with the deadline applied
        per pull; on expiry the producer is closed explicitly, the B1
        terminal ``error`` event goes out in-band, and ``finally`` still
        bills whatever the run consumed up to the cut.
        """
        cap_s = self._deps.stream_max_duration_s
        deadline = _deadline(cap_s)
        try:
            while True:
                try:
                    event = await _next_before(events, deadline)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    # `cap_s` cannot be None here: a None cap yields a None
                    # deadline and `_next_before` never times out on one.
                    assert cap_s is not None
                    await _close_quietly(events)
                    _logger.warning(
                        "orchestrator.stream_deadline_exceeded",
                        extra={"agent_key": agent_key, "cap_s": cap_s},
                    )
                    yield _timeout_event(cap_s)
                    break
                if event.type == "final":
                    event = await self._persist_reply(ctx, record, event)
                yield event
        finally:
            # `finally` so an abandoned or failed run is still billed: it
            # consumed real provider tokens either way. This is sound ONLY
            # because `_TokenMeter` is kept current as chunks arrive — an
            # end-of-stream tally would still be empty here, since closing
            # this generator does not cascade down to the agent's own inner
            # generators (see `_TokenMeter`'s "eager, not end-of-stream").
            await self._capture(ctx, agent_key, provider, record.meter)

    async def _persist_reply(
        self, ctx: ExecutionContext, record: _TurnRecord, event: AgentEvent
    ) -> AgentEvent:
        """Write the agent's reply into the thread and return the ``final``
        event enriched with what `03 §3.1` says that frame carries —
        ``message_id`` · ``content`` · ``usage``.

        The agent's own terminal keys are KEPT alongside them (``citations``,
        ``job_id``, …): the contract's example lists what the platform adds,
        and dropping what the agent said to match an example literally would
        lose real information the streaming client already relies on.

        ``token_count`` on the stored message is the COMPLETION half of the
        turn — what this reply cost — not the turn total, which would double
        count the prompt against every message in the thread.

        **A failed write degrades to the un-enriched event.** By the time a
        ``final`` is in hand the answer has been produced and (for a streaming
        caller) partially delivered; raising here would replace a good answer
        with an error the client cannot act on — the same trade-off, and the
        same operator-facing warning, as ``_capture``. ``invoke_once`` is not
        fooled: it reads the record, which stays empty, and refuses to invent
        an ``AgentInvokeOut``.
        """
        threads = self._deps.conversations
        if threads is None or record.conversation_id is None:
            return event
        text, attachments = _turn_content(event.data)
        completion = record.meter.completion_total
        try:
            message = await threads.append(
                ctx,
                record.conversation_id,
                role=_ROLE_ASSISTANT,
                text=text,
                attachments=attachments,
                token_count=completion or None,
            )
        except Exception as exc:  # never mask a produced answer
            _logger.warning(
                "orchestrator.reply_persist_failed",
                extra={"conversation_id": record.conversation_id},
                exc_info=exc,
            )
            return event
        record.message = message
        return AgentEvent(
            type=event.type,
            data={
                **event.data,
                "message_id": message.id,
                "content": {"text": message.text, "attachments": list(message.attachments)},
                "usage": {
                    "prompt_tokens": record.meter.prompt_total,
                    "completion_tokens": completion,
                },
            },
        )

    async def _capture(
        self, ctx: ExecutionContext, agent_key: str, provider: str, meter: _TokenMeter
    ) -> None:
        """Record consumption after the run (FR-131/134) — synchronous, no
        Streams, idempotent on ``operation_id``.

        Two deliberate silences:

        * **Nothing consumed, nothing recorded.** A run that made no LLM call
          at all (the media agents, D-04) would otherwise write a zero-token
          ledger row per request — pure noise in an append-only table. The
          generation those agents queue is metered by the Phase-5 worker that
          actually performs it.
        * **A capture failure never fails the request.** The answer has
          already been delivered in full by the time this runs; raising here
          would turn a successful, already-streamed response into an error the
          client cannot act on. Logged at warning instead — the same
          reasoning as the executor's swallowed ``dispose`` (decision E), and
          the same trade-off recorded honestly: a lost capture is lost
          revenue, so this log is the operator's signal to investigate.
        """
        capture = self._deps.usage_capture
        if capture is None or meter.total <= 0:
            return
        try:
            await capture.record(
                ctx,
                UsageCharge(
                    agent=agent_key,
                    provider=provider,
                    tokens=meter.total,
                    cost_micros=_V1_COST_MICROS,
                    operation_id=new_uuid7(),
                    estimated=meter.estimated,
                ),
            )
        except Exception as exc:  # never mask a delivered answer
            _logger.warning(
                "orchestrator.usage_capture_failed",
                extra={"agent_key": agent_key, "provider": provider, "tokens": meter.total},
                exc_info=exc,
            )

    async def _pinned_route(
        self, ctx: ExecutionContext, conversation_id: Uuid | None
    ) -> str | None:
        """The conversation's pinned D-16 route, or ``None`` (BE-RAG-003).

        ``None`` on every path that has no thread to ask: a request opening a
        FRESH thread cannot carry a pin (there is nothing pinned yet), and an
        unwired conversations seam degrades to unpinned exactly as
        ``_open_turn`` degrades to persisting nothing.
        """
        if conversation_id is None:
            return None
        threads = self._deps.conversations
        if threads is None:
            return None
        return await threads.routed_model(ctx, conversation_id)

    async def _pinned_scope(
        self, ctx: ExecutionContext, conversation_id: Uuid | None
    ) -> tuple[Uuid, ...]:
        """The conversation's pinned retrieval scope, or ``()`` (BE-RAG-005).

        ``()`` on the same three paths ``_pinned_route`` answers ``None`` on —
        no thread, no conversations seam, nothing pinned — and it means the
        same thing all three times: this run is unscoped and retrieval sees the
        whole workspace corpus. That the "not wired" and the "not pinned"
        answers coincide is deliberate: an orchestrator built without a
        conversations seam must degrade to the behaviour every thread had
        before pins existed, never to an empty knowledge base.
        """
        if conversation_id is None:
            return ()
        threads = self._deps.conversations
        if threads is None:
            return ()
        return await threads.pinned_files(ctx, conversation_id)

    async def _turn_space(self, ctx: ExecutionContext, req: AgentRequest) -> Uuid | None:
        """The space this turn works in (س-32, owner decision 2026-08-26).

        **The thread's space wins, and the request's is only consulted when
        there is no thread yet.** A request that continues a conversation
        cannot restate its space — ``_open_turn`` already ignores
        ``req.space_id`` in that case, so believing it here would hand the
        agent a scope that disagrees with the row every message is written
        into, and a caller could move a thread's retrieval into another space
        just by naming one. Reading the thread is what makes the space a
        property of the conversation rather than of the request.

        ``None`` on three paths, and it means the same thing on all three: no
        space is known for this turn. No conversations seam wired (the
        orchestrator's degraded shape); a thread that is gone; a request that
        opened no thread and named no space — which ``_open_turn`` refuses a
        moment later anyway, and would have raised here first if this read
        owned the reporting. Every consumer of the bundle treats ``None`` as
        "read nothing", never as "read everything".
        """
        threads = self._deps.conversations
        if threads is None:
            return None
        if req.conversation_id is None:
            return req.space_id
        return await threads.space_of(ctx, req.conversation_id)

    async def _resolve_llm(
        self, ctx: ExecutionContext, agent_key: str, *, route: str | None = None
    ) -> ResolvedLLM | None:
        """Resolve this workspace's provider/model/key, or ``None`` for an
        agent that has no use for one.

        ``route`` is the thread's pin and, when present, REPLACES the agent key
        as the routing capability — that is the whole of BE-RAG-003 at this
        layer. It never becomes a ``model=`` override: overriding the model
        inside whatever provider the agent routes to would send an OpenAI model
        name to Ollama the moment the two routes disagree. Pinning the KEY
        moves provider and model together, which is why the pin is a routing
        key and not a model name.

        A pin whose route the operator has since retired misses the table and
        falls through to the resolver's own ``default`` entry — the same thing
        that happens to an agent key with no route of its own. Uniform, and
        strictly better than failing a thread because a config edit no longer
        mentions it.

        The default is ``None`` so the workflow-step path (which has its own
        D-12 thread per RUN, not a user-pinned one) keeps resolving by agent
        key without saying so.

        The skip is not an optimisation. Resolving an LLM performs a real
        credential lookup, and the media agents (D-04: they queue a job and
        stream nothing) would be failed at the door on a deployment that
        configures no LLM credential at all — for a provider they never call.
        The test is the agent's OWN declared metadata rather than a hardcoded
        list of agent keys, so a future plugin gets the right behaviour
        without this file knowing it exists.
        """
        metadata = self._metadata_for(agent_key)
        if metadata is not None and _LLM_CAPABILITY not in metadata.capabilities:
            return None
        provider, resolved = await self._deps.providers.resolve_llm(
            ctx, capability=route or agent_key
        )
        return ResolvedLLM(provider=provider, model=resolved.model, api_key=resolved.api_key)

    def _authorize(self, ctx: ExecutionContext, agent_key: str) -> None:
        """Enforce the agent's OWN declared permissions (02 §3.2, 05 §1.4).

        Two layers of RBAC meet on one request and neither replaces the other:
        the route guard answers "may this role invoke agents at all"
        (``agents:invoke``, 05 §4), and this answers "may it invoke THIS one" —
        a question only this layer can ask, because only this layer resolves
        the manifest.

        **And it is the only layer the WebSocket has.** Guards are FastAPI
        dependencies; a socket has no route, so ``{"type":"invoke"}`` over
        ``/api/v1/ws`` passes no route guard whatsoever. The endpoint checks
        ``agents:invoke`` itself (5.3-ج + 6.4-ب) and this check supplies the
        per-agent half — so the two transports are authorized identically
        rather than one being a way around the other.

        An UNKNOWN key is not refused here: ``registry.create`` owns the
        unknown-agent 404 (the ``_metadata_for`` rule), and a second judgement
        would let two call sites disagree about what "unknown" means. Nothing
        leaks from deferring — a caller who may not invoke the agent still gets
        a 403 from the route guard on the HTTP path, and an unknown key is
        equally unknown to everyone.
        """
        metadata = self._metadata_for(agent_key)
        if metadata is None or not metadata.required_permissions:
            return  # unknown (⇒ 404 in a moment) or an agent that asks nothing
        authorization = self._deps.authorization
        if authorization is None:
            # The one seam that refuses instead of degrading: "we cannot decide"
            # must never become "allowed" on an authorization path.
            raise AppError(
                "authorization is not wired on this deployment",
                code="common.internal",
            )
        missing = sorted(
            permission
            for permission in metadata.required_permissions
            if not authorization.is_allowed(ctx.roles, permission)
        )
        if missing:
            raise ForbiddenError(f"missing permission: {', '.join(missing)}")

    def _metadata_for(self, agent_key: str) -> AgentMetadata | None:
        """This agent's manifest, or ``None`` if the key is unknown here.

        An unknown key is deliberately NOT raised on: ``registry.create`` is
        the one place that owns the unknown-agent 404, and duplicating that
        judgement here would mean two call sites able to disagree about what
        "unknown" means. Returning ``None`` resolves an LLM optimistically and
        lets ``create`` deliver the honest 404 a moment later.
        """
        return next((m for m in self._deps.agents.list() if m.key == agent_key), None)
