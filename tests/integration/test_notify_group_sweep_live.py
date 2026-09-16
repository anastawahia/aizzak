"""Live-Redis proof that a departed replica's notify groups are actually
reclaimed -- the second half of capacity 5.4's acceptance criterion.

**This file exists because the rule it tests could not fire.** ت-2 shipped a
timed sweep for the ``cg.notify.<host>.<pid>`` family, and its gate 1 asked
whether a consumer entry was registered under the group. Redis never removes
a consumer entry when its process dies -- the first sentence of
``sweeper.py``'s own module docstring -- so every uncleanly-killed bridge
left a group reporting ``consumers: 1`` forever, and the gate never passed
for it. The unit tests all passed throughout: every one of them constructed
its dead group with ``consumers=0``, which is the one state a real dead
bridge is never in.

Measured on the live stack 2026-09-10, seventeen minutes after a full
``--force-recreate``: 25 notify groups on ``stream.knowledge`` from seven
hostnames, four of them dead for 6-11 hours, ``app.ops.notify_groups list``
reporting every single one ``LIVE: 1 consumer(s) registered``, and the timed
sweep having fired on schedule and collected exactly one group -- the only
one that happened to hold no consumer row at all.

**So the test had to be built the way the leak is built**: a bridge that
registers a real consumer by really reading, and is then abandoned rather
than shut down. ``deregister_consumer``/``teardown_notify_bridge`` are
deliberately never called here -- calling them would clean up the very
tombstone under test, which is precisely why a CLEAN exit was never the case
that leaked.

Private, uniquely-named streams per run (the
``test_notify_bridge_per_process_group_live`` precedent, R6): this file must
never destroy a group on the shared ``stream.knowledge``/``stream.media``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.infrastructure.messaging.consumers.sweeper import (
    destroy_orphan_notify_groups,
    find_orphan_notify_groups,
    is_orphan,
    read_notify_groups,
)
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

pytestmark = [pytest.mark.live_redis, pytest.mark.asyncio]

# Far below any real `CONSUMER_STALE_IDLE_S`, because a test cannot wait 15
# minutes. The RELATION is what the rule's safety rests on (a live reader's
# idle never exceeds `consumer_block_ms`), and this file asserts that relation
# directly in `test_a_reader_that_is_actually_reading_stays_live` rather than
# trusting the production number to stand in for it.
_STALE_MS = 400


@pytest.fixture
def streams() -> tuple[str, ...]:
    run = uuid.uuid4().hex[:12]
    return (f"test.stream.notify-sweep.{run}",)


async def _register(consumer: RedisStreamsConsumer, stream: str, group: str) -> None:
    """Create a group and register a consumer inside it by actually reading --
    the only way to produce the state a dead bridge leaves behind.

    ``XGROUP CREATECONSUMER`` would also make an entry, but not the one under
    test: a bridge's consumer is born of an ``XREADGROUP``, and this file's
    whole claim is about what that read leaves on the server.
    """
    await consumer.ensure_group(stream, group)
    await consumer.read(
        streams=[stream], group=group, consumer=group.removeprefix("cg."), count=1, block_ms=1
    )


async def test_an_abandoned_bridges_group_is_reclaimed(
    redis_client: Redis, streams: tuple[str, ...]
) -> None:
    """⚠️ THE regression. Before this fix the assertion below read
    ``orphans == []`` in every real deployment, because the abandoned group
    reports ``consumers: 1`` for as long as Redis is up."""
    consumer = RedisStreamsConsumer(redis_client)
    stream = streams[0]
    group = f"cg.notify.deadhost{uuid.uuid4().hex[:8]}.7"
    await _register(consumer, stream, group)

    # The tombstone exists, and the OLD rule's verdict on it is asserted here
    # on real Redis rather than argued: `min_idle_ms=0` is that rule exactly
    # (`_living_reader`'s fallback branch), and it says LIVE -- forever, for a
    # process that will never boot again.
    [before] = await read_notify_groups(consumer, streams)
    assert before.consumers == 1
    assert [r.pending for r in before.readers] == [0]
    assert is_orphan(before, min_idle_ms=0) == (False, "live: 1 consumer(s) registered")

    # ... and it is still a tombstone: nothing has read under it since.
    await _wait_past(_STALE_MS)
    orphans = await find_orphan_notify_groups(
        consumer, streams, settle_seconds=0, min_idle_ms=_STALE_MS
    )
    assert [g.name for g in orphans] == [group]

    assert await destroy_orphan_notify_groups(consumer, orphans) == 1
    assert await read_notify_groups(consumer, streams) == []


async def test_a_reader_that_is_actually_reading_stays_live(
    redis_client: Redis, streams: tuple[str, ...]
) -> None:
    """The safety half, and the one that makes the test above mean something.

    A rule that swept everything would pass the regression test and destroy a
    working replica's group on every sweep. Here the same elapsed wall time
    passes, but the group is read once more inside it -- exactly what a
    blocking ``XREADGROUP`` does every ``consumer_block_ms`` -- and the
    verdict flips back to LIVE.
    """
    consumer = RedisStreamsConsumer(redis_client)
    stream = streams[0]
    group = f"cg.notify.livehost{uuid.uuid4().hex[:8]}.8"
    await _register(consumer, stream, group)

    await _wait_past(_STALE_MS)
    await _register(consumer, stream, group)  # the next blocking read lands

    [group_info] = await read_notify_groups(consumer, streams)
    orphan, reason = is_orphan(group_info, min_idle_ms=_STALE_MS)
    assert orphan is False, reason
    assert "live: read" in reason
    assert (
        await find_orphan_notify_groups(consumer, streams, settle_seconds=0, min_idle_ms=_STALE_MS)
        == []
    )

    await redis_client.xgroup_destroy(stream, group)


async def test_a_tombstone_holding_unacked_entries_is_refused(
    redis_client: Redis, streams: tuple[str, ...]
) -> None:
    """The refusal that outranks every idle reading. A dead consumer owning
    delivered-but-unacked entries owns MESSAGES; ``XGROUP DESTROY`` discards
    them where no ``XAUTOCLAIM`` can reach them afterwards. Long-dead is not
    a licence -- it is the case where reclaiming matters most.
    """
    consumer = RedisStreamsConsumer(redis_client)
    stream = streams[0]
    group = f"cg.notify.deadhost{uuid.uuid4().hex[:8]}.9"
    await consumer.ensure_group(stream, group)
    await redis_client.xadd(stream, {b"ce": b"{}"})
    delivered = await consumer.read(
        streams=[stream], group=group, consumer="notify.dead.9", count=10, block_ms=1
    )
    assert len(delivered) == 1  # delivered and NOT acked -- it is now pending

    await _wait_past(_STALE_MS)
    [group_info] = await read_notify_groups(consumer, streams)
    orphan, reason = is_orphan(group_info, min_idle_ms=_STALE_MS)

    assert orphan is False
    assert "XAUTOCLAIM" in reason

    await redis_client.xgroup_destroy(stream, group)
    await redis_client.delete(stream)


async def _wait_past(min_idle_ms: int) -> None:
    """Sleep until any consumer registered before the call is past
    ``min_idle_ms``, with margin -- `idle` is measured by the SERVER's clock,
    so the wait has to be real rather than monkeypatched."""
    await asyncio.sleep(min_idle_ms / 1000 * 1.5)
