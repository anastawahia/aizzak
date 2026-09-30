"""Hermetic tests for the 5.7 scheduler (``app.ops.scheduler``).

Three layers, the cheapest first: the pure rules (``cycle_of``, ``decide``,
``gate_open``, ``child_env``, ``Policy.from_env``), then one ``Scheduler`` over
an in-memory ledger and a fake runner (order, the purge gate, a missing DSN,
retries, what lands in the ledger), then ``_run_subprocess`` against real,
tiny Python children (exit codes, stderr tail, timeout, shutdown). The round
trip through real Redis and a real tool under two different database roles is
``tests/integration/test_ops_scheduler_live.py``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

import pytest

from app.framework.observability.scheduled_tasks import (
    BACKUP_PRUNE_TASK,
    BACKUP_TASK,
    PURGE_TASK,
    RETENTION_TASK,
    ROTATE_TRANSIT_TASK,
    SCHEDULED_TASKS_BY_NAME,
    SCHEDULER_SERVICE,
)
from app.framework.ports.task_ledger import TaskRecord
from app.ops import scheduler as scheduler_module
from app.ops.scheduler import (
    DAY_S,
    DISABLED,
    DSN_VARIABLES,
    GAVE_UP,
    HOUR_S,
    JOBS,
    NOT_DUE,
    RETRY_LATER,
    RUN,
    ConfigError,
    Job,
    MissingDsnError,
    Policy,
    RunResult,
    Scheduler,
    child_env,
    cycle_of,
    decide,
    gate_open,
)

_POLICY = Policy(offset_s=2 * HOUR_S, retry_after_s=HOUR_S, max_attempts=3, tick_s=0.05)
# 2026-09-30 12:00:00 UTC -- inside the cycle that began at 02:00 that day.
_NOON = 1_790_769_600.0
_CYCLE_START = _NOON - 10 * HOUR_S


def _record(**fields: object) -> TaskRecord:
    return TaskRecord(task="t", **fields)  # type: ignore[arg-type]


# ------------------------------------------------------------ the pure rules --


def test_cycles_start_at_the_offset_not_at_midnight() -> None:
    assert cycle_of(_CYCLE_START, interval_s=DAY_S, offset_s=2 * HOUR_S) == cycle_of(
        _NOON, interval_s=DAY_S, offset_s=2 * HOUR_S
    )
    assert cycle_of(_CYCLE_START - 1, interval_s=DAY_S, offset_s=2 * HOUR_S) == (
        cycle_of(_NOON, interval_s=DAY_S, offset_s=2 * HOUR_S) - 1
    )


def test_a_task_armed_mid_cycle_waits_for_the_next_boundary() -> None:
    """A fresh deploy at noon must not start a two-hour backup in the middle
    of the working day: the first run is at the next 02:00."""
    record = _record(armed_at=_NOON - 60)
    assert decide(record, now=_NOON, interval_s=DAY_S, policy=_POLICY) == NOT_DUE
    tomorrow = _CYCLE_START + DAY_S + 1
    assert decide(record, now=tomorrow, interval_s=DAY_S, policy=_POLICY) == RUN


def test_a_task_that_succeeded_this_cycle_is_not_due_again_until_the_next() -> None:
    record = _record(armed_at=0.0, last_success_at=_CYCLE_START + 300)
    assert decide(record, now=_NOON, interval_s=DAY_S, policy=_POLICY) == NOT_DUE
    assert decide(record, now=_CYCLE_START + DAY_S, interval_s=DAY_S, policy=_POLICY) == RUN


def test_a_scheduler_that_was_down_for_days_runs_the_job_once_not_once_per_day() -> None:
    """Catch-up is ONE run: after it succeeds, the job is done for the cycle
    it is in, however many cycles were missed before it."""
    stale = _record(armed_at=0.0, last_success_at=_NOON - 5 * DAY_S)
    assert decide(stale, now=_NOON, interval_s=DAY_S, policy=_POLICY) == RUN
    caught_up = replace(stale, last_success_at=_NOON + 10)
    assert decide(caught_up, now=_NOON + 20, interval_s=DAY_S, policy=_POLICY) == NOT_DUE


def test_a_failed_attempt_is_retried_after_the_retry_window_and_at_most_three_times() -> None:
    current = cycle_of(_NOON, interval_s=DAY_S, offset_s=_POLICY.offset_s)
    failed = _record(
        armed_at=0.0,
        last_success_at=_NOON - DAY_S,
        last_attempt_at=_NOON,
        attempt_cycle=current,
        cycle_attempts=1,
    )
    assert decide(failed, now=_NOON + 60, interval_s=DAY_S, policy=_POLICY) == RETRY_LATER
    assert decide(failed, now=_NOON + HOUR_S, interval_s=DAY_S, policy=_POLICY) == RUN
    exhausted = replace(failed, cycle_attempts=3)
    assert decide(exhausted, now=_NOON + 5 * HOUR_S, interval_s=DAY_S, policy=_POLICY) == GAVE_UP
    # ...and the next cycle starts over.
    assert decide(exhausted, now=_CYCLE_START + DAY_S, interval_s=DAY_S, policy=_POLICY) == RUN


def test_an_attempt_left_over_from_an_earlier_cycle_does_not_count_against_this_one() -> None:
    current = cycle_of(_NOON, interval_s=DAY_S, offset_s=_POLICY.offset_s)
    record = _record(
        armed_at=0.0,
        last_success_at=_NOON - 3 * DAY_S,
        last_attempt_at=_NOON - DAY_S,
        attempt_cycle=current - 1,
        cycle_attempts=3,
    )
    assert decide(record, now=_NOON, interval_s=DAY_S, policy=_POLICY) == RUN


def test_a_cycle_of_zero_is_the_kill_switch_and_an_unarmed_task_never_starts() -> None:
    assert decide(_record(armed_at=0.0), now=_NOON, interval_s=0.0, policy=_POLICY) == DISABLED
    # Not armed means the ledger could not be written: nothing could record a
    # run, so none is started.
    assert decide(_record(), now=_NOON, interval_s=DAY_S, policy=_POLICY) == NOT_DUE


def test_the_gate_opens_only_on_a_success_in_the_required_tasks_current_cycle() -> None:
    fresh = _record(last_success_at=_CYCLE_START + 480)
    yesterday = _record(last_success_at=_CYCLE_START - 60)
    never = _record()
    assert gate_open(fresh, now=_NOON, interval_s=DAY_S, policy=_POLICY)
    assert not gate_open(yesterday, now=_NOON, interval_s=DAY_S, policy=_POLICY)
    assert not gate_open(never, now=_NOON, interval_s=DAY_S, policy=_POLICY)
    # A switched-off backup does not wave an irreversible purge through.
    assert not gate_open(fresh, now=_NOON, interval_s=0.0, policy=_POLICY)


def test_each_child_gets_its_own_dsn_and_none_of_the_others() -> None:
    environ = {
        "PATH": "/usr/bin",
        "VAULT_ADDR": "http://vault:8200",
        "DATABASE_URL": "postgresql+asyncpg://app@pgbouncer:6432/app",
        "RETENTION_DATABASE_URL": "dsn-retention",
        "WORKSPACE_PURGER_DATABASE_URL": "dsn-purger",
        "TRANSIT_ROTATOR_DATABASE_URL": "dsn-transit",
        "BACKUP_DATABASE_URL": "dsn-backup",
        "METRICS_DATABASE_URL": "dsn-metrics",
    }
    by_task = {job.task: job for job in JOBS}

    retention = child_env(by_task[RETENTION_TASK], environ)
    assert retention["DATABASE_URL"] == "dsn-retention"
    assert not {"dsn-purger", "dsn-transit", "dsn-backup", "dsn-metrics"} & set(retention.values())
    assert retention["VAULT_ADDR"] == "http://vault:8200"

    backup = child_env(by_task[BACKUP_TASK], environ)
    assert backup["BACKUP_DATABASE_URL"] == "dsn-backup"
    assert "DATABASE_URL" not in backup

    # `prune` opens no database connection, so it inherits none -- not even
    # the container's default DATABASE_URL.
    prune = child_env(by_task[BACKUP_PRUNE_TASK], environ)
    assert not DSN_VARIABLES & set(prune)


def test_a_job_whose_dsn_is_missing_is_refused_rather_than_run_with_another() -> None:
    job = next(job for job in JOBS if job.task == RETENTION_TASK)
    with pytest.raises(MissingDsnError, match="RETENTION_DATABASE_URL"):
        child_env(job, {"DATABASE_URL": "postgresql+asyncpg://someone-else"})


def test_every_scheduler_job_is_a_catalog_task_run_by_the_scheduler_service() -> None:
    for job in JOBS:
        assert SCHEDULED_TASKS_BY_NAME[job.task].runner == SCHEDULER_SERVICE
    scheduled = {t.name for t in SCHEDULED_TASKS_BY_NAME.values() if t.runner == SCHEDULER_SERVICE}
    assert scheduled == {job.task for job in JOBS}


def test_purge_comes_after_the_backup_it_is_gated_on() -> None:
    order = [job.task for job in JOBS]
    purge = next(job for job in JOBS if job.task == PURGE_TASK)
    assert purge.requires == BACKUP_TASK
    assert order.index(BACKUP_TASK) < order.index(PURGE_TASK)


def test_the_policy_refuses_nonsense_rather_than_scheduling_it() -> None:
    assert Policy.from_env({}) == Policy()
    with pytest.raises(ConfigError, match="OPS_SCHEDULE_OFFSET_S"):
        Policy.from_env({"OPS_SCHEDULE_OFFSET_S": "two"})
    with pytest.raises(ConfigError):
        Policy.from_env({"OPS_TICK_S": "0"})
    with pytest.raises(ConfigError, match="OPS_PURGE_INTERVAL_S"):
        Scheduler(_MemoryLedger(), environ={"OPS_PURGE_INTERVAL_S": "-1"})


# ------------------------------------------------ the loop, over fakes --


class _MemoryLedger:
    """A ``TaskLedger`` in a dict -- the Redis adapter's semantics (first
    ``armed_at`` kept, counters incremented), none of its I/O."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def _row(self, task: str) -> dict[str, object]:
        return self.rows.setdefault(task, {"successes": 0, "failures": 0, "cycle_attempts": 0})

    async def arm(self, task: str, *, interval_s: float, max_runtime_s: float, at: float) -> None:
        row = self._row(task)
        row.update(interval_s=interval_s, max_runtime_s=max_runtime_s)
        row.setdefault("armed_at", at)

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
        row = self._row(task)
        row.update(interval_s=interval_s, max_runtime_s=max_runtime_s)
        row.setdefault("armed_at", started_at)
        if error is None:
            row["last_success_at"] = finished_at
            row["successes"] = int(row["successes"]) + 1  # type: ignore[call-overload]
        else:
            row["last_failure_at"] = finished_at
            row["last_error"] = error
            row["failures"] = int(row["failures"]) + 1  # type: ignore[call-overload]

    async def attempt(self, task: str, *, at: float, cycle: int, cycle_attempts: int) -> None:
        self._row(task).update(
            last_attempt_at=at, attempt_cycle=cycle, cycle_attempts=cycle_attempts
        )

    async def read(self, tasks: Iterable[str]) -> dict[str, TaskRecord]:
        return {
            task: TaskRecord(task=task, **self.rows.get(task, {}))  # type: ignore[arg-type]
            for task in tasks
        }


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _FakeRunner:
    """Answers each job with a scripted exit code and remembers the argv and
    environment it was handed."""

    def __init__(self, codes: Mapping[str, int] | None = None) -> None:
        self.codes = dict(codes or {})
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(
        self, argv: Sequence[str], env: Mapping[str, str], timeout_s: float
    ) -> RunResult:
        command = " ".join(argv[2:])
        self.calls.append((command, dict(env)))
        code = self.codes.get(command, 0)
        return RunResult(returncode=code, stderr_tail=(f"{argv[2]} said no",) if code else ())


