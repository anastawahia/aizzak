"""The ``http.request`` line (``app/api/middleware/access_log.py``) --
logging-restructure phase 1, م-3.

Hermetic: a bare FastAPI app with the middleware and a ``BaseHTTPMiddleware``
beneath it, which is the production shape that matters (the correlation
middleware runs the rest of the stack in a child task, so a context variable
set below it never reaches this layer). What these pin:

* the line is written AFTER the response -- every SSE frame included -- and
  its ``duration_ms`` covers the whole body;
* it names the route TEMPLATE, never a raw path with ids in it;
* its level follows the status (INFO / WARNING / ERROR);
* the tenant reaches it through ``scope["state"]``, pseudonymised.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api.middleware.access_log import (
    HTTP_REQUEST_EVENT,
    AccessLogMiddleware,
    bind_request_workspace,
)
from app.framework.observability.metrics import UNMATCHED_ROUTE
from app.framework.observability.pseudonymity import pseudonymous_id
from tests.unit.support_log_lines import json_lines

_WORKSPACE = "018f0000-0000-7000-8000-0000000000w1"
_THING = "018f0000-0000-7000-8000-00000000abcd"
_CHUNKS = 3
_CHUNK_PAUSE_S = 0.02

_probe = logging.getLogger("tests.access_log.probe")


async def _tenant(request: Request) -> None:
    """What ``current_principal`` does for a real request."""
    bind_request_workspace(request, _WORKSPACE)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @app.get("/tenant", dependencies=[Depends(_tenant)])
    async def tenant() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/refused", dependencies=[Depends(_tenant)])
    async def refused() -> Response:
        return Response(status_code=403)

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("boom")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            for _ in range(_CHUNKS):
                _probe.info("probe.chunk")
                await asyncio.sleep(_CHUNK_PAUSE_S)
                yield b"data: x\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    # The correlation middleware's shape: Starlette's `BaseHTTPMiddleware`,
    # which runs everything below it in a CHILD task.
    @app.middleware("http")
    async def child_task_layer(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await call_next(request)

    # Added last, so outermost -- as in `create_app`.
    app.add_middleware(AccessLogMiddleware)
    return app


def _only_request_line(lines: list[dict[str, object]]) -> dict[str, object]:
    matches = [line for line in lines if line["message"] == HTTP_REQUEST_EVENT]
    assert len(matches) == 1, f"one request must write exactly one line, got {matches!r}"
    return matches[0]


def test_a_request_writes_one_line_naming_the_route_template() -> None:
    client = TestClient(_app())
    with json_lines() as captured:
        response = client.get(f"/things/{_THING}")

    line = _only_request_line(captured.lines)
    assert line["level"] == "INFO"
    assert line["method"] == "GET"
    assert line["route"] == "/things/{thing_id}"
    assert line["status"] == 200
    assert line["bytes"] == len(response.content)
    assert isinstance(line["duration_ms"], int)
    assert _THING not in json.dumps(line), "the raw id must never reach the line"


def test_an_unmatched_path_is_one_fixed_route_at_warning() -> None:
    client = TestClient(_app())
    with json_lines() as captured:
        response = client.get("/no/such/place")

    assert response.status_code == 404
    line = _only_request_line(captured.lines)
    assert line["level"] == "WARNING"
    assert line["route"] == UNMATCHED_ROUTE


def test_a_route_that_raises_is_a_500_at_error() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    with json_lines() as captured:
        response = client.get("/boom")

    assert response.status_code == 500
    line = _only_request_line(captured.lines)
    assert line["level"] == "ERROR"
    assert line["status"] == 500


def test_a_streamed_response_is_logged_after_its_last_frame() -> None:
    """The defect this line exists to fix: uvicorn's own access line was
    written when the stream STARTED, above everything the stream then did."""
    client = TestClient(_app())
    with json_lines() as captured:
        client.get("/stream")

    # The test client's own `httpx` line is not the app's and is left out.
    order = [line["message"] for line in captured.lines if line["logger"] != "httpx"]
    assert order == ["probe.chunk"] * _CHUNKS + [HTTP_REQUEST_EVENT]
    line = _only_request_line(captured.lines)
    assert line["duration_ms"] >= _CHUNKS * _CHUNK_PAUSE_S * 1000


def test_the_tenant_crosses_the_child_task_and_is_pseudonymised() -> None:
    client = TestClient(_app())
    with json_lines() as captured:
        client.get("/tenant")

    line = _only_request_line(captured.lines)
    assert line["workspace_id"] == pseudonymous_id(_WORKSPACE)
    assert _WORKSPACE not in json.dumps(line)


def test_a_refusal_names_its_tenant_at_warning() -> None:
    client = TestClient(_app())
    with json_lines() as captured:
        client.get("/refused")

    line = _only_request_line(captured.lines)
    assert line["level"] == "WARNING"
    assert line["status"] == 403
    assert line["workspace_id"] == pseudonymous_id(_WORKSPACE)


def test_an_unauthenticated_request_carries_no_tenant() -> None:
    client = TestClient(_app())
    with json_lines() as captured:
        client.get(f"/things/{_THING}")

    assert "workspace_id" not in _only_request_line(captured.lines)
