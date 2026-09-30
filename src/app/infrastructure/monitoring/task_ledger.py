"""``TaskLedger`` over Redis -- one hash per scheduled task (capacity 5.7).

**Which Redis, and why it is not a preference.** ``redis-stream``
(``REDIS_URL``), the ``noeviction`` instance -- never ``redis-cache``. The
ledger is evidence, and ``allkeys-lru`` (5.2) evicts exactly the keys nobody
has touched lately: a task that has not succeeded in a while is, by
construction, the task whose record LRU would pick first. On ``redis-cache``
the one record the alert needs would be the one most likely to vanish, and a
vanished record reads as "never armed" or, worse, as nothing at all. Every
runner already holds a client to ``redis-stream`` (the relay, every ``app``
replica, every worker, the scheduler), so nothing gains a connection.

**Why a hash per task and not one hash for all.** Each writer touches only
its own task's fields, in one ``MULTI``, and ``HINCRBY`` works per field; a
single hash would need ``<task>.<field>`` names and a scan to read one task
back. There are about a dozen keys, fixed by the catalog.

**Timestamps are the writer's wall clock**, not Redis ``TIME``. Every runner
shares a host clock with Prometheus in both deployments, and the smallest
number this ledger is compared against is two cycles of a 60-second loop:
milliseconds of skew are four orders of magnitude below anything it decides.
(5.5's trimmer reads ``TIME`` because it subtracts stream ids from it; nothing
here does.)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from app.framework.ports.task_ledger import TaskRecord

KEY_PREFIX = "ops:task:"


def task_key(task: str) -> str:
    return f"{KEY_PREFIX}{task}"


class RedisTaskLedger:
    """Structural ``TaskLedger`` (the port's Protocol, no inheritance)."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def arm(self, task: str, *, interval_s: float, max_runtime_s: float, at: float) -> None:
        key = task_key(task)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={"interval_s": _num(interval_s), "max_runtime_s": _num(max_runtime_s)},
            )
            pipe.hsetnx(key, "armed_at", _num(at))
            await pipe.execute()

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
        key = task_key(task)
        fields: dict[FieldT, EncodableT] = {
            "interval_s": _num(interval_s),
            "max_runtime_s": _num(max_runtime_s),
            "last_duration_s": _num(max(0.0, finished_at - started_at)),
        }
        if error is None:
            fields["last_success_at"] = _num(finished_at)
            counter = "successes"
        else:
            fields["last_failure_at"] = _num(finished_at)
            fields["last_error"] = error
            counter = "failures"
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=fields)
            # A record whose key was lost (a flushed Redis) is re-armed at the
            # start of the pass that noticed, not at the next restart.
            pipe.hsetnx(key, "armed_at", _num(started_at))
            pipe.hincrby(key, counter, 1)
            await pipe.execute()

    async def attempt(self, task: str, *, at: float, cycle: int, cycle_attempts: int) -> None:
        await self._client.hset(
            task_key(task),
            mapping={
                "last_attempt_at": _num(at),
                "attempt_cycle": str(cycle),
                "cycle_attempts": str(cycle_attempts),
            },
        )

    async def read(self, tasks: Iterable[str]) -> dict[str, TaskRecord]:
        names = list(dict.fromkeys(tasks))
        if not names:
            return {}
        async with self._client.pipeline(transaction=False) as pipe:
            for name in names:
                pipe.hgetall(task_key(name))
            rows = await pipe.execute()
        return {
            name: _parse(name, cast("Mapping[bytes | str, bytes | str]", row))
            for name, row in zip(names, rows, strict=True)
        }


def _num(value: float) -> str:
    # `repr` keeps a float's full precision and reads back with `float()`.
    return repr(float(value))


def _parse(task: str, row: Mapping[bytes | str, bytes | str]) -> TaskRecord:
    fields = {
        (key.decode() if isinstance(key, bytes) else key): (
            value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        )
        for key, value in row.items()
    }

    def number(name: str) -> float | None:
        raw = fields.get(name)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def count(name: str) -> int:
        raw = fields.get(name)
        try:
            return int(raw) if raw is not None else 0
        except ValueError:
            return 0

    cycle = fields.get("attempt_cycle")
    return TaskRecord(
        task=task,
        interval_s=number("interval_s"),
        max_runtime_s=number("max_runtime_s"),
        armed_at=number("armed_at"),
        last_attempt_at=number("last_attempt_at"),
        last_success_at=number("last_success_at"),
        last_failure_at=number("last_failure_at"),
        last_duration_s=number("last_duration_s"),
        last_error=fields.get("last_error"),
        attempt_cycle=int(cycle) if cycle is not None and cycle.lstrip("-").isdigit() else None,
        cycle_attempts=count("cycle_attempts"),
        successes=count("successes"),
        failures=count("failures"),
    )
