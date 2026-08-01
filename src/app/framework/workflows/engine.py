"""Workflow contract types + the ``WorkflowEngine`` port and its concrete
sequential driver (02-port-contracts §3.3, D-04/09/12).

This file carries both halves of the workflow feature. **4.5-a** (the static
half): the three frozen carriers (``WorkflowStep`` / ``WorkflowDefinition`` /
``WorkflowResult``) and the ``WorkflowEngine`` Protocol. **4.5-b** (this
addition, the runtime half): ``SequentialWorkflowEngine``, the concrete driver
that runs a definition's steps in order and chains each step's output into the
next. Shipping the *types* before the *driver* mirrors 4.1 shipping the plugin
types before 4.2's executor drove them; the driver is a pure ADDITION here — it
rewrites nothing in 4.1/4.2 (their ``AgentRegistry``/``AgentLifecycleExecutor``
are consumed as-is).

**Definitions are static, in code (D-09).** A ``WorkflowDefinition`` is a
deliberately dumb, frozen carrier — nothing validates its fields at
construction (the ``AgentMetadata`` precedent, 4.1). The one place a
definition enters the platform is ``WorkflowRegistry.register`` (registry.py),
and that is where every invariant is enforced (≤10 steps, linear, non-empty
``agent_key``, ``input_map`` as ``dict[str, str]`` — 07-nfr-slo "أقصى وكلاء في
Workflow واحد = 10 خطوات"). Parse at the boundary, keep the carrier
unopinionated; the engine below trusts a registry-validated definition (the
executor's "receives a created agent" precedent, 4.2 decision A1).

**Linear only in v1 (D-09, 07-nfr-slo "خطّي v1 بلا حلقات").** ``steps`` is a
flat ``tuple`` — there is no branch/loop structure to express, so linearity is
guaranteed by the type itself.

**``run`` is ``def`` returning ``AsyncIterator``, not ``async def`` — the same
recorded doc-sync deviation as ``LLMProvider.stream`` (02 §1.1) and
``BaseAgent.run`` (02 §3.2).** 02 §3.3 spells it ``async def``, but
``SequentialWorkflowEngine.run`` is an async *generator* (``async def …
yield``), whose method type is ``def run(…) -> AsyncGenerator[AgentEvent,
None]``. Against a ``def`` Protocol returning ``AsyncIterator[AgentEvent]`` that
is a clean structural match (``AsyncGenerator`` ⊑ ``AsyncIterator``); against an
``async def`` Protocol the generator impl would fail the ``mypy --strict``
structural check — the ``_conforms`` proof below pins it. Callers are
unaffected: ``async for event in engine.run(ctx, defn, inp)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.framework.agent_runtime import (
    AgentDependencies,
    AgentEvent,
    AgentLifecycleExecutor,
    AgentRegistry,
    AgentRequest,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.observability import get_logger
from app.framework.types import Json, Uuid

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One agent invocation in a workflow (02 §3.3).

    ``agent_key`` names the agent to run (resolved via ``AgentRegistry`` at run
    time — no direct import, D-05). ``input_map`` is the v1 projection map:
    ``{target_field: context_source_key}`` (both strings; see
    ``SequentialWorkflowEngine._project``). Empty ⇒ the whole accumulated
    context passes through.
    """

    agent_key: str
    input_map: Json


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """A static, code-defined multi-agent pipeline (02 §3.3, D-09).

    Frozen and unvalidated by construction; ``WorkflowRegistry.register`` is
    the validating boundary. ``steps`` is a flat tuple — linear v1, no loops.
    """

    key: str
    name: str
    steps: tuple[WorkflowStep, ...]


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """The accumulated outcome of a workflow run (02 §3.3).

    ``outputs`` collects each step's final output in order; ``conversation_id``
    is the workflow's OWN conversation (D-12). ``WorkflowEngine.run`` streams
    ``AgentEvent``s rather than returning this, so the carrier is assembled by
    the observing orchestrator (4.7) from the ordered per-step ``final`` events
    — defined here now, wired there later (the ``ResolvedProvider`` precedent:
    contract type ahead of its consumer).
    """

    workflow_key: str
    conversation_id: Uuid
    outputs: list[Json]


