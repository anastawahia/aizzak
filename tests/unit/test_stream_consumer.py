"""Unit tests for the generic consumer engine (5.1-ج ·
``infrastructure/messaging/consumers/engine.py``).

Hermetic: ``InMemoryStreamsConsumer`` is a behavioral twin of
``RedisStreamsConsumer`` (in-memory streams/groups/pending-entries,
including its own "redeliver what was never acked" property -- the
consumer-adapter docstring's own "recovery pass" explains why real Redis
needs two ``XREADGROUP`` calls to get this; this fake achieves the same
observable behaviour without a real Redis). ``tests/integration/
test_stream_consumer_live.py`` proves the same properties against real
Redis.

``StreamConsumer.__init__`` types its ``consumer`` parameter as the CONCRETE
``RedisStreamsConsumer`` (the ``OutboxRelay.__init__``/``SqlOutboxRelayStore``
precedent, ``tests/unit/test_outbox_relay.py``'s own ``_relay`` helper) --
every construction below carries a ``# type: ignore[arg-type]`` for the same
reason that precedent does.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, UnsupportedTypeError
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.observability import Heartbeat
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import (
    EventHandler,
    StreamConsumer,
    Subscription,
)
from app.infrastructure.messaging.redis_streams import ConsumerInfo, DlqBacklog, StreamMessage


class InMemoryStreamsConsumer:
    """Behavioral twin of ``RedisStreamsConsumer`` -- in-memory streams,
    consumer groups, and per-``(stream, group)`` pending-entries state.

    ``read`` redelivers whatever is still pending (never ``ack``\\ ed) for a
    ``(stream, group)`` pair FIRST, then hands out never-delivered entries
    (marking them pending), mirroring the real adapter's own two-pass merge
    -- see ``RedisStreamsConsumer.read``'s docstring for why real Redis
    needs a ``0`` "recovery" XREADGROUP plus a ``>`` "fresh" one to get this
    same property; this fake achieves it directly.
    """

    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, bytes | None]]] = {}
        self.groups: set[tuple[str, str]] = set()
        self.pending: dict[tuple[str, str], dict[str, bytes | None]] = {}
        self.read_calls: list[tuple[tuple[str, ...], str]] = []
        self.acked: list[tuple[str, str, str]] = []
        # (stream, group, entry_id, reason, delivery_count) per transfer --
        # the engine-facing record of `RedisStreamsConsumer.dead_letter`.
        self.dead_lettered: list[tuple[str, str, str, str, int]] = []
        # Per-PEL delivery counters, mirroring the VERIFIED real semantics
        # (`StreamMessage.delivery_count`'s docstring): 1 on first delivery,
        # +1 on every recovery re-read of a still-pending entry.
        self.delivery_counts: dict[tuple[str, str], dict[str, int]] = {}
        # ت-2: Redis's consumer registry per `(stream, group)`, plus what was
        # deleted from it -- the state both sweeps in `consumers/sweeper.py`
        # read and write.
        self.consumers: dict[tuple[str, str], dict[str, ConsumerInfo]] = {}
        self.deleted_consumers: list[tuple[str, str, str]] = []
        self._cursor: dict[tuple[str, str], int] = {}
        self._next_id = 0

    def seed(self, stream: str, raw: bytes | None) -> str:
        """Append a fake ``XADD``\\ ed entry; returns its synthetic entry id."""
        self._next_id += 1
        entry_id = f"{self._next_id}-0"
        self.entries.setdefault(stream, []).append((entry_id, raw))
        return entry_id

    async def ensure_group(self, stream: str, group: str) -> None:
        self.groups.add((stream, group))
        self._cursor.setdefault((stream, group), 0)
        self.pending.setdefault((stream, group), {})

    async def destroy_group(self, stream: str, group: str) -> None:
        """§3.81's ``ensure_group`` counterpart -- destroying a group that
        was never created is a silent no-op here too, mirroring the real
        adapter's own idempotence (``RedisStreamsConsumer.destroy_group``)."""
        self.groups.discard((stream, group))
        self._cursor.pop((stream, group), None)
        self.pending.pop((stream, group), None)

    async def read(
        self,
        *,
        streams: Sequence[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[StreamMessage]:
        self.read_calls.append((tuple(streams), group))
        messages: list[StreamMessage] = []
        for stream in streams:
            key = (stream, group)
            pending = self.pending.setdefault(key, {})
            counts = self.delivery_counts.setdefault(key, {})
            for entry_id, raw in list(pending.items()):
                counts[entry_id] = counts.get(entry_id, 0) + 1
                messages.append(
                    StreamMessage(
                        stream=stream,
                        entry_id=entry_id,
                        raw=raw,
                        delivery_count=counts[entry_id],
                    )
                )
            cursor = self._cursor.get(key, 0)
            fresh = self.entries.get(stream, [])[cursor : cursor + count]
            for entry_id, raw in fresh:
                pending[entry_id] = raw
                counts[entry_id] = 1
                messages.append(
                    StreamMessage(stream=stream, entry_id=entry_id, raw=raw, delivery_count=1)
                )
            self._cursor[key] = cursor + len(fresh)
        return messages

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append((stream, group, entry_id))
        self.pending.get((stream, group), {}).pop(entry_id, None)

    # -- ت-2: the consumer registry the two sweeps read and write ---------- #
    def register(
        self, stream: str, group: str, name: str, *, idle_ms: int, pending: int = 0
    ) -> None:
        """Seed a consumer entry the way real Redis keeps one: created by a
        read, and NEVER removed when the process behind it dies."""
        self.consumers.setdefault((stream, group), {})[name] = ConsumerInfo(
            name=name, pending=pending, idle_ms=idle_ms
        )

    async def list_consumers(self, stream: str, group: str) -> list[ConsumerInfo]:
        return list(self.consumers.get((stream, group), {}).values())

    async def delete_consumer(self, stream: str, group: str, consumer: str) -> int:
        self.deleted_consumers.append((stream, group, consumer))
        info = self.consumers.get((stream, group), {}).pop(consumer, None)
        return 0 if info is None else info.pending

    async def reclaim(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int = 100,
        max_batches: int = 100,
    ) -> list[str]:
        """Move every idle-enough consumer's pending count onto ``consumer``
        -- the observable effect of ``XAUTOCLAIM``, which is that entries
        CHANGE OWNER rather than disappearing."""
        registry = self.consumers.setdefault((stream, group), {})
        moved = 0
        for name, info in list(registry.items()):
            if name == consumer or info.idle_ms < min_idle_ms:
                continue
            moved += info.pending
            registry[name] = ConsumerInfo(name=name, pending=0, idle_ms=info.idle_ms)
        current = registry.get(consumer)
        registry[consumer] = ConsumerInfo(
            name=consumer,
            pending=(0 if current is None else current.pending) + moved,
            idle_ms=0,
        )
        return [f"claimed-{index}" for index in range(moved)]

    async def dead_letter(
        self,
        *,
        stream: str,
        group: str,
        entry_id: str,
        raw: bytes | None,
        reason: str,
        delivery_count: int,
    ) -> None:
        """Record the transfer and remove the entry from pending -- the
        observable effect of the real adapter's XADD-to-``<stream>.dlq`` +
        XACK MULTI."""
        self.dead_lettered.append((stream, group, entry_id, reason, delivery_count))
        self.entries.setdefault(f"{stream}.dlq", []).append((entry_id, raw))
        self.pending.get((stream, group), {}).pop(entry_id, None)

    # -- ت-6: what the DLQ report reads -------------------------------------- #
    async def dlq_backlog(self, stream: str) -> DlqBacklog:
        """The observable effect of the real adapter's ``XLEN`` + one-entry
        ``XRANGE`` over ``<stream>.dlq`` -- computed from the SAME in-memory
        list ``dead_letter`` above appends to, so a test that dead-letters a
        message then reads the backlog exercises the real sequence a worker
        performs rather than two disconnected fakes. ``oldest_age_s`` is 0.0
        here: this fake's ids are not wall-clock stamps (the real derivation
        is ``redis_streams._entry_id_age_s``, tested in
        ``tests/unit/test_dlq_watch.py`` against real Streams id shapes)."""
        entries = self.entries.get(f"{stream}.dlq", [])
        if not entries:
            return DlqBacklog(
                stream=stream,
                depth=0,
                oldest_entry_id=None,
                oldest_age_s=None,
                oldest_reason=None,
            )
        oldest_id = entries[0][0]
        reason = next(
            (
                r
                for s, _g, entry_id, r, _d in self.dead_lettered
                if s == stream and entry_id == oldest_id
            ),
            None,
        )
        return DlqBacklog(
            stream=stream,
            depth=len(entries),
            oldest_entry_id=oldest_id,
            oldest_age_s=0.0,
            oldest_reason=reason,
        )


def _envelope_bytes(
    event_type: str,
    *,
    workspace_id: str = "ws-1",
    correlation_id: str | None = "corr-1",
    data: Json | None = None,
    subject: str = "subject-1",
) -> bytes:
    """A REAL CloudEvents envelope (``build_envelope``, not a hand-rolled
    dict) serialized to bytes -- exactly what ``RedisStreamsConsumer.read``
    would hand back as ``StreamMessage.raw``.

    ``subject`` is a parameter since capacity 5.1: it is the aggregate id the
    engine partitions a batch on (``_lanes``), so "two messages about one
    document" and "two messages about two documents" are two different
    envelopes here rather than two different comments.
    """
    envelope = build_envelope(
        event_id=new_uuid7(),
        source="test",
        event_type=event_type,
        subject=subject,
        occurred_at=utc_now(),
        workspace_id=workspace_id,
        data=data or {},
        correlation_id=correlation_id,
    )
    return json.dumps(envelope).encode()


def _consumer(
    fake: InMemoryStreamsConsumer,
    *,
    block_ms: int = 10,
    batch_count: int = 10,
    max_deliveries: int = 5,
    heartbeat: Heartbeat | None = None,
    sweep_interval_s: float = 0.0,
    stale_idle_ms: int = 0,
    dlq_watch_interval_s: float = 0.0,
    concurrency: int = 1,
    drain_timeout_s: float = 0.0,
) -> StreamConsumer:
    return StreamConsumer(
        fake,  # type: ignore[arg-type]
        consumer_name="test-consumer",
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
        heartbeat=heartbeat,
        sweep_interval_s=sweep_interval_s,
        stale_idle_ms=stale_idle_ms,
        dlq_watch_interval_s=dlq_watch_interval_s,
        concurrency=concurrency,
        drain_timeout_s=drain_timeout_s,
    )


class OverlapRecorder:
    """A handler that records how many of itself were running at once, and in
    what order they started -- the two facts every concurrency test below is
    actually about (capacity 5.1).

    Each call yields to the loop at least once (``asyncio.sleep(0)`` is not
    enough to let a sibling task START on every 3.12 scheduling path, so it
    sleeps a real, tiny interval), which is what makes "did these two overlap"
    observable rather than a coincidence of scheduling order.
    """

    def __init__(self, *, delay_s: float = 0.01) -> None:
        self.delay_s = delay_s
        self.in_flight = 0
        self.peak = 0
        self.started: list[str] = []
        self.finished: list[str] = []
        self.overlapped_subjects: set[tuple[str, ...]] = set()
        self._live_subjects: set[str] = set()

    async def __call__(self, ctx: ExecutionContext, envelope: Json) -> None:
        subject = str(envelope["subject"])
        self.started.append(subject)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        if self._live_subjects:
            self.overlapped_subjects.add(tuple(sorted({subject, *self._live_subjects})))
        self._live_subjects.add(subject)
        try:
            await asyncio.sleep(self.delay_s)
        finally:
            self._live_subjects.discard(subject)
            self.in_flight -= 1
            self.finished.append(subject)


class CountingHeartbeat:
    """Records beats instead of touching a file (ت-3)."""

    def __init__(self) -> None:
        self.beats = 0

    def beat(self) -> None:
        self.beats += 1


# --------------------------------------------------------------------------- #
# Dispatch + ctx construction                                                 #
# --------------------------------------------------------------------------- #
async def test_dispatches_by_type_and_builds_ctx_from_the_envelope() -> None:
    """Mutation-battery target #4: a hardcoded/empty ``workspace_id`` in
    ``ctx`` construction would fail this assertion directly."""
    fake = InMemoryStreamsConsumer()
    seen: list[tuple[ExecutionContext, Json]] = []

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        seen.append((ctx, envelope))

    sub = Subscription(
        stream="stream.knowledge",
        group="cg.knowledge",
        handlers={"knowledge.document.registered.v1": handler},
    )
    fake.seed(
        "stream.knowledge",
        _envelope_bytes(
            "knowledge.document.registered.v1", workspace_id="ws-42", correlation_id="corr-99"
        ),
    )

    handled = await _consumer(fake).run_once([sub])

    assert handled == 1
    assert len(seen) == 1
    ctx, envelope = seen[0]
    assert ctx.workspace_id == "ws-42"
    assert ctx.correlation_id == "corr-99"
    assert ctx.user_id is None
    assert ctx.roles == frozenset()
    assert ctx.request_id is None
    assert envelope["type"] == "knowledge.document.registered.v1"


async def test_successful_handler_is_xacked() -> None:
    fake = InMemoryStreamsConsumer()

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        return None

    sub = Subscription(
        stream="stream.media", group="cg.media", handlers={"media.job.requested.v1": handler}
    )
    entry_id = fake.seed("stream.media", _envelope_bytes("media.job.requested.v1"))

    await _consumer(fake).run_once([sub])

    assert fake.acked == [("stream.media", "cg.media", entry_id)]


async def test_correlation_id_falls_back_to_envelope_id_when_absent() -> None:
    fake = InMemoryStreamsConsumer()
    seen: list[ExecutionContext] = []

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        seen.append(ctx)

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": handler}
    )
    raw = _envelope_bytes("memory.item.stored.v1", correlation_id=None)
    envelope = json.loads(raw)
    assert "correlationid" not in envelope  # build_envelope omits, never nulls
    fake.seed("stream.memory", raw)

    await _consumer(fake).run_once([sub])

    assert seen[0].correlation_id == envelope["id"]


# --------------------------------------------------------------------------- #
# Handler failure -> redelivery (mutation-battery target #2)                  #
# --------------------------------------------------------------------------- #
async def test_handler_raise_leaves_the_entry_unacked_and_it_is_redelivered() -> None:
    """An ``except`` that XACKs on handler failure would make the SECOND
    ``run_once`` see zero messages instead of the same entry again."""
    fake = InMemoryStreamsConsumer()
    attempts: list[Json] = []

    async def flaky(ctx: ExecutionContext, envelope: Json) -> None:
        attempts.append(envelope)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": flaky}
    )
    entry_id = fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1"))
    consumer = _consumer(fake)

    first = await consumer.run_once([sub])
    assert first == 0
    assert len(attempts) == 1
    assert fake.acked == []

    second = await consumer.run_once([sub])
    assert second == 1
    assert len(attempts) == 2
    assert fake.acked == [("stream.memory", "cg.memory", entry_id)]


