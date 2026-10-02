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

* ``stream_lag_seconds`` — Wave 0 step 0.2 of ``docs/capacity-plan.md``: how
  far BEHIND THE HEAD each consumer group is, in seconds, for every
  ``(stream, group)`` pair in ``STATIC_CONSUMER_TOPOLOGY``. Seconds rather
  than the entry count Redis reports directly (``XINFO GROUPS``' ``lag``),
  because the acceptance gate is written in time — §7 item 5, "تأخّرُ المجاري
  مستقرٌّ دون دقيقتين" — and an entry count cannot answer it: a thousand
  entries behind is a second on a quiet stream and an hour on a busy one.
  Measured as the gap between the stream's ``last-generated-id`` and the
  group's ``last-delivered-id``, both of which carry a millisecond timestamp
  in the id itself, so a fully caught-up group reads ``0.0`` no matter how old
  the last entry is. It is also the ONE reading that can see ``ح-17``
  approaching: ``XADD MAXLEN ~`` trims by length with no regard for the
  slowest group's position, so a group whose lag is climbing is a group whose
  unread entries are on their way to being deleted with no error, no alert and
  no log line anywhere.

* ``stream_queue_wait_seconds`` — capacity 5.3: how long the OLDEST entry a
  group has not yet been handed has been waiting, by Redis's own clock, for
  every ``STATIC_CONSUMER_TOPOLOGY`` pair; ``0.0`` when nothing is waiting.
  It is the number the indexing queue's admission gate reads, and it differs
  from ``stream_lag_seconds`` exactly where a gate must not be wrong: after a
  quiet spell, the first entry of a burst that lands while every handler is
  busy puts ``stream_lag_seconds`` at the length of the QUIET SPELL (the gap
  between that entry and the last one delivered), while this reads the
  seconds it has actually waited. It also counts nothing in flight -- a
  summary build pending for half an hour is work, not queue.

* ``stream_retention`` — capacity 5.5 (``ح-17``): what each published stream
  holds and how far behind its slowest reader is, the numbers that watch the
  trim ``outbox-relay`` now performs (``infrastructure/messaging/
  stream_retention.py``). ``StreamRetention`` below says what each field
  answers; the reason there is an AGE at all is the plan's own sentence --
  «لا ``XLEN`` وحده، فالطولُ لا يقول شيئاً عن الاستهلاك».

* ``scheduled_tasks`` — capacity 5.7: one ``TaskRecord`` for EVERY task in
  ``SCHEDULED_TASKS`` (``framework/observability/scheduled_tasks.py``),
  including the ones nothing has ever written -- an empty record, never a
  missing key, because "expected and never armed" is itself the signal
  ``AizzakOpsTaskNeverArmed`` fires on. Read from the task ledger
  (``framework/ports/task_ledger.py``), the same fresh-per-scrape rule as
  every method above.

Implemented by ``infrastructure.monitoring.metrics_source.SqlRedisMetricsSource``
(the Composition Root's only caller); a fake substitutes it in
``tests/unit/test_api_metrics_router.py`` so the rendering logic can be
exercised without a live Postgres/Redis.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.framework.ports.task_ledger import TaskRecord


@dataclass(frozen=True, slots=True)
class StreamRetention:
    """One reading of every published stream (capacity 5.5).

    * ``lengths`` -- ``XLEN`` per stream. Alone it says nothing about
      consumption; next to ``backstop`` it says how close ``XADD``'s
      ``MAXLEN`` is to deleting entries (``AizzakStreamBackstopHigh``, 70%).
    * ``backstop`` -- that ``MAXLEN``; ``None`` when it is switched off.
    * ``oldest_unconsumed_age_s`` -- per ``(stream, group)``, how long the
      oldest entry that group has not FINISHED has been waiting: pending or
      not yet delivered, ``0.0`` when it has finished everything. The
      ``cg.notify.<host>.<pid>`` groups are one label, ``cg.notify``, at their
      maximum -- their names change with every deploy.
    * ``unread_trimmed`` -- per ``(stream, group)``, entries removed from the
      stream before that group read them. Zero by construction while only the
      5.5 trim runs; anything else is the loss ``ح-17`` describes, caught in
      the act (it is visible while the group is still behind the gap).
    """

    lengths: Mapping[str, int]
    backstop: int | None
    oldest_unconsumed_age_s: Mapping[tuple[str, str], float]
    unread_trimmed: Mapping[tuple[str, str], int]


class MetricsSource(Protocol):
    async def outbox_oldest_unpublished_age_seconds(self) -> float: ...

    async def dlq_depths(self) -> dict[str, int]: ...

    async def stream_lag_seconds(self) -> dict[tuple[str, str], float]: ...

    async def stream_queue_wait_seconds(self) -> dict[tuple[str, str], float]: ...

    async def stream_retention(self) -> StreamRetention: ...

    async def scheduled_tasks(self) -> Mapping[str, TaskRecord]: ...
