"""Unit + ASGI tests for the SSE encoder (``app/api/v1/sse.py``, 5.3-ب).

Hermetic throughout. The generator-level tests pin the 03 §3.1 wire grammar
(frame bytes, UTF-8, keep-alive comments, termination after ``final``/
``error``, the B1→problem translation) and the transport-boundary duty of
closing the producer. The ASGI test then proves the same encoder over a real
HTTP response — headers included — through the exact surface (a Starlette
``StreamingResponse``) the Phase-6 routers will mount it on, so AC-11's
"Streaming Responses" half is demonstrated end-to-end at the protocol level
without waiting for 6.1.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.api.v1.sse import (
    KEEPALIVE_INTERVAL_S,
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    encode_frame,
    sse_stream,
)
from app.framework.agent_runtime.base_agent import AgentEvent

_CORRELATION = "018f0000-0000-7000-8000-0000000000cc"


async def _events(*items: AgentEvent) -> AsyncIterator[AgentEvent]:
    for item in items:
        yield item


async def _drain(frames: AsyncIterator[bytes]) -> list[bytes]:
    return [frame async for frame in frames]


def _parse(frames: list[bytes]) -> list[tuple[str, dict[str, object]]]:
    """Decode non-comment frames back into (event, data) pairs."""
    parsed = []
    for frame in frames:
        text = frame.decode()
        if text.startswith(":"):
            continue
        event_line, data_line, _ = text.split("\n", 2)
        parsed.append(
            (
                event_line.removeprefix("event: "),
                json.loads(data_line.removeprefix("data: ")),
            )
        )
    return parsed


# --------------------------------------------------------------------------- #
# Frame grammar                                                               #
# --------------------------------------------------------------------------- #
def test_encode_frame_is_byte_exact() -> None:
    frame = encode_frame("token", {"delta": "hi"})

    assert frame == b'event: token\ndata: {"delta":"hi"}\n\n'


def test_utf8_rides_the_wire_unescaped() -> None:
    """The contract fixes UTF-8 encoding — Arabic deltas travel as
    themselves, not as ``\\uXXXX`` escapes."""
    frame = encode_frame("token", {"delta": "مرحب"})

    assert "مرحب".encode() in frame
    assert b"\\u" not in frame


def test_default_keepalive_matches_the_contract() -> None:
    assert KEEPALIVE_INTERVAL_S == 15.0
    assert SSE_MEDIA_TYPE.startswith("text/event-stream")
    assert SSE_HEADERS["Cache-Control"] == "no-cache"


# --------------------------------------------------------------------------- #
# Stream semantics                                                            #
# --------------------------------------------------------------------------- #
async def test_a_full_stream_encodes_in_order_and_ends_after_final() -> None:
    frames = await _drain(
        sse_stream(
            _events(
                AgentEvent(type="token", data={"delta": "مرحب"}),
                AgentEvent(type="tool_call", data={"tool": "rag_search", "args": {}}),
                AgentEvent(type="final", data={"message_id": "018f", "content": {}}),
            )
        )
    )

    assert _parse(frames) == [
        ("token", {"delta": "مرحب"}),
        ("tool_call", {"tool": "rag_search", "args": {}}),
        ("final", {"message_id": "018f", "content": {}}),
    ]


async def test_nothing_is_emitted_after_the_terminal_event() -> None:
    """The encoder enforces the termination clause itself: a buggy producer
    yielding past its ``final`` never reaches the wire — and it is CLOSED,
    not abandoned."""
    closed = False

    async def _buggy() -> AsyncIterator[AgentEvent]:
        nonlocal closed
        try:
            yield AgentEvent(type="final", data={})
            yield AgentEvent(type="token", data={"delta": "ghost"})
        finally:
            closed = True

    frames = await _drain(sse_stream(_buggy()))

    assert [name for name, _ in _parse(frames)] == ["final"]
    assert closed


async def test_the_error_event_reaches_the_wire_as_a_problem_object() -> None:
    """B1's ``{code, status, detail}`` is a layer dialect; 03 §3.1 shows the
    client receives RFC 9457 — with the correlation id the router passed."""
    frames = await _drain(
        sse_stream(
            _events(
                AgentEvent(type="token", data={"delta": "x"}),
                AgentEvent(
                    type="error",
                    data={"code": "agent.failed", "status": 502, "detail": "boom"},
                ),
            ),
            correlation_id=_CORRELATION,
        )
    )

    parsed = _parse(frames)
    assert [name for name, _ in parsed] == ["token", "error"]
    assert parsed[-1][1] == {
        "type": "https://errors.platform/agent.failed",
        "title": "Agent execution failed",
        "status": 502,
        "code": "agent.failed",
        "detail": "boom",
        "correlation_id": _CORRELATION,
    }


async def test_idle_gaps_are_bridged_by_keepalive_comments() -> None:
    async def _slow() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "a"})
        await asyncio.sleep(0.12)
        yield AgentEvent(type="final", data={})

    frames = await _drain(sse_stream(_slow(), keepalive_interval_s=0.05))

    keepalives = [f for f in frames if f == b":keep-alive\n\n"]
    assert keepalives, "an idle gap longer than the interval must heartbeat"
    # Grammar: comment frames start with ':' so EventSource clients ignore
    # them; and the data frames still arrive intact around them.
    assert [name for name, _ in _parse(frames)] == ["token", "final"]


async def test_a_producer_ending_without_a_terminal_event_just_ends() -> None:
    """No synthetic ``final`` is invented for an outcome the run never
    reported (not our executor's shape — it guarantees a terminal event)."""
    frames = await _drain(sse_stream(_events(AgentEvent(type="token", data={"delta": "x"}))))

    assert [name for name, _ in _parse(frames)] == ["token"]


async def test_client_disconnect_cascades_the_close_into_the_producer() -> None:
    """Starlette acloses the body iterator on disconnect; that close must
    reach the producer NOW (its `finally` chain bills/disposes) — the
    4.7-c-2 lesson at the transport boundary."""
    closed = False

    async def _producer() -> AsyncIterator[AgentEvent]:
        nonlocal closed
        try:
            yield AgentEvent(type="token", data={"delta": "a"})
            yield AgentEvent(type="final", data={})
        finally:
            closed = True

    stream = sse_stream(_producer())
    assert (await stream.__anext__()).startswith(b"event: token")
    await stream.aclose()

    assert closed


# --------------------------------------------------------------------------- #
# Over ASGI — the surface Phase 6 mounts                                      #
# --------------------------------------------------------------------------- #
async def test_the_encoder_serves_a_real_sse_response_over_asgi() -> None:
    app = FastAPI()

    @app.post("/invoke")
    async def invoke() -> StreamingResponse:
        events = _events(
            AgentEvent(type="token", data={"delta": "مرحب"}),
            AgentEvent(type="final", data={"message_id": "018f"}),
        )
        return StreamingResponse(
            sse_stream(events, correlation_id=_CORRELATION),
            media_type=SSE_MEDIA_TYPE,
            headers=SSE_HEADERS,
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/invoke")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    body = response.content.decode()
    assert 'event: token\ndata: {"delta":"مرحب"}\n\n' in body
    assert 'event: final\ndata: {"message_id":"018f"}\n\n' in body
    assert body.index("event: token") < body.index("event: final")


async def test_asgi_pre_flight_failures_stay_http_errors_not_streams() -> None:
    """The other half of the orchestrator's pre-flight/in-flight contract,
    seen from the transport: a handler that raises BEFORE returning the
    response yields a real HTTP error status, not a 200 with a broken body.
    (FastAPI's default 500 here; 6.2 will shape the problem+json body.)"""
    app = FastAPI()

    @app.post("/invoke")
    async def invoke() -> StreamingResponse:
        raise RuntimeError("pre-flight failure")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/invoke")

    assert response.status_code == 500
    assert not response.headers["content-type"].startswith("text/event-stream")


async def test_keepalive_timer_never_cancels_the_consumers_own_await() -> None:
    """The 5.3-أ lesson holds here too: the timeout wraps ONE pull, so a
    consumer that dawdles between pulls (longer than the interval) is never
    cancelled by the encoder's heartbeat timer."""
    frames = sse_stream(
        _events(
            AgentEvent(type="token", data={"delta": "a"}),
            AgentEvent(type="final", data={}),
        ),
        keepalive_interval_s=0.03,
    )

    first = await frames.__anext__()
    await asyncio.sleep(0.1)  # dawdle past several intervals — must be safe
    rest = await _drain(frames)

    assert first.startswith(b"event: token")
    assert [name for name, _ in _parse(rest)] == ["final"]


async def test_disconnect_with_a_pull_in_flight_still_reaps_the_producer() -> None:
    """Disconnect can land while the producer is mid-await (the pull task is
    live). The encoder must cancel and REAP that task before closing the
    generator — `aclose()` on a generator whose `__anext__` is running is a
    RuntimeError, and an unreaped task would die at GC, not now."""
    closed = False

    async def _producer() -> AsyncIterator[AgentEvent]:
        nonlocal closed
        try:
            yield AgentEvent(type="token", data={"delta": "a"})
            await asyncio.sleep(30)  # disconnect arrives during this await
            yield AgentEvent(type="final", data={})
        finally:
            closed = True

    stream = sse_stream(_producer(), keepalive_interval_s=0.02)
    assert (await stream.__anext__()).startswith(b"event: token")
    # Pull the NEXT frame just far enough to observe a heartbeat, proving the
    # pull task is genuinely in flight, then disconnect.
    assert await stream.__anext__() == b":keep-alive\n\n"
    await stream.aclose()

    assert closed
