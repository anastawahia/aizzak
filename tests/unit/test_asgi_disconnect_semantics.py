"""What the INSTALLED uvicorn and Starlette do when the client hangs up (gap th-3).

design.md 7.1 rests the Idempotency-Key design on three facts about the server
stack. They are facts about third-party code, so they are pinned here against the
real thing -- a real ``uvicorn.Server`` on a loopback port, a raw socket client,
and an app behind an ``@app.middleware("http")`` (as ``correlation_middleware``
is) -- and a dependency upgrade that changes one of them fails THIS file, loudly:

1. a plain JSON handler is NOT cancelled when the client disconnects: the turn
   runs to its end, so its result is recorded and a retry replays it;
2. a ``StreamingResponse`` body IS cancelled (ASGI spec 2.3 ``http.disconnect``
   listening), so its ``finally`` runs inside a cancelled scope;
3. in that unwind a bare ``await`` is cancelled again, while a write run through
   ``idempotent_turn._shielded`` still completes in the background.

Run for both HTTP implementations uvicorn can use (``httptools`` is what the
production image installs).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
from collections.abc import AsyncIterator

import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.api.v1.idempotent_turn import _shielded

_IMPLS = ["h11", "httptools"] if importlib.util.find_spec("httptools") else ["h11"]


class _Probe:
    """What the handlers observed, shared between the server tasks and the test."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.write_release = asyncio.Event()
        self.finished = asyncio.Event()
        self.write_done = asyncio.Event()
        self.bare_write_done = asyncio.Event()
        self.cancelled = False
        self.events: list[str] = []


def _build_app(probe: _Probe) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["x-seen"] = "1"
        return response

    @app.post("/json")
    async def _json() -> JSONResponse:
        probe.entered.set()
        try:
            await probe.release.wait()
        except asyncio.CancelledError:
            probe.cancelled = True
            raise
        probe.events.append("handler-completed")
        probe.finished.set()
        return JSONResponse({"ok": True})

    @app.get("/stream")
    async def _stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            probe.entered.set()
            yield b"event: accepted\ndata: {}\n\n"
            try:
                await probe.release.wait()
                yield b"event: final\ndata: {}\n\n"
            except asyncio.CancelledError:
                probe.cancelled = True
                raise
            finally:
                # What the orchestrator's `finally` does on abandonment: tell the
                # ledger. Once through the shield, once bare, in the same unwind.
                bare = asyncio.ensure_future(_write(probe.bare_write_done, probe))
                try:
                    await bare
                except asyncio.CancelledError:
                    probe.events.append("bare-await-cancelled")
                try:
                    await _shielded(_write(probe.write_done, probe))
                except asyncio.CancelledError:
                    probe.events.append("shielded-await-cancelled")
                probe.finished.set()

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


async def _write(done: asyncio.Event, probe: _Probe) -> None:
    await probe.write_release.wait()
    done.set()


@contextlib.asynccontextmanager
async def _serve(app: FastAPI, impl: str) -> AsyncIterator[int]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", http=impl, lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        yield int(server.servers[0].sockets[0].getsockname()[1])
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, timeout=10)


async def _hang_up_after_entering(port: int, request: bytes, probe: _Probe) -> None:
    """Send ``request`` on a raw socket, wait until the handler is running (and, for
    a stream, until the first bytes arrived), then close the socket abruptly."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    async with asyncio.timeout(5):
        await probe.entered.wait()
        if b"GET /stream" in request:
            await reader.readuntil(b"event: accepted")
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


_JSON_REQUEST = (
    b"POST /json HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\n"
    b"Content-Length: 2\r\n\r\n{}"
)
_STREAM_REQUEST = b"GET /stream HTTP/1.1\r\nHost: t\r\nAccept: text/event-stream\r\n\r\n"


@pytest.mark.parametrize("impl", _IMPLS)
async def test_a_json_handler_is_not_cancelled_when_the_client_hangs_up(impl: str) -> None:
    probe = _Probe()
    async with _serve(_build_app(probe), impl) as port:
        await _hang_up_after_entering(port, _JSON_REQUEST, probe)
        await asyncio.sleep(0.3)  # long enough for uvicorn to notice the lost connection
        assert probe.cancelled is False

        probe.release.set()  # the "turn" finishes after the client has gone
        async with asyncio.timeout(5):
            await probe.finished.wait()

    assert probe.cancelled is False
    assert probe.events == ["handler-completed"]


@pytest.mark.parametrize("impl", _IMPLS)
async def test_a_streaming_body_is_cancelled_when_the_client_hangs_up(impl: str) -> None:
    probe = _Probe()
    async with _serve(_build_app(probe), impl) as port:
        await _hang_up_after_entering(port, _STREAM_REQUEST, probe)
        async with asyncio.timeout(5):
            await probe.finished.wait()

    assert probe.cancelled is True  # the generator was torn down, never released


@pytest.mark.parametrize("impl", _IMPLS)
async def test_a_shielded_write_completes_after_a_disconnect_while_a_bare_await_is_cancelled(
    impl: str,
) -> None:
    probe = _Probe()
    async with _serve(_build_app(probe), impl) as port:
        await _hang_up_after_entering(port, _STREAM_REQUEST, probe)
        async with asyncio.timeout(5):
            await probe.finished.wait()
        # The unwind is over and BOTH writes are still waiting on their store.
        assert not probe.write_done.is_set()
        assert not probe.bare_write_done.is_set()
        # the store answers now, long after the awaiter was cancelled
        probe.write_release.set()
        async with asyncio.timeout(5):
            await probe.write_done.wait()

    assert probe.write_done.is_set()
    # the unwind re-cancels awaits: that is the hazard `_shielded` exists for (the
    # bare one is cancelled; the shielded one's AWAITER is cancelled too, its WRITE is not)
    assert "bare-await-cancelled" in probe.events
    assert "shielded-await-cancelled" in probe.events