async def test_handler_failure_is_logged_at_error_without_the_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = InMemoryStreamsConsumer()
    secret_marker = "TOP-SECRET-MEMORY-CONTENT"

    async def always_fails(ctx: ExecutionContext, envelope: Json) -> None:
        raise RuntimeError("boom")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": always_fails}
    )
    fake.seed(
        "stream.memory",
        _envelope_bytes("memory.item.stored.v1", data={"content": secret_marker}),
    )

    with caplog.at_level(logging.ERROR):
        await _consumer(fake).run_once([sub])

    assert any(record.getMessage() == "handler_failed" for record in caplog.records)
    assert secret_marker not in caplog.text


# --------------------------------------------------------------------------- #
# No handler for `type` -> XACK-skip (mutation-battery target #1)             #
# --------------------------------------------------------------------------- #
async def test_no_handler_for_type_is_xack_skipped_without_invoking_any_handler() -> None:
    """Raising instead of ack-skipping here would make this test observe an
    unhandled exception instead of a clean, acked skip -- the
    ``knowledge.document.indexed.v1`` on ``cg.knowledge`` scenario (that
    type belongs to ``cg.notify``/5.3, 04 §2's topology table)."""
    fake = InMemoryStreamsConsumer()
    called = False

    async def registered_handler(ctx: ExecutionContext, envelope: Json) -> None:
        nonlocal called
        called = True

    sub = Subscription(
        stream="stream.knowledge",
        group="cg.knowledge",
        handlers={"knowledge.document.registered.v1": registered_handler},
    )
    entry_id = fake.seed("stream.knowledge", _envelope_bytes("knowledge.document.indexed.v1"))

    handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert called is False
    assert fake.acked == [("stream.knowledge", "cg.knowledge", entry_id)]


