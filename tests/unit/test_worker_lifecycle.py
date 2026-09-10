"""``workers/lifecycle.run_worker`` -- capacity step 5.1's invariant 4
(``docs/capacity-plan.md`` §5 wave 5), and the module's own ت-2 contract.

⭐ **WHAT 5.1 CHANGED, AND WHY IT HAD TO.** A shutdown signal used to cancel
the read loop where it stood: the message this process had already taken out
of the stream was abandoned mid-handler, left ``pending``, and recovered
later by the sweeper. That was tolerable while ONE message could be in flight.
Bounded concurrency makes it ``WORKER_CONCURRENCY`` messages -- «التزامنُ بلا
هذا يحوّل كلَّ نشرٍ إلى أربع مهامّ مبتورةٍ بدل واحدة» -- so a signal now asks
the loop to stop TAKING and gives what it already took a deadline to finish.

⚠️ **The deadline is bounded and the tests below assert BOTH halves**, because
only asserting the first would describe a shutdown that never ends: a drain
that completes returns as soon as the work does, and a drain that expires
cancels what is left rather than waiting for it. A worker that waited out a
1,800-second summary build would have replaced a truncated job with a stalled
deploy.

Hermetic: a purpose-built stand-in for ``RedisStreamsConsumer`` (this file
needs four of its methods, not the behavioural twin
``test_stream_consumer.py`` carries), driven through a REAL ``StreamConsumer``
-- the drain is a conversation between the two, so faking the engine would
have tested the fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Sequence

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import ConsumerInfo, StreamMessage
from app.workers.lifecycle import run_worker

pytestmark = pytest.mark.anyio

_STREAM = "stream.memory"
_GROUP = "cg.memory"
_TYPE = "memory.item.stored.v1"


class EndlessStream:
    """A stream that always has one more message. Four methods, because that
    is all ``StreamConsumer`` calls on this path: the loop reads and acks, and
    the exit path lists then deletes this process's consumer entry."""

    def __init__(self, *, subjects: Sequence[str] = ("doc-a",)) -> None:
        self.subjects = list(subjects)
        self.acked: list[str] = []
        self.deleted: list[str] = []
        self.reads = 0

    async def ensure_group(self, stream: str, group: str) -> None:
        return None

    async def read(
        self,
        *,
        streams: Sequence[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[StreamMessage]:
        self.reads += 1
        # A real blocking XREADGROUP yields to the loop; without this the
        # drain could never be observed, because nothing else would ever run.
        await asyncio.sleep(0)
        return [
            StreamMessage(
                stream=_STREAM,
                entry_id=f"{self.reads}-{index}",
                raw=_envelope(subject),
                delivery_count=1,
            )
            for index, subject in enumerate(self.subjects[:count])
        ]

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append(entry_id)

    async def list_consumers(self, stream: str, group: str) -> list[ConsumerInfo]:
        return [ConsumerInfo(name="test-consumer", pending=0, idle_ms=0)]

    async def delete_consumer(self, stream: str, group: str, consumer: str) -> int:
        self.deleted.append(consumer)
        return 0


def _envelope(subject: str) -> bytes:
    return json.dumps(
        build_envelope(
            event_id=new_uuid7(),
            source="test",
            event_type=_TYPE,
            subject=subject,
            occurred_at=utc_now(),
            workspace_id="ws-1",
            data={},
        )
    ).encode()


async def _sigterm() -> None:
    """Deliver a REAL ``SIGTERM`` to this process, which is the only way to
    exercise the path an operator's ``docker compose restart`` takes.

    Calling ``StreamConsumer.request_stop`` directly would test a different
    thing: ``run_worker`` owns its own shutdown ``Event``, and the whole
    sequence under test -- signal, then stop, then drain, then cancel -- lives
    between the two. Safe under pytest because ``run_worker`` has installed a
    loop handler by the time this is called, and every test below awaits the
    worker task before that handler is removed.
    """
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0)


def _consumer(
    fake: EndlessStream, *, drain_timeout_s: float, concurrency: int = 1
) -> StreamConsumer:
    return StreamConsumer(
        fake,  # type: ignore[arg-type]
        consumer_name="test-consumer",
        block_ms=1,
        batch_count=concurrency,
        max_deliveries=5,
        concurrency=concurrency,
        drain_timeout_s=drain_timeout_s,
    )


def _subscription(handler: object) -> list[Subscription]:
    return [
        Subscription(
            stream=_STREAM,
            group=_GROUP,
            handlers={_TYPE: handler},  # type: ignore[dict-item]
        )
    ]


async def test_a_stopped_worker_finishes_the_batch_it_already_took() -> None:
    """The invariant itself: every message this process pulled out of the
    stream is completed and acked, and the entries are gone from its pending
    list -- there is nothing for the sweeper to recover."""
    fake = EndlessStream(subjects=["doc-a", "doc-b", "doc-c", "doc-d"])
    consumer = _consumer(fake, drain_timeout_s=5.0, concurrency=4)
    completed: list[str] = []

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await asyncio.sleep(0.05)
        completed.append(str(envelope["subject"]))

    worker = asyncio.create_task(run_worker(consumer, _subscription(_handle)))
    await asyncio.sleep(0.01)
    await _sigterm()
    await asyncio.wait_for(worker, timeout=5.0)

    assert sorted(completed) == ["doc-a", "doc-b", "doc-c", "doc-d"]
    assert len(fake.acked) == 4


async def test_the_drain_returns_as_soon_as_the_work_does() -> None:
    """It is a DEADLINE, not a delay. A shutdown that always spent its whole
    budget would make every deploy cost `WORKER_DRAIN_TIMEOUT_S`, and the
    cheapest way to get that wrong is to sleep instead of to wait."""
    fake = EndlessStream()
    consumer = _consumer(fake, drain_timeout_s=30.0)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await asyncio.sleep(0)

    worker = asyncio.create_task(run_worker(consumer, _subscription(_handle)))
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await _sigterm()
    await asyncio.wait_for(worker, timeout=5.0)

    assert asyncio.get_running_loop().time() - started < 1.0


async def test_work_that_outlives_the_deadline_is_cancelled_not_waited_for() -> None:
    """The other half, and the one that keeps a bounded drain bounded: a
    handler that runs past the deadline is cancelled, its entry is never
    acked, and the sweeper's job is exactly what it was before 5.1. A worker
    that waited out `summarize_job_max_duration_s` would have traded a
    truncated job for a half-hour stall."""
    fake = EndlessStream()
    consumer = _consumer(fake, drain_timeout_s=0.05)
    cancelled = asyncio.Event()

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = asyncio.create_task(run_worker(consumer, _subscription(_handle)))
    await asyncio.sleep(0.01)
    await _sigterm()
    await asyncio.wait_for(worker, timeout=5.0)

    assert cancelled.is_set()
    assert fake.acked == []


async def test_a_worker_with_no_drain_configured_is_cancelled_where_it_stands() -> None:
    """`0` is the pre-5.1 path and stays reachable: every direct caller of a
    worker builder (a live integration test, a bare `python -m`) gets it, so
    turning the drain on had to be something the `_from_env` path chooses."""
    fake = EndlessStream()
    consumer = _consumer(fake, drain_timeout_s=0.0)
    cancelled = asyncio.Event()

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = asyncio.create_task(run_worker(consumer, _subscription(_handle)))
    await asyncio.sleep(0.01)
    await _sigterm()
    await asyncio.wait_for(worker, timeout=5.0)

    assert cancelled.is_set()
    assert fake.acked == []


async def test_the_consumer_entry_is_still_removed_after_a_drain() -> None:
    """ت-2's contract has to survive the new step in front of it: the whole
    reason this module exists is that a restart used to leave a permanent
    tombstone inside `cg.knowledge`/`cg.media`/`cg.memory`."""
    fake = EndlessStream()
    consumer = _consumer(fake, drain_timeout_s=5.0)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await asyncio.sleep(0)

    worker = asyncio.create_task(run_worker(consumer, _subscription(_handle)))
    await asyncio.sleep(0.01)
    await _sigterm()
    await asyncio.wait_for(worker, timeout=5.0)

    assert fake.deleted == ["test-consumer"]
