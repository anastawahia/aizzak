"""The RED and saturation metric definitions — Wave 0 step 0.2 of
``docs/capacity-plan.md``.

``ح-12`` is "لا قياس": three gauges exist (``api/metrics.py``), all three of
external platform state, and none of them says anything about a request. So
no number in that plan can be proved or disproved today, which is why Wave 0
blocks every wave after it.

**Deliberately NOT re-exported from ``observability/__init__``.** Every
process in this codebase imports that package for ``get_logger``; re-exporting
here would put ``prometheus_client`` — and, when ``PROMETHEUS_MULTIPROC_DIR``
is set, its mmap files — into four worker processes that expose no HTTP
listener to scrape. Callers name this module directly.

── The multi-process problem, and the two different answers to it ──────────
``ports/metrics_source.py`` already states the pitfall: gunicorn's
``WEB_CONCURRENCY`` means "the API" is several sibling OS processes with
independent heaps, so an in-process counter answers with whichever sibling
caught the scrape. Its answer was to hold no state at all — every gauge is
recomputed from Postgres/Redis on each scrape, so every sibling computes the
same value by construction.

**That answer cannot work for a request counter.** A count of requests THIS
process served exists nowhere but in this process; there is no external store
to re-read. So the metrics here take the other answer, the one that module's
docstring names and defers: ``PROMETHEUS_MULTIPROC_DIR``. Each process writes
its samples to mmap files in a shared directory and the scrape aggregates
across all of them (``api/metrics.py``'s renderer, ``deploy/gunicorn.conf.py``
for the directory's lifecycle). Both answers now coexist in one endpoint, and
the split is exactly "is this fact external state or is it this process's own
history".

The ``multiprocess_mode`` on each gauge below is therefore load-bearing, not
decoration:

* ``livesum`` for the pool and socket gauges — the quantity wanted is the sum
  across LIVE processes, which is precisely what ``§3``'s connection-budget
  equation compares against ``MAX_CLIENT_CONN``; a dead worker's last value
  must not keep counting against it.
* ``max`` for event-loop lag — the diagnostic question is "is ANY sibling
  blocked", not "what is the average across siblings", and averaging is what
  hides the one stuck process (``ح-5``'s exact shape).

── Cardinality ────────────────────────────────────────────────────────────
``0.2``'s own warning: "تعدّدٌ عالٍ يُسقط Prometheus قبل أن يُسقط التطبيق".
Every label here is low-cardinality by construction — the route TEMPLATE and
never the raw path, an unrouted request folded into one fixed
``<unmatched>`` label so a scanner cannot mint a time series per URL it
guesses, and no ``workspace_id`` anywhere.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

# The metric names, as constants: `deploy/prometheus/alerts.yml` and the tests
# import these rather than repeating the literals, the same discipline
# `api/metrics.py` already applies to its three.
HTTP_REQUESTS_METRIC = "aizzak_http_requests_total"
HTTP_DURATION_METRIC = "aizzak_http_request_duration_seconds"
DB_POOL_IN_USE_METRIC = "aizzak_db_pool_in_use"
DB_POOL_AVAILABLE_METRIC = "aizzak_db_pool_available"
DB_POOL_OVERFLOW_METRIC = "aizzak_db_pool_overflow"
EVENT_LOOP_LAG_METRIC = "aizzak_event_loop_lag_seconds"
RATE_LIMIT_REJECTIONS_METRIC = "aizzak_rate_limit_rejections_total"
WS_CONNECTIONS_METRIC = "aizzak_ws_connections"
AUTH_PRINCIPAL_CACHE_METRIC = "aizzak_auth_principal_cache_total"
API_RATE_LIMIT_METRIC = "aizzak_api_rate_limit_total"

# The route label for a request that matched no route -- one fixed string, so
# 404 traffic costs exactly one time series no matter how many distinct URLs
# it invents.
UNMATCHED_ROUTE = "<unmatched>"

# ── Histogram buckets, calibrated on 07-nfr-slo §2 rather than on defaults ──
# `0.2` asks for "هستوغرام بقِسَمٍ معايرةٍ على ميزانيّات `07 §2`", and the
# reason is a query, not neatness: with a bucket boundary sitting EXACTLY on
# each budget, "what fraction of reads met the 150ms budget" is a division of
# two numbers Prometheus already has, and needs no interpolation. Ask the same
# question of the client library's default buckets (…0.1, 0.25, 0.5…) and the
# answer has to be interpolated between 100ms and 250ms — an estimate of
# compliance, reported as compliance.
#
#   0.150  read p95      0.250  write p95
#   0.400  RAG retrieval 1.200  first streamed token
DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.15,
    0.25,
    0.4,
    0.6,
    1.0,
    1.2,
    2.5,
    5.0,
    10.0,
    float("inf"),
)

http_requests_total = Counter(
    HTTP_REQUESTS_METRIC,
    "HTTP requests served, by route template, method and status code.",
    ["route", "method", "status"],
)

http_request_duration_seconds = Histogram(
    HTTP_DURATION_METRIC,
    "HTTP request duration in seconds, bucketed on 07-nfr-slo §2's budgets.",
    ["route", "method"],
    buckets=DURATION_BUCKETS,
)

db_pool_in_use = Gauge(
    DB_POOL_IN_USE_METRIC,
    "Connections checked OUT of this process's SQLAlchemy pool.",
    multiprocess_mode="livesum",
)

db_pool_available = Gauge(
    DB_POOL_AVAILABLE_METRIC,
    "Connections idle in this process's SQLAlchemy pool.",
    multiprocess_mode="livesum",
)

db_pool_overflow = Gauge(
    DB_POOL_OVERFLOW_METRIC,
    "Overflow connections open beyond pool_size in this process.",
    multiprocess_mode="livesum",
)

event_loop_lag_seconds = Gauge(
    EVENT_LOOP_LAG_METRIC,
    "Worst observed asyncio scheduling delay in the last sampling window.",
    multiprocess_mode="max",
)

rate_limit_rejections_total = Counter(
    RATE_LIMIT_REJECTIONS_METRIC,
    "Requests refused with 429, by reason. An INTENDED refusal, never an error "
    "(capacity-plan.md §7 item 4) -- counted here so it can be read next to the "
    "error rate rather than inside it.",
    ["reason"],
)

ws_connections = Gauge(
    WS_CONNECTIONS_METRIC,
    "WebSocket sessions this process currently holds open.",
    multiprocess_mode="livesum",
)

auth_principal_cache_total = Counter(
    AUTH_PRINCIPAL_CACHE_METRIC,
    "Principal resolutions on the authentication path, by cache result. A "
    "`miss` is exactly the request that paid the auth path's two database "
    "round trips, so `rate(miss) / rate(hit+miss)` IS capacity-plan step "
    "1.1's acceptance number -- database transactions per authenticated "
    "request -- read off the platform rather than inferred from a pool gauge.",
    ["result"],
)

api_rate_limit_total = Counter(
    API_RATE_LIMIT_METRIC,
    "Rate-limit decisions on the API path, by outcome. Distinct from "
    "`aizzak_rate_limit_rejections_total`, which counts how much load was "
    "shed across the whole platform: this one says WHY, and it is the only "
    "place the two capacity-plan 1.2 buckets are told apart -- a tenant "
    "refused at its workspace ceiling is a capacity conversation, one user "
    "refused at theirs is not. `unavailable` is the fail-open path: Redis "
    "did not answer and the request was admitted UNCOUNTED, so a rise here "
    "means the ceilings are not being enforced at all.",
    ["outcome"],
)


@dataclass(frozen=True, slots=True)
class PoolStats:
    """The three numbers ``§3``'s connection-budget equation is written in."""

    in_use: int
    available: int
    overflow: int