# --------------------------------------------------------------------------- #
# Malformed envelope -> immediate DLQ (mutation-battery target #3; 5.2-ب)     #
# --------------------------------------------------------------------------- #
async def test_missing_ce_field_is_logged_and_dead_lettered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    entry_id = fake.seed("stream.media", None)

    with caplog.at_level(logging.ERROR):
        handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert fake.dead_lettered == [("stream.media", "cg.media", entry_id, "malformed_envelope", 1)]
    assert fake.pending[("stream.media", "cg.media")] == {}  # transferred, not retried
    assert any(record.getMessage() == "malformed_envelope" for record in caplog.records)
    assert any(record.getMessage() == "dead_lettered" for record in caplog.records)


async def test_invalid_json_ce_is_logged_and_dead_lettered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    entry_id = fake.seed("stream.media", b"{not-valid-json")

    with caplog.at_level(logging.ERROR):
        handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert fake.dead_lettered == [("stream.media", "cg.media", entry_id, "malformed_envelope", 1)]
    assert any(record.getMessage() == "malformed_envelope" for record in caplog.records)
    assert "not-valid-json" not in caplog.text


async def test_non_object_json_ce_is_treated_as_malformed() -> None:
    """``json.loads`` happily parses ``"[1, 2, 3]"`` -- not an envelope
    object, and the engine must not crash calling ``.get`` on it."""
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    entry_id = fake.seed("stream.media", b"[1, 2, 3]")

    handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert [t[2] for t in fake.dead_lettered] == [entry_id]


# --------------------------------------------------------------------------- #
# Unroutable envelope -> immediate DLQ                                        #
# --------------------------------------------------------------------------- #
async def test_missing_workspaceid_is_logged_and_dead_lettered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    envelope = json.loads(_envelope_bytes("media.job.requested.v1"))
    del envelope["workspaceid"]
    entry_id = fake.seed("stream.media", json.dumps(envelope).encode())

    with caplog.at_level(logging.ERROR):
        handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert fake.dead_lettered == [("stream.media", "cg.media", entry_id, "unroutable_envelope", 1)]
    assert any(record.getMessage() == "unroutable_envelope" for record in caplog.records)


async def test_missing_type_is_dead_lettered() -> None:
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    envelope = json.loads(_envelope_bytes("media.job.requested.v1"))
    del envelope["type"]
    entry_id = fake.seed("stream.media", json.dumps(envelope).encode())

    handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert [t[2] for t in fake.dead_lettered] == [entry_id]


async def test_empty_workspaceid_is_treated_as_missing() -> None:
    fake = InMemoryStreamsConsumer()
    sub = Subscription(stream="stream.media", group="cg.media", handlers={})
    envelope = json.loads(_envelope_bytes("media.job.requested.v1"))
    envelope["workspaceid"] = ""
    entry_id = fake.seed("stream.media", json.dumps(envelope).encode())

    handled = await _consumer(fake).run_once([sub])

    assert handled == 0
    assert [t[2] for t in fake.dead_lettered] == [entry_id]


