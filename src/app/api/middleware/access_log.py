"""One ``http.request`` line per HTTP request -- the line that CLOSES it
(``docs/delivery/logging-restructure/plan.md`` phase 1, م-3).

**What it replaces.** Until this layer the only per-request line was
uvicorn's ``uvicorn.access``: ``"POST /api/v1/conversations/<id>/messages
HTTP/1.1" 200`` with no duration, the raw path instead of the route, and no
tenant. For a streamed answer it was worse than thin: uvicorn writes it when
the response STARTS, so the ``200`` landed above the agent's own lines and
said nothing about how the turn ended. ``configure_logging`` now silences that
logger and this line takes its place.

**Written after the last byte, by construction.** A pure ASGI layer (the same
shape as ``RedMetricsMiddleware``, and for its reason: no anyio memory stream
around every body) whose ``await`` on the app returns only once the whole
response -- every SSE frame included -- has gone out. So the line is the LAST
one a request writes, and ``duration_ms`` is what the caller waited for.

**Fields.** ``method``, ``route`` (the template, ``…/{conversation_id}/
messages``, via ``route_label`` -- never the raw path, which would put ids and
nothing else of use on every line; the edge's own line has the full path under
the same ``request_id``), ``status``, ``duration_ms`` and ``bytes``. The level
follows the status: a 4xx is the caller's degraded outcome (``WARNING``), a
5xx the platform's failure (``ERROR``).

**Why the tenant comes from ``scope["state"]`` and not the context variable.**
This layer runs OUTSIDE the task the route runs in: the correlation
middleware is Starlette's ``BaseHTTPMiddleware``, which starts the rest of the
stack in a child task, so a ``ContextVar.set`` made by a dependency below it is
invisible up here. The scope's ``state`` dict, on the other hand, is one object
shared by every layer of this request. ``current_principal`` writes the tenant
into it through ``bind_request_workspace``; this layer reads it back. The
formatter pseudonymises it on the way out like every other ``workspace_id``.

**No ``user_id``**, deliberately: ``observability/pseudonymity.py`` keeps raw
user ids to a handful of operator-facing lines, and a field on every request
would turn the log store into a per-user activity index.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.middleware.metrics import route_label
from app.framework.observability import get_logger

_logger = get_logger(__name__)

# The event name, and the `scope["state"]` key the tenant travels under. The
# key is namespaced so it cannot collide with anything a route puts on
# `request.state` for its own use.
HTTP_REQUEST_EVENT = "http.request"
_WORKSPACE_STATE_KEY = "log_workspace_id"

_CLIENT_ERROR_STATUS = 400
_SERVER_ERROR_STATUS = 500


def bind_request_workspace(connection: HTTPConnection, workspace_id: str) -> None:
    """Record the request's tenant where the ``http.request`` line can read
    it. Called by ``current_principal`` -- the tenant is the PRINCIPAL's,
    never anything the client sent."""
    connection.scope.setdefault("state", {})[_WORKSPACE_STATE_KEY] = workspace_id


class AccessLogMiddleware:
    """Write one ``http.request`` line once each HTTP response has finished."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Created HERE if the server did not, so this layer and the dependency
        # far below it hold the same dict -- see the module docstring.
        state: dict[str, Any] = scope.setdefault("state", {})
        started = time.perf_counter()
        # 500 for the reason `RedMetricsMiddleware` gives: reaching the
        # `finally` without a response start means an exception on the way out.
        status = _SERVER_ERROR_STATUS
        sent = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            fields: dict[str, Any] = {
                "method": str(scope.get("method", "UNKNOWN")),
                # Read after the call: the router fills in `route` as it runs.
                "route": route_label(scope),
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "bytes": sent,
            }
            workspace_id = state.get(_WORKSPACE_STATE_KEY)
            if workspace_id is not None:
                fields["workspace_id"] = workspace_id
            _logger.log(_level_for(status), HTTP_REQUEST_EVENT, extra=fields)


def _level_for(status: int) -> int:
    """The log level a response status deserves (10-code-standards §10)."""
    if status >= _SERVER_ERROR_STATUS:
        return logging.ERROR
    if status >= _CLIENT_ERROR_STATUS:
        return logging.WARNING
    return logging.INFO
