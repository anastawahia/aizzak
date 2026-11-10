"""The frames of a single-agent turn after th-3 (gap th-3, QA-owned).

Pins, over the REAL ``AgentOrchestrator`` and the REAL ``sse_stream`` encoder:

* AC-1.2 -- through the real llm-fallback path the order is
  ``accepted, notice, token+, final`` and ``accepted`` appears once;
* AC-1.3 / NFR-4 -- an agent that never produces an event still lets the client
  see ``accepted`` BEFORE the first ``:keep-alive``;
* AC-1.4 / NFR-5 -- minus the ``accepted`` frame and the one new ``final`` key
  (``user_message_id``), every frame equals what the platform sent before th-3.
  The ``_LEGACY_*`` literals below were captured by running these same agents
  on ``b104878`` (the commit before th-3), not copied from the implementation;
* AC-1.9 / NFR-9 -- ``accepted`` has exactly three keys and no part of the text,
  on the wire too (Arabic, long);
* AC-1.10 -- ``runWorkflow`` by SSE has no ``accepted`` frame and the frames it
  always had.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator
from app.api.v1.sse import encode_frame, sse_stream
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from tests.unit import test_api_workflows_router as workflows_suite
from tests.unit.test_llm_fallback import _DOWN, _ScriptedLLM
from tests.unit.test_llm_fallback import _orchestrator as _fallback_orchestrator
from tests.unit.test_orchestrator import (
    _ctx,
    _FakeThreads,
    _metadata,
    _turn_orchestrator,
)

_ARABIC = "ما هي سعة المضخة في محطة الضخ الرئيسية؟ "
_CITATION = {
    "document_id": "0190aaaa-0000-7000-8000-000000000001",
    "file_name": "دليل.pdf",
    "page": 3,
    "chunk_id": "0190bbbb-0000-7000-8000-000000000001",
    "rank": 1,
}


class _StreamingAgent(BaseAgent):
    metadata = _metadata("streaming", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "مرح"})
        yield AgentEvent(type="token", data={"delta": "با"})
        yield AgentEvent(type="final", data={"text": "مرحبا"})


class _CitingAgent(BaseAgent):
    metadata = _metadata("citing", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "الجواب"})
        yield AgentEvent(type="final", data={"text": "الجواب", "citations": [_CITATION]})


class _SilentAgent(BaseAgent):
    """Produces no event at all, until it is torn down."""

    metadata = _metadata("silent", capabilities=frozenset())

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        await asyncio.Event().wait()
        yield AgentEvent(type="final", data={})  # pragma: no cover - never reached


def _request(text: str = "hi") -> AgentRequest:
    return AgentRequest(conversation_id="conv-1", input={"text": text})


async def _frames(
    orchestrator: AgentOrchestrator, key: str, text: str = "hi"
) -> list[tuple[str, dict[str, Any]]]:
    stream = await orchestrator.invoke(_ctx(), key, _request(text))
    return [(event.type, dict(event.data)) async for event in stream]


def _without_th3(frames: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """What th-3 added, taken out: the ``accepted`` frame and ``final.user_message_id``."""
    kept = [(name, dict(data)) for name, data in frames if name != "accepted"]
    for name, data in kept:
        if name == "final":
            data.pop("user_message_id")
    return kept


# What the platform sent for these agents BEFORE th-3 (captured on b104878).
_LEGACY_STREAMING: list[tuple[str, dict[str, Any]]] = [
    ("token", {"delta": "مرح"}),
    ("token", {"delta": "با"}),
    (
        "final",
        {
            "text": "مرحبا",
            "message_id": "msg-2",
            "content": {"text": "مرحبا", "attachments": []},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
    ),
]
_LEGACY_CITING: list[tuple[str, dict[str, Any]]] = [
    ("token", {"delta": "الجواب"}),
    (
        "final",
        {
            "text": "الجواب",
            "citations": [_CITATION],
            "message_id": "msg-2",
            "content": {"text": "الجواب", "attachments": []},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
    ),
]
_NOTICE = "⚠️ The cloud model could not be reached, so the local model answered this question."
_SWITCH = {
    "from": {"provider": "openai", "model": "gpt-cloud"},
    "to": {"provider": "ollama", "model": "gemma-local"},
}
_LEGACY_FALLBACK: list[tuple[str, dict[str, Any]]] = [
    ("notice", {"kind": "llm_fallback", "detail": _NOTICE, **_SWITCH}),
    ("token", {"delta": "ok"}),
    (
        "final",
        {
            "text": "ok",
            "fallback": _SWITCH,
            "message_id": "msg-2",
            "content": {"text": f"{_NOTICE}\n\nok", "attachments": []},
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    ),
]


# -- AC-1.4 / NFR-5 -----------------------------------------------------------
async def test_a_streamed_turn_is_the_legacy_frames_plus_accepted_and_one_final_key() -> None:
    threads = _FakeThreads(known="conv-1")

    frames = await _frames(_turn_orchestrator(_StreamingAgent, threads), "streaming")

    assert [name for name, _ in frames] == ["accepted", "token", "token", "final"]
    assert frames[0][1] == {"conversation_id": "conv-1", "user_message_id": "msg-1", "seq": 1}
    assert frames[-1][1]["user_message_id"] == "msg-1"
    assert _without_th3(frames) == _LEGACY_STREAMING


async def test_a_citing_turn_is_the_legacy_frames_plus_accepted_and_one_final_key() -> None:
    threads = _FakeThreads(known="conv-1")

    frames = await _frames(_turn_orchestrator(_CitingAgent, threads), "citing")

    assert _without_th3(frames) == _LEGACY_CITING
    # th-1's citations ride the final untouched.
    assert frames[-1][1]["citations"] == [_CITATION]


async def test_a_turn_that_fell_back_to_the_local_model_keeps_its_legacy_frames() -> None:
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", ["ok"])
    orchestrator, _threads, _capture = _fallback_orchestrator(cloud, local)
    stream = await orchestrator.invoke(_ctx(), "chat", _request())

    frames = [(event.type, dict(event.data)) async for event in stream]

    legacy = _without_th3(frames)
    assert legacy == _LEGACY_FALLBACK


# -- AC-1.2 -------------------------------------------------------------------
async def test_a_fallback_turn_orders_accepted_notice_tokens_final_with_one_accepted() -> None:
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", ["a", "b", "c"])
    orchestrator, threads, _capture = _fallback_orchestrator(cloud, local)
    stream = await orchestrator.invoke(_ctx(), "chat", _request())

    names = [event.type async for event in stream]

    assert names[0] == "accepted"
    assert names[1] == "notice"
    assert names[2:-1] == ["token", "token", "token"]
    assert names[-1] == "final"
    assert names.count("accepted") == 1
    assert threads.statuses == [None, "complete"]


async def test_a_turn_that_ends_in_error_after_the_notice_still_has_one_accepted_first() -> None:
    cloud = _ScriptedLLM("openai", fails_first=_DOWN)
    local = _ScriptedLLM("ollama", fails_first=_DOWN)
    orchestrator, _threads, _capture = _fallback_orchestrator(cloud, local)
    stream = await orchestrator.invoke(_ctx(), "chat", _request())

    names = [event.type async for event in stream]

    assert names[0] == "accepted"
    assert names[-1] == "error"
    assert names.count("accepted") == 1


# -- AC-1.3 / NFR-4 -----------------------------------------------------------
async def test_accepted_reaches_the_client_before_the_first_keepalive() -> None:
    threads = _FakeThreads(known="conv-1")
    events = await _turn_orchestrator(_SilentAgent, threads).invoke(_ctx(), "silent", _request())
    body = sse_stream(events, keepalive_interval_s=0.02)
    try:
        first = await asyncio.wait_for(anext(body), timeout=2)
        second = await asyncio.wait_for(anext(body), timeout=2)
    finally:
        await body.aclose()

    assert first.startswith(b"event: accepted\n")
    assert second == b":keep-alive\n\n"
    assert threads.reply_tos == [None]  # the user message only: the turn never answered


# -- AC-1.9 / NFR-9 -----------------------------------------------------------
async def test_accepted_has_exactly_three_keys_and_none_of_a_long_arabic_text() -> None:
    threads = _FakeThreads(known="conv-1")
    text = _ARABIC * 500
    events = await _turn_orchestrator(_StreamingAgent, threads).invoke(
        _ctx(), "streaming", _request(text)
    )

    body = b"".join([chunk async for chunk in sse_stream(events)])

    first_frame, _, rest = body.partition(b"\n\n")
    assert first_frame.startswith(b"event: accepted\ndata: ")
    data = json.loads(first_frame.split(b"data: ", 1)[1])
    assert set(data) == {"conversation_id", "user_message_id", "seq"}
    assert "المضخة" not in first_frame.decode()
    # the text is stored (the threads fake), but never echoed anywhere on the wire
    assert threads.appended[0][2] == text
    assert "المضخة" not in rest.decode()


async def test_the_encoded_accepted_frame_is_utf8_and_one_line() -> None:
    frame = encode_frame(
        "accepted", {"conversation_id": "c", "user_message_id": "m", "seq": 1}
    ).decode()

    assert frame == (
        'event: accepted\ndata: {"conversation_id":"c","user_message_id":"m","seq":1}\n\n'
    )


# -- AC-1.10 ------------------------------------------------------------------
# `final` only: the encoder ends the stream at the first terminal frame (b104878).
_LEGACY_WORKFLOW_FRAMES: list[str] = ["final"]


def test_a_streamed_workflow_run_has_no_accepted_frame() -> None:
    app, _stack = workflows_suite._make_app(
        definitions=(workflows_suite._definition("step_a", "step_b"),)
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": workflows_suite._SPACE, "input": {"text": "hi"}, "stream": True},
            headers=workflows_suite._AUTH,
        )

    assert response.status_code == 200
    names = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert "accepted" not in names
    assert "user_message_id" not in response.text
    assert names == _LEGACY_WORKFLOW_FRAMES


async def test_without_a_conversations_seam_the_frames_are_exactly_the_legacy_ones() -> None:
    """AC-1.8 on the wire: no ``accepted``, no ``user_message_id``, nothing stored,
    so no ``message_id`` / ``usage`` either -- as on b104878."""
    frames = await _frames(_turn_orchestrator(_StreamingAgent, None), "streaming")

    assert frames == [
        ("token", {"delta": "مرح"}),
        ("token", {"delta": "با"}),
        ("final", {"text": "مرحبا"}),
    ]