async def test_missing_id_is_dead_lettered_instead_of_killing_the_worker() -> None:
    """Regression for the 5.2-أ-recorded worker-killing bug (engine module
    docstring, policy 2): a type+workspaceid envelope WITHOUT an ``id``
    previously escaped ``_dispatch`` as a raw ``KeyError`` from the
    ``correlationid`` fallback -- crashing the worker loop, which restarted
    onto the SAME still-pending entry: a crash loop on one poisoned payload.
    It must instead be dead-lettered like any other unroutable envelope,
    with a registered handler never invoked."""
    fake = InMemoryStreamsConsumer()
    called = False

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        nonlocal called
        called = True

    sub = Subscription(
        stream="stream.media", group="cg.media", handlers={"media.job.requested.v1": handler}
    )
    envelope = json.loads(_envelope_bytes("media.job.requested.v1"))
    del envelope["id"]
    del envelope["correlationid"]  # force the fallback path that crashed
    entry_id = fake.seed("stream.media", json.dumps(envelope).encode())

    handled = await _consumer(fake).run_once([sub])  # must NOT raise

    assert handled == 0
    assert called is False
    assert fake.dead_lettered == [("stream.media", "cg.media", entry_id, "unroutable_envelope", 1)]


# --------------------------------------------------------------------------- #
# Handler failure x N -> DLQ (04 §3's «بعد N=5»; 5.2-ب)                       #
# --------------------------------------------------------------------------- #
async def test_handler_failures_below_the_threshold_redeliver_without_dlq() -> None:
    fake = InMemoryStreamsConsumer()

    async def always_fails(ctx: ExecutionContext, envelope: Json) -> None:
        raise RuntimeError("still transient, maybe")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": always_fails}
    )
    entry_id = fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1"))
    consumer = _consumer(fake, max_deliveries=3)

    await consumer.run_once([sub])  # attempt 1 (fresh)
    await consumer.run_once([sub])  # attempt 2 (recovered)

    assert fake.dead_lettered == []
    assert fake.acked == []
    assert entry_id in fake.pending[("stream.memory", "cg.memory")]  # still retryable


