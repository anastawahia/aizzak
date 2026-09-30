"""Capacity 5.5 (``ح-17``) against a real Redis: the loss the length cap
causes, reproduced, and the trim that replaces it, shown not to cause it.

Every test builds its own stream under a unique ``stream.test.trim.*`` name
and deletes it afterwards -- the live Redis also holds the stack's real
streams, and ``PUBLISHED_STREAMS`` is never touched here. Entry ids are
written EXPLICITLY, stamped from Redis's own clock, so twenty minutes of
arrivals at 100 a minute -- 5.5's acceptance load -- can be laid down in a
second, with the timestamps the trim rule and the ages actually read.

The scenario, in every test that needs it:

* ``history`` -- 500 entries across the hour before the outage, read and
  acked by both groups;
* ``cg.worker`` stops cleanly twenty minutes ago: it DEREGISTERS, so the
  group has ZERO consumers -- which is what ``StreamConsumer.deregister``
  leaves behind and exactly what an abandoned group also looks like;
* ``outage`` -- 2,000 entries over those twenty minutes, all read and acked
  by ``cg.notify.test.1``, none by ``cg.worker``.

A trim that honoured only the groups with a live consumer would delete all
2,000. The length cap deletes whatever does not fit. The 5.5 trim deletes
only history older than the stopped group's cursor minus the margin.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from redis.asyncio import Redis

from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from app.infrastructure.messaging.stream_retention import (
    StreamTrimmer,
    read_stream_snapshot,
)
from app.infrastructure.monitoring.metrics_source import SqlRedisMetricsSource

pytestmark = pytest.mark.live_redis

_WORKER = "cg.worker"
_NOTIFY = "cg.notify.test.1"
_MIN_MS = 60_000
_HISTORY = 500
_OUTAGE = 2_000  # 100 a minute for 20 minutes -- 5.5's acceptance load
_MARGIN_S = 600.0


@dataclass(frozen=True, slots=True)
class _Scenario:
    stream: str
    cursor_id: str  # cg.worker's last-delivered-id when it stopped
    outage_ids: list[str]


async def _server_ms(client: Redis) -> int:
    seconds, micros = await client.time()
    return int(seconds) * 1000 + int(micros) // 1000


async def _xadd_at(client: Redis, stream: str, ms_values: list[int]) -> list[str]:
    ids = [f"{ms}-{seq}" for seq, ms in enumerate(ms_values)]
    async with client.pipeline(transaction=False) as pipe:
        for entry_id in ids:
            pipe.xadd(stream, {b"ce": b"{}"}, id=entry_id)
        await pipe.execute()
    return ids


async def _read_all(client: Redis, stream: str, group: str, consumer: str) -> list[str]:
    reply = await client.xreadgroup(group, consumer, {stream: ">"}, count=100_000)
    ids = [entry_id.decode() for entry_id, _ in (reply[0][1] if reply else [])]
    if ids:
        await client.xack(stream, group, *ids)
    return ids


@pytest.fixture
async def scenario(redis_client: Redis) -> AsyncIterator[_Scenario]:
    stream = f"stream.test.trim.{uuid.uuid4().hex[:10]}"
    await redis_client.xgroup_create(stream, _WORKER, id="$", mkstream=True)
    await redis_client.xgroup_create(stream, _NOTIFY, id="$")
    now = await _server_ms(redis_client)
    stopped_at = now - 20 * _MIN_MS
    try:
        history_start = stopped_at - 60 * _MIN_MS
        await _xadd_at(
            redis_client,
            stream,
            [history_start + i * (60 * _MIN_MS // _HISTORY) for i in range(_HISTORY)],
        )
        worker_read = await _read_all(redis_client, stream, _WORKER, "worker-1")
        assert len(worker_read) == _HISTORY
        # A clean stop: the engine removes its own consumer entry.
        await redis_client.xgroup_delconsumer(stream, _WORKER, "worker-1")
        outage_ids = await _xadd_at(
            redis_client,
            stream,
            [stopped_at + 1 + i * (20 * _MIN_MS // _OUTAGE) for i in range(_OUTAGE)],
        )
        notify_read = await _read_all(redis_client, stream, _NOTIFY, "bridge-1")
        assert len(notify_read) == _HISTORY + _OUTAGE
        yield _Scenario(stream=stream, cursor_id=worker_read[-1], outage_ids=outage_ids)
    finally:
        await redis_client.delete(stream, f"{stream}.dlq")


async def _surviving(client: Redis, stream: str, ids: list[str]) -> int:
    present = await client.xrange(stream, min=ids[0], max=ids[-1])
    return len(present)


async def test_the_length_cap_alone_loses_what_a_stopped_group_had_not_read(
    redis_client: Redis, scenario: _Scenario
) -> None:
    """``ح-17`` reproduced: the pre-5.5 trim, ``MAXLEN ~``, at a cap smaller
    than the outage. It cannot see that ``cg.worker`` has not read the
    outage, and deletes it from the oldest end -- and Redis's own counters
    say so, which is what ``aizzak_stream_unread_trimmed_entries`` reads."""
    await redis_client.xtrim(scenario.stream, maxlen=1_000, approximate=True)

    lost_on_stream = _OUTAGE - await _surviving(redis_client, scenario.stream, scenario.outage_ids)
    snapshot = await read_stream_snapshot(redis_client, scenario.stream)
    assert snapshot is not None
    worker = next(g for g in snapshot.groups if g.name == _WORKER)

    assert lost_on_stream > 0
    assert worker.unread_trimmed(snapshot.entries_added) == lost_on_stream
    delivered = await _read_all(redis_client, scenario.stream, _WORKER, "worker-2")
    assert len(delivered) == _OUTAGE - lost_on_stream  # nothing redelivers them


async def test_the_trim_keeps_everything_a_stopped_group_has_not_read(
    redis_client: Redis, scenario: _Scenario
) -> None:
    """5.5's acceptance at the stream level: after the outage, zero of the
    2,000 entries are gone, the loss counter reads zero, and the restarted
    worker is handed every one of them. And the trim DID work: history older
    than the stopped group's cursor minus the margin is gone -- a trimmer
    that deleted nothing would pass the first half trivially."""
    before = await redis_client.xlen(scenario.stream)
    trimmer = StreamTrimmer(
        redis_client,
        streams=[scenario.stream],
        interval_s=60,
        margin_s=_MARGIN_S,
        backstop=100_000,
    )

    [report] = await trimmer.trim_once()
    await trimmer.trim_once()  # a second pass has nothing more to take

    assert report.holder == _WORKER
    assert report.trimmed > 0
    assert await redis_client.xlen(scenario.stream) < before
    assert await _surviving(redis_client, scenario.stream, scenario.outage_ids) == _OUTAGE
    snapshot = await read_stream_snapshot(redis_client, scenario.stream)
    assert snapshot is not None
    worker = next(g for g in snapshot.groups if g.name == _WORKER)
    assert worker.consumers == 0
    assert worker.unread_trimmed(snapshot.entries_added) == 0
    first_kept = snapshot.first_entry_id
    cursor_ms = int(scenario.cursor_id.split("-")[0])
    assert first_kept is not None and first_kept[0] <= cursor_ms - int(_MARGIN_S * 1000) + 1

    delivered = await _read_all(redis_client, scenario.stream, _WORKER, "worker-2")
    assert delivered == scenario.outage_ids


async def test_a_pending_entry_below_the_cursor_survives_and_comes_back_whole(
    redis_client: Redis, scenario: _Scenario
) -> None:
    """The PEL term. The worker takes ten outage entries and does not ack them
    (a handler still running), then its cursor moves past them. A trim with
    NO margin cuts right at the oldest pending entry -- and the recovery read
    still returns them with their payload, not the empty shell a trimmed
    pending entry comes back as."""
    taken = await redis_client.xreadgroup(_WORKER, "worker-2", {scenario.stream: ">"}, count=10)
    in_flight = [entry_id.decode() for entry_id, _ in taken[0][1]]
    later = await redis_client.xreadgroup(_WORKER, "worker-2", {scenario.stream: ">"}, count=500)
    await redis_client.xack(scenario.stream, _WORKER, *[e for e, _ in later[0][1]])

    trimmer = StreamTrimmer(
        redis_client, streams=[scenario.stream], interval_s=60, margin_s=0, backstop=None
    )
    [report] = await trimmer.trim_once()

    assert report.holder == _WORKER
    recovered = await redis_client.xreadgroup(_WORKER, "worker-2", {scenario.stream: "0"})
    entries = recovered[0][1]
    assert [e.decode() for e, _ in entries] == in_flight
    assert all(fields for _, fields in entries), "a pending entry came back without its payload"


async def test_the_engine_dead_letters_a_pending_entry_the_backstop_removed(
    redis_client: Redis, scenario: _Scenario
) -> None:
    """The one way an in-flight entry can still vanish: the MAXLEN backstop
    (or a hand-run XTRIM) cutting under the PEL. The real engine over the real
    adapter must not crash on the empty entry, and must not file it as a
    malformed payload: it moves it to the DLQ as ``entry_trimmed``, with the
    source id an operator needs to find the event in the outbox."""
    consumer = RedisStreamsConsumer(redis_client)
    engine = StreamConsumer(
        consumer, consumer_name="worker-3", block_ms=10, batch_count=4, max_deliveries=5
    )
    subscription = Subscription(stream=scenario.stream, group=_WORKER, handlers={})
    taken = await redis_client.xreadgroup(_WORKER, "worker-3", {scenario.stream: ">"}, count=1)
    [(lost_id, _)] = taken[0][1]
    await redis_client.xtrim(scenario.stream, maxlen=0, approximate=False)

    await engine.run_once([subscription])

    dlq = await redis_client.xrange(f"{scenario.stream}.dlq")
    assert len(dlq) == 1
    _, fields = dlq[0]
    assert fields[b"reason"] == b"entry_trimmed"
    assert fields[b"source_entry_id"] == lost_id
    assert b"ce" not in fields
    assert await redis_client.xpending(scenario.stream, _WORKER) == {
        "pending": 0,
        "min": None,
        "max": None,
        "consumers": [],
    }


async def test_the_metrics_source_reports_the_age_and_no_loss(
    redis_client: Redis, scenario: _Scenario
) -> None:
    """``/metrics``' reading of the same stream: the stopped group's oldest
    unconsumed entry is ~20 minutes old (the first outage entry), the notify
    family has finished everything, and nothing was trimmed unread."""
    source = SqlRedisMetricsSource(
        None,  # type: ignore[arg-type]  # stream_retention never touches Postgres
        redis_client,
        stream_maxlen=100_000,
        streams=[scenario.stream],
    )

    retention = await source.stream_retention()

    assert retention.lengths[scenario.stream] == _HISTORY + _OUTAGE
    assert retention.backstop == 100_000
    worker_age = retention.oldest_unconsumed_age_s[(scenario.stream, _WORKER)]
    assert 19 * 60 <= worker_age <= 21 * 60
    assert retention.oldest_unconsumed_age_s[(scenario.stream, "cg.notify")] == 0.0
    assert retention.unread_trimmed[(scenario.stream, _WORKER)] == 0
    assert json.dumps(sorted(retention.unread_trimmed.values())) == "[0, 0]"
