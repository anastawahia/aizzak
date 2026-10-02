"""``queue_wait_seconds`` on a real Redis — capacity-plan step 5.3.

The number the indexing queue's admission gate reads
(``api/middleware/queue_backpressure.py``). The property that made it the
gate's number rather than ``stream_lag_seconds`` is a property of Redis's
stream ids, so it is proven here against a real server and not a fake:

**after a quiet spell, an entry nobody has been handed yet reads as seconds of
WAIT, while the head-to-cursor gap reads as the length of the SILENCE.** A
gate on the gap would refuse the first uploads of every burst that follows a
quiet hour.

Keys are this test's own (``live_redis``'s rule: the default server is the
development stack's ``redis-stream``), and removed afterwards.
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from app.framework.identifiers import new_uuid7
from app.infrastructure.monitoring.metrics_source import queue_wait_seconds

pytestmark = [pytest.mark.live_redis, pytest.mark.anyio]


async def _head_gap_s(redis: Redis, stream: str, group: str) -> float:
    """``stream_lag_seconds``'s arithmetic, for the same pair."""
    info = await redis.xinfo_stream(stream)
    groups = await redis.xinfo_groups(stream)
    cursor = next(g["last-delivered-id"] for g in groups if g["name"] in (group, group.encode()))

    def ms(raw: object) -> int:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return int(text.split("-", 1)[0])

    return (ms(info["last-generated-id"]) - ms(cursor)) / 1000


async def test_queue_wait_counts_the_wait_not_the_silence_before_it(redis_client: Redis) -> None:
    stream = f"test.queue_wait.{new_uuid7()}"
    group = "cg.test"
    try:
        await redis_client.xgroup_create(stream, group, id="$", mkstream=True)
        assert await queue_wait_seconds(redis_client, stream, group) == 0.0

        # One entry, handed out and acknowledged -- then a quiet spell.
        first = await redis_client.xadd(stream, {"n": "1"})
        await redis_client.xreadgroup(group, "c1", {stream: ">"}, count=1)
        await redis_client.xack(stream, group, first)
        await asyncio.sleep(2.0)

        # The burst's first entry lands while no handler takes it.
        await redis_client.xadd(stream, {"n": "2"})

        assert await _head_gap_s(redis_client, stream, group) >= 2.0
        assert await queue_wait_seconds(redis_client, stream, group) < 1.0

        # And a wait that is real is read as one.
        await asyncio.sleep(1.5)
        assert await queue_wait_seconds(redis_client, stream, group) >= 1.5

        # Handing it out ends the wait, acknowledged or not: in-flight work is
        # not queue.
        await redis_client.xreadgroup(group, "c1", {stream: ">"}, count=1)
        assert await queue_wait_seconds(redis_client, stream, group) == 0.0
    finally:
        await redis_client.delete(stream)


async def test_a_missing_stream_or_group_has_no_reading(redis_client: Redis) -> None:
    stream = f"test.queue_wait.{new_uuid7()}"
    try:
        assert await queue_wait_seconds(redis_client, stream, "cg.test") is None
        await redis_client.xgroup_create(stream, "cg.other", id="$", mkstream=True)
        assert await queue_wait_seconds(redis_client, stream, "cg.test") is None
    finally:
        await redis_client.delete(stream)