async def test_handler_failure_at_the_threshold_is_dead_lettered_with_the_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Attempt N (here N=3) fails -> the entry moves to ``<stream>.dlq``
    with a reason naming the exception, leaves pending, and is NEVER
    retried again -- a fourth run_once finds nothing."""
    fake = InMemoryStreamsConsumer()
    attempts = 0

    async def always_fails(ctx: ExecutionContext, envelope: Json) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("permanently broken handler dependency")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": always_fails}
    )
    entry_id = fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1"))
    consumer = _consumer(fake, max_deliveries=3)

    with caplog.at_level(logging.ERROR):
        await consumer.run_once([sub])  # 1
        await consumer.run_once([sub])  # 2
        await consumer.run_once([sub])  # 3 -> DLQ
        fourth = await consumer.run_once([sub])

    assert attempts == 3
    assert fourth == 0
    assert len(fake.dead_lettered) == 1
    stream, group, dlq_entry, reason, deliveries = fake.dead_lettered[0]
    assert (stream, group, dlq_entry) == ("stream.memory", "cg.memory", entry_id)
    assert reason.startswith("handler_failed: RuntimeError")
    assert "permanently broken handler dependency" in reason
    assert deliveries == 3
    assert fake.pending[("stream.memory", "cg.memory")] == {}
    assert any(record.getMessage() == "dead_lettered" for record in caplog.records)


async def test_a_success_before_the_threshold_never_reaches_the_dlq() -> None:
    """The counter counts DELIVERIES, not failures of other entries: an
    entry that succeeds on attempt 2 of 3 is acked normally."""
    fake = InMemoryStreamsConsumer()
    attempts = 0

    async def flaky(ctx: ExecutionContext, envelope: Json) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient failure")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": flaky}
    )
    entry_id = fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1"))
    consumer = _consumer(fake, max_deliveries=3)

    assert await consumer.run_once([sub]) == 0  # attempt 1 fails
    assert await consumer.run_once([sub]) == 1  # attempt 2 succeeds

    assert fake.dead_lettered == []
    assert fake.acked == [("stream.memory", "cg.memory", entry_id)]


# --------------------------------------------------------------------------- #
# One read per GROUP covering multiple streams (the knowledge shape)          #
# --------------------------------------------------------------------------- #
async def test_one_read_call_per_group_covers_every_stream_in_that_group() -> None:
    fake = InMemoryStreamsConsumer()

    async def noop(ctx: ExecutionContext, envelope: Json) -> None:
        return None

    subs = [
        Subscription(
            stream="stream.files", group="cg.knowledge", handlers={"files.file.uploaded.v1": noop}
        ),
        Subscription(
            stream="stream.knowledge",
            group="cg.knowledge",
            handlers={"knowledge.document.registered.v1": noop},
        ),
    ]
    fake.seed("stream.files", _envelope_bytes("files.file.uploaded.v1"))
    fake.seed("stream.knowledge", _envelope_bytes("knowledge.document.registered.v1"))

    handled = await _consumer(fake).run_once(subs)

    assert handled == 2
    # ONE call covering both streams -- not one call per stream.
    assert fake.read_calls == [(("stream.files", "stream.knowledge"), "cg.knowledge")]


async def test_distinct_groups_each_get_their_own_read_call() -> None:
    fake = InMemoryStreamsConsumer()

    async def noop(ctx: ExecutionContext, envelope: Json) -> None:
        return None

    subs = [
        Subscription(
            stream="stream.media", group="cg.media", handlers={"media.job.requested.v1": noop}
        ),
        Subscription(
            stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": noop}
        ),
    ]

    await _consumer(fake).run_once(subs)

    assert sorted(fake.read_calls) == sorted(
        [(("stream.media",), "cg.media"), (("stream.memory",), "cg.memory")]
    )


# --------------------------------------------------------------------------- #
# setup() / run()                                                             #
# --------------------------------------------------------------------------- #
async def test_setup_ensures_group_for_every_subscription() -> None:
    fake = InMemoryStreamsConsumer()
    subs = [
        Subscription(stream="stream.files", group="cg.knowledge", handlers={}),
        Subscription(stream="stream.knowledge", group="cg.knowledge", handlers={}),
        Subscription(stream="stream.media", group="cg.media", handlers={}),
    ]

    await _consumer(fake).setup(subs)

    assert fake.groups == {
        ("stream.files", "cg.knowledge"),
        ("stream.knowledge", "cg.knowledge"),
        ("stream.media", "cg.media"),
    }


async def test_teardown_destroys_group_for_every_subscription() -> None:
    """§3.81: ``teardown`` is ``setup``'s counterpart -- run on a CLEAN
    shutdown so a per-process notify group never outlives its process."""
    fake = InMemoryStreamsConsumer()
    subs = [
        Subscription(stream="stream.files", group="cg.knowledge", handlers={}),
        Subscription(stream="stream.knowledge", group="cg.knowledge", handlers={}),
        Subscription(stream="stream.media", group="cg.media", handlers={}),
    ]
    await _consumer(fake).setup(subs)

    await _consumer(fake).teardown(subs)

    assert fake.groups == set()


async def test_teardown_deduplicates_a_group_shared_across_several_streams() -> None:
    """One consumer group may own several ``Subscription``\\ s across
    DIFFERENT streams (the class docstring's own ``cg.knowledge`` example) --
    ``teardown`` must destroy each DISTINCT ``(stream, group)`` pair exactly
    once, not once per subscription."""
    fake = InMemoryStreamsConsumer()
    subs = [
        Subscription(stream="stream.knowledge", group="cg.notify.h.1", handlers={}),
        Subscription(stream="stream.media", group="cg.notify.h.1", handlers={}),
    ]
    await _consumer(fake).setup(subs)

    await _consumer(fake).teardown(subs)

    assert fake.groups == set()


async def test_teardown_on_a_never_set_up_subscription_is_a_silent_no_op() -> None:
    fake = InMemoryStreamsConsumer()
    subs = [Subscription(stream="stream.media", group="cg.media", handlers={})]

    await _consumer(fake).teardown(subs)  # must not raise

    assert fake.groups == set()


async def test_run_calls_setup_once_then_loops_run_once_until_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = InMemoryStreamsConsumer()
    subs = [Subscription(stream="stream.media", group="cg.media", handlers={})]
    consumer = _consumer(fake)

    calls = {"n": 0}

    async def fake_run_once(subscriptions: list[Subscription]) -> int:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError()
        return 0

    monkeypatch.setattr(consumer, "run_once", fake_run_once)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run(subs)

    assert fake.groups == {("stream.media", "cg.media")}  # setup() ran
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# Liveness heartbeat (ت-3, docs/operational-findings.md §3)                    #
# --------------------------------------------------------------------------- #
async def test_an_idle_read_still_beats() -> None:
    """THE point of the heartbeat, and the case a naive "beat when a message
    is handled" implementation would get wrong: a worker on a quiet stream
    handles nothing for hours and is perfectly healthy. Beating only on
    handled messages would report every idle worker as dead."""
    fake = InMemoryStreamsConsumer()
    beats = CountingHeartbeat()
    subs = [Subscription(stream="stream.memory", group="cg.memory", handlers={})]

    handled = await _consumer(fake, heartbeat=beats).run_once(subs)

    assert handled == 0  # nothing to read
    assert beats.beats == 1  # ...and the completed XREADGROUP still counted


async def test_every_message_beats_so_a_long_batch_is_not_read_as_a_wedged_loop() -> None:
    fake = InMemoryStreamsConsumer()
    beats = CountingHeartbeat()

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        return None

    sub = Subscription(
        stream="stream.media", group="cg.media", handlers={"media.job.requested.v1": handler}
    )
    for _ in range(3):
        fake.seed("stream.media", _envelope_bytes("media.job.requested.v1"))

    await _consumer(fake, heartbeat=beats).run_once([sub])

    # One for the read, one per dispatched message.
    assert beats.beats == 4


async def test_a_failing_handler_still_beats() -> None:
    """Deliberate: a handler that raises is a MESSAGE-level failure (it is
    logged, retried and eventually dead-lettered by the policy above). The
    loop itself is turning perfectly, and reporting the container unhealthy
    for it would conflate a poison payload with a dead consumer."""
    fake = InMemoryStreamsConsumer()
    beats = CountingHeartbeat()

    async def handler(ctx: ExecutionContext, envelope: Json) -> None:
        raise RuntimeError("boom")

    sub = Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": handler}
    )
    fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1"))

    await _consumer(fake, heartbeat=beats).run_once([sub])

    assert beats.beats == 2


async def test_no_heartbeat_configured_is_a_silent_no_op() -> None:
    """A bare python -m app.workers.memory_worker and every test in this
    file construct the engine without one; it must not become required."""
    fake = InMemoryStreamsConsumer()
    subs = [Subscription(stream="stream.memory", group="cg.memory", handlers={})]

    assert await _consumer(fake).run_once(subs) == 0  # must not raise


# --------------------------------------------------------------------------- #
# Consumer housekeeping (ت-2, docs/operational-findings.md §2)                 #
# --------------------------------------------------------------------------- #
def _memory_sub() -> Subscription:
    return Subscription(stream="stream.memory", group="cg.memory", handlers={})


async def test_the_sweep_is_off_unless_both_knobs_are_wired() -> None:
    """The default matters as much as the behaviour: the API's own notify
    bridge builds a `StreamConsumer` too, and a consumer-level sweep there
    could delete a live bridge's registration -- so "off" must be what an
    unconfigured engine does, not merely what the workers happen to pass."""
    fake = InMemoryStreamsConsumer()
    fake.register("stream.memory", "cg.memory", "memory.deadhost.1", idle_ms=99_999_999)

    assert await _consumer(fake).sweep_stale([_memory_sub()]) == []
    assert fake.deleted_consumers == []


async def test_the_sweep_removes_a_ghost_and_keeps_the_live_reader() -> None:
    """The measured case (2026-08-13): a dead container's consumer entry
    sitting beside the live one, inflating `consumers` forever."""
    fake = InMemoryStreamsConsumer()
    fake.register("stream.memory", "cg.memory", "test-consumer", idle_ms=12)
    fake.register("stream.memory", "cg.memory", "memory.deadhost.1", idle_ms=3_600_000)

    swept = await _consumer(fake, sweep_interval_s=1, stale_idle_ms=900_000).sweep_stale(
        [_memory_sub()]
    )

    assert swept == ["memory.deadhost.1"]
    assert list(fake.consumers[("stream.memory", "cg.memory")]) == ["test-consumer"]


async def test_a_sweep_failure_never_reaches_the_read_loop() -> None:
    """Housekeeping must not be able to stop message processing: the sweep
    rides the same loop as `run_once`, so an exception escaping it would cost
    the worker its next XREADGROUP."""

    class _Exploding(InMemoryStreamsConsumer):
        async def list_consumers(self, stream: str, group: str) -> list[ConsumerInfo]:
            raise RuntimeError("redis is having a moment")

    engine = _consumer(_Exploding(), sweep_interval_s=1, stale_idle_ms=1)

    assert await engine.sweep_stale([_memory_sub()]) == []  # logged, not raised


async def test_the_loop_sweeps_on_its_own_schedule() -> None:
    """The wiring, not just the method: `run` must actually reach the sweep
    between reads. Driven with a tiny interval and cancelled as soon as the
    ghost is gone -- the loop itself never terminates by design."""

    class _Yielding(InMemoryStreamsConsumer):
        """The fake returns instantly, unlike a real blocking ``XREADGROUP``;
        without a yield the `while True` loop would starve this test's own
        polling coroutine of the event loop it shares."""

        async def read(self, **kwargs: object) -> list[StreamMessage]:
            await asyncio.sleep(0.001)
            return await super().read(**kwargs)  # type: ignore[arg-type]

    fake = _Yielding()
    fake.register("stream.memory", "cg.memory", "memory.deadhost.1", idle_ms=3_600_000)
    engine = _consumer(fake, block_ms=1, sweep_interval_s=0.01, stale_idle_ms=1_000)

    task = asyncio.create_task(engine.run([_memory_sub()]))
    try:
        async with asyncio.timeout(5):
            while ("stream.memory", "cg.memory", "memory.deadhost.1") not in fake.deleted_consumers:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()

    assert fake.deleted_consumers == [("stream.memory", "cg.memory", "memory.deadhost.1")]


# --------------------------------------------------------------------------- #
# DLQ reporting (ت-6, docs/operational-findings.md §6)                        #
# --------------------------------------------------------------------------- #
async def test_the_dlq_report_is_off_unless_the_knob_is_wired() -> None:
    """Same reason the sweep defaults to off: this engine also drives the
    API's notify bridge, whose streams belong to the WORKERS -- a bridge that
    watched them would report another process's backlog once per API worker
    process while the owning worker reports it once."""
    fake = InMemoryStreamsConsumer()
    fake.entries["stream.memory.dlq"] = [("1-0", b"{}")]

    assert await _consumer(fake).watch_dlq([_memory_sub()]) == []


async def test_the_report_names_the_backlog_it_found() -> None:
    """Reading is the whole feature, so the return value carries the same
    facts the log line does -- a caller (and this test) asserts on what was
    found, not on a call count."""
    fake = InMemoryStreamsConsumer()
    fake.entries["stream.memory.dlq"] = [("1-0", b"{}"), ("2-0", None)]

    found = await _consumer(fake, dlq_watch_interval_s=60).watch_dlq([_memory_sub()])

    assert [(b.stream, b.depth) for b in found] == [("stream.memory", 2)]


async def test_a_dlq_read_failure_never_reaches_the_read_loop() -> None:
    """Reporting is strictly less important than consuming: an exception
    escaping the watch would cost the worker its next XREADGROUP over a
    housekeeping read that the next tick would have retried anyway."""

    class _Exploding(InMemoryStreamsConsumer):
        async def dlq_backlog(self, stream: str) -> DlqBacklog:
            raise RuntimeError("redis is having a moment")

    engine = _consumer(_Exploding(), dlq_watch_interval_s=60)

    assert await engine.watch_dlq([_memory_sub()]) == []  # logged, not raised


async def test_the_loop_reports_a_backlog_on_its_very_first_pass(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one behaviour that distinguishes this timer from the sweep's: a
    DLQ backlog is durable state that predates this process, so an operator
    restarting a worker must be told about it immediately -- NOT after a full
    `dlq_watch_interval_s` (300 s by default, and the interval here is long
    enough that a "wait first" implementation could not pass this test)."""

    class _Yielding(InMemoryStreamsConsumer):
        async def read(self, **kwargs: object) -> list[StreamMessage]:
            await asyncio.sleep(0.001)
            return await super().read(**kwargs)  # type: ignore[arg-type]

    fake = _Yielding()
    fake.entries["stream.memory.dlq"] = [("1-0", b"{}")]
    engine = _consumer(fake, block_ms=1, dlq_watch_interval_s=3600)

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(engine.run([_memory_sub()]))
        try:
            async with asyncio.timeout(5):
                while not any(r.message == "dlq.backlog" for r in caplog.records):
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()

    reported = [r for r in caplog.records if r.message == "dlq.backlog"]
    assert reported and reported[0].depth == 1  # type: ignore[attr-defined]


async def test_deregister_removes_only_this_processs_own_entry() -> None:
    """A clean exit's counterpart to `setup` -- and deliberately NOT
    `teardown`: the group itself (`cg.memory`) must survive, or the module's
    delivery position resets to the stream tail."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    fake.register("stream.memory", "cg.memory", "test-consumer", idle_ms=5)
    fake.register("stream.memory", "cg.memory", "memory.otherhost.1", idle_ms=5)

    await _consumer(fake).deregister([_memory_sub()])

    assert fake.deleted_consumers == [("stream.memory", "cg.memory", "test-consumer")]
    assert ("stream.memory", "cg.memory") in fake.groups


async def test_deregister_keeps_the_entry_while_it_still_owns_messages() -> None:
    """Shutting down with unacked entries must not discard them: the
    tombstone is left for the next boot's sweep, which reclaims them to a
    live consumer FIRST and only then deletes the name."""
    fake = InMemoryStreamsConsumer()
    fake.register("stream.memory", "cg.memory", "test-consumer", idle_ms=5, pending=2)

    await _consumer(fake).deregister([_memory_sub()])

    assert fake.deleted_consumers == []


# --------------------------------------------------------------------------- #
# Capacity 5.1 -- bounded concurrency (`ح-6`)                                 #
# --------------------------------------------------------------------------- #
def _seed_batch(fake: InMemoryStreamsConsumer, subjects: Sequence[str]) -> None:
    for subject in subjects:
        fake.seed("stream.memory", _envelope_bytes("memory.item.stored.v1", subject=subject))


def _sub(handler: EventHandler) -> Subscription:
    return Subscription(
        stream="stream.memory", group="cg.memory", handlers={"memory.item.stored.v1": handler}
    )


async def test_concurrency_of_one_is_the_pre_5_1_loop_message_for_message() -> None:
    """The `م-8` reversal switch, asserted rather than asserted-about.

    ``WORKER_CONCURRENCY=1`` is what a deployment sets to put this engine back
    in the shape the 0.5 baseline is measured in, so "1 means sequential" has
    to be a property of the code and not of how a batch happened to schedule:
    strict arrival order, and never two handlers alive at once.
    """
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-b", "doc-c", "doc-d"])
    recorder = OverlapRecorder()

    handled = await _consumer(fake, concurrency=1).run_once([_sub(recorder)])

    assert handled == 4
    assert recorder.peak == 1
    assert recorder.started == ["doc-a", "doc-b", "doc-c", "doc-d"]
    assert recorder.finished == recorder.started


async def test_a_batch_of_distinct_aggregates_runs_through_every_lane() -> None:
    """The step's whole purpose: four messages about four different documents
    are four things this process can be waiting on at once, and the sequential
    loop made it wait on them one at a time."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-b", "doc-c", "doc-d"])
    recorder = OverlapRecorder()

    handled = await _consumer(fake, concurrency=4).run_once([_sub(recorder)])

    assert handled == 4
    assert recorder.peak == 4
    assert len(fake.acked) == 4


