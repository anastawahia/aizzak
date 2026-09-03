"""The per-process burst guard — ``api/middleware/inflight.py`` (plan 1.2).

Driven as raw ASGI rather than through ``TestClient``, for one reason: the
whole behaviour under test is what happens while another request is STILL
being served, and ``TestClient`` serves one request at a time by construction.
A blocking app plus two concurrent tasks is the saturation this layer exists
for, expressed exactly.

What is asserted here is what a caller and an operator actually get: a 429 in
the RFC 9457 shape with a ``Retry-After``, a correlation id on a request that
never reached the middleware that mints them, a slot that comes back even when
the request it belonged to blew up, and the two exemptions (health/metrics,
WebSockets) without which this guard would cause the outage it prevents.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.api.errors import CORRELATION_HEADER, PROBLEM_MEDIA_TYPE
from app.api.middleware.inflight import EXEMPT_PATHS, InFlightLimitMiddleware

_PATH = "/api/v1/agents"


def _scope(path: str = _PATH, *, kind: str = "http", headers: Any = None) -> dict[str, Any]:
    return {
        "type": kind,
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
    }


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


class _Sent:
    """Collects the ASGI messages a response emitted."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        return int(self.messages[0]["status"])

    @property
    def headers(self) -> dict[str, str]:
        return {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in self.messages[0]["headers"]
        }

    @property
    def body(self) -> dict[str, Any]:
        raw = b"".join(m.get("body", b"") for m in self.messages[1:])
        parsed: dict[str, Any] = json.loads(raw)
        return parsed


class _BlockingApp:
    """An inner app that parks until released — one request held in flight."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class _OkApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _saturate(middleware: InFlightLimitMiddleware, inner: _BlockingApp) -> asyncio.Task[None]:
    """Start one request and wait until it is genuinely inside the app."""
    held = asyncio.create_task(middleware(_scope(), _receive, _Sent()))
    await asyncio.wait_for(inner.entered.wait(), timeout=1)
    return held


# --------------------------------------------------------------------------- #
# Under the ceiling                                                           #
# --------------------------------------------------------------------------- #
async def test_a_request_below_the_ceiling_passes_straight_through() -> None:
    inner = _OkApp()
    sent = _Sent()

    await InFlightLimitMiddleware(inner, max_in_flight=2)(_scope(), _receive, sent)

    assert inner.calls == 1
    assert sent.status == 200


async def test_the_slot_is_returned_when_the_request_finishes() -> None:
    """Otherwise the ceiling shrinks by one per request — the failure that
    looks like a slow leak and is really a closing door."""
    inner = _OkApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)

    for _ in range(5):
        await middleware(_scope(), _receive, _Sent())

    assert inner.calls == 5
    assert middleware.in_flight == 0


async def test_the_slot_is_returned_even_when_the_request_raises() -> None:
    """A handler that blows up still has to give its slot back: a platform
    that leaked one per 500 would refuse everything shortly after an
    incident."""

    async def _explodes(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    middleware = InFlightLimitMiddleware(_explodes, max_in_flight=1)

    with pytest.raises(RuntimeError):
        await middleware(_scope(), _receive, _Sent())

    assert middleware.in_flight == 0


# --------------------------------------------------------------------------- #
# At the ceiling                                                              #
# --------------------------------------------------------------------------- #
async def test_a_full_process_refuses_immediately_rather_than_queueing() -> None:
    """The plan's acceptance wording ("answers 429 when full"), and the reason
    this is a counter and not an ``asyncio.Semaphore``: a semaphore's
    ``acquire`` would WAIT here, converting a refusal the client can retry
    into latency nobody budgeted."""
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    sent = _Sent()

    # No timeout and no gather: if this queued rather than refused, the await
    # itself would never return and the test would hang -- which is the
    # failure being ruled out.
    await middleware(_scope(), _receive, sent)

    assert sent.status == 429
    assert inner.calls == 1
    inner.release.set()
    await held


async def test_the_refusal_is_an_rfc_9457_problem_with_a_retry_after() -> None:
    """Rendered by hand, because this layer sits OUTSIDE the exception
    handlers — so the contract has to be met here or not at all."""
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    sent = _Sent()

    await middleware(_scope(), _receive, sent)

    assert sent.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert sent.headers["retry-after"] == "1"
    body = sent.body
    assert body["code"] == "common.rate_limited"
    assert body["status"] == 429
    assert body["type"].endswith("common.rate_limited")
    assert body["instance"] == _PATH
    inner.release.set()
    await held


async def test_a_refused_request_still_carries_a_correlation_id() -> None:
    """The middleware that mints them is INSIDE this one and was never
    entered, so a refusal would otherwise be the one response an operator
    cannot trace."""
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    sent = _Sent()

    await middleware(_scope(), _receive, sent)

    assert sent.headers[CORRELATION_HEADER.lower()]
    assert sent.body["correlation_id"] == sent.headers[CORRELATION_HEADER.lower()]
    inner.release.set()
    await held


async def test_a_client_supplied_correlation_id_is_echoed_on_the_refusal() -> None:
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    sent = _Sent()
    supplied = "018f0000-0000-7000-8000-00000000000c"

    await middleware(
        _scope(headers=[(CORRELATION_HEADER.encode(), supplied.encode())]), _receive, sent
    )

    assert sent.headers[CORRELATION_HEADER.lower()] == supplied
    inner.release.set()
    await held


async def test_capacity_returns_as_soon_as_the_held_request_completes() -> None:
    """A refusal must be a moment, not a state: the guard sheds a burst and
    then gets out of the way."""
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    refused = _Sent()
    await middleware(_scope(), _receive, refused)
    assert refused.status == 429

    inner.release.set()
    await held
    inner.release.clear()
    inner.entered.clear()
    admitted = asyncio.create_task(middleware(_scope(), _receive, _Sent()))
    await asyncio.wait_for(inner.entered.wait(), timeout=1)

    assert inner.calls == 2
    inner.release.set()
    await admitted


# --------------------------------------------------------------------------- #
# The exemptions, without which this guard causes the outage it prevents      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", sorted(EXEMPT_PATHS))
async def test_health_and_metrics_answer_even_when_the_process_is_full(path: str) -> None:
    """Refusing a readiness probe is how an orchestrator concludes a busy
    replica is dead and restarts it — a load spike turned into a rolling
    outage. Refusing the scrape blinds the operator at the same moment."""
    inner = _BlockingApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)
    held = await _saturate(middleware, inner)
    inner.entered.clear()
    sent = _Sent()

    probe = asyncio.create_task(middleware(_scope(path), _receive, sent))
    await asyncio.wait_for(inner.entered.wait(), timeout=1)

    assert inner.calls == 2
    inner.release.set()
    await asyncio.gather(held, probe)
    assert sent.status == 200


async def test_a_websocket_connection_is_never_counted() -> None:
    """A socket is in flight for as long as the tab is open, so counting them
    here would exhaust the budget with the first few dozen users and then
    refuse every HTTP request forever. The socket ceiling is
    ``ws_connections_per_user``, held elsewhere."""
    inner = _OkApp()
    middleware = InFlightLimitMiddleware(inner, max_in_flight=1)

    for _ in range(3):
        await middleware(_scope(kind="websocket"), _receive, _Sent())

    assert inner.calls == 3
    assert middleware.in_flight == 0