class AgentDepsProvider(Protocol):
    """Supplies the per-request ``AgentDependencies`` for ONE step's agent
    (4.7-e-1).

    **Why the engine asks per step instead of holding one bundle.** Until
    4.7-e-1 the engine took a single ``AgentDependencies`` in its constructor
    and handed it to every step. That was wrong in two ways that only became
    visible once the orchestrator drove it:

    * **It ignored per-agent provider routing.** ``ProviderResolver`` is keyed
      by capability, and the orchestrator uses the agent's own key as that key
      — so an operator can pin ``rag_agent`` to one model and
      ``data_analysis_agent`` to another (D-16, FR-73: switching providers is
      configuration, not code). One bundle for the whole run silently gave
      every step whichever route the workflow key happened to resolve, quietly
      discarding configuration the operator had written down.
    * **It forced a credential on runs that need none.** ``_resolve_llm``
      skips resolution for an agent whose metadata declares no ``chat``
      capability (the media agents, D-04). Resolving once for the workflow
      throws that away: an image → video pipeline would demand an LLM
      credential for a provider no step ever calls, and fail at the door on a
      deployment that configures none.

    The engine stays dumb — it neither resolves nor knows what a provider is;
    it asks this port for the bundle belonging to the agent it is about to
    create. ``for_agent`` is ``async`` because resolution performs a real
    credential lookup.
    """

    async def for_agent(self, agent_key: str) -> AgentDependencies: ...


class StaticAgentDeps:
    """An ``AgentDepsProvider`` that hands the SAME bundle to every step.

    For callers with nothing to resolve — tests, and any future driver whose
    steps share one bundle by construction. Keeping this here means the
    per-step port costs a simple caller exactly one wrapper, and nobody has to
    hand-roll a fake provider to run a workflow.
    """

    def __init__(self, deps: AgentDependencies) -> None:
        self._deps = deps

    async def for_agent(self, agent_key: str) -> AgentDependencies:
        return self._deps


class WorkflowEngine(Protocol):
    """Runs a ``WorkflowDefinition`` step by step, streaming agent events
    (02 §3.3, D-04).

    Light steps run synchronously in-process; a heavy step's own offload to
    Redis Streams is that agent's concern (Phase 5), not the engine's — the
    engine stays a pure synchronous sequential driver. See ``run``'s ``def``
    form in the module docstring.
    """

    def run(
        self,
        ctx: ExecutionContext,
        definition: WorkflowDefinition,
        initial_input: Json,
    ) -> AsyncIterator[AgentEvent]: ...


def _error_event(exc: Exception) -> AgentEvent:
    """Shape a failure into the safe, minimal terminal ``error`` event.

    Identical shape to the 4.2 executor's error event (its decision G): an
    ``AppError`` surfaces its client-facing ``{code, status, detail}`` (unknown
    agent → 404, bad projection → 422); anything unexpected is a generic 500
    with no raw message/traceback leaked. Kept LOCAL so 4.5 stays purely
    additive over 4.1/4.2 (no edit to ``executor.py``); a future consolidation
    could extract a shared ``agent_runtime.error_event`` (doc-sync note). The
    point of matching the shape is that 4.7/5.3 see ONE failure shape whether a
    step's agent failed (event from the executor) or the engine failed between
    steps (event from here).
    """
    if isinstance(exc, AppError):
        return AgentEvent(
            type="error",
            data={"code": exc.code, "status": exc.status, "detail": exc.detail},
        )
    return AgentEvent(
        type="error",
        data={"code": "common.internal", "status": 500, "detail": "internal error"},
    )


