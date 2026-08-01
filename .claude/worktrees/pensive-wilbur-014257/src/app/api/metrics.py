"""``GET /metrics`` — Prometheus exposition of the two P1-3 health signals
(``docs/p1-hardening-plan.md`` §3 step 10; 07-nfr-slo §7's "زمن دورة الـ
Outbox ومَلء DLQ مؤشّرا صحّة رئيسيان عند تفعيل المراقبة") plus, since ن-10,
a THIRD signal of a different nature: whether this process can currently
authenticate to Vault (``framework/ports/vault_health.py``; the outage that
motivated it is ``docs/log/3.94.md``). Deliberately a metric and an alert
rather than a readiness probe — ``/health/ready`` probes no dependency by
design (§3.75), and reversing that would trade a silent failure for a
flapping one.

Mounted at the ROOT, unauthenticated, exactly like ``/health`` (``api/
health.py``) — an operator's scraper, not a tenant-facing route, and 03
§0's bearer requirement was never meant to gate a process's own liveness/
metrics surface. **Unlike ``/health``, this endpoint must NEVER be reachable
through the public edge** (mzalaq #2 of the design brief): Prometheus text
about the Outbox/DLQ is operator information, the SAME trust boundary the
embedding service already draws with ``expose:`` instead of ``ports:``
(``docker-compose.yml``, ``release-blockers-plan.md``'s rejected-port
precedent, n-3). ``deploy/nginx/app-locations.conf`` and ``deploy/runpod/
nginx.conf`` both answer this path with a local ``404`` before it ever
reaches ``location /``'s catch-all proxy — enforced there, not here, because
an application-layer check can be bypassed by anything that talks to the
container directly on its Compose-internal address (``app:8000``), which a
real Prometheus scraper is expected to do.

**Why a FRESH ``CollectorRegistry`` every request, never the module-global
default one ``prometheus_client`` ships.** The design brief's central
pitfall: gunicorn's own default ``WEB_CONCURRENCY=2`` (``Dockerfile``,
``deploy/runpod/supervisord.conf``, ``docker-compose.yml``) means "the API"
is at least two SIBLING processes, each with an independent Python heap. A
``Gauge`` registered once at import time and ``.set()`` on every scrape would
answer with THAT process's own last-written value — whichever sibling
happened to catch the request — which is precisely the "same knob, two
different in-memory copies" defect P0-2 (``ConnectionHub``, docs/log/3.81.md)
already named and fixed for WebSocket session counts. The fix here is the
SAME shape reapplied: build a brand-new, empty ``CollectorRegistry`` INSIDE
the request handler, register both gauges against it, set them from
``MetricsSource`` (which itself re-reads Postgres/Redis on every call — see
that port's own docstring), render, and let the registry be garbage
collected the moment the response is sent. Nothing here survives between
requests, so which of the ``WEB_CONCURRENCY`` siblings answers a given scrape
is invisible to the numbers it returns: both compute the identical value from
the identical external source, every time.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from starlette.responses import Response

from app.framework.ports.metrics_source import MetricsSource
from app.framework.ports.vault_health import VaultHealth

metrics_router = APIRouter(tags=["metrics"])

# The three metric names the alert rules in `deploy/prometheus/alerts.yml`
# reference verbatim -- `tests/unit/test_prometheus_alert_rules.py` imports
# these SAME constants rather than repeating the literals, so the two files
# cannot drift apart silently.
OUTBOX_AGE_METRIC = "aizzak_outbox_oldest_unpublished_age_seconds"
DLQ_DEPTH_METRIC = "aizzak_dlq_depth"
# ن-10 (docs/log/3.94.md). A 1/0 gauge, not a timestamp or a TTL: see
# `infrastructure/monitoring/vault_health.py` for why the more informative
# "seconds until the secret_id expires" was rejected (it needs an `auth/*`
# grant `deploy/vault/app-policy.hcl` forbids, and puts the secret_id on the
# wire every scrape).
VAULT_AUTH_METRIC = "aizzak_vault_authenticated"

_NOT_WIRED_STATUS = 503


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Render both gauges from a live ``MetricsSource`` read — never from
    anything cached on ``request.app.state`` beyond the source itself."""
    source: MetricsSource | None = getattr(request.app.state, "metrics_source", None)
    if source is None:
        # Reachable only in a test/dev app built without `metrics_source=`
        # (`create_app`'s default `None`, so the other 6.1 router tests keep
        # constructing `create_app` exactly as before) -- a production app
        # (`create_production_app`) always wires the real adapter.
        return Response(
            content=b"metrics source not wired\n",
            status_code=_NOT_WIRED_STATUS,
            media_type="text/plain",
        )

    registry = CollectorRegistry()
    outbox_age = Gauge(
        OUTBOX_AGE_METRIC,
        "Age in seconds of the oldest unpublished platform.outbox row (0 if none waiting).",
        registry=registry,
    )
    outbox_age.set(await source.outbox_oldest_unpublished_age_seconds())

    dlq_depth = Gauge(
        DLQ_DEPTH_METRIC,
        "XLEN of <stream>.dlq for each source stream (P1-4's python -m app.ops.dlq "
        "is the response tool once this is non-zero).",
        ["stream"],
        registry=registry,
    )
    for stream, depth in (await source.dlq_depths()).items():
        dlq_depth.labels(stream=stream).set(depth)

    vault_health: VaultHealth | None = getattr(request.app.state, "vault_health", None)
    if vault_health is not None:
        # OMITTED, never rendered as `0`, when unwired -- the same shape as
        # the `metrics_source is None` guard above and for the same reason: a
        # test/dev app built through `create_app`'s default `None` (every
        # pre-existing 6.1 router test) must keep rendering the two P1-3
        # gauges exactly as before. Rendering `0` here instead would make an
        # UNWIRED probe indistinguishable from a DEAD Vault credential, i.e.
        # it would arm a `critical` alert on a fact nobody measured.
        # `create_production_app` always wires the real `VaultProbe`.
        vault_authenticated = Gauge(
            VAULT_AUTH_METRIC,
            "1 if this process can currently make an authorized Vault call, 0 otherwise "
            "(an expired secret_id, a sealed Vault, a timeout -- indistinguishable here).",
            registry=registry,
        )
        # `authenticated()` is contractually TOTAL (the port's docstring), so
        # this needs no try/except: a Vault outage must never cost the scrape
        # its Outbox/DLQ numbers, which an exception raised HERE -- after both
        # gauges are already set but before `generate_latest` runs -- would.
        vault_authenticated.set(1.0 if await vault_health.authenticated() else 0.0)

    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
