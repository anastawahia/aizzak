"""MetricsSource driven port — the two health signals ``/metrics`` exposes
(P1-3, ``docs/p1-hardening-plan.md`` §3 step 10).

07-nfr-slo §7 names exactly two "مؤشّرا صحّة رئيسيان" (key health signals)
once monitoring exists: the Outbox's publish cycle time and the DLQ's fill
level. Until this port, nothing measured either — the structured JSON log is
a good record of what already happened, never an answer to "is the platform
degraded right now", which is what a scrape needs.

**Both methods are gauges of REAL, external state, computed fresh on every
call — never a value this port (or anything behind it) accumulates in its
own process memory.** That is the whole answer to the multi-process
pitfall named in the design brief: gunicorn's default ``WEB_CONCURRENCY=2``
means "the API" is at least two SIBLING OS processes, each with its own
Python heap, so an in-process counter/gauge would answer with whichever
process happened to serve the scrape — a number that flips between two
different truths depending on load-balancer luck, not a health signal. A
value derived HERE, at call time, from Postgres/Redis (state neither
process owns privately) is identical no matter which sibling computes it,
by construction rather than by coordination.

* ``outbox_oldest_unpublished_age_seconds`` — how long (in seconds) the
  OLDEST still-unpublished ``platform.outbox`` row has been waiting, ``0.0``
  if none are waiting at all. This is the SLO's own "زمن دورة الـ Outbox
  (نشر بعد الالتزام)" quantity, read as a live gauge rather than a
  historical percentile: under a healthy relay this is always small (07
  §2's own budget: p50 0.5s / p95 1.5s / p99 3s), and it climbs
  monotonically for as long as the relay is stalled or down — exactly the
  failure ``outbox_relay.py``'s own module docstring says cannot silently
  vanish (every payload is pre-validated at produce time, so the only
  publish failures are transport-level and apply to the WHOLE remaining
  batch, never one poisoned row).
* ``dlq_depths`` — ``XLEN`` of ``<stream>.dlq`` for every source stream this
  platform's workers consume, keyed by the SOURCE stream name (never the
  ``.dlq`` suffix, the ``ops.dlq`` module's own convention). A dead-lettered
  entry never self-heals: it sits there until an operator runs ``python -m
  app.ops.dlq requeue``/``purge`` (step 7) by hand, so ANY non-zero depth is
  already an anomaly worth a look, not merely a big number.

Implemented by ``infrastructure.monitoring.metrics_source.SqlRedisMetricsSource``
(the Composition Root's only caller); a fake substitutes it in
``tests/unit/test_api_metrics_router.py`` so the rendering logic can be
exercised without a live Postgres/Redis.
"""

from __future__ import annotations

from typing import Protocol


class MetricsSource(Protocol):
    async def outbox_oldest_unpublished_age_seconds(self) -> float: ...

    async def dlq_depths(self) -> dict[str, int]: ...
