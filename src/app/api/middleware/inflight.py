"""The per-process in-flight ceiling — capacity-plan step 1.2's third guard.

The two Redis buckets next door (``rate_limit.py``) count requests PER MINUTE.
Nothing in a per-minute window stops four hundred requests arriving in the
same instant: every one of them is inside every ceiling, and the platform
accepts all four hundred, opens four hundred database sessions' worth of work
and discovers the problem as a timeout. `§1`'s own measurement is the reason
this is not hypothetical — throughput peaks at concurrency **4** and falls to
162 rps by 32, so past a point admitting more work makes the platform finish
LESS of it. This layer is what refuses instantly instead of queueing.

**A counter, not an ``asyncio.Semaphore``, and that is a deliberate departure
from the plan's wording.** A semaphore's ``acquire`` WAITS — which is the one
behaviour 1.2's acceptance criterion rules out ("يُجيب 429 عند الامتلاء"):
queueing behind a saturated process converts a fast refusal the client can
retry into an unbounded latency nobody budgeted. Checking ``locked()`` first
does not fix it either, because the ``await`` between that check and the
acquire is exactly the window a sibling task takes the last slot in. A plain
integer needs no such window: the check and the increment below sit in ONE
synchronous stretch with no ``await`` between them, so cooperative scheduling
cannot interleave — the ``ConnectionHub.try_register`` argument, which this
codebase already relies on for the WebSocket cap.

**Per PROCESS, and the plan's "per replica" is worth stating precisely.** This
counter lives on one event loop's heap. Both deployment paths default gunicorn
to ``WEB_CONCURRENCY=2``, so a replica's real ceiling is the configured number
times its worker count. That is correct for what this guard does — it protects
one process's event loop, and each process has its own — but it means the
number here is not a platform-wide admission control and must not be read as
one. The platform-wide ceilings are the Redis buckets.

**Health and metrics are exempt.** A saturated replica must still answer
``/health/ready`` and ``/metrics``: refusing the readiness probe is how an
orchestrator concludes a merely-busy replica is dead and restarts it, turning
a load spike into a rolling outage, and refusing the scrape blinds the
operator at the exact moment the graphs matter. Both are unauthenticated,
cheap, and touch no database.

**WebSockets pass through untouched.** A WS connection is in flight for as long
as the user keeps the tab open, so counting sockets here would exhaust the
budget with the first few dozen users and refuse HTTP requests forever. The
per-user socket ceiling is ``ws_connections_per_user``, enforced by
``ConnectionHub`` over its own registry.

**Installed INSIDE ``RedMetricsMiddleware`` and outside everything else**, so a
refusal is counted and timed like any other response, while the work it
refuses — correlation binding, routing, authentication, the whole dependency
graph — is never entered. That placement is also why the problem body is
rendered here by hand: the exception handlers live on Starlette's innermost
``ExceptionMiddleware``, so an ``AppError`` raised at this depth would escape
to the server as a 500. The contract is met by building the same RFC 9457
body, under the same media type, with the same correlation header.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import CORRELATION_HEADER, PROBLEM_MEDIA_TYPE, problem
from app.framework.errors import RateLimitedError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.observability.metrics import (
    api_rate_limit_total,
    rate_limit_rejections_total,
)

_logger = get_logger(__name__)

# The root paths that must answer even when the process is full -- see the
# module docstring. Exact matches only: a prefix test would exempt any route a
# later step happens to mount under a name starting with these.
EXEMPT_PATHS = frozenset({"/health", "/health/ready", "/metrics"})

# What a refused caller is told to wait. One second, and it is not a guess:
# unlike a per-minute window there is no instant to compute here, and the
# saturation this sheds is measured in the milliseconds a request takes to
# finish. The smallest legal delay-seconds is therefore the honest one --
# anything larger would keep a client away long after the burst had drained.
RETRY_AFTER_S = 1


class InFlightLimitMiddleware:
    """Refuse, rather than queue, once this process is already full."""

    def __init__(self, app: ASGIApp, *, max_in_flight: int) -> None:
        self._app = app
        self._max_in_flight = max_in_flight
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        """How many HTTP requests this process is serving right now."""
        return self._in_flight

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in EXEMPT_PATHS:
            await self._app(scope, receive, send)
            return

        # No `await` between this test and the increment: that is what makes
        # the pair atomic under cooperative scheduling, and it is the whole
        # reason this is an integer rather than a semaphore.
        if self._in_flight >= self._max_in_flight:
            await self._refuse(scope, receive, send)
            return
        self._in_flight += 1
        try:
            await self._app(scope, receive, send)
        finally:
            # In a `finally` because a client that disconnects mid-response
            # cancels this task, and a slot leaked on cancellation would
            # shrink the ceiling permanently -- the failure that looks like a
            # slow memory leak and is actually a closing door.
            self._in_flight -= 1

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        """The 429, rendered without the handler stack this sits outside of."""
        correlation_id = _correlation_of(scope)
        api_rate_limit_total.labels(outcome="refused_in_flight").inc()
        # The same platform-wide "how much load was shed" counter the AppError
        # handler feeds, under the same catalog code -- a request refused here
        # sheds exactly as much as one refused by a bucket, and an operator
        # reading that series should not have to know which layer said no.
        rate_limit_rejections_total.labels(reason=RateLimitedError.code).inc()
        _logger.warning(
            "api.in_flight_limit_reached",
            extra={"correlation_id": correlation_id, "in_flight": self._in_flight},
        )
        body = problem(
            RateLimitedError.code,
            RateLimitedError.status,
            detail="server is at its in-flight request limit",
            correlation_id=correlation_id,
            instance=str(scope.get("path") or ""),
        )
        response = JSONResponse(
            body,
            status_code=RateLimitedError.status,
            media_type=PROBLEM_MEDIA_TYPE,
            headers={
                CORRELATION_HEADER: correlation_id,
                "Retry-After": str(RETRY_AFTER_S),
            },
        )
        await response(scope, receive, send)


def _correlation_of(scope: Scope) -> str:
    """The caller's correlation id, or a fresh one.

    A deliberate duplicate of ``correlation_middleware``'s first line rather
    than a call into it: that middleware is INSIDE this one, and entering it
    is precisely the work being refused. The behaviour is the contract's
    either way -- echo what the client sent, mint one otherwise -- so a
    refused request is as traceable as a served one.
    """
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers") or ()
    wanted = CORRELATION_HEADER.lower().encode("latin-1")
    for raw_name, raw_value in headers:
        if raw_name.lower() == wanted:
            supplied = raw_value.decode("latin-1").strip()
            if supplied:
                return supplied
    return new_uuid7()
