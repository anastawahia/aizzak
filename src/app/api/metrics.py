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

**Wave 0 step 0.2 (``docs/capacity-plan.md``) adds a second family, and it is
correct for the OPPOSITE reason.** RED — a request counter, a duration
histogram — cannot be recomputed from anything external: a count of what THIS
process served exists only in this process. So those metrics take the other
answer to the same multi-process problem, the one this docstring named and
deferred: ``PROMETHEUS_MULTIPROC_DIR``, mmap files per process, aggregated at
scrape time (``framework/observability/metrics.py`` defines them,
``deploy/gunicorn.conf.py`` owns the directory's lifecycle, ``_process_metrics``
below renders them). The fresh-registry-per-request shape above is untouched
and still governs every gauge of external state; a fourth such gauge,
``aizzak_stream_lag_seconds``, joins them here.

The 503 for an unwired ``metrics_source`` deliberately still swallows BOTH
families. A process that cannot answer for the platform's state has nothing to
tell a scraper about its own request rate either, and answering 200 with half
the exposition would make an unwired app indistinguishable from a healthy one
whose Outbox happened to be empty.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from fastapi import APIRouter, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily, Metric
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
# Wave 0 step 0.2 (`docs/capacity-plan.md`) -- the fourth live gauge, and the
# first that carries two labels. Its own reasoning is in the port.
STREAM_LAG_METRIC = "aizzak_stream_lag_seconds"

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

    families: list[Metric] = []

    families.append(
        GaugeMetricFamily(
            OUTBOX_AGE_METRIC,
            "Age in seconds of the oldest unpublished platform.outbox row (0 if none waiting).",
            value=await source.outbox_oldest_unpublished_age_seconds(),
        )
    )

    dlq_depth = GaugeMetricFamily(
        DLQ_DEPTH_METRIC,
        "XLEN of <stream>.dlq for each source stream (P1-4's python -m app.ops.dlq "
        "is the response tool once this is non-zero).",
        labels=["stream"],
    )
    for stream, depth in (await source.dlq_depths()).items():
        dlq_depth.add_metric([stream], depth)
    families.append(dlq_depth)

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
        # `authenticated()` is contractually TOTAL (the port's docstring), so
        # this needs no try/except: a Vault outage must never cost the scrape
        # its Outbox/DLQ numbers, which an exception raised HERE -- after both
        # gauges are already built but before `generate_latest` runs -- would.
        families.append(
            GaugeMetricFamily(
                VAULT_AUTH_METRIC,
                "1 if this process can currently make an authorized Vault call, 0 otherwise "
                "(an expired secret_id, a sealed Vault, a timeout -- indistinguishable here).",
                value=1.0 if await vault_health.authenticated() else 0.0,
            )
        )

    stream_lag = GaugeMetricFamily(
        STREAM_LAG_METRIC,
        "Seconds a consumer group is behind its stream's head (0 when caught up).",
        labels=["stream", "group"],
    )
    for (stream, group), lag in (await source.stream_lag_seconds()).items():
        stream_lag.add_metric([stream, group], lag)
    families.append(stream_lag)

    registry = CollectorRegistry()
    registry.register(_Snapshot(families))
    return Response(
        content=generate_latest(registry) + _process_metrics(),
        media_type=CONTENT_TYPE_LATEST,
    )


class _Snapshot:
    """A ``Collector`` over values already read, for THIS scrape only.

    **Why not ``Gauge(..., registry=registry)``, which is what this module did
    until Wave 0 step 0.2.** That form was correct while
    ``PROMETHEUS_MULTIPROC_DIR`` was unset, and step 0.2 sets it. In
    multiprocess mode ``prometheus_client`` swaps the value class GLOBALLY --
    every ``Gauge`` becomes mmap-backed no matter which registry it is
    attached to -- so each of these four gauges would be written to the shared
    directory as a side effect of being built, and then reported a SECOND time
    by ``MultiProcessCollector`` with a ``pid`` label. Measured, not predicted:
    a scrape rendered ``aizzak_outbox_oldest_unpublished_age_seconds`` three
    times, once live and twice from the files (one per pid that had ever
    served a scrape), and the stale copies never expire because a
    ``gauge_all`` file survives ``mark_process_dead`` by design.

    A ``MetricFamily`` never touches the value class: it carries its samples
    inline and is rendered straight to text. So the property this module's
    docstring is built on -- nothing survives between requests -- holds again,
    and it now holds under both process models rather than only one.
    """

    def __init__(self, families: list[Metric]) -> None:
        self._families = families

    def collect(self) -> Iterable[Metric]:
        return iter(self._families)


def _process_metrics() -> bytes:
    """The RED and saturation families (Wave 0 step 0.2), rendered from
    whichever registry this deployment's process model makes correct.

    Two registries and a concatenation rather than one, because the two
    families are correct for OPPOSITE reasons and mixing them into a single
    collector would break whichever one lost. Everything above is external
    state re-read per scrape and therefore identical from any sibling; the
    families here are each process's own accumulated history, which no sibling
    can recompute. Concatenation is legal Prometheus exposition as long as the
    two halves share no metric name, and they share none by construction --
    the names live in two modules, both as constants.

    ``PROMETHEUS_MULTIPROC_DIR`` decides which registry answers:

    * **Set** (production, gunicorn -- see ``deploy/gunicorn.conf.py``): the
      per-process samples live in mmap files under that directory, and
      ``MultiProcessCollector`` aggregates every sibling's. This is the whole
      reason a request counter can be correct here at all; without it a scrape
      would report whichever worker the load balancer happened to pick.
    * **Unset** (tests, a single uvicorn): the default registry is already the
      complete picture, and it carries the standard ``process_*``/``python_*``
      collectors as a bonus -- including ``process_resident_memory_bytes``,
      which is the quantity §7 item 2's eight-hour soak reads a SLOPE from.
      Those same collectors are deliberately NOT rendered in multiprocess
      mode: they describe the one process that answered the scrape, so
      aggregating them across siblings would produce a number that is not any
      process's memory. Container-level memory is the right source there, and
      it belongs to step 0.3's Prometheus deployment.
    """
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return generate_latest(REGISTRY)
    registry = CollectorRegistry()
    # `prometheus_client` ships `py.typed`, but this one constructor carries no
    # annotations, so `--strict` reads it as untyped. A narrow ignore on the
    # call rather than an entry in `[[tool.mypy.overrides]]`: the rest of the
    # library IS typed and this file leans on that everywhere else, and
    # silencing the whole module would hide the next real signature change.
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return generate_latest(registry)
