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
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.observability import Heartbeat
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import ConsumerInfo, StreamMessage


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


def _envelope_bytes(
    event_type: str,
    *,
    workspace_id: str = "ws-1",
    correlation_id: str | None = "corr-1",
    data: Json | None = None,
) -> bytes:
    """A REAL CloudEvents envelope (``build_envelope``, not a hand-rolled
    dict) serialized to bytes -- exactly what ``RedisStreamsConsumer.read``
    would hand back as ``StreamMessage.raw``."""
    envelope = build_envelope(
        event_id=new_uuid7(),
        source="test",
        event_type=event_type,
        subject="subject-1",
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
    )


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
