"""Live-Redis tests for ``RedisStreamsConsumer`` (5.1-ج).

``live_redis`` only -- this file exercises real ``XGROUP``/``XREADGROUP``/
``XACK`` against a real local Redis. Every stream/group name is unique per
test (``stream.test.<uuid>``/``cg.test.<uuid>`` -- the ``test_outbox_relay_
live.py`` module docstring's own precedent, R6) with ``XGROUP DESTROY`` +
``DEL`` cleanup in ``finally`` -- never ``FLUSH`` (the server may hold
unrelated data).
"""

from __future__ import annotations

import json

import pytest
from redis.asyncio import Redis

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

pytestmark = [pytest.mark.live_redis]

_CE = b"ce"


async def _cleanup(client: Redis, stream: str, groups: list[str]) -> None:
    for group in groups:
        await client.xgroup_destroy(stream, group)
    await client.delete(stream)


@pytest.mark.anyio
async def test_an_unacked_entry_is_redelivered_on_the_next_read(redis_client: Redis) -> None:
    """Proves ``RedisStreamsConsumer.read``'s own "recovery pass" deviation
    (its class docstring) against REAL Redis: a plain ``>`` read alone could
    never do this (verified empirically while designing the adapter)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    try:
        await consumer.ensure_group(stream, group)
        await redis_client.xadd(stream, {_CE: b'{"id":"evt-1"}'})

        first = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert len(first) == 1
        entry_id = first[0].entry_id
        assert first[0].raw == b'{"id":"evt-1"}'
        # deliberately NOT acked -- proving redelivery, not a fresh XADD.

        second = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert [message.entry_id for message in second] == [entry_id]

        await consumer.ack(stream, group, entry_id)

        third = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert third == []
    finally:
        await _cleanup(redis_client, stream, [group])


@pytest.mark.anyio
async def test_two_groups_on_one_stream_each_independently_receive_the_entry(
    redis_client: Redis,
) -> None:
    """D-20's own premise: a global event fans out to EVERY consumer group
    on its stream (``cg.knowledge`` and a future ``cg.notify`` both read
    ``stream.knowledge`` independently, 04 §2's topology table) -- each
    group gets its own delivery cursor and PEL over the SAME entries."""
    stream = f"stream.test.{new_uuid7()}"
    group_a = f"cg.test.a.{new_uuid7()}"
    group_b = f"cg.test.b.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    try:
        await consumer.ensure_group(stream, group_a)
        await consumer.ensure_group(stream, group_b)
        await redis_client.xadd(stream, {_CE: b'{"id":"evt-1"}'})

        a_messages = await consumer.read(
            streams=[stream], group=group_a, consumer="c1", count=10, block_ms=200
        )
        b_messages = await consumer.read(
            streams=[stream], group=group_b, consumer="c1", count=10, block_ms=200
        )

        assert len(a_messages) == 1
        assert len(b_messages) == 1
        assert a_messages[0].entry_id == b_messages[0].entry_id == b_messages[0].entry_id
        assert a_messages[0].raw == b_messages[0].raw == b'{"id":"evt-1"}'

        # Acking group A must not affect group B's own PEL -- independent
        # cursors, proven by group B still redelivering after A is clean.
        await consumer.ack(stream, group_a, a_messages[0].entry_id)
        a_again = await consumer.read(
            streams=[stream], group=group_a, consumer="c1", count=10, block_ms=200
        )
        b_again = await consumer.read(
            streams=[stream], group=group_b, consumer="c1", count=10, block_ms=200
        )
        assert a_again == []
        assert [message.entry_id for message in b_again] == [b_messages[0].entry_id]

        await consumer.ack(stream, group_b, b_messages[0].entry_id)
    finally:
        await _cleanup(redis_client, stream, [group_a, group_b])


@pytest.mark.anyio
async def test_engine_redelivers_to_a_handler_that_raised_on_the_first_attempt(
    redis_client: Redis,
) -> None:
    """Mutation-battery target #2's LIVE half: an ``except`` that XACKs on
    handler failure would make the SECOND ``run_once`` see nothing here --
    against REAL ``XREADGROUP``/``XACK``, not just the hermetic
    ``InMemoryStreamsConsumer`` twin (``test_stream_consumer.py``)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    attempts: list[Json] = []

    async def flaky(ctx: ExecutionContext, envelope: Json) -> None:
        attempts.append(envelope)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")

    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.requested.v1": flaky}
    )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="c1",
        block_ms=200,
        batch_count=10,
        max_deliveries=5,
    )
    try:
        await consumer.setup([subscription])
        envelope = build_envelope(
            event_id=new_uuid7(),
            source="test",
            event_type="media.job.requested.v1",
            subject="job-1",
            occurred_at=utc_now(),
            workspace_id="ws-1",
            data={},
        )
        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        first = await consumer.run_once([subscription])
        assert first == 0
        assert len(attempts) == 1

        second = await consumer.run_once([subscription])
        assert second == 1
        assert len(attempts) == 2

        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0
    finally:
        await _cleanup(redis_client, stream, [group])