async def test_the_lanes_are_a_ceiling_not_a_suggestion() -> None:
    """Eight distinct aggregates through three lanes is three at a time, not
    eight -- otherwise `WORKER_CONCURRENCY` would bound nothing and the pool
    it sizes (`workers/bootstrap._worker_pool_size`) would be undersized by
    however wide the batch happened to be."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, [f"doc-{index}" for index in range(8)])
    recorder = OverlapRecorder()

    handled = await _consumer(fake, batch_count=8, concurrency=3).run_once([_sub(recorder)])

    assert handled == 8
    assert recorder.peak == 3


async def test_two_messages_about_one_aggregate_are_never_in_flight_together() -> None:
    """Invariant 2, and the reason the batch is partitioned rather than
    semaphored: «رسالتَين لمستندٍ واحد يجب ألّا تُعالَجا معاً».

    Four messages, one subject, four lanes available -- and still exactly one
    of them running at any instant, in arrival order.
    """
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-a", "doc-a", "doc-a"])
    recorder = OverlapRecorder()

    handled = await _consumer(fake, concurrency=4).run_once([_sub(recorder)])

    assert handled == 4
    assert recorder.peak == 1
    assert recorder.overlapped_subjects == set()


async def test_one_slow_aggregate_does_not_serialise_the_others() -> None:
    """The containing half of the invariant above: serialising an aggregate
    against ITSELF must not serialise it against anything else, or the lock
    would have cost exactly what the concurrency bought."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-a", "doc-b", "doc-c"])
    recorder = OverlapRecorder()

    await _consumer(fake, concurrency=4).run_once([_sub(recorder)])

    assert recorder.peak == 3  # a's two are one lane; b and c are their own
    assert any("doc-b" in pair for pair in recorder.overlapped_subjects)