class SequentialWorkflowEngine:
    """Concrete ``WorkflowEngine``: runs a definition's steps in order, chaining
    each step's output into the next (D-04 light-synchronous).

    **Collaborators are injected (manual DI), engine built per request.** It
    holds an ``AgentRegistry`` and an ``AgentLifecycleExecutor`` (both stateless
    platform singletons) and an ``AgentDepsProvider``. ``deps`` is per-request
    but ``run``'s port signature is fixed with no ``deps`` slot, so it rides the
    constructor — the orchestrator (4.7-e) builds one engine per request,
    exactly as it builds one ``BaseAgent`` per request. **4.7-e-1 turned that
    constructor argument from a bundle into a provider**; see
    ``AgentDepsProvider`` for the two bugs a single shared bundle caused.
    There is no per-run state on ``self`` (the running context is a coroutine
    local), so a shared instance is concurrency-safe — the executor's decision
    D applied to the engine.

    **The running context is a blackboard (input_map projection).** The context
    starts as a COPY of ``initial_input`` (never mutating the caller's dict).
    Each step's ``AgentRequest.input`` is projected from the context by
    ``input_map`` (``{target: source_key}``; empty ⇒ whole context passes
    through). After a step completes, its ``final`` output is merged back into
    the context, so the next step reads what earlier steps wrote. This is the
    per-step ``outputs`` list that assembles ``WorkflowResult`` at the
    orchestrator (4.7).

    **Failure model mirrors the executor's B1/G — one failure shape, never
    raised.** A step's own failure arrives as an ``error`` event from
    ``executor.drive`` (which never re-raises); the engine forwards it and then
    HALTS — no later steps run, since the failed step produced no output to
    chain. A failure BETWEEN steps (unknown ``agent_key`` → registry 404, or a
    projection error → 422) is caught here and emitted as the same terminal
    ``error`` event via ``_error_event``, then the workflow halts. As with the
    executor (decision F), only ``Exception`` is caught — ``CancelledError``
    propagates.

    **Conversation/result are the orchestrator's (D-12, 4.7).** Steps run with
    ``conversation_id=None`` here; the workflow's OWN conversation (D-12) and
    the ``WorkflowResult`` assembly belong to 4.7, which observes this stream.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        executor: AgentLifecycleExecutor,
        deps: AgentDepsProvider,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._deps = deps

    async def run(
        self,
        ctx: ExecutionContext,
        definition: WorkflowDefinition,
        initial_input: Json,
    ) -> AsyncIterator[AgentEvent]:
        """Drive ``definition``'s steps in order, streaming every agent event;
        chain outputs via ``input_map``; halt on the first failure."""
        # Copy so the caller's dict is never mutated by the blackboard merges.
        context: Json = dict(initial_input)
        _logger.info(
            "workflow.started",
            extra={"workflow_key": definition.key, "steps": len(definition.steps)},
        )
        for index, step in enumerate(definition.steps):
            # Projection + dependency resolution + agent creation are the
            # between-steps failure window (bad input_map source → 422; a
            # missing credential → whatever the resolver raises; unknown
            # agent_key → 404). Convert to the one terminal error shape and
            # halt — never raise (B1/G/F). Resolution sits INSIDE the try for
            # that reason: it performs real I/O, and a step that cannot be
            # given its provider must fail like any other step, not detonate
            # out of the generator past the caller's error handling.
            try:
                step_input = self._project(step.input_map, context)
                step_deps = await self._deps.for_agent(step.agent_key)
                agent = self._registry.create(step.agent_key, ctx, step_deps)
            except Exception as exc:  # engine-side failure → terminal error, halt
                _logger.warning(
                    "workflow.halted",
                    extra={"workflow_key": definition.key, "step": index},
                    exc_info=exc,
                )
                yield _error_event(exc)
                return
            req = AgentRequest(conversation_id=None, input=step_input)
            # `drive` never raises (it converts agent failures to `error`
            # events and disposes); a mid-stream `error` means this step failed.
            step_output: Json | None = None
            failed = False
            async for event in self._executor.drive(agent, req):
                yield event
                if event.type == "error":
                    failed = True
                elif event.type == "final":
                    step_output = event.data
            if failed:
                _logger.warning(
                    "workflow.halted",
                    extra={"workflow_key": definition.key, "step": index},
                )
                return
            # Merge this step's output onto the blackboard for the next step.
            if step_output is not None:
                context.update(step_output)
        _logger.info("workflow.completed", extra={"workflow_key": definition.key})

    @staticmethod
    def _project(input_map: Json, context: Json) -> Json:
        """Build a step's input Json from the running context (v1 semantics).

        Empty ``input_map`` ⇒ the whole context passes through (a copy).
        Otherwise each ``{target: source_key}`` copies ``context[source_key]``
        under ``target``. A ``source_key`` absent from the context is a runtime
        referential error (a prior step did not produce it) → ``ValidationError``
        (422), caught by ``run`` and surfaced as a terminal ``error`` event.
        Register already guarantees the STRUCTURE (``dict[str, str]``); the
        isinstance guard here also holds for a hand-built (unregistered)
        definition.
        """
        if not input_map:
            return dict(context)
        projected: Json = {}
        for field, source in input_map.items():
            if not isinstance(source, str):
                raise ValidationError(
                    f"input_map source for {field!r} must be a context key string "
                    f"(got {type(source).__name__})"
                )
            if source not in context:
                raise ValidationError(
                    f"input_map source {source!r} for {field!r} is not in the workflow context"
                )
            projected[field] = context[source]
        return projected


if TYPE_CHECKING:

    def _conforms(engine: SequentialWorkflowEngine) -> WorkflowEngine:
        """mypy-gate structural proof that the concrete engine satisfies the
        §3.3 port — and, crucially, that its ``async def … yield`` ``run``
        (called type ``AsyncGenerator[AgentEvent, None]``) matches the ``def
        run -> AsyncIterator[AgentEvent]`` Protocol (the ``BaseAgent`` /
        registries precedent). Flip the Protocol to ``async def`` and this
        goes red."""
        return engine
