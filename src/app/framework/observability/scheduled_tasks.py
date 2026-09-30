"""Every scheduled task this platform runs, named once -- and the one call a
periodic loop makes to say it finished a pass (capacity step 5.7).

**Why a catalog, when each runner already knows its own task.** The ledger
(``framework/ports/task_ledger.py``) can say when a task last succeeded; it
cannot say that a task it has never heard of SHOULD exist. A runner that was
never deployed writes nothing, and an absent record looks exactly like an
absent obligation. This table is the obligation: ``/metrics`` reports one
``aizzak_ops_task_expected`` series per row whether or not anything ever wrote
it, and ``AizzakOpsTaskNeverArmed`` fires on the difference. The
``HEARTBEAT_PROCESS_NAMES`` precedent (``heartbeat.py``): a name that exists on
only one side is a silent mismatch, so both sides resolve through here.

**What is in it, and why the plan's list was not copied verbatim.** 5.7 names
six tools -- ``retention``, ``purge``, ``dlq``, ``notify_groups`` daily,
``rotate_transit`` on a declared cycle, ``backup`` on its own. Two of them
were already automated, far more often than daily, before 5.7 began:

* the notify-group rule has run inside every ``app`` replica every
  ``NOTIFY_GROUP_SWEEP_INTERVAL_S`` (900 s) since ت-2 -- a daily run of the CLI
  would have been a second, slower runner of the same code;
* the DLQs are read by every worker every ``DLQ_WATCH_INTERVAL_S`` (300 s)
  since ت-6 (``consumers/dlq_watch.py``), and by every scrape since P1-3.

What neither had was the thing 5.7 actually asks for: a record that it
succeeded. So they are rows here with their EXISTING runners, and the DLQ
watch is one row per stream -- two ``worker-knowledge`` replicas both watch
``stream.knowledge``, and one ``dlq_watch`` row would let a dead
``worker-memory`` hide behind them. The 5.5 trimmer joins them because its own
status entry sends it here («قاعدةُ 5.7 … هي بيتُه»). The remaining four tools
had no runner at all, and get one: ``app.ops.scheduler`` in the
``ops-scheduler`` service.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.framework.observability.logging import get_logger
from app.framework.ports.task_ledger import TaskLedger, TaskRecord

_logger = get_logger(__name__)

# The runner of every task the 5.7 scheduler owns -- the Compose service name,
# because that is what an operator reads in `docker compose ps`.
SCHEDULER_SERVICE = "ops-scheduler"

STREAM_TRIM_TASK = "stream_trim"
NOTIFY_GROUPS_TASK = "notify_groups"
BACKUP_TASK = "backup"
BACKUP_PRUNE_TASK = "backup_prune"
RETENTION_TASK = "retention"
PURGE_TASK = "purge"
ROTATE_TRANSIT_TASK = "rotate_transit"

_DLQ_WATCH_PREFIX = "dlq_watch:"

# An error kept in the ledger is for a person reading `status` or a dashboard,
# not a log archive: the full traceback is in the runner's own log.
_MAX_ERROR_CHARS = 500


def dlq_watch_task(stream: str) -> str:
    """The ledger name of the DLQ watch over ``<stream>.dlq``."""
    return f"{_DLQ_WATCH_PREFIX}{stream}"


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """One row: the ledger key (also the ``task`` label), the Compose service
    whose process runs it, and one line of what it is for."""

    name: str
    runner: str
    purpose: str


def _worker_service(group: str) -> str:
    # `cg.knowledge` -> `worker-knowledge`: the Compose naming the three
    # consumers were split into (docs/log/3.133.md). A guard test checks that
    # every runner named in this table is a service that exists.
    return f"worker-{group.removeprefix('cg.')}"


SCHEDULED_TASKS: tuple[ScheduledTask, ...] = (
    ScheduledTask(
        STREAM_TRIM_TASK,
        "outbox-relay",
        "trim every published stream below its slowest reader (5.5)",
    ),
    ScheduledTask(
        NOTIFY_GROUPS_TASK,
        "app",
        "destroy cg.notify.* groups whose bridge is gone (ت-2, 5.4)",
    ),
    *(
        ScheduledTask(
            dlq_watch_task(binding.stream),
            _worker_service(binding.group),
            f"read {binding.stream}.dlq and report what is parked there (ت-6)",
        )
        for binding in STATIC_CONSUMER_TOPOLOGY
    ),
    # The four below run in catalog order inside one scheduler pass, and the
    # order is load-bearing: `purge` refuses to run unless `backup` succeeded
    # in the same cycle (`app.ops.scheduler`), so `backup` must come first.
    ScheduledTask(
        BACKUP_TASK,
        SCHEDULER_SERVICE,
        "base backup + logical dump + Qdrant snapshots + ship the WAL spool (2.5)",
    ),
    ScheduledTask(
        BACKUP_PRUNE_TASK,
        SCHEDULER_SERVICE,
        "apply the backup retention windows, WAL tied to the oldest base (2.5)",
    ),
    ScheduledTask(
        RETENTION_TASK,
        SCHEDULER_SERVICE,
        "age out outbox / processed_events / idempotency_keys / usage_records (2.8)",
    ),
    ScheduledTask(
        PURGE_TASK,
        SCHEDULER_SERVICE,
        "purge the content of workspaces deleted more than 30 days ago (BE-ADM-014)",
    ),
    ScheduledTask(
        ROTATE_TRANSIT_TASK,
        SCHEDULER_SERVICE,
        "rewrap every tenant ciphertext onto the Transit key's current version (P1-9)",
    ),
)

SCHEDULED_TASKS_BY_NAME: dict[str, ScheduledTask] = {task.name: task for task in SCHEDULED_TASKS}


# The states `task_state` reports -- the same partition the two alerts draw,
# so `python -m app.ops.scheduler status` and Prometheus cannot disagree about
# which task is late.
NEVER_ARMED = "never_armed"
DISABLED = "disabled"
OVERDUE = "overdue"
FAILING = "failing"
WAITING_FIRST_RUN = "waiting_first_run"
OK = "ok"


def task_state(record: TaskRecord, *, now: float) -> str:
    """One word for where a task stands, by the rule the alerts encode:

    * ``never_armed`` -- nothing has ever declared it (``AizzakOpsTaskNeverArmed``);
    * ``disabled`` -- its runner declared a cycle of 0;
    * ``overdue`` -- its last success, or its arming if it never succeeded, is
      older than two cycles plus one run (``AizzakOpsTaskOverdue``);
    * ``failing`` -- not late yet, but its latest pass failed;
    * ``waiting_first_run`` -- armed, not late, and nothing has run yet;
    * ``ok``.
    """
    if record.armed_at is None:
        return NEVER_ARMED
    deadline = record.max_success_age_s
    if deadline is None:
        return DISABLED
    since = record.last_success_at if record.last_success_at is not None else record.armed_at
    if now - since > deadline:
        return OVERDUE
    if record.last_failure_at is not None and (
        record.last_success_at is None or record.last_failure_at > record.last_success_at
    ):
        return FAILING
    if record.last_success_at is None:
        return WAITING_FIRST_RUN
    return OK


def describe_error(error: BaseException | str) -> str:
    """The one-line form a failure is kept in, bounded."""
    text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    text = " ".join(text.split())
    return text if len(text) <= _MAX_ERROR_CHARS else text[: _MAX_ERROR_CHARS - 1] + "…"


class TaskReport:
    """What a periodic loop calls around each pass. **Never raises.**

    The heartbeat's rule (``heartbeat.py``): an observability aid that can
    take the loop down is a net loss, so a ledger that cannot be written is
    logged on the first failure and on the recovery, and ignored in between
    -- the task itself keeps running. What it costs is visibility: a task
    whose reports cannot land will eventually read as overdue, which is the
    correct direction for that error to fail in.

    ``ledger=None`` is the no-op form, for a loop built without Redis (a unit
    test, a direct ``python -m`` run) -- the ``NullHeartbeat`` precedent, so
    call sites never branch on it.
    """

    def __init__(
        self,
        ledger: TaskLedger | None,
        task: str,
        *,
        interval_s: float,
        max_runtime_s: float = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if task not in SCHEDULED_TASKS_BY_NAME:
            # A name outside the catalog would be written and never read:
            # `/metrics` reports the catalog, not whatever keys exist.
            raise ValueError(f"{task!r} is not in SCHEDULED_TASKS")
        self._ledger = ledger
        self._task = task
        self._interval_s = interval_s
        self._max_runtime_s = max_runtime_s
        self._clock = clock
        self._failing = False

    @property
    def task(self) -> str:
        return self._task

    def now(self) -> float:
        return self._clock()

    async def arm(self) -> None:
        if self._ledger is None:
            return
        try:
            await self._ledger.arm(
                self._task,
                interval_s=self._interval_s,
                max_runtime_s=self._max_runtime_s,
                at=self._clock(),
            )
        except Exception:
            self._write_failed()
        else:
            self._write_ok()

    async def succeeded(self, *, started_at: float) -> None:
        await self._record(started_at=started_at, error=None)

    async def failed(self, *, started_at: float, error: BaseException | str) -> None:
        await self._record(started_at=started_at, error=describe_error(error))

    async def _record(self, *, started_at: float, error: str | None) -> None:
        if self._ledger is None:
            return
        try:
            await self._ledger.record(
                self._task,
                interval_s=self._interval_s,
                max_runtime_s=self._max_runtime_s,
                started_at=started_at,
                finished_at=self._clock(),
                error=error,
            )
        except Exception:
            self._write_failed()
        else:
            self._write_ok()

    def _write_failed(self) -> None:
        if not self._failing:
            self._failing = True
            _logger.warning(
                "scheduled_task.ledger_write_failed", extra={"task": self._task}, exc_info=True
            )

    def _write_ok(self) -> None:
        if self._failing:
            self._failing = False
            _logger.info("scheduled_task.ledger_write_recovered", extra={"task": self._task})
