"""Health probes — ``GET /health`` · ``GET /health/ready`` (03-api-spec §1).

The two endpoints the whole contract exempts from authentication ("بلا
مصادقة"), and the only ones mounted at the ROOT rather than under
``/api/v1``: an orchestrator (Nginx, a load balancer, Docker's healthcheck —
08 §2's topology) probes a fixed, unversioned path.

Two distinct signals, the Kubernetes/Compose split applied honestly:

* **``/health`` is LIVENESS** — the process is up and serving. It touches no
  dependency on purpose: a liveness probe that failed when Redis blipped would
  make an orchestrator kill and restart a perfectly healthy replica, turning a
  downstream hiccup into an outage. It is a pure "am I running" answer.
* **``/health/ready`` is READINESS** — this replica has finished startup and
  has not begun shutting down, so it may receive traffic. ``create_app``'s
  lifespan flips ``app.state.ready`` true after wiring completes and false as
  teardown begins; during either window this returns 503 so the orchestrator
  routes elsewhere. Probing the data stores themselves is a deliberate
  extension point for Phase 7/ops, not v1: readiness here is "startup done",
  which is the signal a rolling deploy actually needs.

⭐ **``POST /health/drain`` is the third — capacity step 7.2.** It is not a
probe; it is the only way to tell a replica "you are about to be replaced"
BEFORE the signal that replaces it. That ordering is the whole reason it
exists: on ``SIGTERM`` uvicorn stops accepting and closes every WebSocket
itself, measured in 3.3 at 1012 after 0.10 s — all of them, in one tick, which
is precisely the reconnect herd step 7.2 is written to prevent. A shutdown hook
cannot spread what uvicorn has already closed, so the spreading has to start
one step earlier, and something outside the process has to say when.

⚠️ **It is unauthenticated and it MUST NOT be reachable from outside**, which
is the same trust boundary ``/metrics`` already draws and is enforced the same
way: ``deploy/nginx/app-locations.conf`` answers this path locally with 404
rather than proxying it, so a probe through the edge cannot even learn it
exists. A real caller reaches it over the Compose-internal network
(``<replica-ip>:8000/health/drain``), which is exactly what
``deploy/rolling-deploy.sh`` does — it must address ONE replica, so going
through a load balancer would be wrong even if the path were exposed.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.framework.observability import get_logger
from app.framework.types import Json

health_router = APIRouter(tags=["health"])

_logger = get_logger(__name__)

_NOT_READY_STATUS = 503

# The default drain window, and a ceiling on what a caller may ask for.
#
# 10s against gunicorn's `--graceful-timeout 30` and the 45s external stop
# window 3.3 shipped: the drain has to FINISH inside the budget that follows
# it, because whatever is still open when `SIGTERM` lands is closed by uvicorn
# in one tick anyway — the failure mode this endpoint exists to avoid. The
# ceiling is not paranoia about hostile callers (nothing outside the network
# can reach this): it is that a mistyped window silently converts a rolling
# deploy into a stall, and a clamp says so in the response instead.
_DEFAULT_DRAIN_WINDOW_S = 10.0
_MAX_DRAIN_WINDOW_S = 25.0


@health_router.get("/health")
async def health() -> Json:
    """Liveness: 200 as long as the process can answer. No dependency touched."""
    return {"status": "ok"}


@health_router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: 200 once startup finished, 503 during startup/shutdown.

    ``app.state.ready`` is set by the lifespan; absent (a bare app never taken
    through its lifespan) is treated as not-ready — the safe default, since a
    replica that never announced readiness must not be sent traffic.
    """
    is_ready = bool(getattr(request.app.state, "ready", False))
    if is_ready:
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "starting"}, status_code=_NOT_READY_STATUS)


# `include_in_schema=False`, the `/metrics` precedent exactly (P1-3). This is
# operator tooling, not API surface: `openapi.yaml` is the contract a client is
# written against, and a verb no client may call -- and that the edge answers
# with 404 -- would document a capability that does not exist from outside.
# `test_api_conventions` compares the published operations against that file
# one for one, so anything published here has to be declared there.
@health_router.post("/health/drain", include_in_schema=False)
async def drain(
    request: Request,
    window_s: float = Query(default=_DEFAULT_DRAIN_WINDOW_S, ge=0.0),
) -> JSONResponse:
    """Begin draining this replica: readiness goes false, and every WebSocket
    it holds is asked to reconnect, spread over ``window_s``.

    Returns as soon as the closes are SCHEDULED, reporting how many and over
    what window, so the caller knows exactly how long to wait before sending
    the signal that stops the process. A drain that only answered when the last
    peer had gone would put the caller's timeout in charge of the window
    instead of this number.

    **Idempotent, and it has to be.** A deploy script that retries — or an
    operator who runs it twice — must not schedule a second close for a socket
    already counted in the first, which would double the reconnect rate at
    exactly the moment the rate is the thing being controlled. The second call
    answers with what the first one started.

    Answers 200 even with no hub wired and no sockets open: "nothing to drain"
    is a successful drain, and a caller that had to distinguish those cases
    would need to know whether this replica happens to serve WebSockets.
    """
    already = bool(getattr(request.app.state, "draining", False))
    request.app.state.ready = False
    request.app.state.draining = True

    window = min(window_s, _MAX_DRAIN_WINDOW_S)
    hub = getattr(request.app.state, "hub", None)
    if already or hub is None:
        scheduled = int(getattr(request.app.state, "drain_scheduled", 0))
    else:
        scheduled = await hub.drain(window_s=window)
        request.app.state.drain_scheduled = scheduled

    _logger.info(
        "health.drain_requested",
        extra={"sessions": scheduled, "window_s": window, "repeat": already},
    )
    return JSONResponse(
        {
            "status": "draining",
            "sessions": scheduled,
            "window_s": window,
            "clamped": window != window_s,
            "repeat": already,
        }
    )
