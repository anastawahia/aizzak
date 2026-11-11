"""The pre-acceptance refusals of ``POST /conversations/{id}/messages`` (gap th-3, QA-owned).

Fills the two pieces of AC-1.7 the backend suite does not reach, over the real
router, orchestrator and conversations use-cases:

* **409 for a thread deleted between the router's read and the append.** The
  router's own read (404) cannot see the race; the append can, and it must still
  be a problem response -- never an ``accepted`` frame, never a stored question,
  and (with a key) the key must be freed (AC-3.7);
* **403 on the SSE branch with no key.** The permission guard answers before any
  stream starts, whatever the body or the ``Accept`` header says.

Reuses the router suite's app builder so both suites exercise one wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.agents.orchestrator import AgentOrchestrator
from app.api.main import PROBLEM_MEDIA_TYPE
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.clock import utc_now
from app.framework.errors import ConflictError
from tests.unit import test_api_conversations_router as router_suite
from tests.unit.test_api_conversations_router import (
    _KEY,
    _FakeAuth,
    _post,
    _setup,
)

_W1 = router_suite._W1
_U1 = router_suite._U1


@pytest.fixture(autouse=True)
def _reset_runs() -> None:
    router_suite._RUNS.clear()


def _delete_between_read_and_append(
    monkeypatch: pytest.MonkeyPatch, stack: Any, conversation_id: str
) -> None:
    """Soft-delete the thread right after the router's read, before the append:
    ``_admit`` is the last pre-flight step that runs between the two."""
    real_admit = AgentOrchestrator._admit

    async def _admit_then_lose_the_thread(self: AgentOrchestrator, *args: Any, **kw: Any) -> Any:
        reservation = await real_admit(self, *args, **kw)
        stack.repository.rows[conversation_id].deleted_at = utc_now()
        return reservation

    monkeypatch.setattr(AgentOrchestrator, "_admit", _admit_then_lose_the_thread)


@pytest.mark.parametrize("stream", [True, False])
def test_a_thread_deleted_between_the_read_and_the_append_is_a_409_with_no_frame_or_question(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    client, conversation_id, store, stack = _setup()
    _delete_between_read_and_append(monkeypatch, stack, conversation_id)

    response = _post(client, conversation_id, stream=stream, accept_sse=stream)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert "event: accepted" not in response.text
    assert "user_message_id" not in response.text
    assert stack.repository.messages.get(conversation_id, []) == []
    assert router_suite._RUNS == []  # the agent never ran
    assert store.calls == []  # no key, no ledger traffic


@pytest.mark.parametrize("stream", [True, False])
def test_that_409_frees_the_key_so_the_retry_after_the_cause_is_gone_can_run(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    client, conversation_id, store, stack = _setup()
    _delete_between_read_and_append(monkeypatch, stack, conversation_id)

    refused = _post(client, conversation_id, key=_KEY, stream=stream, accept_sse=stream)

    assert refused.status_code == 409
    assert "event: accepted" not in refused.text
    assert store.calls == ["claim", "release"]
    assert store.rows == {}
    assert stack.repository.messages.get(conversation_id, []) == []


@pytest.mark.parametrize(
    ("stream_in_body", "accept_header"), [(True, False), (False, True), (True, True)]
)
def test_a_viewer_is_refused_with_403_before_any_stream_and_without_a_key(
    monkeypatch: pytest.MonkeyPatch, stream_in_body: bool, accept_header: bool
) -> None:
    client, conversation_id, store, stack = _setup()

    async def _as_viewer(self: _FakeAuth, token: str) -> Any:
        return router_suite.Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"viewer"}))

    monkeypatch.setattr(_FakeAuth, "authenticate", _as_viewer)

    response = _post(client, conversation_id, stream=stream_in_body, accept_sse=accept_header)

    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "authz.forbidden"
    assert "event: accepted" not in response.text
    assert stack.repository.messages.get(conversation_id, []) == []
    assert store.calls == []
    assert router_suite._RUNS == []


# -- AC-5.2 / AC-4.4 / AC-5.4: the two paths the router suite reaches only partly ----
def _register_failing_agent(client: Any) -> None:
    """An agent that fails before it says anything (so no partial message exists)."""

    class _Mute(BaseAgent):
        metadata = AgentMetadata(
            key="mute",
            name="Mute",
            version="1.0.0",
            description="fails before the first token",
            capabilities=frozenset(),
            required_permissions=frozenset({"agents:invoke"}),
        )

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            router_suite._RUNS.append("mute")
            raise ConflictError("down before the first token")
            yield  # pragma: no cover

    client.app.state.test_orchestrator._deps.agents.register(_Mute.metadata, _Mute)


def test_a_json_failure_before_any_token_has_the_question_id_and_replays_the_same_problem() -> None:
    client, conversation_id, store, stack = _setup(agent_key="mute")
    _register_failing_agent(client)

    first = _post(client, conversation_id, key=_KEY)
    second = _post(client, conversation_id, key=_KEY)

    (user,) = stack.repository.messages[conversation_id]  # the question, and no answer
    assert user.role.value == "user"
    assert first.status_code == 409
    assert first.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert first.json()["user_message_id"] == user.id
    assert "message_id" not in first.json()
    assert second.status_code == first.status_code
    for member in ("code", "status", "user_message_id", "title"):
        assert second.json()[member] == first.json()[member]
    assert "message_id" not in second.json()
    assert router_suite._RUNS == ["mute"]  # the repeat ran nothing
    assert store.calls == ["claim", "checkpoint", "complete", "claim"]


@pytest.mark.parametrize("stream", [True, False])
async def test_a_reader_who_left_right_after_accepted_gets_agent_failed_for_the_same_question(
    stream: bool,
) -> None:
    client, conversation_id, store, stack = _setup(agent_key="slow")
    orchestrator = client.app.state.test_orchestrator  # type: ignore[attr-defined]
    ctx = router_suite._scope_ctx()
    ledger = await router_suite.claim_turn(
        store,
        ctx,
        conversation_id=conversation_id,
        key=_KEY,
        content=router_suite._THREAD_BODY,
        stale_after_s=router_suite.stale_after_s(router_suite.Settings()),
    )
    assert isinstance(ledger, router_suite.IdempotentTurnLedger)
    events = await orchestrator.invoke(
        ctx,
        "slow",
        router_suite.AgentRequest(conversation_id=conversation_id, input=router_suite._THREAD_BODY),
        ledger=ledger,
    )
    accepted = await events.__anext__()
    await events.aclose()  # gone before any token

    repeat = _post(client, conversation_id, key=_KEY, stream=stream)

    (user,) = stack.repository.messages[conversation_id]  # one question, no partial
    assert accepted.type == "accepted"
    assert accepted.data["user_message_id"] == user.id
    if stream:
        frames = router_suite._frames(repeat.text)
        assert [name for name, _ in frames] == ["accepted", "error"]
        assert frames[0][1]["user_message_id"] == user.id
        assert frames[0][1]["replayed"] is True
        assert frames[1][1]["code"] == "agent.failed"
        assert "message_id" not in frames[1][1]
    else:
        assert repeat.status_code == 502
        assert repeat.json()["code"] == "agent.failed"
        assert repeat.json()["user_message_id"] == user.id
        assert "message_id" not in repeat.json()
    assert store.calls[-1] == "claim"  # the repeat only read