_ENV = {
    "RETENTION_DATABASE_URL": "dsn-retention",
    "WORKSPACE_PURGER_DATABASE_URL": "dsn-purger",
    "TRANSIT_ROTATOR_DATABASE_URL": "dsn-transit",
    "BACKUP_DATABASE_URL": "dsn-backup",
}


async def _armed_yesterday(ledger: _MemoryLedger, scheduler: Scheduler, clock: _Clock) -> None:
    """Arm every job a day ago, so they are all due now."""
    clock.now -= DAY_S
    await scheduler.arm_all()
    clock.now += DAY_S


async def test_a_due_night_runs_every_job_once_in_catalog_order() -> None:
    ledger, clock, runner = _MemoryLedger(), _Clock(_NOON), _FakeRunner()
    scheduler = Scheduler(ledger, environ=_ENV, policy=_POLICY, clock=clock, runner=runner)
    await _armed_yesterday(ledger, scheduler, clock)

    outcomes = await scheduler.tick()

    assert outcomes == [(job.task, "succeeded") for job in JOBS]
    assert [command for command, _ in runner.calls] == [" ".join(job.argv) for job in JOBS]
    # And nothing runs twice in the same cycle.
    assert await scheduler.tick() == []
    records = await ledger.read(job.task for job in JOBS)
    assert all(records[job.task].successes == 1 for job in JOBS)


