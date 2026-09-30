"""The runner the ops tools never had (capacity step 5.7).

    docker compose up -d ops-scheduler                               # the standing loop
    docker compose exec ops-scheduler python -m app.ops.scheduler status
    docker compose exec ops-scheduler python -m app.ops.scheduler run-once retention --yes

**What 5.7 found before it scheduled anything.** «``src/app/ops/`` فيه تسعُ
أدواتٍ لا يشغّلها شيء». Of the six the step names, two had been running for
weeks -- the notify-group rule inside every ``app`` replica, the DLQ watch
inside every worker (``framework/observability/scheduled_tasks.py`` says why
they stay where they are). Four had never run unattended once: ``backup``
(the newest base backup on the live stack was **624 hours** old on
2026-09-30, and ``status`` still read ``pitr_possible: true`` -- true, with
26 days of WAL to replay), ``retention`` (**6,943** ``idempotency_keys`` past
their two-day window), ``purge`` and ``rotate_transit`` (the Transit key at
version 1 for 66 days). This module is their runner.

**One process, a subprocess per job, one role per subprocess.** Each job runs
as ``python -m <tool> ...`` -- the exact command the runbook gives an operator,
so a scheduled run and a manual one cannot be two different programs. The
container holds every job's DSN (``RETENTION_DATABASE_URL``,
``WORKSPACE_PURGER_DATABASE_URL``, ``TRANSIT_ROTATOR_DATABASE_URL``,
``BACKUP_DATABASE_URL``); each child is handed ITS OWN under the name the tool
reads and none of the others (``child_env``), so "one role per process" holds
for every process that opens a database connection. And since 5.7 the three
cross-tenant tools verify that role themselves (``app.ops.role_guard``): a DSN
wired to the wrong variable is an exit code, not a nightly empty success.

**Jobs run one at a time, in catalog order, and the order is a rule.**
``backup`` first, because ``purge`` refuses to run unless a backup succeeded
in the current cycle (``Job.requires``). 08 §4.6 gave that as a human step --
«قبل أيّ تشغيلٍ فعليّ: تأكّد من وجود نسخةٍ احتياطيّة» -- and scheduling purge
removes the human, so the precondition had to become code or disappear. A
refused purge is recorded as a FAILURE: a backup that keeps failing surfaces
twice, on its own row and on the purge it is holding back.

**When a job is due -- cycles, not "24 hours since the last run".** A job's
cycle is ``interval`` wide, anchored at ``OPS_SCHEDULE_OFFSET_S`` past
midnight UTC (default 02:00), and a job is due once per cycle it has not yet
succeeded in. So runs do not drift later by their own duration every day, a
scheduler that was down at 02:00 runs the job once when it returns (not once
per missed day), and a task armed mid-cycle waits for the next boundary
rather than firing the moment a container starts. A failed attempt is retried
after ``OPS_RETRY_AFTER_S``, at most ``OPS_MAX_ATTEMPTS_PER_CYCLE`` times per
cycle -- a transient Qdrant restart costs an hour, and a broken configuration
costs three runs a day rather than forty-eight. All of that state lives in the
task ledger (``TaskRecord.attempt_cycle``/``cycle_attempts``), so a restart
neither repeats a job nor forgets it.

**What "success" means here is the tool's exit code, and that is why 5.7
changed three tools.** A scheduler can only be as honest as the exit codes it
reads, and on 2026-09-30 three of them could report success having done
nothing: retention and purge under the wrong role (``role_guard``), and a
rewrap sweep next to a key that never rotates (``rotate_transit``'s cycle
check, exit 3).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import os
import signal
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from app.framework.observability import Heartbeat, NullHeartbeat, build_heartbeat, get_logger
from app.framework.observability.logging import configure_logging
from app.framework.observability.scheduled_tasks import (
    BACKUP_PRUNE_TASK,
    BACKUP_TASK,
    NEVER_ARMED,
    OK,
    OVERDUE,
    PURGE_TASK,
    RETENTION_TASK,
    ROTATE_TRANSIT_TASK,
    SCHEDULED_TASKS,
    SCHEDULER_SERVICE,
    describe_error,
    task_state,
)
from app.framework.ports.task_ledger import TaskLedger, TaskRecord
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.monitoring.task_ledger import RedisTaskLedger

_logger = get_logger(__name__)

DAY_S = 86_400.0
HOUR_S = 3_600.0
MINUTE_S = 60.0

# How long a child gets between SIGTERM and SIGKILL -- inside the service's
# `stop_grace_period` (45 s), so a shutdown mid-job still records the job.
_TERMINATE_GRACE_S = 20.0
# Lines of a child's stderr kept for the ledger's `last_error`.
_TAIL_LINES = 20


@dataclass(frozen=True, slots=True)
class Job:
    """One scheduled tool run.

    * ``argv`` -- what follows ``python -m``: the module and its arguments,
      exactly as the runbook gives them to an operator.
    * ``dsn_env`` -- the variable in THIS container holding the job's own DSN;
      ``None`` for a job that opens no database connection.
    * ``dsn_target`` -- the name the tool reads it under.
    * ``max_runtime_s`` -- killed past this, and the allowance the overdue
      alert adds to two cycles.
    * ``requires`` -- a task that must have succeeded in its current cycle
      before this one may run.
    """

    task: str
    argv: tuple[str, ...]
    dsn_env: str | None
    dsn_target: str = "DATABASE_URL"
    max_runtime_s: float = HOUR_S
    requires: str | None = None

    @property
    def interval_env(self) -> str:
        return f"OPS_{self.task.upper()}_INTERVAL_S"


JOBS: tuple[Job, ...] = (
    # 2.5 measured `full` on the seeded stack at ~8 min (base 54 s, dump
    # 144 s, 202 Qdrant snapshots 262 s); two hours is room for a corpus
    # several times that on a slower disk, not a target.
    Job(
        BACKUP_TASK,
        ("app.ops.backup", "full"),
        dsn_env="BACKUP_DATABASE_URL",
        dsn_target="BACKUP_DATABASE_URL",
        max_runtime_s=2 * HOUR_S,
    ),
    # MinIO only -- `prune` never opens a database connection, so it is given
    # no DSN at all (`BackupConfig.require_database`).
    Job(BACKUP_PRUNE_TASK, ("app.ops.backup", "prune"), dsn_env=None, max_runtime_s=HOUR_S / 2),
    Job(
        RETENTION_TASK,
        ("app.ops.retention", "sweep"),
        dsn_env="RETENTION_DATABASE_URL",
        max_runtime_s=HOUR_S / 2,
    ),
    Job(
        PURGE_TASK,
        ("app.ops.purge", "run", "--yes"),
        dsn_env="WORKSPACE_PURGER_DATABASE_URL",
        max_runtime_s=HOUR_S,
        requires=BACKUP_TASK,
    ),
    Job(
        ROTATE_TRANSIT_TASK,
        ("app.ops.rotate_transit", "sweep"),
        dsn_env="TRANSIT_ROTATOR_DATABASE_URL",
        max_runtime_s=HOUR_S / 2,
    ),
)

JOBS_BY_TASK: dict[str, Job] = {job.task: job for job in JOBS}

# Every DSN variable a child could inherit from this container. Each child
# gets none of them except its own (`child_env`) -- and `DATABASE_URL` is in
# the set so a job that needs no database cannot fall back on one either.
DSN_VARIABLES = frozenset(
    {
        "DATABASE_URL",
        "METRICS_DATABASE_URL",
        *(job.dsn_env for job in JOBS if job.dsn_env is not None),
        *(job.dsn_target for job in JOBS),
    }
)

# The scheduler's defaults (`Policy`). 02:00 UTC is 05:00 on the operator's
# own clock (+03) and the quietest hour the load runs have seen; an hour
# between retries outlasts a container restart or a Qdrant reload; three
# attempts a cycle bounds what a broken configuration costs.
_DEFAULT_OFFSET_S = 2 * HOUR_S
_DEFAULT_RETRY_AFTER_S = HOUR_S
_DEFAULT_MAX_ATTEMPTS = 3
# Also the heartbeat's period -- far inside `HEARTBEAT_MAX_AGE_S` (300 s).
_DEFAULT_TICK_S = 30.0

# Decisions `decide` returns.
NOT_DUE = "not_due"
RUN = "run"
RETRY_LATER = "retry_later"
GAVE_UP = "gave_up"
DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Policy:
    """The scheduler's own knobs, all from the environment
    (``Policy.from_env``) -- the ``BackupConfig`` precedent: an operations
    runner's cadence is not part of the platform's settings contract."""

    offset_s: float = _DEFAULT_OFFSET_S
    retry_after_s: float = _DEFAULT_RETRY_AFTER_S
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    tick_s: float = _DEFAULT_TICK_S

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Policy:
        policy = cls(
            offset_s=_seconds(environ, "OPS_SCHEDULE_OFFSET_S", _DEFAULT_OFFSET_S),
            retry_after_s=_seconds(environ, "OPS_RETRY_AFTER_S", _DEFAULT_RETRY_AFTER_S),
            max_attempts=int(
                _seconds(environ, "OPS_MAX_ATTEMPTS_PER_CYCLE", _DEFAULT_MAX_ATTEMPTS)
            ),
            tick_s=_seconds(environ, "OPS_TICK_S", _DEFAULT_TICK_S),
        )
        if policy.tick_s <= 0 or policy.max_attempts < 1:
            raise ConfigError("OPS_TICK_S must be > 0 and OPS_MAX_ATTEMPTS_PER_CYCLE >= 1")
        return policy


def _seconds(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not a number") from exc
    if value < 0 or math.isnan(value) or math.isinf(value):
        raise ConfigError(f"{name}={raw!r} must be a finite number >= 0")
    return value


class ConfigError(RuntimeError):
    """A setting this scheduler cannot run with -- refused at startup."""


def interval_of(job: Job, environ: Mapping[str, str]) -> float:
    """The job's cycle: ``OPS_<TASK>_INTERVAL_S``, a day by default, ``0``
    to switch it off (the ``م-8`` kill switch -- the task is then armed with
    a cycle of 0 and no alert expects it)."""
    return _seconds(environ, job.interval_env, DAY_S)


def cycle_of(at: float, *, interval_s: float, offset_s: float) -> int:
    """Which cycle ``at`` falls in: cycles are ``interval_s`` wide and start
    ``offset_s`` past the Unix epoch (so, for a day, at that time UTC)."""
    return math.floor((at - offset_s) / interval_s)


def decide(record: TaskRecord, *, now: float, interval_s: float, policy: Policy) -> str:
    """Whether to run a job now -- pure, so every branch is a unit test.

    Due once per cycle not yet succeeded in, counted from the last success or,
    for a task that never succeeded, from its arming: a task armed mid-cycle
    first runs at the next boundary."""
    if interval_s <= 0:
        return DISABLED
    anchor = record.last_success_at if record.last_success_at is not None else record.armed_at
    current = cycle_of(now, interval_s=interval_s, offset_s=policy.offset_s)
    # `anchor is None`: not armed -- the ledger could not be written, and
    # nothing could record the run either, so do not start one.
    if (
        anchor is None
        or cycle_of(anchor, interval_s=interval_s, offset_s=policy.offset_s) >= current
    ):
        return NOT_DUE
    if record.attempt_cycle != current or record.last_attempt_at is None:
        return RUN
    if record.cycle_attempts >= policy.max_attempts:
        return GAVE_UP
    return RUN if now - record.last_attempt_at >= policy.retry_after_s else RETRY_LATER


def gate_open(required: TaskRecord, *, now: float, interval_s: float, policy: Policy) -> bool:
    """Has ``required`` succeeded in ITS current cycle? Closed when it is
    switched off: an irreversible job does not run without the backup it is
    gated on, and "the backup is disabled" is not an exception to that."""
    if interval_s <= 0 or required.last_success_at is None:
        return False
    return cycle_of(
        required.last_success_at, interval_s=interval_s, offset_s=policy.offset_s
    ) >= cycle_of(now, interval_s=interval_s, offset_s=policy.offset_s)


class MissingDsnError(RuntimeError):
    def __init__(self, job: Job) -> None:
        super().__init__(
            f"{job.dsn_env} is not set in the ops-scheduler environment -- {job.task} runs "
            f"as its own role and is refused rather than started with another one's DSN"
        )


def child_env(job: Job, environ: Mapping[str, str]) -> dict[str, str]:
    """The environment one job runs with: everything this container has,
    minus every DSN, plus the job's own under the name its tool reads."""
    env = {key: value for key, value in environ.items() if key not in DSN_VARIABLES}
    if job.dsn_env is not None:
        dsn = environ.get(job.dsn_env, "")
        if not dsn:
            raise MissingDsnError(job)
        env[job.dsn_target] = dsn
    return env


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int | None
    stderr_tail: tuple[str, ...]
    timed_out: bool = False
    interrupted: bool = False

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.interrupted

    def describe(self, *, timeout_s: float) -> str:
        if self.interrupted:
            return "interrupted by scheduler shutdown"
        if self.timed_out:
            return f"timed out after {timeout_s:.0f}s and was terminated"
        tail = " | ".join(self.stderr_tail[-5:])
        return f"exit {self.returncode}" + (f": {tail}" if tail else "")


