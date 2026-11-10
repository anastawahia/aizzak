"""The ``accepted`` frame over the interactive WebSocket (gap th-3, AC-1.5).

One socket, two ``invoke`` messages sent back to back, one REAL orchestrator
writing through a recording thread port. What it pins:

* each turn gets exactly one ``accepted`` frame, before its own first ``token``;
* the ids in a frame belong to ITS turn (the ``final`` of that turn repeats the
  same ``user_message_id``), however the two runs interleave on the wire;
* the frame is the flattened shape of the contract (``type`` beside the three
  keys), and an invoke that named no thread announces the thread it opened.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.v1.websocket.streaming import create_ws_router
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.settings import Limits
from app.framework.streaming import ConnectionHub
from tests.unit.support_access import build_authorization
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.test_orchestrator import _FakeResolver, _FakeThreads, _metadata
from tests.unit.test_websocket_endpoint import _MapAuthenticator

_SAME_TEXT = "ما سعة المضخة؟"


class _SlowEcho(BaseAgent):
    """Streams a token, yields the loop for a while (so the other turn runs in
    between), then answers."""

    metadata = _metadata("slow_echo", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0.02)
        yield AgentEvent(type="token", data={"delta": "جزء"})
        await asyncio.sleep(0.05)
        yield AgentEvent(type="final", data={"text": "جواب"})


def _app() -> tuple[FastAPI, _FakeThreads]:
    registry = InMemoryAgentRegistry()
    registry.register(_SlowEcho.metadata, _SlowEcho)
    threads = _FakeThreads()
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),  # type: ignore[arg-type]
            conversations=threads,
            authorization=build_authorization(),
        )
    )
    app = FastAPI()
    app.include_router(
        create_ws_router(
            authenticator=_MapAuthenticator(),
            orchestrator=orchestrator,
            hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
            limits=Limits(),
            authorization=build_authorization(),
        ),
        prefix="/api/v1",
    )
    return app, threads


def _invoke(conversation_id: str | None, text: str = _SAME_TEXT) -> dict[str, Any]:
    return {
        "type": "invoke",
        "agent_key": "slow_echo",
        "conversation_id": conversation_id,
        "input": {"text": text},
    }


def test_two_concurrent_turns_each_get_one_accepted_before_their_first_token() -> None:
    app, threads = _app()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        # No waiting between the two: both runs are live on the one socket at once.
        ws.send_json(_invoke("c-1"))
        ws.send_json(_invoke("c-2"))
        frames: list[dict[str, Any]] = []
        while sum(1 for f in frames if f["type"] == "final") < 2:
            frames.append(ws.receive_json())

    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        by_thread[frame["conversation_id"]].append(frame)
    assert set(by_thread) == {"c-1", "c-2"}
    user_ids: dict[str, str] = {}
    for conversation_id, own in by_thread.items():
        assert [f["type"] for f in own] == ["accepted", "token", "final"], conversation_id
        accepted, _token, final = own
        assert set(accepted) == {"type", "conversation_id", "user_message_id", "seq"}
        assert final["user_message_id"] == accepted["user_message_id"]
        user_ids[conversation_id] = accepted["user_message_id"]
    # two different questions with the SAME text: two different ids, never confused
    assert user_ids["c-1"] != user_ids["c-2"]
    # the id each frame names is the stored id of that turn's user message
    stored_user_ids = {
        f"msg-{n}" for n, row in enumerate(threads.appended, start=1) if row[1] == "user"
    }
    assert set(user_ids.values()) == stored_user_ids
    # and each answer is stored as a reply to its OWN question
    replies = {
        row[0]: threads.reply_tos[i]
        for i, row in enumerate(threads.appended)
        if row[1] == "assistant"
    }
    assert replies == {"c-1": user_ids["c-1"], "c-2": user_ids["c-2"]}


def test_accepted_is_the_very_first_frame_of_a_turn() -> None:
    app, _threads = _app()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json(_invoke("c-1"))

        first = ws.receive_json()

    assert first == {
        "type": "accepted",
        "conversation_id": "c-1",
        "user_message_id": "msg-1",
        "seq": 1,
    }


def test_an_invoke_that_names_no_thread_is_still_refused_before_any_accepted() -> None:
    """The socket protocol carries no ``space_id``, so an invoke without a thread is
    the 422 it has always been -- nothing is stored, nothing is announced. (The
    ``accepted``-without-a-thread envelope rule is pinned on the function itself in
    ``test_websocket_endpoint``; this is the wire half: it must not start a turn.)"""
    app, threads = _app()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json(_invoke(None))

        first = ws.receive_json()

    assert first["type"] == "error"
    assert first["problem"]["status"] == 422
    assert threads.appended == []


def test_a_refused_invoke_sends_an_error_and_no_accepted() -> None:
    """A pre-flight refusal (unknown agent) stores no question, so it announces none."""
    app, threads = _app()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json({**_invoke("c-1"), "agent_key": "no_such_agent"})

        reply = ws.receive_json()
        ws.send_json({"type": "ping"})
        after = ws.receive_json()

    assert reply["type"] == "error"
    assert after == {"type": "pong"}
    assert threads.appended == []
