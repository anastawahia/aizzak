"""Live-Redis proof for the DLQ operator tool (``app.ops.dlq``, P1-4,
``docs/p1-hardening-plan.md`` §3 step 7).

``live_redis`` only, and every stream/group is uniquely named per test
(``stream.test.<uuid>``/``cg.test.<uuid>`` -- the ``test_stream_consumer_
live.py``/``test_notify_bridge_per_process_group_live.py`` precedent, R6)
with cleanup in ``finally``. This file never touches the real
``stream.knowledge``/``stream.media``.

The exit criterion this file exists for: a real message dead-lettered through
the SAME ``RedisStreamsConsumer.dead_letter`` production code path, requeued
through ``app.ops.dlq.requeue``, and proven to arrive at a fresh
``XREADGROUP`` read of the source stream -- not merely "no exception was
raised". Hermetic entry-parsing/pitfall coverage (the two classes,
``purge``'s ``--yes`` gate) lives in ``tests/unit/test_ops_dlq.py``.
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from app.framework.identifiers import new_uuid7
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from app.ops.dlq import peek, purge, requeue

pytestmark = [pytest.mark.live_redis]

_CE = b"ce"


async def _cleanup(client: Redis, stream: str, group: str) -> None:
    await client.xgroup_destroy(stream, group)
    await client.delete(stream)
    await client.delete(f"{stream}.dlq")


@pytest.mark.anyio
async def test_a_dead_lettered_entry_is_requeued_and_reaches_a_fresh_consumer_read(
    redis_client: Redis,
) -> None:
    """The full operator round trip: a message fails, the engine's own
    ``dead_letter`` moves it (production code, not a fake), ``peek`` shows it
    with ``reinjectable=True``, ``requeue`` copies the SAME ``ce`` bytes back
    onto the source stream, and a fresh ``XREADGROUP`` on that stream proves
    a consumer genuinely receives it -- as a NEW entry id, Streams ids never
    reused."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    original_ce = b'{"type":"knowledge.document.registered.v1","id":"evt-1"}'
    try:
        await consumer.ensure_group(stream, group)
        await redis_client.xadd(stream, {_CE: original_ce})

        [message] = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        original_entry_id = message.entry_id
        assert message.raw == original_ce

        # The engine's OWN transfer -- exhausted retries, `handler_failed`
        # (04 §3's `N=5` shape, here just one call since the reason string
        # is what this test cares about, not the retry count).
        await consumer.dead_letter(
            stream=stream,
            group=group,
            entry_id=original_entry_id,
            raw=message.raw,
            reason="handler_failed: RuntimeError: boom",
            delivery_count=message.delivery_count,
        )

        entries = await peek(redis_client, stream)
        assert len(entries) == 1
        [dlq_entry] = entries
        assert dlq_entry.reinjectable is True
        assert dlq_entry.ce == original_ce
        assert dlq_entry.source_stream == stream
        assert dlq_entry.source_entry_id == original_entry_id
        assert dlq_entry.consumer_group == group
        assert dlq_entry.reason == "handler_failed: RuntimeError: boom"

        # The source group's PEL is empty -- `dead_letter`'s own XACK half.
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        new_entry_id = await requeue(redis_client, stream, dlq_entry.entry_id)
        assert new_entry_id != original_entry_id

        # PROOF of arrival: a fresh `>` read on the SAME group/stream sees
        # the reinjected entry as a brand-new delivery.
        [redelivered] = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert redelivered.entry_id == new_entry_id
        assert redelivered.raw == original_ce
        assert redelivered.delivery_count == 1  # a fresh entry, not a recovery
        await consumer.ack(stream, group, new_entry_id)

        # The DLQ copy is untouched until an explicit purge.
        assert len(await peek(redis_client, stream)) == 1
        deleted = await purge(redis_client, stream, [dlq_entry.entry_id])
        assert deleted == 1
        assert await peek(redis_client, stream) == []
    finally:
        await _cleanup(redis_client, stream, group)


@pytest.mark.anyio
async def test_a_ce_less_malformed_entry_is_visible_but_refused_on_requeue(
    redis_client: Redis,
) -> None:
    """Pitfall 1, against real Redis bytes (not the hermetic stub): a poison
    ``XADD`` with no ``ce`` field at all dead-letters as bookkeeping-only,
    ``peek`` marks it ``reinjectable=False``, and ``requeue`` refuses it by
    name instead of XADDing an empty envelope."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    consumer = RedisStreamsConsumer(redis_client)
    try:
        await consumer.ensure_group(stream, group)
        await redis_client.xadd(stream, {b"not-ce": b"poison"})

        [message] = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )
        assert message.raw is None

        await consumer.dead_letter(
            stream=stream,
            group=group,
            entry_id=message.entry_id,
            raw=message.raw,
            reason="malformed_envelope",
            delivery_count=message.delivery_count,
        )

        [dlq_entry] = await peek(redis_client, stream)
        assert dlq_entry.reinjectable is False
        assert dlq_entry.ce is None
        assert dlq_entry.reason == "malformed_envelope"

        with pytest.raises(ValueError, match="bookkeeping-only"):
            await requeue(redis_client, stream, dlq_entry.entry_id)

        # Refused cleanly -- nothing landed on the source stream from this.
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0
    finally:
        await _cleanup(redis_client, stream, group)
