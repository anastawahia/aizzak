"""RED metrics on every HTTP request - Wave 0 step 0.2 of
``docs/capacity-plan.md``.

**A pure ASGI middleware, not ``@app.middleware("http")``.** The one already
installed (``_install_correlation_middleware``) is Starlette's
``BaseHTTPMiddleware``, which wraps every response in an anyio memory stream
so it can hand a ``Request``/``Response`` pair to user code. That is a real
per-request cost on a platform whose read budget is 150ms, and this layer
exists to MEASURE that budget -- paying a second copy of the same overhead to
do it would be measuring the instrument. The raw form needs neither: it reads
the status off the ``http.response.start`` message as it passes and times the
call, and it never touches the body, so a streamed SSE response flows through
untouched.

**Installed LAST in ``create_app``, which is what makes it OUTERMOST.**
Starlette's ``add_middleware`` does ``user_middleware.insert(0, ...)`` and the
stack is then built by wrapping in reverse, so the last layer added is the
first a request meets. The order matters here and not merely aesthetically:
the duration recorded is what the caller actually waited for, correlation-id
generation and problem rendering included. A layer that measured only what was
inside it would report a budget nobody experiences.

**Route TEMPLATE, never the raw path** (``0.2``'s own cardinality warning).
``GET /api/v1/files/{file_id}`` is one time series; the same handler reached
through ten thousand file ids is still one. A request that matched no route at
all carries the single fixed ``<unmatched>`` label, which is what stops a
scanner walking random URLs from minting a series per guess -- the failure
mode where the monitoring falls over before the thing it monitors.

── How the template is recovered, and why it is not read off ``app.routes`` ──
The first version of this file built an ``id(endpoint) -> path`` map by walking
``app.routes`` once, and on the live stack it labelled EVERY request
``<unmatched>`` -- the metric existed, moved, and said nothing. Two facts about
FastAPI 0.139 that only surfaced by running it:

1. ``app.routes`` is not flat. A router added with ``include_router`` appears
   as a single ``fastapi.routing._IncludedRouter`` node with ``path = None``,
   ``endpoint = None`` and no ``.routes`` of its own; the real ``APIRoute``
   objects live behind a private, version-cached ``effective_candidates()``.
   Every route in this application is registered that way, so the map held
   only FastAPI's four built-in doc routes. The unit tests missed it because
   they registered handlers with the ``@app.get`` decorator, which does still
   land directly in ``app.routes``.
2. The router puts the matched route in the scope as ``scope["route"]``, which
   is authoritative and needs no route table -- but its ``path`` is relative
   to the router that owns it, so an included route reports
   ``/things/{thing_id}`` with the ``/api/v1`` prefix missing. Two routers
   mounted under different prefixes with the same relative path would collapse
   into one series.

So the prefix is recovered rather than assumed: substitute this request's
``path_params`` back into the template to reconstruct exactly the suffix the
router matched, and whatever precedes it in ``scope["path"]`` is the prefix.
That is arithmetic on two values the ASGI scope already carries, with no
private API and nothing to drift; if the reconstruction does not line up (a
sub-application mounted with its own ``root_path``, say) the bare template is
used, which is still bounded and still not a raw path.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.framework.observability.metrics import (
    UNMATCHED_ROUTE,
    http_request_duration_seconds,
    http_requests_total,
)


class RedMetricsMiddleware:
    """Count and time every HTTP request, labelled by route template."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = str(scope.get("method", "UNKNOWN"))
        started = time.perf_counter()
        # 500 rather than 0 as the default: the only way to reach the `finally`
        # without an `http.response.start` is an exception on the way out, and
        # that is what the caller receives from the error middleware above.
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            # Read AFTER the call: the router writes `route` and `path_params`
            # into this same scope dict while handling the request
            # (`starlette.routing`'s `scope.update(child_scope)`), so before
            # the call there is nothing to resolve.
            route = _route_label(scope)
            http_requests_total.labels(route=route, method=method, status=str(status)).inc()
            http_request_duration_seconds.labels(route=route, method=method).observe(elapsed)


def _route_label(scope: Scope) -> str:
    """The full path template of the route that handled this request."""
    route = scope.get("route")
    # `path_format` is the template with Starlette's converters stripped
    # (`/files/{path:path}` -> `/files/{path}`), which is both the more
    # readable label and the one whose `{name}` placeholders match the keys in
    # `path_params`. `path` is the fallback for a route type that has no
    # compiled format.
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not isinstance(template, str) or not template:
        return UNMATCHED_ROUTE
    return _with_router_prefix(template, scope)


def _with_router_prefix(template: str, scope: Scope) -> str:
    """Put back the ``include_router`` prefix that ``scope["route"]`` drops.

    Reconstruct the suffix the router actually matched by substituting this
    request's own path parameters into the template; whatever precedes it in
    the request path is the prefix. Exact rather than heuristic -- the
    reconstruction either is the tail of the path or it is not -- and it can
    only ever return a template or a prefixed template, never a raw path, so
    the cardinality guarantee holds even when the arithmetic does not.
    """
    full = scope.get("path")
    if not isinstance(full, str):
        return template
    matched = template
    params = scope.get("path_params") or {}
    for name, value in params.items():
        matched = matched.replace("{" + name + "}", str(value))
    if matched and full.endswith(matched):
        return full[: len(full) - len(matched)] + template
    return template