async def test_a_failed_backup_refuses_the_purge_and_both_are_recorded_as_failures() -> None:
    """The runbook's «تأكّد من وجود نسخةٍ احتياطيّة» made mechanical: purge is
    irreversible, and a night whose backup failed does not purge -- loudly, on
    purge's own row, so the backup's failure shows up twice."""
    ledger, clock = _MemoryLedger(), _Clock(_NOON)
    runner = _FakeRunner({"app.ops.backup full": 2})
    scheduler = Scheduler(ledger, environ=_ENV, policy=_POLICY, clock=clock, runner=runner)
    await _armed_yesterday(ledger, scheduler, clock)

    outcomes = dict(await scheduler.tick())

    assert outcomes[BACKUP_TASK] == "failed"
    assert outcomes[PURGE_TASK] == "refused"
    assert not [command for command, _ in runner.calls if command.startswith("app.ops.purge")]
    records = await ledger.read([BACKUP_TASK, PURGE_TASK])
    assert records[BACKUP_TASK].last_error == "exit 2: app.ops.backup said no"
    assert records[PURGE_TASK].last_error is not None
    assert "backup has not succeeded" in records[PURGE_TASK].last_error
    assert records[PURGE_TASK].last_success_at is None


async def test_the_purge_runs_once_the_retried_backup_succeeds() -> None:
    ledger, clock = _MemoryLedger(), _Clock(_NOON)
    runner = _FakeRunner({"app.ops.backup full": 1})
    scheduler = Scheduler(ledger, environ=_ENV, policy=_POLICY, clock=clock, runner=runner)
    await _armed_yesterday(ledger, scheduler, clock)
    await scheduler.tick()

    runner.codes.clear()
    clock.now += HOUR_S
    outcomes = dict(await scheduler.tick())

    assert outcomes == {BACKUP_TASK: "succeeded", PURGE_TASK: "succeeded"}


