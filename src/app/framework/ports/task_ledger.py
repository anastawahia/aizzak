"""TaskLedger driven port — where every scheduled task says when it last
succeeded (capacity step 5.7, ``docs/capacity-plan.md`` §5 wave 5).

**The sentence this port exists for is the plan's own:** «مهمّةٌ مجدولةٌ تفشل
صامتةً أسوأُ من غياب المهمّة — لأنّها تشتري طمأنينةً كاذبة». A task that is not
scheduled is a known gap; a task that is scheduled and quietly stopped working
is a gap everybody believes is closed. So a scheduled task in this platform is
not done when it RUNS on a timer -- it is done when it also writes down, in
one shared place, that it succeeded, and something reads that back.

Before 5.7 the platform already ran three periodic tasks in-process (the 5.5
stream trimmer in ``outbox-relay``, the notify-group sweep in ``app``, the DLQ
watch in every worker), and all three had the same property: a pass that
fails is logged, and a pass that succeeds says nothing. Silence was both the
healthy state and the dead one. This ledger is what tells them apart.

**Why one record per task and not a metric per process.** A task's runner is
often several processes (three ``app`` replicas each run the notify sweep, two
``worker-knowledge`` replicas both watch one DLQ) and sometimes a short-lived
one (``app.ops.retention`` under the 5.7 scheduler exits after every run). A
process-local gauge would be reset by every restart and summed wrongly across
replicas; a shared record keyed by the TASK answers the only question that
matters -- "did anybody complete this recently" -- and survives both.

Implemented by ``infrastructure.monitoring.task_ledger.RedisTaskLedger``.
Written by ``framework.observability.scheduled_tasks.TaskReport`` (the
in-process loops) and ``app.ops.scheduler`` (the tools); read by
``MetricsSource.scheduled_tasks`` on every ``/metrics`` scrape.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Everything the ledger holds for one task. Every timestamp is Unix
    seconds, and ``None`` means "never" -- never armed, never succeeded, never
    failed -- rather than a zero that would read as 1970.

    * ``interval_s`` -- the cycle the runner declared when it last armed the
      task. ``0`` is a runner that has the task switched off (the ``م-8``
      kill-switch convention); ``None`` is a runner that never armed it.
    * ``max_runtime_s`` -- the longest one pass may legitimately take, so a
      daily backup that runs for an hour is not late at 48 h plus a minute.
    * ``armed_at`` -- when a runner first declared the task. Kept on the
      first write only, so a task that has never succeeded is late two cycles
      after it was armed, not two cycles after every restart.
    * ``attempt_cycle``/``cycle_attempts`` -- the 5.7 scheduler's own
      bookkeeping (which cycle it last tried, and how often); the in-process
      loops never set them.
    """

    task: str
    interval_s: float | None = None
    max_runtime_s: float | None = None
    armed_at: float | None = None
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_duration_s: float | None = None
    last_error: str | None = None
    attempt_cycle: int | None = None
    cycle_attempts: int = 0
    successes: int = 0
    failures: int = 0

    @property
    def max_success_age_s(self) -> float | None:
        """The plan's «تنبيهٌ عند تجاوز دورتين», as a number: two cycles plus
        the longest one run may take. ``None`` for a task that is switched off
        or was never armed -- there is no deadline to be late for."""
        if not self.interval_s:
            return None
        return 2 * self.interval_s + (self.max_runtime_s or 0.0)


class TaskLedger(Protocol):
    async def arm(self, task: str, *, interval_s: float, max_runtime_s: float, at: float) -> None:
        """Declare the task's cycle. Overwrites ``interval_s``/
        ``max_runtime_s`` (a changed setting must show up), keeps the FIRST
        ``armed_at``."""
        ...

    async def record(
        self,
        task: str,
        *,
        interval_s: float,
        max_runtime_s: float,
        started_at: float,
        finished_at: float,
        error: str | None,
    ) -> None:
        """One finished pass: a success when ``error`` is ``None``, a failure
        otherwise. Re-arms in the same write, so a ledger that lost the key
        (a flushed Redis) is repaired by the next pass rather than by the next
        restart."""
        ...

    async def attempt(self, task: str, *, at: float, cycle: int, cycle_attempts: int) -> None:
        """The scheduler is about to run the task: remember when, and which
        attempt of which cycle this is, so a restart neither repeats nor
        forgets it."""
        ...

    async def read(self, tasks: Iterable[str]) -> dict[str, TaskRecord]:
        """One record per name asked for -- an empty ``TaskRecord`` for a task
        nothing has written yet, never a missing key."""
        ...