async def test_a_message_with_no_usable_subject_serialises_with_nothing() -> None:
    """An envelope this engine cannot classify keeps its pre-5.1 treatment --
    it is dispatched, not filtered and not dead-lettered -- and it takes a
    lane of its own rather than being lumped with every other subject-less
    message into one accidental queue."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    for _ in range(3):
        fake.seed(
            "stream.memory",
            json.dumps(
                {
                    "type": "memory.item.stored.v1",
                    "workspaceid": "ws-1",
                    "id": str(new_uuid7()),
                }
            ).encode(),
        )
    recorder = OverlapRecorder()

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await recorder(ctx, {**envelope, "subject": envelope.get("id")})

    handled = await _consumer(fake, concurrency=3).run_once([_sub(_handle)])

    assert handled == 3
    assert recorder.peak == 3
    assert fake.dead_lettered == []


async def test_run_once_returns_only_after_every_lane_has_finished() -> None:
    """Invariant 3, expressed where it is actually enforced.

    ``run``'s loop fires ``sweep_stale``/``watch_dlq`` AFTER ``run_once``
    returns, and the engine's own comment promises the sweep "cannot fire while
    a handler is mid-flight". That promise is only true if the barrier is real
    -- a pipelined loop would have broken it silently.
    """
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-b", "doc-c", "doc-d"])
    recorder = OverlapRecorder(delay_s=0.02)

    await _consumer(fake, concurrency=4).run_once([_sub(recorder)])

    assert recorder.in_flight == 0
    assert len(recorder.finished) == 4


async def test_the_beat_lands_while_a_batch_is_still_in_flight() -> None:
    """Invariant 1: «النبضُ يجب أن يظلّ يعبّر عن حياة الحلقة لا عن اكتمال أطول
    مهمّة».

    Before 5.1 a beat could not happen while a handler ran, which is why two
    services raised ``HEARTBEAT_MAX_AGE_S`` to 600. Concurrency would have made
    the silence ``concurrency`` times likelier to hit the longest handler in a
    batch, so the loop now beats on ``block_ms`` for as long as -- and ONLY as
    long as -- a batch is in flight.
    """
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-b"])
    heartbeat = CountingHeartbeat()
    seen: list[int] = []

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await asyncio.sleep(0.08)
        seen.append(heartbeat.beats)

    await _consumer(fake, block_ms=10, concurrency=2, heartbeat=heartbeat).run_once([_sub(_handle)])

    # The read's own beat is 1; anything above it landed DURING the handlers.
    assert max(seen) > 1


async def test_nothing_beats_while_the_loop_is_merely_waiting_for_a_read() -> None:
    """The containing half: a ticker that beat unconditionally would report a
    loop wedged inside ``read`` as healthy forever -- which is the exact
    failure ``HealthSettings.heartbeat_max_age_s`` exists to catch. The ticker
    lives and dies with a batch, so an empty read leaves exactly the one beat
    ``run_once`` has always produced."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    heartbeat = CountingHeartbeat()

    async def _never_called(ctx: ExecutionContext, envelope: Json) -> None:  # pragma: no cover
        raise AssertionError("no message was seeded")

    await _consumer(fake, block_ms=1, concurrency=4, heartbeat=heartbeat).run_once(
        [_sub(_never_called)]
    )
    await asyncio.sleep(0.05)

    assert heartbeat.beats == 1


async def test_request_stop_leaves_the_loop_after_the_batch_in_flight() -> None:
    """Invariant 4's engine half: a stop is not a cancellation. The message
    this process already took out of the stream is finished and acked; the
    loop simply does not read again."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a", "doc-b"])
    consumer = _consumer(fake, block_ms=1, concurrency=2)
    finished: list[str] = []

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        consumer.request_stop()
        await asyncio.sleep(0.01)
        finished.append(str(envelope["subject"]))

    await asyncio.wait_for(consumer.run([_sub(_handle)]), timeout=2.0)

    assert sorted(finished) == ["doc-a", "doc-b"]
    assert len(fake.acked) == 2


async def test_a_permanent_failure_is_dead_lettered_on_the_first_delivery() -> None:
    """Invariant 5: «العابرُ يُعاد، وخطأُ التحقّق أو نوعٌ غير مدعومٍ يذهب إلى
    DLQ من أوّل مرّة».

    ``delivery_count`` is 1 here, four short of ``max_deliveries`` -- and the
    entry still leaves the pipeline, because attempt 5 would decode, route and
    fail on exactly the same bytes attempt 1 did.
    """
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a"])

    async def _reject(ctx: ExecutionContext, envelope: Json) -> None:
        raise UnsupportedTypeError("application/x-nope")

    handled = await _consumer(fake, max_deliveries=5).run_once([_sub(_reject)])

    assert handled == 0
    assert len(fake.dead_lettered) == 1
    _stream, _group, _entry, reason, deliveries = fake.dead_lettered[0]
    assert deliveries == 1
    assert reason.startswith("handler_rejected: UnsupportedTypeError")


async def test_a_conflict_keeps_its_whole_retry_budget() -> None:
    """The two 4xx that are NOT permanent, and the reason including them would
    be a bug: ``409`` means "someone else got there first, come back" (10 §6's
    optimistic-lock convention). A budget spent on the first collision would
    dead-letter the message a retry was going to fix."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a"])

    async def _conflict(ctx: ExecutionContext, envelope: Json) -> None:
        raise ConflictError("version moved")

    await _consumer(fake, max_deliveries=5).run_once([_sub(_conflict)])

    assert fake.dead_lettered == []
    assert fake.acked == []  # still pending, i.e. redelivered


