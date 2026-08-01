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
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.framework.types import Json

health_router = APIRouter(tags=["health"])

_NOT_READY_STATUS = 503


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
