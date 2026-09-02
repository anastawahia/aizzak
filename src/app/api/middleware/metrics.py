"""RED metrics on every HTTP request — Wave 0 step 0.2 of
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

**Installed OUTERMOST, before the correlation middleware**, so the duration it
records is what the caller actually waited for -- correlation-id generation
included. The cost is that ``app.routes`` is not populated yet when this is
constructed (``create_app`` installs middleware before it includes routers),
which is why the route-template map below is built lazily on first request
rather than in ``__init__``.

**Route TEMPLATE, never the raw path** (``0.2``'s own cardinality warning).
``GET /api/v1/files/{id}`` is one time series; the same handler reached
through ten thousand file ids is still one. A request that matched no route
at all carries the single fixed ``<unmatched>`` label, which is what stops a
scanner walking random URLs from minting a series per guess -- the failure
mode where the monitoring falls over before the thing it monitors.
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

    def __init__(self, app: ASGIApp, *, routed: object) -> None:
        self._app = app
        # The FastAPI instance, held only to read `.routes` once, lazily. Typed
        # as `object` so this module needs no import of the app it wraps.
        self._routed = routed
        self._templates: dict[int, str] | None = None

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
            # Read AFTER the call: the router writes `endpoint` into this same
            # scope dict while handling the request (`starlette.routing`'s
            # `scope.update(child_scope)`), so before the call there is nothing
            # to resolve.
            route = self._route_label(scope)
            http_requests_total.labels(route=route, method=method, status=str(status)).inc()
            http_request_duration_seconds.labels(route=route, method=method).observe(elapsed)

    def _route_label(self, scope: Scope) -> str:
        endpoint = scope.get("endpoint")
        if endpoint is None:
            return UNMATCHED_ROUTE
        return self._template_map().get(id(endpoint), UNMATCHED_ROUTE)

    def _template_map(self) -> dict[int, str]:
        """``id(endpoint) -> path template``, built once on first request.

        Keyed by ``id`` rather than by the function itself because an endpoint
        need not be hashable -- a callable class instance is a legitimate
        Starlette endpoint -- and because the objects are app-lifetime
        singletons held by the route table, so their ids are stable for as
        long as this map is.
        """
        if self._templates is None:
            built: dict[int, str] = {}
            for route in getattr(self._routed, "routes", ()):
                endpoint = getattr(route, "endpoint", None)
                path = getattr(route, "path", None)
                if endpoint is not None and isinstance(path, str):
                    built[id(endpoint)] = path
            self._templates = built
        return self._templates