async def test_a_missing_dsn_is_recorded_not_raised() -> None:
    ledger, clock, runner = _MemoryLedger(), _Clock(_NOON), _FakeRunner()
    environ = {k: v for k, v in _ENV.items() if k != "TRANSIT_ROTATOR_DATABASE_URL"}
    scheduler = Scheduler(ledger, environ=environ, policy=_POLICY, clock=clock, runner=runner)
    await _armed_yesterday(ledger, scheduler, clock)

    outcomes = dict(await scheduler.tick())

    assert outcomes[ROTATE_TRANSIT_TASK] == "refused"
    record = (await ledger.read([ROTATE_TRANSIT_TASK]))[ROTATE_TRANSIT_TASK]
    assert record.last_error is not None and "TRANSIT_ROTATOR_DATABASE_URL" in record.last_error


async def test_a_switched_off_job_is_armed_with_a_cycle_of_zero_and_never_run() -> None:
    ledger, clock, runner = _MemoryLedger(), _Clock(_NOON), _FakeRunner()
    scheduler = Scheduler(
        ledger,
        environ={**_ENV, "OPS_ROTATE_TRANSIT_INTERVAL_S": "0"},
        policy=_POLICY,
        clock=clock,
        runner=runner,
    )
    await _armed_yesterday(ledger, scheduler, clock)

    outcomes = dict(await scheduler.tick())

    assert ROTATE_TRANSIT_TASK not in outcomes
    record = (await ledger.read([ROTATE_TRANSIT_TASK]))[ROTATE_TRANSIT_TASK]
    assert record.interval_s == 0.0
    assert record.max_success_age_s is None