@pytest.mark.anyio
async def test_ensure_group_is_idempotent_across_repeated_calls(redis_client: Redis) -> None:
    """A worker restart re-runs ``setup()`` -> ``ensure_group`` again on the
    same ``(stream, group)`` -- ``BUSYGROUP`` must be swallowed, not raised,
    against a REAL Redis (the hermetic ``ResponseError`` test's live twin)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    try:
        await consumer.ensure_group(stream, group)
        await consumer.ensure_group(stream, group)  # must not raise
        await consumer.ensure_group(stream, group)  # a third time, for good measure
    finally:
        await _cleanup(redis_client, stream, [group])


# --------------------------------------------------------------------------- #
# 5.2-ب: delivery counts + DLQ, live                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_delivery_count_grows_across_recovery_redeliveries(redis_client: Redis) -> None:
    """The empirically-verified semantics ``StreamMessage.delivery_count``'s
    docstring documents, proven through the adapter itself against real
    Redis: fresh delivery counts 1, every un-acked recovery re-read counts
    one more -- the counter genuinely counts processing attempts."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    try:
        await consumer.ensure_group(stream, group)
        await redis_client.xadd(stream, {_CE: b'{"id":"evt-1"}'})

        first = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert [message.delivery_count for message in first] == [1]

        second = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert [message.delivery_count for message in second] == [2]

        third = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert [message.delivery_count for message in third] == [3]

        await consumer.ack(stream, group, first[0].entry_id)
    finally:
        await _cleanup(redis_client, stream, [group])


@pytest.mark.anyio
async def test_a_persistently_failing_handler_is_dead_lettered_after_n_attempts(
    redis_client: Redis,
) -> None:
    """04 §3's «بعد N=5 ⇒ يُنقل إلى stream.<m>.dlq مع سبب», live end to end
    (N=3 here): the real engine drives real redeliveries off the real PEL
    until the threshold, then the entry lands on ``<stream>.dlq`` carrying
    the ORIGINAL ``ce`` bytes verbatim + the failure bookkeeping, and the
    source group's pending count drops to zero -- the poison event is out
    of the pipeline without being lost."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    dlq = f"{stream}.dlq"
    attempts: list[Json] = []

    async def always_fails(ctx: ExecutionContext, envelope: Json) -> None:
        attempts.append(envelope)
        raise RuntimeError("permanently broken dependency")

    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.requested.v1": always_fails}
    )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="c1",
        block_ms=200,
        batch_count=10,
        max_deliveries=3,
    )
    try:
        await consumer.setup([subscription])
        envelope = build_envelope(
            event_id=new_uuid7(),
            source="test",
            event_type="media.job.requested.v1",
            subject="job-1",
            occurred_at=utc_now(),
            workspace_id="ws-1",
            data={},
        )
        ce_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        await redis_client.xadd(stream, {_CE: ce_bytes})

        assert await consumer.run_once([subscription]) == 0  # attempt 1
        assert await consumer.run_once([subscription]) == 0  # attempt 2
        assert await consumer.run_once([subscription]) == 0  # attempt 3 -> DLQ
        assert await consumer.run_once([subscription]) == 0  # nothing left
        assert len(attempts) == 3

        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        dlq_entries = await redis_client.xrange(dlq)
        assert len(dlq_entries) == 1
        _, fields = dlq_entries[0]
        assert fields[_CE] == ce_bytes  # verbatim, re-injectable
        assert fields[b"reason"].startswith(b"handler_failed: RuntimeError")
        assert fields[b"source_stream"] == stream.encode()
        assert fields[b"consumer_group"] == group.encode()
        assert fields[b"deliveries"] == b"3"
    finally:
        await _cleanup(redis_client, stream, [group])
        await redis_client.delete(dlq)


@pytest.mark.anyio
async def test_a_malformed_entry_is_dead_lettered_immediately(redis_client: Redis) -> None:
    """Policy 1 live: an entry with no ``ce`` field at all (a poison XADD
    from outside the producer path) moves to the DLQ on its FIRST delivery
    -- no retry budget for permanently-broken bytes -- and the pipeline
    keeps flowing (nothing pending afterwards)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    dlq = f"{stream}.dlq"
    subscription = Subscription(stream=stream, group=group, handlers={})
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="c1",
        block_ms=200,
        batch_count=10,
        max_deliveries=5,
    )
    try:
        await consumer.setup([subscription])
        await redis_client.xadd(stream, {b"not-ce": b"poison"})

        assert await consumer.run_once([subscription]) == 0

        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        dlq_entries = await redis_client.xrange(dlq)
        assert len(dlq_entries) == 1
        _, fields = dlq_entries[0]
        assert _CE not in fields  # nothing to preserve -- bookkeeping only
        assert fields[b"reason"] == b"malformed_envelope"
        assert fields[b"deliveries"] == b"1"  # first delivery, immediate transfer
    finally:
        await _cleanup(redis_client, stream, [group])
        await redis_client.delete(dlq)
