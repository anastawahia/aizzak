"""Capacity 5.7 against real Redis and real Postgres: the task ledger, the
metrics source that reads it, the scheduler driving a REAL tool as a
subprocess under two different database roles, and the RLS behaviour that
made the role check necessary.

⚠️ ``TEST_REDIS_URL`` is the development stack's own ``redis-stream``, so
every ledger key written here carries a unique ``test.`` name and is deleted
afterwards -- a catalog name (``retention``, ``backup``...) written from a
test would tell the live stack's dashboard that a job ran when it did not.
The one read of catalog names (the metrics source) only reads.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.observability.scheduled_tasks import SCHEDULED_TASKS
from app.framework.ports.task_ledger import TaskRecord
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.monitoring.metrics_source import SqlRedisMetricsSource
from app.infrastructure.monitoring.task_ledger import RedisTaskLedger, task_key
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.ops.retention import sweep_idempotency_keys
from app.ops.scheduler import Job, Policy, Scheduler
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]


@pytest.fixture
async def test_tasks(redis_client: Redis) -> AsyncIterator[list[str]]:
    """Hands out unique ledger names and deletes whatever was written under
    them, pass or fail."""
    names: list[str] = []
    try:
        yield names
    finally:
        if names:
            await redis_client.delete(*(task_key(name) for name in names))


def _unique(names: list[str], label: str) -> str:
    name = f"test.{label}.{new_uuid7()}"
    names.append(name)
    return name


# ------------------------------------------------------------- the ledger --


async def test_the_ledger_keeps_the_first_arming_and_counts_every_outcome(
    redis_client: Redis, test_tasks: list[str]
) -> None:
    ledger = RedisTaskLedger(redis_client)
    task = _unique(test_tasks, "ledger")
    never = _unique(test_tasks, "never")

    await ledger.arm(task, interval_s=60.0, max_runtime_s=5.0, at=100.0)
    # A restart re-arms with a changed setting: the cycle moves, the arming
    # does not -- a task that never succeeded stays late against its FIRST arm.
    await ledger.arm(task, interval_s=120.0, max_runtime_s=5.0, at=200.0)
    await ledger.record(
        task, interval_s=120.0, max_runtime_s=5.0, started_at=300.0, finished_at=301.5, error=None
    )
    await ledger.record(
        task, interval_s=120.0, max_runtime_s=5.0, started_at=400.0, finished_at=402.0, error="boom"
    )
    await ledger.attempt(task, at=500.0, cycle=7, cycle_attempts=2)

    records = await ledger.read([task, never])

    assert records[task] == TaskRecord(
        task=task,
        interval_s=120.0,
        max_runtime_s=5.0,
        armed_at=100.0,
        last_attempt_at=500.0,
        last_success_at=301.5,
        last_failure_at=402.0,
        last_duration_s=2.0,
        last_error="boom",
        attempt_cycle=7,
        cycle_attempts=2,
        successes=1,
        failures=1,
    )
    assert records[never] == TaskRecord(task=never)


async def test_a_record_lost_with_redis_is_rearmed_by_the_next_pass(
    redis_client: Redis, test_tasks: list[str]
) -> None:
    """A flushed ``redis-stream`` takes the ledger with it (5.6's scenario).
    The next pass must re-arm, or the task would read "never armed" until the
    runner restarts -- and a never-armed task has no clock to be late against."""
    ledger = RedisTaskLedger(redis_client)
    task = _unique(test_tasks, "lost")
    await ledger.arm(task, interval_s=60.0, max_runtime_s=0.0, at=100.0)
    await redis_client.delete(task_key(task))

    await ledger.record(
        task, interval_s=60.0, max_runtime_s=0.0, started_at=900.0, finished_at=901.0, error="x"
    )

    record = (await ledger.read([task]))[task]
    assert record.armed_at == 900.0
    assert record.max_success_age_s == 120.0


async def test_the_metrics_source_reports_every_catalog_task_in_order(
    metrics_engine: AsyncEngine, redis_client: Redis
) -> None:
    """One record per catalog task whether or not anything wrote it -- read
    only, from the development stack's own ledger."""
    records = await SqlRedisMetricsSource(metrics_engine, redis_client).scheduled_tasks()
    assert list(records) == [task.name for task in SCHEDULED_TASKS]


# ------------------------------------------ a real tool, two real roles --


def _child_environ(dsn: str) -> dict[str, str]:
    environ = dict(os.environ)
    environ["RETENTION_DATABASE_URL"] = dsn
    return environ


async def test_the_scheduler_runs_the_real_tool_and_the_wrong_role_is_a_recorded_failure(
    live_db: LiveDbDsns, redis_client: Redis, test_tasks: list[str]
) -> None:
    """The same command twice, through the scheduler's own path -- a real
    ``python -m app.ops.retention`` subprocess against ``aizzak_test``. Under
    ``retention_sweeper`` it succeeds; wired to the table owner it is REFUSED
    by the tool (exit 2), and the ledger carries the reason. Before 5.7 the
    second run exited 0 with ``affected: 0`` on the RLS tables -- the silent
    success the next test reproduces."""
    ledger = RedisTaskLedger(redis_client)
    right = Job(
        _unique(test_tasks, "retention-right-role"),
        ("app.ops.retention", "sweep", "--dry-run", "--table", "idempotency_keys"),
        dsn_env="RETENTION_DATABASE_URL",
        max_runtime_s=120.0,
    )
    wrong = Job(
        _unique(test_tasks, "retention-wrong-role"),
        right.argv,
        dsn_env="RETENTION_DATABASE_URL",
        max_runtime_s=120.0,
    )
    policy = Policy(tick_s=1.0)

    ok = Scheduler(
        ledger,
        jobs=(right,),
        environ=_child_environ(live_db.retention),
        policy=policy,
        python=sys.executable,
    )
    refused = Scheduler(
        ledger,
        jobs=(wrong,),
        environ=_child_environ(live_db.owner),
        policy=policy,
        python=sys.executable,
    )
    await ok.arm_all()
    await refused.arm_all()

    assert await ok.run_job(right, {}) == "succeeded"
    assert await refused.run_job(wrong, {}) == "failed"

    records = await ledger.read([right.task, wrong.task])
    assert records[right.task].successes == 1
    assert records[right.task].last_error is None
    failure = records[wrong.task]
    assert failure.successes == 0 and failure.failures == 1
    assert failure.last_error is not None
    assert failure.last_error.startswith("exit 2:")
    assert "connected as 'aizzak_owner'" in failure.last_error


async def test_rls_answers_the_wrong_role_with_an_empty_table_not_an_error(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory, retention_engine: AsyncEngine
) -> None:
    """The mechanism behind the 2026-09-30 measurement (the owner's dry run
    said 0 where 6,943 keys were stale), on the real schema: two stale keys,
    and the same count under two roles. The owner is bound by FORCE ROW
    LEVEL SECURITY and sees none -- no error, no warning, a zero."""
    workspace = new_uuid7()
    ctx = ExecutionContext(
        workspace_id=workspace, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )
    stale = datetime.now(UTC) - timedelta(days=10)
    async with tenant_session(ctx) as session:
        for _ in range(2):
            await session.execute(
                text(
                    "INSERT INTO platform.idempotency_keys "
                    "(workspace_id, endpoint, idempotency_key, request_hash, created_at) "
                    "VALUES (:ws, 'POST /files', :key, 'hash', :created_at)"
                ),
                {"ws": workspace, "key": new_uuid7(), "created_at": stale},
            )

    owner = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with owner.begin() as conn:
            seen_by_owner = await sweep_idempotency_keys(conn, dry_run=True)
    finally:
        await owner.dispose()
    async with retention_engine.begin() as conn:
        seen_by_sweeper = await sweep_idempotency_keys(conn, dry_run=True)

    assert seen_by_owner.affected == 0
    assert seen_by_sweeper.affected >= 2