async def test_run_forever_beats_ticks_and_stops_on_request() -> None:
    ledger, clock = _MemoryLedger(), _Clock(_NOON)

    class _Beats:
        count = 0

        def beat(self) -> None:
            self.count += 1

    beats = _Beats()
    scheduler = Scheduler(
        ledger, environ=_ENV, policy=_POLICY, clock=clock, runner=_FakeRunner(), heartbeat=beats
    )
    task = asyncio.create_task(scheduler.run_forever())
    await asyncio.sleep(0.2)
    scheduler.request_stop()
    await asyncio.wait_for(task, timeout=2)
    assert beats.count >= 2
    assert set(ledger.rows) == {job.task for job in JOBS}


# ------------------------------------------------ real children --


def _python_job(code: str, *, timeout: float = 30.0) -> tuple[Sequence[str], float]:
    return (sys.executable, "-c", code), timeout


async def test_a_real_child_that_exits_zero_succeeds_and_its_output_is_forwarded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    scheduler = Scheduler(_MemoryLedger(), environ=_ENV, policy=_POLICY)
    argv, timeout = _python_job("print('swept 3 rows')")
    result = await scheduler._run_subprocess(argv, {}, timeout)
    assert result.succeeded
    assert "swept 3 rows" in capsys.readouterr().out


async def test_a_real_child_that_fails_leaves_its_stderr_tail_for_the_ledger() -> None:
    scheduler = Scheduler(_MemoryLedger(), environ=_ENV, policy=_POLICY)
    argv, timeout = _python_job(
        "import sys; print('first', file=sys.stderr); print('refused: wrong role', "
        "file=sys.stderr); sys.exit(2)"
    )
    result = await scheduler._run_subprocess(argv, {}, timeout)
    assert not result.succeeded
    assert result.returncode == 2
    assert result.describe(timeout_s=timeout) == "exit 2: first | refused: wrong role"


async def test_a_real_child_past_its_deadline_is_terminated_and_says_so() -> None:
    scheduler = Scheduler(_MemoryLedger(), environ=_ENV, policy=_POLICY)
    argv, _ = _python_job("import time; time.sleep(30)")
    result = await asyncio.wait_for(scheduler._run_subprocess(argv, {}, 0.3), timeout=10)
    assert result.timed_out and not result.succeeded
    assert result.describe(timeout_s=0.3) == "timed out after 0s and was terminated"


async def test_a_stop_request_interrupts_the_child_at_once_not_at_the_next_beat() -> None:
    """SIGTERM gives the container `stop_grace_period` (45 s); waiting for
    the next beat (`tick_s`, 30 s by default) and then 20 s of child grace
    would outlast it and lose the record."""
    policy = replace(_POLICY, tick_s=30.0)
    scheduler = Scheduler(_MemoryLedger(), environ=_ENV, policy=policy)
    argv, _ = _python_job("import time; time.sleep(30)")
    run = asyncio.create_task(scheduler._run_subprocess(argv, {}, 60.0))
    await asyncio.sleep(0.3)
    scheduler.request_stop()
    result = await asyncio.wait_for(run, timeout=10)
    assert result.interrupted and not result.succeeded


def test_a_line_longer_than_the_readers_limit_does_not_block_the_child() -> None:
    """``readline`` raises on a line over 64 KiB and would stop the copy,
    leaving the child blocked on a full pipe; the pump reads chunks."""

    async def run() -> RunResult:
        scheduler = Scheduler(_MemoryLedger(), environ=_ENV, policy=_POLICY)
        argv, timeout = _python_job(
            "import sys; sys.stderr.write('x' * 200_000 + '\\n'); sys.exit(3)"
        )
        return await scheduler._run_subprocess(argv, {}, timeout)

    result = asyncio.run(run())
    assert result.returncode == 3
    assert len(result.stderr_tail[-1]) == 200_000


def test_run_once_requires_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.scheduler", "run-once", "retention"])
    with pytest.raises(SystemExit, match="--yes"):
        scheduler_module.main()


def test_job_intervals_are_named_after_their_task() -> None:
    assert Job(RETENTION_TASK, ("x",), dsn_env=None).interval_env == "OPS_RETENTION_INTERVAL_S"