Runner = Callable[[Sequence[str], Mapping[str, str], float], Awaitable[RunResult]]


class Scheduler:
    """The loop: every ``tick_s``, beat, arm every job, read the ledger, run
    whatever is due, one at a time. Stops between jobs on request; a job in
    flight is sent SIGTERM and recorded as interrupted."""

    def __init__(
        self,
        ledger: TaskLedger,
        *,
        jobs: Sequence[Job] = JOBS,
        environ: Mapping[str, str] | None = None,
        policy: Policy | None = None,
        heartbeat: Heartbeat | None = None,
        clock: Callable[[], float] = time.time,
        runner: Runner | None = None,
        python: str = sys.executable,
    ) -> None:
        self._ledger = ledger
        self._jobs = tuple(jobs)
        self._environ = dict(os.environ if environ is None else environ)
        self._policy = policy or Policy.from_env(self._environ)
        self._heartbeat: Heartbeat = heartbeat or NullHeartbeat()
        self._clock = clock
        self._python = python
        self._stop = asyncio.Event()
        self._runner: Runner = runner or self._run_subprocess
        # Validated up front: a typo in an interval is a refusal to start,
        # not a job that silently never runs.
        self._intervals = {job.task: interval_of(job, self._environ) for job in self._jobs}

    def request_stop(self) -> None:
        self._stop.set()

    async def arm_all(self) -> None:
        now = self._clock()
        for job in self._jobs:
            await self._ledger.arm(
                job.task,
                interval_s=self._intervals[job.task],
                max_runtime_s=job.max_runtime_s,
                at=now,
            )

    async def tick(self) -> list[tuple[str, str]]:
        """One pass: re-arm (cheap, and it restores a ledger a flushed Redis
        lost), then run every due job in order. Returns ``(task, outcome)``
        for each job it started or refused."""
        await self.arm_all()
        outcomes: list[tuple[str, str]] = []
        for job in self._jobs:
            if self._stop.is_set():
                break
            # Re-read before every job: `purge`'s gate must see the backup
            # that finished a moment ago.
            records = await self._ledger.read(j.task for j in self._jobs)
            decision = decide(
                records[job.task],
                now=self._clock(),
                interval_s=self._intervals[job.task],
                policy=self._policy,
            )
            if decision != RUN:
                continue
            outcomes.append((job.task, await self.run_job(job, records)))
        return outcomes

    async def run_job(self, job: Job, records: Mapping[str, TaskRecord]) -> str:
        """Attempt ``job`` once, whatever the schedule says, and record the
        outcome: ``succeeded``, ``failed`` or ``refused`` (its gate, or its
        DSN). ``run-once`` calls this directly."""
        interval = self._intervals[job.task]
        started_at = self._clock()
        record = records.get(job.task, TaskRecord(task=job.task))
        cycle = cycle_of(started_at, interval_s=interval or DAY_S, offset_s=self._policy.offset_s)
        attempts = record.cycle_attempts + 1 if record.attempt_cycle == cycle else 1
        await self._ledger.attempt(job.task, at=started_at, cycle=cycle, cycle_attempts=attempts)

        refusal: str | None = None
        env: dict[str, str] = {}
        if job.requires is not None and not gate_open(
            records.get(job.requires, TaskRecord(task=job.requires)),
            now=started_at,
            interval_s=self._intervals.get(job.requires, 0.0),
            policy=self._policy,
        ):
            refusal = (
                f"refused: {job.requires} has not succeeded in its current cycle, and "
                f"{job.task} is irreversible -- it runs only after a backup it could be "
                "restored from"
            )
        else:
            try:
                env = child_env(job, self._environ)
            except MissingDsnError as exc:
                refusal = str(exc)
        if refusal is not None:
            _logger.error("ops_scheduler.job_refused", extra={"task": job.task, "reason": refusal})
            await self._record(job, started_at=started_at, error=refusal)
            return "refused"

        _logger.info(
            "ops_scheduler.job_started",
            extra={"task": job.task, "argv": list(job.argv), "cycle": cycle, "attempt": attempts},
        )
        result = await self._runner((self._python, "-m", *job.argv), env, job.max_runtime_s)
        if result.succeeded:
            await self._record(job, started_at=started_at, error=None)
            _logger.info(
                "ops_scheduler.job_succeeded",
                extra={"task": job.task, "duration_s": round(self._clock() - started_at, 1)},
            )
            return "succeeded"
        error = result.describe(timeout_s=job.max_runtime_s)
        await self._record(job, started_at=started_at, error=error)
        _logger.error(
            "ops_scheduler.job_failed",
            extra={"task": job.task, "error": error, "attempt": attempts},
        )
        return "failed"

    async def _record(self, job: Job, *, started_at: float, error: str | None) -> None:
        await self._ledger.record(
            job.task,
            interval_s=self._intervals[job.task],
            max_runtime_s=job.max_runtime_s,
            started_at=started_at,
            finished_at=self._clock(),
            error=None if error is None else describe_error(error),
        )

    async def run_forever(self) -> None:
        """Tick until asked to stop. A tick that cannot reach the ledger is
        logged and retried next tick -- without the ledger nothing can be
        decided or recorded, so nothing is run."""
        failing = False
        while not self._stop.is_set():
            self._heartbeat.beat()
            try:
                await self.tick()
            except Exception:
                if not failing:
                    _logger.error("ops_scheduler.tick_failed", exc_info=True)
                failing = True
            else:
                if failing:
                    _logger.info("ops_scheduler.tick_recovered")
                failing = False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._policy.tick_s)

    async def _run_subprocess(
        self, argv: Sequence[str], env: Mapping[str, str], timeout_s: float
    ) -> RunResult:
        """Run one tool, forwarding its output to this container's own
        streams (so the tool's lines reach Loki under ``ops-scheduler``),
        beating while it runs, and keeping its stderr tail for the ledger."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("the child's output pipes were not created")
        tail: deque[str] = deque(maxlen=_TAIL_LINES)
        pumps = [
            asyncio.create_task(_pump(proc.stdout, sys.stdout, None)),
            asyncio.create_task(_pump(proc.stderr, sys.stderr, tail)),
        ]
        waiter = asyncio.create_task(proc.wait())
        # Woken by the stop request itself, not by the next beat: SIGTERM has
        # `stop_grace_period` (45 s) to turn into a recorded interruption.
        stopping = asyncio.create_task(self._stop.wait())
        deadline = time.monotonic() + timeout_s
        timed_out = interrupted = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                else:
                    done, _ = await asyncio.wait(
                        {waiter, stopping},
                        timeout=min(self._policy.tick_s, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if waiter in done:
                        break
                    self._heartbeat.beat()
                    interrupted = stopping in done
                if interrupted or timed_out:
                    await _terminate(proc, waiter)
                    break
        finally:
            stopping.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        return RunResult(
            returncode=proc.returncode,
            stderr_tail=tuple(tail),
            timed_out=timed_out,
            interrupted=interrupted,
        )


async def _pump(stream: asyncio.StreamReader, sink: TextIO, tail: deque[str] | None) -> None:
    """Copy a child's stream to ours line by line, in chunks rather than
    ``readline`` -- a line longer than the reader's limit would otherwise
    raise, stop the copy, and leave the child blocked on a full pipe."""
    pending = b""
    while chunk := await stream.read(65_536):
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for raw in lines:
            _emit(raw, sink, tail)
    if pending:
        _emit(pending, sink, tail)


def _emit(raw: bytes, sink: TextIO, tail: deque[str] | None) -> None:
    line = raw.decode("utf-8", "replace").rstrip("\r")
    sink.write(line + "\n")
    sink.flush()
    if tail is not None and line.strip():
        tail.append(line.strip())


async def _terminate(proc: asyncio.subprocess.Process, waiter: asyncio.Task[int]) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    done, _ = await asyncio.wait({waiter}, timeout=_TERMINATE_GRACE_S)
    if not done:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await waiter


# ------------------------------------------------------------------- cli --


def _span(seconds: float) -> str:
    if seconds < 2 * MINUTE_S:
        return f"{seconds:.0f}s"
    if seconds < 2 * HOUR_S:
        return f"{seconds / MINUTE_S:.0f}m"
    if seconds < 2 * DAY_S:
        return f"{seconds / HOUR_S:.3g}h"
    return f"{seconds / DAY_S:.3g}d"


def _age(now: float, at: float | None) -> str:
    return "never" if at is None else f"{_span(max(0.0, now - at))} ago"


def print_status(records: Mapping[str, TaskRecord], *, now: float) -> int:
    """Every catalog task, one line each; exit 1 when any is overdue or was
    never armed -- the two states an alert fires on."""
    worst = 0
    for task in SCHEDULED_TASKS:
        record = records.get(task.name, TaskRecord(task=task.name))
        state = task_state(record, now=now)
        if state in (OVERDUE, NEVER_ARMED):
            worst = 1
        interval = "-" if record.interval_s is None else _span(record.interval_s)
        print(
            f"{state:<17} {task.name:<28} {task.runner:<17} every {interval:<7} "
            f"last success {_age(now, record.last_success_at):<12} "
            f"ok {record.successes} / failed {record.failures}"
        )
        if record.last_error and state != OK:
            print(f"{'':<17} last error: {record.last_error}")
    return worst


async def _status() -> int:
    client = create_redis_client(load_settings().redis)
    try:
        records = await RedisTaskLedger(client).read(task.name for task in SCHEDULED_TASKS)
    finally:
        await client.aclose()
    return print_status(records, now=time.time())


async def _run_once(task: str) -> int:
    client = create_redis_client(load_settings().redis)
    try:
        ledger = RedisTaskLedger(client)
        scheduler = Scheduler(ledger)
        await scheduler.arm_all()
        records = await ledger.read(job.task for job in JOBS)
        outcome = await scheduler.run_job(JOBS_BY_TASK[task], records)
    finally:
        await client.aclose()
    print(f"{task}: {outcome}")
    return 0 if outcome == "succeeded" else 1


async def _run() -> int:
    settings = load_settings()
    client = create_redis_client(settings.redis)
    scheduler = Scheduler(
        RedisTaskLedger(client),
        heartbeat=build_heartbeat(settings.health.heartbeat_dir, SCHEDULER_SERVICE),
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, scheduler.request_stop)
    _logger.info(
        "ops_scheduler.started",
        extra={
            "jobs": {job.task: interval_of(job, os.environ) for job in JOBS},
            "offset_s": Policy.from_env(os.environ).offset_s,
        },
    )
    try:
        await scheduler.run_forever()
    finally:
        await client.aclose()
    _logger.info("ops_scheduler.stopped")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.scheduler",
        description="Run the ops tools on their cycles and record every run (capacity 5.7).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("run", help="the standing loop (the ops-scheduler service's command)")
    sub.add_parser(
        "status", help="every scheduled task's last success and state; exit 1 if any is late"
    )
    once = sub.add_parser(
        "run-once", help="run ONE scheduler job now, through the same gate and ledger"
    )
    once.add_argument("task", choices=sorted(JOBS_BY_TASK))
    once.add_argument(
        "--yes",
        action="store_true",
        help="required -- this runs the real tool as its real role (purge deletes, "
        "retention deletes, rotate_transit rewrites ciphertext)",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    configure_logging(load_settings().log_level)
    if args.action == "run-once" and not args.yes:
        raise SystemExit(
            "run-once refused: pass --yes -- it runs the real job with its real role "
            "and records the outcome in the task ledger."
        )
    try:
        if args.action == "run":
            code = asyncio.run(_run())
        elif args.action == "status":
            code = asyncio.run(_status())
        else:
            code = asyncio.run(_run_once(args.task))
    except ConfigError as exc:
        print(f"ops-scheduler: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
