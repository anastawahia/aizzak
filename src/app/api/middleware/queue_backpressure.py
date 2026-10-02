"""Declared backpressure on the indexing queue — capacity-plan step 5.3 (``ق-6``
applied to the queue).

    @router.post("/documents", status_code=202, dependencies=[
        Depends(require(Permission.KNOWLEDGE_MANAGE)),
        Depends(knowledge_queue_open),
        Depends(heavy_job),
    ])

**What it refuses, and why a 429 is the honest answer.** When the oldest entry
``cg.knowledge`` has not yet been handed has already waited longer than the
declared ceiling (``RateLimitSettings.queue_lag_ceiling_s``, 120 s — the two minutes
``08 §4.0`` and §7 item 5 write), a new submission answers **429 with
``Retry-After``** instead of 202. A 202 at that moment is a receipt for work
the platform already knows it will not do within its own budget; the client
learns nothing, waits, and sees a document that stays ``pending`` — a capacity
shortfall dressed as a fault. ``ق-6`` chose the explicit refusal, and this is
that choice on the queue rather than on a provider.

**The number is a gauge's number, read through the gauge's port.** The wait
comes from ``MetricsSource.stream_queue_wait_seconds`` -- the method that fills
``aizzak_stream_queue_wait_seconds`` on ``/metrics`` -- so the gate and the
graph an operator watches can never disagree. It is how long the oldest entry
``cg.knowledge`` has not yet been handed has waited, by Redis's clock: the
QUEUE, not the work in flight. Two neighbouring numbers were the obvious
candidates and both would refuse wrongly:

* ``aizzak_stream_lag_seconds`` is the gap between the head's id and the
  group's delivery cursor. After ten quiet minutes, the first upload of a
  burst that lands while every handler is busy reads as ten minutes of lag --
  the length of the silence, not of any wait -- and the gate would close on
  a queue that is seconds deep.
* ``aizzak_stream_oldest_unconsumed_age_seconds`` (5.5) counts pending
  entries too, and a summary build legitimately holds one pending for up to
  ``Limits.summarize_job_max_duration_s`` (1,800 s); a gate on it would refuse
  every upload for half an hour because one summary was running.

**Read at most once per ``refresh_s`` per process.** The read is a ``MULTI``
and an ``XRANGE`` per bound stream; a burst of uploads should not multiply
them, and a wait measured in minutes does not move meaningfully in five seconds. Each
gunicorn sibling keeps its own copy — harmless, since the value is derived
from Redis and not accumulated here (``ports/metrics_source.py``).

**Fails OPEN, like every capacity control in this package.** A Redis that does
not answer is counted (``unavailable``) and the submission is admitted:
refusing every upload because the wait could not be read would turn a degraded
dependency into the outage the gate exists to prevent. A group with no reading
at all (the stream does not exist yet) is admitted too — there is no queue to
be behind.

**Where it is hung** is ``api/middleware/heavy_jobs.py`` (``knowledge_queue_open``),
beside the other guard on the same doors -- this module holds only the policy,
so ``ApiServices`` can name its type without an import cycle.

**Order on the route: after ``require``, before ``heavy_job``.** A caller
without the permission must hear about the permission (1.3's argument), and a
submission shed here must not spend the user's 30-job budget on work the
platform declined: being told "the queue is full, retry in 30 s" and then
"you have used your minute" for the same request would be two refusals for
one cause.

**Scope: ``stream.knowledge`` only.** It is the queue 5.3 grades and the one
whose ceiling is declared. ``POST /media/jobs`` is not gated — an image job
takes minutes by nature, so a lag ceiling for it is a different number nobody
has signed, and inventing one here would be the defect 1.3 refused to commit.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from app.framework.errors import RateLimitedError, ValidationError
from app.framework.observability import get_logger
from app.framework.observability.metrics import queue_backpressure_total
from app.framework.ports.metrics_source import MetricsSource

_logger = get_logger(__name__)

# The pair this gate watches. Literal rather than imported from the module's
# own constants, on `framework/events/topology.py`'s argument: the binding
# table is guarded against drift by `tests/unit/test_stream_topology.py`, and
# `tests/unit/test_queue_backpressure.py` pins this pair to a row of it.
KNOWLEDGE_STREAM = "stream.knowledge"
KNOWLEDGE_GROUP = "cg.knowledge"

_DETAIL = "the indexing queue is saturated; retry later"


class QueueBackpressure:
    """One stream's admission gate, over the ``MetricsSource`` port.

    Stateless apart from a few seconds of cached reading, so every process
    builds its own and they all answer from the same Redis.
    """

    def __init__(
        self,
        source: MetricsSource,
        *,
        stream: str,
        group: str,
        lag_ceiling_s: float,
        retry_after_s: int,
        refresh_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if lag_ceiling_s <= 0:
            # Off is a WIRING decision (`QUEUE_LAG_CEILING_S=0` builds no gate,
            # `م-8`); a zero here would refuse every submission.
            raise ValidationError("queue backpressure lag_ceiling_s must be positive")
        if retry_after_s <= 0:
            raise ValidationError("queue backpressure retry_after_s must be positive")
        self._source = source
        self._key = (stream, group)
        self._lag_ceiling_s = lag_ceiling_s
        self._retry_after_s = retry_after_s
        self._refresh_s = refresh_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._read_at: float | None = None
        self._wait_s: float | None = None

    @property
    def stream(self) -> str:
        return self._key[0]

    @property
    def lag_ceiling_s(self) -> float:
        return self._lag_ceiling_s

    async def check(self) -> None:
        """Admit this submission, or raise the 429 the API contract renders."""
        try:
            wait = await self._current_wait()
        except Exception as exc:
            # Fail OPEN -- the module docstring. The class name only: the
            # line repeats for as long as the outage lasts.
            queue_backpressure_total.labels(stream=self.stream, outcome="unavailable").inc()
            _logger.warning(
                "api.queue_backpressure_unavailable",
                extra={"stream": self.stream, "error": type(exc).__name__},
            )
            return
        if wait is None or wait <= self._lag_ceiling_s:
            queue_backpressure_total.labels(stream=self.stream, outcome="allowed").inc()
            return
        queue_backpressure_total.labels(stream=self.stream, outcome="refused").inc()
        raise RateLimitedError(_DETAIL, retry_after_s=self._retry_after_s)

    async def _current_wait(self) -> float | None:
        if self._fresh():
            return self._wait_s
        async with self._lock:
            # Re-checked under the lock: the request that waited behind the
            # one doing the read uses its answer instead of reading again.
            if not self._fresh():
                waits = await self._source.stream_queue_wait_seconds()
                self._wait_s = waits.get(self._key)
                self._read_at = self._clock()
            return self._wait_s

    def _fresh(self) -> bool:
        return self._read_at is not None and self._clock() - self._read_at < self._refresh_s
