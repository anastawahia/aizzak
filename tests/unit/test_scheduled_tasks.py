"""The 5.7 task catalog and the one call every periodic loop makes
(``framework/observability/scheduled_tasks.py``).

The catalog is the obligation the ledger cannot express -- a task nothing
ever wrote looks exactly like no task at all -- so it is checked against the
two files it names: every runner is a Compose service, and every stream a
worker consumes has a DLQ-watch row of its own. ``TaskReport`` is checked for
the heartbeat's promise: it can lose visibility, never the loop.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.framework.observability.scheduled_tasks import (
    DISABLED,
    FAILING,
    NEVER_ARMED,
    OK,
    OVERDUE,
    SCHEDULED_TASKS,
    SCHEDULED_TASKS_BY_NAME,
    STREAM_TRIM_TASK,
    WAITING_FIRST_RUN,
    TaskReport,
    describe_error,
    dlq_watch_task,
    task_state,
)
from app.framework.ports.task_ledger import TaskRecord

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_every_task_is_named_once() -> None:
    names = [task.name for task in SCHEDULED_TASKS]
    assert len(names) == len(set(names))
    assert set(SCHEDULED_TASKS_BY_NAME) == set(names)


def test_every_runner_is_a_compose_service() -> None:
    """A runner that is not a service is a task nobody can run -- and the
    never-armed alert would fire on it forever with no service to start."""
    services = set(yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"])
    missing = {task.name: task.runner for task in SCHEDULED_TASKS if task.runner not in services}
    assert not missing, missing


def test_every_consumed_stream_has_a_dlq_watch_row_of_its_own() -> None:
    """Per stream, not per worker: two `worker-knowledge` replicas both watch
    `stream.knowledge`, and one row for the whole watch would let a dead
    `worker-memory` hide behind them."""
    watched = {name for name in SCHEDULED_TASKS_BY_NAME if name.startswith("dlq_watch:")}
    assert watched == {dlq_watch_task(b.stream) for b in STATIC_CONSUMER_TOPOLOGY}


def test_a_deadline_is_two_cycles_plus_one_run_and_none_when_switched_off() -> None:
    assert TaskRecord(task="t", interval_s=60.0).max_success_age_s == 120.0
    assert (
        TaskRecord(task="t", interval_s=86_400.0, max_runtime_s=7_200.0).max_success_age_s
        == 180_000.0
    )
    assert TaskRecord(task="t", interval_s=0.0).max_success_age_s is None
    assert TaskRecord(task="t").max_success_age_s is None


@pytest.mark.parametrize(
    ("record", "now", "state"),
    [
        (TaskRecord(task="t"), 1_000.0, NEVER_ARMED),
        (TaskRecord(task="t", interval_s=0.0, armed_at=0.0), 1e9, DISABLED),
        (TaskRecord(task="t", interval_s=60.0, armed_at=0.0), 60.0, WAITING_FIRST_RUN),
        (TaskRecord(task="t", interval_s=60.0, armed_at=0.0), 121.0, OVERDUE),
        (
            TaskRecord(task="t", interval_s=60.0, armed_at=0.0, last_success_at=100.0),
            150.0,
            OK,
        ),
        (
            TaskRecord(
                task="t",
                interval_s=60.0,
                armed_at=0.0,
                last_success_at=100.0,
                last_failure_at=140.0,
            ),
            150.0,
            FAILING,
        ),
        (
            TaskRecord(task="t", interval_s=60.0, armed_at=0.0, last_failure_at=10.0),
            30.0,
            FAILING,
        ),
        (
            TaskRecord(
                task="t",
                interval_s=60.0,
                armed_at=0.0,
                last_success_at=100.0,
                last_failure_at=140.0,
            ),
            221.0,
            OVERDUE,
        ),
    ],
)
def test_task_state_draws_the_line_the_two_alerts_draw(
    record: TaskRecord, now: float, state: str
) -> None:
    assert task_state(record, now=now) == state


def test_an_error_is_kept_on_one_bounded_line() -> None:
    text = describe_error(ValueError("line one\n   line two"))
    assert text == "ValueError: line one line two"
    assert len(describe_error("x" * 5_000)) == 500


class _RecordingLedger:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def arm(self, task: str, **kwargs: object) -> None:
        if self.fail:
            raise ConnectionError("redis-stream is down")
        self.calls.append(("arm", {"task": task, **kwargs}))

    async def record(self, task: str, **kwargs: object) -> None:
        if self.fail:
            raise ConnectionError("redis-stream is down")
        self.calls.append(("record", {"task": task, **kwargs}))

    async def attempt(self, task: str, **kwargs: object) -> None:
        raise AssertionError("an in-process loop never books attempts")

    async def read(self, tasks: Iterable[str]) -> dict[str, TaskRecord]:
        raise AssertionError("a reporter never reads")


def test_a_task_outside_the_catalog_is_refused_at_construction() -> None:
    """A name outside the catalog would be written and never read: `/metrics`
    reports the catalog, not whatever keys happen to exist."""
    with pytest.raises(ValueError, match="SCHEDULED_TASKS"):
        TaskReport(None, "stream_trimm", interval_s=60.0)


async def test_a_report_writes_the_cycle_and_the_outcome() -> None:
    ledger = _RecordingLedger()
    clock = iter([100.0, 107.0, 200.0, 201.0])
    report = TaskReport(
        ledger,  # type: ignore[arg-type]
        STREAM_TRIM_TASK,
        interval_s=60.0,
        max_runtime_s=5.0,
        clock=lambda: next(clock),
    )
    await report.arm()
    await report.succeeded(started_at=100.0)
    await report.failed(started_at=200.0, error=ConnectionError("gone"))

    assert ledger.calls == [
        ("arm", {"task": "stream_trim", "interval_s": 60.0, "max_runtime_s": 5.0, "at": 100.0}),
        (
            "record",
            {
                "task": "stream_trim",
                "interval_s": 60.0,
                "max_runtime_s": 5.0,
                "started_at": 100.0,
                "finished_at": 107.0,
                "error": None,
            },
        ),
        (
            "record",
            {
                "task": "stream_trim",
                "interval_s": 60.0,
                "max_runtime_s": 5.0,
                "started_at": 200.0,
                "finished_at": 200.0,
                "error": "ConnectionError: gone",
            },
        ),
    ]


async def test_a_ledger_that_cannot_be_written_costs_visibility_never_the_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The heartbeat's rule: an observability aid that can take the loop
    down is a net loss. Logged on the first failure and on the recovery, not
    on every pass in between."""
    ledger = _RecordingLedger(fail=True)
    report = TaskReport(ledger, STREAM_TRIM_TASK, interval_s=60.0)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        await report.arm()
        await report.succeeded(started_at=0.0)
        await report.failed(started_at=0.0, error="x")
        ledger.fail = False
        await report.succeeded(started_at=0.0)

    messages = [r.getMessage() for r in caplog.records]
    assert messages.count("scheduled_task.ledger_write_failed") == 1
    assert messages.count("scheduled_task.ledger_write_recovered") == 1
    assert len(ledger.calls) == 1


async def test_no_ledger_is_the_null_form() -> None:
    report = TaskReport(None, STREAM_TRIM_TASK, interval_s=60.0)
    await report.arm()
    await report.succeeded(started_at=0.0)
    await report.failed(started_at=0.0, error="x")
