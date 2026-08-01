"""``AgentLifecycleExecutor`` — the FR-15 five-state driver (Phase 4.2).

Drives ONE already-created agent instance through
``Initialized → Running → Completed/Failed → Disposed``, disposing ALWAYS —
even on exception — which is AC-06. It builds strictly ON the 4.1 contract
types (``BaseAgent``/``AgentRequest``/``AgentEvent``/``AgentLifecycle``); it is
a new file that rewrites none of them (the approved 4.1↔4.2 boundary, status
§3.26 decision 1). ``AgentLifecycle`` in 4.1 is values-only — THIS is the
machine that transitions between them (09-testing-strategy §2 places its unit
tests here).

**Scope — the pure driver, nothing more.** The orchestrator (4.7) wraps this
with usage enforcement/capture, ``ProviderResolver`` and SSE/WS transport;
11-agent-authoring-guide §8.2 shows that wrapping and says outright that "the
full lifecycle is not repeated here" — this file is that lifecycle. So the
executor is transport-agnostic and usage-agnostic, and imports only sibling
``agent_runtime`` types + ``errors`` + ``observability`` (framework-kernel
stays 8/0).

**Decision A1 — receives a created instance, ``drive(agent, req)``.** The
"Created" transition and the unknown-key 404 belong to ``AgentRegistry.create``
(architecture Fig 5: "Created: by Registry"), invoked by the orchestrator/API
BEFORE the executor. The executor owns ``Initialized → Disposed`` only, which
is exactly the window where an agent can acquire resources — so the
dispose-always guarantee covers precisely what needs releasing. Keeping the
registry out of here also keeps the driver unit-testable with a hand-built
fake agent and decoupled from the growth of ``AgentDependencies`` (4.7's work).

**Decision B1 — failure surfaces as a terminal in-band ``error`` event, never
re-raised.** Any exception from ``initialize`` or ``run`` yields a final
``AgentEvent(type="error", …)``, transitions to ``Failed``, disposes, and ends
the stream normally. That gives 5.3/4.7 ONE failure shape regardless of *when*
the failure happened — an ``initialize`` failure (mappable to an HTTP status,
nothing sent yet) and a mid-stream ``run`` failure (200 already sent, only an
in-band event can carry it) come out identically. It matches 11 §6 literally
("الفشل ⇒ حالة Failed + حدث خطأ RFC 9457 عبر البثّ").

**Decision G — the error payload is a safe, minimal shape.** For an
``AppError`` it is ``{code, status, detail}`` — the same client-facing fields
the API's RFC 9457 handler would surface (adapters already translate technical
failures into ``AppError``s with safe details and never leak driver types
upward). For any *unexpected* exception it is a generic
``common.internal``/500 — never ``str(exc)`` and never a traceback into the
client stream; the traceback goes only to the log via ``exc_info``. Enriching
to full problem+json (``type`` URI, ``instance``, ``correlation_id``) belongs
to the transport boundary (م6.2/4.7), not here.

**Decision E — a ``dispose`` failure never masks the run outcome.** ``dispose``
runs in ``finally`` inside its own guard: a failure there is logged and
swallowed, so it can neither corrupt a successful ``final`` (already yielded)
nor hide the original failure (its error event was already yielded before
``finally``).

**Decision F — catches ``Exception``, not ``BaseException``.**
``asyncio.CancelledError`` (a ``BaseException`` in py312), ``KeyboardInterrupt``
and ``SystemExit`` propagate — swallowing cancellation would break async
cancellation and 5.3's client-disconnect handling. The ``finally`` still
disposes on cancellation, so resources are released when a client hangs up.

**Decision D — stateless.** No per-instance request state; every drive keeps
its state in coroutine locals, so one shared executor is safe across concurrent
requests (FR-10/11 applied to the driver itself). Its only collaborator is a
module logger.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.lifecycle import AgentLifecycle
from app.framework.errors import AppError
from app.framework.observability import get_logger

_logger = get_logger(__name__)


def _agent_key(agent: BaseAgent) -> str:
    """Best-effort key for logs. Never raises — a driven agent always carries
    ``metadata`` (the loader enforced it), but the logging path must not be the
    thing that crashes the machine, so fall back to the class name."""
    metadata = getattr(type(agent), "metadata", None)
    key = getattr(metadata, "key", None)
    return key if isinstance(key, str) else type(agent).__name__


def _error_event(exc: Exception) -> AgentEvent:
    """Translate a failure into the safe, minimal ``error`` event (decision G).

    An ``AppError`` carries client-facing fields already; anything else is an
    unexpected internal failure and is reported generically — the raw message
    and traceback never reach the client stream.
    """
    if isinstance(exc, AppError):
        data = {"code": exc.code, "status": exc.status, "detail": exc.detail}
    else:
        # 03 §4's entry for exactly this ("خطأ غير متوقّع (يُخفي التفاصيل)").
        # Was `common.error` — a code the catalog never defined, so a client
        # could not switch on the one outcome it most needs to recognise.
        data = {"code": "common.internal", "status": 500, "detail": "internal error"}
    return AgentEvent(type="error", data=data)


class AgentLifecycleExecutor:
    """Drives a created agent through the FR-15 machine, ``dispose`` guaranteed."""

    async def drive(self, agent: BaseAgent, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        """Run one request end to end: ``initialize`` → stream ``run`` →
        ``Completed``, or on any failure a terminal ``error`` event →
        ``Failed``; ``dispose`` always (decisions A1/B1/E, AC-06).

        The agent arrives already created (decision A1); "Created" is logged
        here only as the trace anchor.
        """
        key = _agent_key(agent)
        self._transition(key, AgentLifecycle.CREATED)
        try:
            await agent.initialize()
            self._transition(key, AgentLifecycle.INITIALIZED)
            self._transition(key, AgentLifecycle.RUNNING)
            # `agent.run` returns an AsyncIterator directly (the ABC's `def`
            # form) or an async generator (the guide's `async def … yield`
            # form); `async for` consumes both — never `await agent.run`.
            async for event in agent.run(req):
                yield event
            self._transition(key, AgentLifecycle.COMPLETED)
        except Exception as exc:  # any agent failure → Failed path (B1, F)
            _logger.warning(
                "agent_lifecycle.failed",
                extra={"agent_key": key, "lifecycle": AgentLifecycle.FAILED},
                exc_info=exc,
            )
            yield _error_event(exc)
        finally:
            await self._dispose(agent, key)

    async def _dispose(self, agent: BaseAgent, key: str) -> None:
        """Release resources — the always-run leg (AC-06). A failure here is
        logged and swallowed so it never masks the run outcome (decision E)."""
        try:
            await agent.dispose()
        except Exception as exc:  # dispose must not mask the outcome (E, F)
            _logger.warning(
                "agent_lifecycle.dispose_failed",
                extra={"agent_key": key, "lifecycle": AgentLifecycle.DISPOSED},
                exc_info=exc,
            )
        else:
            self._transition(key, AgentLifecycle.DISPOSED)

    @staticmethod
    def _transition(key: str, state: AgentLifecycle) -> None:
        """Record one lifecycle transition (the loader's event-log style)."""
        _logger.info(
            "agent_lifecycle.transition",
            extra={"agent_key": key, "lifecycle": state},
        )
