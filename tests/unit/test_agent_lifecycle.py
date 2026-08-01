"""Unit tests for ``AgentLifecycleExecutor`` (Phase 4.2 — FR-15, AC-06).

Purely hermetic, no ``live_*`` marker: the lifecycle machine is pure kernel
logic with no service behind it, so it is exercised with hand-built fake agents
(09-testing-strategy §2 places these tests here). Each ``SpyAgent`` records the
order of ``initialize``/``run``/``dispose`` and can be told to fail at a chosen
stage, so the tests can assert BOTH the emitted event stream and the exact
lifecycle transitions.

Focus areas, one per section below:

* the success path (events pass straight through, both authoring styles);
* the failure path (decision B1: a terminal in-band ``error`` event, never
  re-raised) at ``initialize`` and mid-``run``;
* the safe error payload (decision G: ``AppError`` fields through, unexpected
  exceptions generic — no raw message/traceback leaked);
* dispose-always (AC-06), including when ``run`` raises and when ``dispose``
  itself raises (decision E — it must not mask the outcome);
* ``BaseException`` (``CancelledError``/``SystemExit``) propagates, not caught
  as an ``error`` event (decision F), with ``dispose`` still run;
* statelessness — one shared executor drives concurrent requests cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable

import pytest

from app.framework.agent_runtime import (
    AgentDependencies,
    AgentEvent,
    AgentLifecycle,
    AgentLifecycleExecutor,
    AgentMetadata,
    AgentRequest,
    BaseAgent,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7

# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #

_EXECUTOR_LOGGER = "app.framework.agent_runtime.executor"


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def make_metadata(key: str) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=f"{key} agent",
        version="1.0.0",
        description="synthetic test agent",
        capabilities=frozenset({"chat"}),
        required_permissions=frozenset({"agents:invoke"}),
    )


def make_request() -> AgentRequest:
    return AgentRequest(conversation_id=None, input={"text": "hi"})


def token(value: str) -> AgentEvent:
    return AgentEvent(type="token", data={"delta": value})


class SpyAgent(BaseAgent):
    """A configurable fake agent in the canonical async-generator style (11 §3).

    Records call order and can be told to fail at ``initialize`` or ``run``, and
    to have ``dispose`` raise — everything the lifecycle assertions need.
    """

    metadata = make_metadata("spy_agent")

    def __init__(
        self,
        ctx: ExecutionContext,
        deps: AgentDependencies,
        *,
        events: Iterable[AgentEvent] = (),
        fail_at: str | None = None,
        fail_exc: BaseException | None = None,
        dispose_exc: BaseException | None = None,
    ) -> None:
        super().__init__(ctx, deps)
        self.events = tuple(events)
        self.fail_at = fail_at
        self.fail_exc = fail_exc
        self.dispose_exc = dispose_exc
        self.calls: list[str] = []

    async def initialize(self) -> None:
        self.calls.append("initialize")
        if self.fail_at == "initialize":
            raise self.fail_exc or RuntimeError("initialize boom")

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        self.calls.append("run")
        for event in self.events:
            yield event
        if self.fail_at == "run":
            raise self.fail_exc or RuntimeError("run boom")

    async def dispose(self) -> None:
        self.calls.append("dispose")
        if self.dispose_exc is not None:
            raise self.dispose_exc


class DefStyleAgent(BaseAgent):
    """The SECOND legal authoring style: ``def run`` returning an inner async
    generator (the port's ``def stream`` shape). Proves the executor consumes
    both forms via ``async for`` (decision K)."""

    metadata = make_metadata("def_style_agent")

    def __init__(self, ctx: ExecutionContext, deps: AgentDependencies) -> None:
        super().__init__(ctx, deps)
        self.calls: list[str] = []

    async def initialize(self) -> None:
        self.calls.append("initialize")

    def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        self.calls.append("run")

        async def _events() -> AsyncIterator[AgentEvent]:
            yield AgentEvent(type="final", data={"ok": True})

        return _events()

    async def dispose(self) -> None:
        self.calls.append("dispose")


async def drain(agent: BaseAgent, req: AgentRequest | None = None) -> list[AgentEvent]:
    """Drive ``agent`` to completion with a fresh executor and collect events."""
    executor = AgentLifecycleExecutor()
    return [event async for event in executor.drive(agent, req or make_request())]


def lifecycle_seq(caplog: pytest.LogCaptureFixture) -> list[AgentLifecycle]:
    """The ordered lifecycle states the executor logged."""
    return [
        record.lifecycle
        for record in caplog.records
        if record.name == _EXECUTOR_LOGGER and hasattr(record, "lifecycle")
    ]


# --------------------------------------------------------------------------- #
# Success path                                                                #
# --------------------------------------------------------------------------- #


async def test_success_path_streams_events_then_completes_and_disposes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), events=[token("a"), token("b")])
    with caplog.at_level(logging.INFO):
        events = await drain(agent)

    # The agent's own events pass straight through — the executor adds nothing
    # to a successful stream.
    assert events == [token("a"), token("b")]
    assert agent.calls == ["initialize", "run", "dispose"]
    assert lifecycle_seq(caplog) == [
        AgentLifecycle.CREATED,
        AgentLifecycle.INITIALIZED,
        AgentLifecycle.RUNNING,
        AgentLifecycle.COMPLETED,
        AgentLifecycle.DISPOSED,
    ]


async def test_success_emits_no_error_event() -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), events=[token("only")])
    events = await drain(agent)
    assert [e.type for e in events] == ["token"]


async def test_both_authoring_styles_are_driven_identically() -> None:
    ctx, deps = make_ctx(), AgentDependencies()
    def_agent = DefStyleAgent(ctx, deps)
    events = await drain(def_agent)
    assert events == [AgentEvent(type="final", data={"ok": True})]
    assert def_agent.calls == ["initialize", "run", "dispose"]


async def test_no_conversation_id_is_carried_through_unchanged() -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), events=[token("x")])
    req = AgentRequest(conversation_id=new_uuid7(), input={"text": "hi"}, stream=True)
    events = await drain(agent, req)
    assert events == [token("x")]


# --------------------------------------------------------------------------- #
# Failure path (decision B1 — terminal in-band error event, no re-raise)       #
# --------------------------------------------------------------------------- #


async def test_initialize_failure_yields_terminal_error_and_disposes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), fail_at="initialize")
    with caplog.at_level(logging.INFO):
        events = await drain(agent)  # does not raise

    assert [e.type for e in events] == ["error"]
    # run was never reached; dispose still ran (AC-06).
    assert agent.calls == ["initialize", "dispose"]
    # Truthful states: never reached Initialized/Running.
    assert lifecycle_seq(caplog) == [
        AgentLifecycle.CREATED,
        AgentLifecycle.FAILED,
        AgentLifecycle.DISPOSED,
    ]


async def test_mid_stream_run_failure_appends_error_after_streamed_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        events=[token("a"), token("b")],
        fail_at="run",
    )
    with caplog.at_level(logging.INFO):
        events = await drain(agent)

    # Tokens flowed first (200 already sent), then the terminal error event.
    assert [e.type for e in events] == ["token", "token", "error"]
    assert agent.calls == ["initialize", "run", "dispose"]
    assert lifecycle_seq(caplog) == [
        AgentLifecycle.CREATED,
        AgentLifecycle.INITIALIZED,
        AgentLifecycle.RUNNING,
        AgentLifecycle.FAILED,
        AgentLifecycle.DISPOSED,
    ]


async def test_failure_yields_exactly_one_error_event_as_the_terminal_event() -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), events=[token("a")], fail_at="run")
    events = await drain(agent)
    assert sum(1 for e in events if e.type == "error") == 1
    assert events[-1].type == "error"


# --------------------------------------------------------------------------- #
# Safe error payload (decision G)                                             #
# --------------------------------------------------------------------------- #


async def test_app_error_maps_to_its_code_status_and_detail() -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        fail_at="run",
        fail_exc=ValidationError("k must be positive"),
    )
    events = await drain(agent)
    assert events[-1] == AgentEvent(
        type="error",
        data={"code": "common.validation_error", "status": 422, "detail": "k must be positive"},
    )


async def test_custom_app_error_code_and_status_flow_through() -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        fail_at="run",
        fail_exc=AppError("nope", code="usage.quota_exceeded", status=429),
    )
    events = await drain(agent)
    assert events[-1].data == {
        "code": "usage.quota_exceeded",
        "status": 429,
        "detail": "nope",
    }


async def test_unexpected_exception_is_generic_and_never_leaks_the_message() -> None:
    secret = "SENSITIVE-token-abc123 password=hunter2"
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        fail_at="run",
        fail_exc=RuntimeError(secret),
    )
    events = await drain(agent)

    error = events[-1]
    assert error.data == {"code": "common.internal", "status": 500, "detail": "internal error"}
    # The raw exception text is nowhere in what reaches the client stream.
    assert "SENSITIVE" not in str(error.data)
    assert "hunter2" not in str(error.data)


# --------------------------------------------------------------------------- #
# Dispose-always (AC-06) + decision E                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fail_at",
    [None, "initialize", "run"],
    ids=["success", "initialize-fails", "run-fails"],
)
async def test_dispose_runs_exactly_once_on_every_path(fail_at: str | None) -> None:
    agent = SpyAgent(make_ctx(), AgentDependencies(), events=[token("a")], fail_at=fail_at)
    await drain(agent)
    assert agent.calls.count("dispose") == 1


async def test_dispose_failure_is_swallowed_and_does_not_mask_a_successful_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        events=[AgentEvent(type="final", data={"ok": True})],
        dispose_exc=RuntimeError("close failed"),
    )
    with caplog.at_level(logging.INFO):  # INFO so a spurious DISPOSED transition is captured too
        events = await drain(agent)  # must NOT raise

    # The successful final survived; no error event was appended after it.
    assert events == [AgentEvent(type="final", data={"ok": True})]
    assert any(
        r.name == _EXECUTOR_LOGGER and r.msg == "agent_lifecycle.dispose_failed"
        for r in caplog.records
    )
    # A FAILED dispose must not ALSO announce a successful DISPOSED transition:
    # that `_transition` call lives in `_dispose`'s `else:` (only on success).
    # Pins the else branch — without it the log lies about disposal succeeding.
    assert not [
        r
        for r in caplog.records
        if r.name == _EXECUTOR_LOGGER
        and r.msg == "agent_lifecycle.transition"
        and getattr(r, "lifecycle", None) == AgentLifecycle.DISPOSED
    ]


async def test_dispose_failure_does_not_mask_a_run_failure() -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        events=[token("a")],
        fail_at="run",
        fail_exc=RuntimeError("boom"),
        dispose_exc=RuntimeError("close failed"),
    )
    events = await drain(agent)  # neither exception escapes
    # The run failure's error event is preserved; dispose failure is swallowed.
    assert [e.type for e in events] == ["token", "error"]
    assert agent.calls == ["initialize", "run", "dispose"]


# --------------------------------------------------------------------------- #
# BaseException propagates (decision F)                                       #
# --------------------------------------------------------------------------- #


async def test_cancellation_propagates_and_still_disposes() -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        events=[token("a")],
        fail_at="run",
        fail_exc=asyncio.CancelledError(),
    )
    executor = AgentLifecycleExecutor()
    with pytest.raises(asyncio.CancelledError):
        async for _ in executor.drive(agent, make_request()):
            pass
    # `except Exception` did not swallow it, and `finally` still released.
    assert "dispose" in agent.calls
    # No error event manufactured for a BaseException.
    assert agent.calls == ["initialize", "run", "dispose"]


async def test_system_exit_propagates_and_still_disposes() -> None:
    agent = SpyAgent(
        make_ctx(),
        AgentDependencies(),
        fail_at="run",
        fail_exc=SystemExit(1),
    )
    executor = AgentLifecycleExecutor()
    with pytest.raises(SystemExit):
        async for _ in executor.drive(agent, make_request()):
            pass
    assert "dispose" in agent.calls


# --------------------------------------------------------------------------- #
# Stateless — one executor, concurrent drives (decision D)                     #
# --------------------------------------------------------------------------- #


async def test_one_executor_drives_concurrent_requests_without_cross_talk() -> None:
    executor = AgentLifecycleExecutor()
    ctx, deps = make_ctx(), AgentDependencies()
    agent_a = SpyAgent(ctx, deps, events=[token("a1"), token("a2")])
    agent_b = SpyAgent(ctx, deps, events=[token("b1")])

    async def collect(agent: SpyAgent) -> list[str]:
        return [e.data["delta"] async for e in executor.drive(agent, make_request())]

    result_a, result_b = await asyncio.gather(collect(agent_a), collect(agent_b))

    assert result_a == ["a1", "a2"]
    assert result_b == ["b1"]
    assert agent_a.calls == ["initialize", "run", "dispose"]
    assert agent_b.calls == ["initialize", "run", "dispose"]