async def test_a_rate_limit_keeps_its_whole_retry_budget() -> None:
    """``429``'s the other one, and it says the same thing louder."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a"])

    async def _throttled(ctx: ExecutionContext, envelope: Json) -> None:
        raise AppError("slow down", code="common.rate_limited")

    await _consumer(fake, max_deliveries=5).run_once([_sub(_throttled)])

    assert fake.dead_lettered == []


async def test_a_server_side_failure_still_spends_the_budget_one_delivery_at_a_time() -> None:
    """The containing guard for the whole classification: a 5xx -- and
    anything that is not an ``AppError`` at all, which is every driver error,
    socket reset and timeout -- is the transient class 04 §3's ``N=5`` was
    written for, and 5.1 must not have quietly moved it."""
    fake = InMemoryStreamsConsumer()
    await fake.ensure_group("stream.memory", "cg.memory")
    _seed_batch(fake, ["doc-a"])

    async def _boom(ctx: ExecutionContext, envelope: Json) -> None:
        raise TimeoutError("qdrant did not answer")

    consumer = _consumer(fake, max_deliveries=3)
    for _ in range(2):
        await consumer.run_once([_sub(_boom)])
        assert fake.dead_lettered == []
    await consumer.run_once([_sub(_boom)])

    assert len(fake.dead_lettered) == 1
    assert fake.dead_lettered[0][3].startswith("handler_failed: TimeoutError")


# --------------------------------------------------------------------------- #
# run_forever -- a loop whose owner does not exit when it dies (2026-09-28)    #
# --------------------------------------------------------------------------- #
class _FlakyReads(InMemoryStreamsConsumer):
    """Fails a read the two ways the real adapter does when Redis misbehaves:
    the next ``failures`` reads time out, and a read of a group that no longer
    exists fails ``NOGROUP`` -- both as the adapter's own ``AppError``. Yields
    on every read, as a blocking ``XREADGROUP`` does, so the loop can run as a
    task beside the test."""

    def __init__(self, *, failures: int = 0) -> None:
        super().__init__()
        self.failures = failures

    async def read(
        self,
        *,
        streams: Sequence[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[StreamMessage]:
        await asyncio.sleep(0.001)
        if self.failures > 0:
            self.failures -= 1
            raise AppError("event consume failed", code="common.internal")
        if any((stream, group) not in self.groups for stream in streams):
            raise AppError("event consume failed", code="common.internal")
        return await super().read(
            streams=streams, group=group, consumer=consumer, count=count, block_ms=block_ms
        )


def _retries(caplog: pytest.LogCaptureFixture) -> list[float]:
    return [
        record.retry_in_s  # type: ignore[attr-defined]
        for record in caplog.records
        if record.message == "consumer_loop_failed"
    ]


async def test_run_still_lets_a_read_failure_escape() -> None:
    """The workers' half, unchanged: their loop IS their process, so the
    failure must still reach ``workers/lifecycle.py``, which exits and lets
    the container restart."""
    with pytest.raises(AppError):
        await _consumer(_FlakyReads(failures=1)).run([_memory_sub()])


async def test_run_forever_reads_again_after_a_failed_read() -> None:
    """The 2026-09-28 death, replayed: one read times out, and a message
    published afterwards must still reach its handler."""
    fake = _FlakyReads(failures=1)
    _seed_batch(fake, ["doc-a"])
    handled = asyncio.Event()

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        handled.set()

    engine = _consumer(fake, block_ms=1)
    task = asyncio.create_task(engine.run_forever([_sub(_handle)], first_backoff_s=0.001))
    try:
        await asyncio.wait_for(handled.wait(), timeout=5)
    finally:
        task.cancel()

    assert len(fake.acked) == 1


async def test_run_forever_recreates_a_group_that_vanished() -> None:
    """Why ``run`` is re-entered rather than the read retried: a group
    destroyed while the loop runs fails every later read ``NOGROUP``, and only
    ``setup`` brings it back."""
    fake = _FlakyReads()
    handled = asyncio.Event()

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        handled.set()

    engine = _consumer(fake, block_ms=1)
    task = asyncio.create_task(engine.run_forever([_sub(_handle)], first_backoff_s=0.001))
    try:
        async with asyncio.timeout(5):
            while not fake.read_calls:
                await asyncio.sleep(0.01)
            await fake.destroy_group("stream.memory", "cg.memory")
            _seed_batch(fake, ["doc-a"])
            await handled.wait()
    finally:
        task.cancel()

    assert ("stream.memory", "cg.memory") in fake.groups


async def test_run_forever_doubles_its_backoff_only_for_failures_in_a_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three failures in a row wait longer each time, up to the ceiling; one
    after a clean pass starts over, because it is a new outage."""
    fake = _FlakyReads(failures=3)
    engine = _consumer(fake, block_ms=1)

    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(
            engine.run_forever([_memory_sub()], first_backoff_s=0.001, max_backoff_s=0.003)
        )
        try:
            async with asyncio.timeout(5):
                while len(fake.read_calls) < 3:
                    await asyncio.sleep(0.01)
                fake.failures = 1
                while len(_retries(caplog)) < 4:
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()

    assert _retries(caplog) == pytest.approx([0.001, 0.002, 0.003, 0.001])


async def test_run_forever_returns_once_stopped() -> None:
    fake = _FlakyReads()
    _seed_batch(fake, ["doc-a"])
    engine = _consumer(fake, block_ms=1)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        engine.request_stop()

    await asyncio.wait_for(engine.run_forever([_sub(_handle)]), timeout=2.0)

    assert len(fake.acked) == 1


async def test_run_forever_still_stops_on_cancellation() -> None:
    """The lifespan cancels the bridge at shutdown and then awaits it; a
    retry loop that caught the cancellation would hang every deploy."""
    engine = _consumer(_FlakyReads(failures=1_000), block_ms=1)
    task = asyncio.create_task(engine.run_forever([_memory_sub()], first_backoff_s=0.001))
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