def pool_stats_of(pool: object) -> PoolStats | None:
    """Read a SQLAlchemy pool structurally, or ``None`` when it cannot be read.

    Structural rather than typed against ``QueuePool`` on purpose: the three
    methods below belong to ``QueuePool``/``AsyncAdaptedQueuePool``, and the
    pool a given engine holds is a configuration decision -- ``NullPool`` and
    ``StaticPool`` are both real choices this codebase's tests make, and
    neither implements them. A ``None`` here means "this process has no pool
    to report", which is a true statement and the correct thing to publish
    nothing about; raising would take the whole scrape down over a gauge.
    """
    try:
        checked_out = pool.checkedout()  # type: ignore[attr-defined]
        checked_in = pool.checkedin()  # type: ignore[attr-defined]
        overflow = pool.overflow()  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return None
    # SQLAlchemy's `overflow()` starts at `-pool_size` and climbs, so it is
    # negative for as long as the pool has never been full. Clamping is not
    # cosmetic: a negative "overflow" published as-is would subtract from the
    # `livesum` across siblings and understate the total.
    return PoolStats(
        in_use=int(checked_out),
        available=int(checked_in),
        overflow=max(0, int(overflow)),
    )


async def sample_process_metrics(
    pool: object | None = None,
    *,
    report_interval_s: float = 5.0,
    probe_interval_s: float = 0.25,
) -> None:
    """Publish this process's saturation gauges, forever.

    A background task rather than scrape-time sampling, and the reason is the
    multi-process design above: a gauge set only while ANSWERING a scrape is
    written by exactly one sibling, so the ``livesum`` across the others reads
    whatever they last happened to write -- which, for a sibling that never
    served a scrape, is nothing at all. Every process must publish its own
    numbers on its own clock for the sum to mean anything.

    **Event-loop lag is measured by the shape of the probe, not by a call.**
    There is no API that reports it; the only honest measurement is to ask the
    loop to wake you at a known time and see how late it is. So the probe
    sleeps ``probe_interval_s`` and the overshoot IS the lag -- a loop blocked
    in a synchronous call (``ح-5``'s ``model.encode``) cannot run the timer
    callback, and the overshoot is exactly how long it was blocked for. The
    WORST overshoot in each window is published rather than the last one: a
    single 900ms stall between two prompt wake-ups is the event, and reporting
    the wake-up that followed it would erase it.
    """
    worst = 0.0
    window_started = time.monotonic()
    while True:
        before = time.monotonic()
        await asyncio.sleep(probe_interval_s)
        lag = max(0.0, (time.monotonic() - before) - probe_interval_s)
        worst = max(worst, lag)

        if time.monotonic() - window_started < report_interval_s:
            continue
        event_loop_lag_seconds.set(worst)
        worst = 0.0
        window_started = time.monotonic()

        stats = pool_stats_of(pool) if pool is not None else None
        if stats is not None:
            db_pool_in_use.set(stats.in_use)
            db_pool_available.set(stats.available)
            db_pool_overflow.set(stats.overflow)
