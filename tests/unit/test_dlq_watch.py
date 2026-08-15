"""Hermetic tests for the automatic DLQ report (ت-6 ·
``infrastructure/messaging/consumers/dlq_watch.py`` and the
``RedisStreamsConsumer.dlq_backlog`` read underneath it).

``_StubRedis`` implements only the two commands this path issues (``XLEN``
and a one-entry ``XRANGE``) -- the ``test_consumer_sweeper.py`` precedent:
stub the client, drive the REAL ``RedisStreamsConsumer`` so the reply-shape
handling is exercised rather than mocked away.

What this file is FOR. The finding this closes is not "a message was in the
DLQ" -- it is that a permanently non-empty DLQ turned «not empty» into the
normal state, and that a watcher which only counted entries would reproduce
that exactly. So the age of the oldest entry, the silence while queues are
empty, and the guarantee that LOOKING never consumes are each tested by name.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.framework.errors import AppError
from app.infrastructure.messaging.consumers.dlq_watch import report_dlq_backlog
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

_MEMORY = "stream.memory"
_KNOWLEDGE = "stream.knowledge"

# The measured entry, kept as the fixture for every positive case below: the
# one that sat on `stream.memory.dlq` from 2026-08-03 to 2026-08-15 with
# nothing reporting it (`docs/operational-findings.md` §6).
_REASON = b"handler_failed: NotFoundError: memory item not found"


def _entry_id(*, age_s: float) -> str:
    """A Streams id `<unix-ms>-<seq>` stamped ``age_s`` seconds ago -- the
    only place an entry's age comes from (``dead_letter`` writes no
    timestamp field, so this must work on entries already on the live
    stack)."""
    return f"{int((time.time() - age_s) * 1000)}-0"


class _StubRedis:
    """Replies in redis-py's own shapes for the two commands ``dlq_backlog``
    issues, and RECORDS every call -- including the consuming ones it must
    never make."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[bytes, bytes]]]] = {}
        self.xlen_calls: list[str] = []
        self.xrange_calls: list[tuple[str, int | None]] = []
        # Nothing below ever appends to these -- that is the assertion.
        self.xreadgroup_calls: list[object] = []
        self.xack_calls: list[object] = []
        self.xdel_calls: list[object] = []
        self.error: Exception | None = None

    def seed(self, dlq: str, entries: list[tuple[str, dict[bytes, bytes]]]) -> None:
        self.streams[dlq] = entries

    async def xlen(self, name: str) -> int:
        self.xlen_calls.append(name)
        if self.error is not None:
            raise self.error
        return len(self.streams.get(name, []))

    async def xrange(
        self, name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> list[tuple[bytes, dict[bytes, bytes]]]:
        self.xrange_calls.append((name, count))
        if self.error is not None:
            raise self.error
        rows = self.streams.get(name, [])
        limited = rows if count is None else rows[:count]
        return [(entry_id.encode(), fields) for entry_id, fields in limited]

    async def xreadgroup(self, *args: object, **kwargs: object) -> None:
        self.xreadgroup_calls.append((args, kwargs))

    async def xack(self, *args: object) -> None:
        self.xack_calls.append(args)

    async def xdel(self, *args: object) -> None:
        self.xdel_calls.append(args)


def _consumer(stub: _StubRedis) -> RedisStreamsConsumer:
    return RedisStreamsConsumer(cast("Redis", cast("Any", stub)))


# --------------------------------------------------------------------------- #
# The read itself (`RedisStreamsConsumer.dlq_backlog`)                        #
# --------------------------------------------------------------------------- #
async def test_a_dlq_that_never_existed_reads_as_an_empty_backlog() -> None:
    """The common case by far, and it must not be an error: a module whose
    workers have never dead-lettered anything has no `<stream>.dlq` key at
    all, and both Redis commands answer for a missing stream."""
    backlog = await _consumer(_StubRedis()).dlq_backlog(_MEMORY)

    assert backlog.is_empty
    assert (backlog.depth, backlog.oldest_entry_id, backlog.oldest_age_s) == (0, None, None)
    assert backlog.stream == _MEMORY  # the SOURCE name, never the .dlq suffix


async def test_the_backlog_carries_the_oldest_entrys_age_not_only_the_depth() -> None:
    """The whole point of ت-6: `depth=1` twelve days old and `depth=1` a
    minute old are the same number describing entirely different situations,
    and it was the first that read as normal on the live stack."""
    stub = _StubRedis()
    old = _entry_id(age_s=12 * 24 * 3600)
    stub.seed(
        f"{_MEMORY}.dlq",
        [
            (old, {b"reason": _REASON, b"ce": b"{}"}),
            (_entry_id(age_s=5), {b"reason": b"malformed_envelope"}),
        ],
    )

    backlog = await _consumer(stub).dlq_backlog(_MEMORY)

    assert backlog.depth == 2
    assert backlog.oldest_entry_id == old  # XRANGE - + is oldest-first
    assert backlog.oldest_reason == _REASON.decode()
    assert backlog.oldest_age_s is not None
    assert backlog.oldest_age_s == pytest.approx(12 * 24 * 3600, rel=0.01)


async def test_looking_at_a_dlq_never_consumes_anything() -> None:
    """`ops.dlq peek`'s own rule, restated for the unattended caller: a
    watcher that could move an entry would be able to lose the very messages
    it exists to report. Only XLEN and a bounded XRANGE are allowed."""
    stub = _StubRedis()
    stub.seed(f"{_MEMORY}.dlq", [(_entry_id(age_s=60), {b"reason": _REASON})])

    await _consumer(stub).dlq_backlog(_MEMORY)

    assert stub.xlen_calls == [f"{_MEMORY}.dlq"]
    assert stub.xrange_calls == [(f"{_MEMORY}.dlq", 1)]  # one entry, never the queue
    assert (stub.xreadgroup_calls, stub.xack_calls, stub.xdel_calls) == ([], [], [])


async def test_an_entry_without_a_reason_field_is_reported_not_raised() -> None:
    """`dead_letter` always writes `reason`, but this read must survive an
    entry some other tool XADDed by hand -- a watcher is not the place to
    raise KeyError over a bookkeeping field."""
    stub = _StubRedis()
    stub.seed(f"{_MEMORY}.dlq", [(_entry_id(age_s=1), {b"ce": b"{}"})])

    backlog = await _consumer(stub).dlq_backlog(_MEMORY)

    assert backlog.depth == 1
    assert backlog.oldest_reason is None


async def test_a_redis_failure_is_translated_like_every_other_command() -> None:
    """This adapter's standing error policy: no `redis`-package exception
    type ever escapes it (the module docstring's "translate, never fail
    open")."""
    stub = _StubRedis()
    stub.error = RedisConnectionError("redis is unreachable")

    with pytest.raises(AppError):
        await _consumer(stub).dlq_backlog(_MEMORY)


# --------------------------------------------------------------------------- #
# The report (`report_dlq_backlog`)                                           #
# --------------------------------------------------------------------------- #
async def test_an_empty_dlq_logs_nothing_at_all(caplog: pytest.LogCaptureFixture) -> None:
    """Silence is the healthy state (module docstring). An "all clear" line
    every interval, per worker, forever, is exactly the volume that makes a
    real warning unreadable -- and loop liveness is already the ت-3
    heartbeat's job."""
    stub = _StubRedis()

    with caplog.at_level(logging.INFO):
        found = await report_dlq_backlog(_consumer(stub), streams=[_MEMORY, _KNOWLEDGE])

    assert found == []
    assert caplog.records == []
    assert stub.xlen_calls == [f"{_MEMORY}.dlq", f"{_KNOWLEDGE}.dlq"]  # both were read


async def test_a_non_empty_dlq_warns_with_the_triage_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line has to answer "what now" by itself: an operator reading it at
    3am should not have to find out that P1-4's tool exists."""
    stub = _StubRedis()
    stub.seed(f"{_MEMORY}.dlq", [(_entry_id(age_s=3600), {b"reason": _REASON})])

    with caplog.at_level(logging.WARNING):
        found = await report_dlq_backlog(_consumer(stub), streams=[_MEMORY, _KNOWLEDGE])

    assert [backlog.stream for backlog in found] == [_MEMORY]
    record = next(r for r in caplog.records if r.message == "dlq.backlog")
    assert record.levelno == logging.WARNING
    assert record.stream == _MEMORY  # type: ignore[attr-defined]
    assert record.dlq == f"{_MEMORY}.dlq"  # type: ignore[attr-defined]
    assert record.depth == 1  # type: ignore[attr-defined]
    assert record.oldest_reason == _REASON.decode()  # type: ignore[attr-defined]
    assert record.oldest_age_s == pytest.approx(3600, rel=0.01)  # type: ignore[attr-defined]
    assert record.triage == f"python -m app.ops.dlq peek {_MEMORY}"  # type: ignore[attr-defined]


async def test_a_stream_named_twice_is_read_and_reported_once() -> None:
    """The knowledge worker subscribes to two streams under ONE group, and a
    future binding could name the same stream under two -- neither may make
    the same queue produce two warnings per tick."""
    stub = _StubRedis()
    stub.seed(f"{_KNOWLEDGE}.dlq", [(_entry_id(age_s=10), {b"reason": b"unroutable_envelope"})])

    found = await report_dlq_backlog(_consumer(stub), streams=[_KNOWLEDGE, _KNOWLEDGE, _MEMORY])

    assert len(found) == 1
    assert stub.xlen_calls == [f"{_KNOWLEDGE}.dlq", f"{_MEMORY}.dlq"]
