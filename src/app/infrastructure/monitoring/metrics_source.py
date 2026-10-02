"""``MetricsSource`` over Postgres (the Outbox age) + Redis (DLQ depth) —
P1-3, ``docs/p1-hardening-plan.md`` §3 step 10.

**Where each number actually lives, and why this one adapter reads both.**
``outbox-relay`` (``infrastructure/messaging/outbox.py``) is the ONE process
that stamps ``published_at`` — it OWNS the Outbox's true state — but it runs
no HTTP server at all (``docker-compose.yml``'s own comment: "SINGLE
instance", started as ``python -m app.workers.outbox_relay``, nothing else).
``app`` is the one process with an HTTP listener, but it holds no read
access to ``platform.outbox`` at all: ``app_rw`` is deliberately
INSERT-only there (``app.ops.provision``'s own docstring). So *measuring*
this metric happens over a connection ``app`` did not otherwise have any
reason to hold — a FIFTH least-privilege role, ``metrics_reader``
(``app.ops.provision.METRICS_ROLE``), granted ``SELECT`` on
``platform.outbox`` and nothing else, exactly the ``outbox_relay``/
``retention_sweeper`` precedent applied to a fourth distinct job. *Exposing*
it, correctly, stays where the one HTTP listener already is.

The DLQ half has no such asymmetry: ``dead_letter``
(``infrastructure/messaging/redis_streams.py``) writes to ``<stream>.dlq``
over the SAME shared Redis client every process in this codebase already
holds (no ACL split on the Redis side, unlike Postgres's role model), so
``app``'s own ``redis_client`` (``composition_root.py``) reads it directly —
no new connection, no new credential.

**Every value here is computed FRESH on every call — see the port's own
docstring for why that is what makes this correct under gunicorn's
multi-worker default without ``PROMETHEUS_MULTIPROC_DIR``.**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.framework.events.topology import PUBLISHED_STREAMS, STATIC_CONSUMER_TOPOLOGY
from app.framework.observability.scheduled_tasks import SCHEDULED_TASKS
from app.framework.ports.metrics_source import StreamRetention
from app.framework.ports.task_ledger import TaskRecord
from app.infrastructure.messaging.stream_retention import read_stream_snapshot
from app.infrastructure.monitoring.task_ledger import RedisTaskLedger

# Every source stream this platform's workers consume, derived from the ONE
# canonical topology table (`framework/events/topology.py`) rather than typed
# out again here.
#
# ⚠️ **This list was a hardcoded `("stream.knowledge", "stream.media")` until
# 2026-08-15, and it was WRONG -- measurably, not theoretically.** The live
# stack's only dead-lettered entry sat on `stream.memory.dlq` (one entry,
# there since 2026-08-03), and `stream.memory` was not in the pair: the gauge
# this module exists to publish would have reported a clean `0` for every
# stream it knew about while the DLQ that actually held something was not
# read at all. The gap was invisible because the metric's own alert
# (`AizzakDlqNotEmpty`) has no scraper evaluating it yet, so nothing ever
# compared the numbers with reality (ت-6, `docs/operational-findings.md` §6).
#
# The original comment justified the literal by saying `app.infrastructure`
# may not import `app.modules` (true, and unchanged) "and there is no single
# canonical stream registry to import instead" -- which stopped being true
# when `STATIC_CONSUMER_TOPOLOGY` landed (stream-topology-plan.md §1-ج). That
# table lives in `app.framework`, which infrastructure may import freely, and
# it is already the file a new module's stream must be added to for its
# consumer group to be provisioned at all. Deriving from it means a stream
# cannot be consumed by this platform and simultaneously unwatched here.
#
# `dict.fromkeys` deduplicates while preserving order: the topology binds
# `stream.files` and `stream.knowledge` to the SAME `cg.knowledge` group, and
# a stream must be XLEN-ed once no matter how many groups read it.
DLQ_SOURCE_STREAMS: tuple[str, ...] = tuple(
    dict.fromkeys(binding.stream for binding in STATIC_CONSUMER_TOPOLOGY)
)


class SqlRedisMetricsSource:
    """Structural ``MetricsSource`` (Protocol match, no inheritance -- the
    ``RedisCache``/every adapter-over-a-port precedent in this codebase)."""

    def __init__(
        self,
        engine: AsyncEngine,
        redis_client: Redis,
        *,
        stream_maxlen: int | None = None,
        streams: Sequence[str] = PUBLISHED_STREAMS,
    ) -> None:
        self._engine = engine
        self._redis = redis_client
        # The streams `stream_retention` reads -- the published list in every
        # deployment; a parameter only so a live test can point it at streams
        # of its own instead of the stack's real ones.
        self._streams = tuple(streams)
        # capacity 5.5: the `XADD ... MAXLEN ~` backstop, reported next to each
        # stream's length so the 70% rule is a ratio in PromQL rather than a
        # byte count typed into `alerts.yml` (the 5.2 precedent). Config, not
        # Redis state: `None` means the backstop is off, and the gauge is then
        # absent rather than zero.
        self._stream_maxlen = stream_maxlen

    async def outbox_oldest_unpublished_age_seconds(self) -> float:
        """``min(created_at)`` over the unpublished half of ``platform.outbox``
        -- the exact partial index ``ix_outbox_unpublished`` (``created_at``
        ``WHERE published_at IS NULL``, ``0001_baseline_platform.py``) exists
        for, so this is an index scan for the single oldest row, never a
        sequential scan of the whole (unbounded) table.

        ``0.0`` when nothing is waiting -- the healthy, common case, not an
        error: an outbox with zero unpublished rows has no "age" to report,
        and ``0.0`` is exactly the value a scrape during that state should
        show (below every SLO threshold in ``07-nfr-slo`` §2, because there
        is genuinely nothing in flight).
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT min(created_at) FROM platform.outbox WHERE published_at IS NULL")
            )
            oldest: datetime | None = result.scalar_one_or_none()
        if oldest is None:
            return 0.0
        # `max(0.0, ...)` guards only against clock skew between this
        # process and Postgres (a negative age is never a real value) --
        # never against the ordinary case, which is always non-negative.
        return max(0.0, (datetime.now(UTC) - oldest).total_seconds())

    async def stream_lag_seconds(self) -> dict[tuple[str, str], float]:
        """How far behind the head each consumer group is, in seconds.

        Two reads per stream and no scan: `XINFO STREAM` gives the head's
        `last-generated-id` and `XINFO GROUPS` gives each group's
        `last-delivered-id`. Both are Redis stream ids of the form
        `<milliseconds>-<sequence>`, so the difference between their
        millisecond halves IS the lag in time -- no clock of ours enters the
        subtraction, which is what keeps the number honest across a process
        whose clock drifts from Redis's.

        A group fully caught up reads exactly `0.0`, however old the stream's
        last entry is, because both ids are then the same one. That is the
        property an "age of the last entry" metric would NOT have: on a quiet
        stream it would climb forever while every consumer was idle and
        healthy, and an operator would learn to ignore it.

        A stream that does not exist yet is SKIPPED rather than reported as
        `0.0`. Before the first publish there is no such stream, and a `0.0`
        would be indistinguishable from a healthy caught-up group -- i.e. it
        would assert something nobody measured, which is the same defect the
        Vault gauge's `None` guard exists to avoid (`api/metrics.py`).
        """
        lags: dict[tuple[str, str], float] = {}
        for binding in STATIC_CONSUMER_TOPOLOGY:
            try:
                info = await self._redis.xinfo_stream(binding.stream)
                groups = await self._redis.xinfo_groups(binding.stream)
            except ResponseError:
                # `no such key` -- nothing has ever been published here.
                continue
            head_ms = _stream_id_ms(_field(info, "last-generated-id"))
            if head_ms is None:
                continue
            for group in groups:
                if _as_text(_field(group, "name")) != binding.group:
                    continue
                delivered_ms = _stream_id_ms(_field(group, "last-delivered-id"))
                if delivered_ms is None:
                    continue
                lags[(binding.stream, binding.group)] = max(0.0, (head_ms - delivered_ms) / 1000)
        return lags

    async def stream_queue_wait_seconds(self) -> dict[tuple[str, str], float]:
        """Seconds the oldest undelivered entry of each bound group has
        waited (``queue_wait_seconds`` below, once per binding).

        Absent for a stream that does not exist yet or a group not created on
        it -- the ``stream_lag_seconds`` rule, for the same reason.
        """
        waits: dict[tuple[str, str], float] = {}
        for binding in STATIC_CONSUMER_TOPOLOGY:
            wait = await queue_wait_seconds(self._redis, binding.stream, binding.group)
            if wait is not None:
                waits[(binding.stream, binding.group)] = wait
        return waits

    async def stream_retention(self) -> StreamRetention:
        """Every ``PUBLISHED_STREAMS`` entry, one transactional snapshot
        each (``stream_retention.read_stream_snapshot``), folded into the
        port's four answers.

        The snapshot is the SAME reading the relay's trimmer decides from, so
        a gauge and a trim can never disagree about where a group stands. It
        also looks up, for each group that is behind, the first entry it has
        not been handed -- one ``XRANGE ... COUNT 1`` -- which is what turns
        a position into an age. Caught-up groups (the common case, and every
        notify group almost always) cost nothing beyond the transaction.

        A stream that does not exist yet is absent, never ``0`` -- the
        ``stream_lag_seconds`` rule, for the same reason.
        """
        lengths: dict[str, int] = {}
        ages: dict[tuple[str, str], float] = {}
        unread_trimmed: dict[tuple[str, str], int] = {}
        for stream in self._streams:
            snapshot = await read_stream_snapshot(self._redis, stream, with_undelivered=True)
            if snapshot is None:
                continue
            lengths[stream] = snapshot.length
            for group in snapshot.groups:
                key = (stream, group.family)
                age = snapshot.age_s(group.oldest_unconsumed_id)
                ages[key] = max(ages.get(key, 0.0), age)
                lost = group.unread_trimmed(snapshot.entries_added)
                if lost is not None:
                    unread_trimmed[key] = max(unread_trimmed.get(key, 0), lost)
        return StreamRetention(
            lengths=lengths,
            backstop=self._stream_maxlen,
            oldest_unconsumed_age_s=ages,
            unread_trimmed=unread_trimmed,
        )

    async def scheduled_tasks(self) -> dict[str, TaskRecord]:
        """Every catalog task's ledger record, in catalog order -- one
        pipelined ``HGETALL`` per task on the SAME ``redis-stream`` client the
        DLQ depths are read with. A task nothing has written comes back as an
        empty record rather than being left out: the scrape reports what is
        EXPECTED, and the difference is the ``AizzakOpsTaskNeverArmed`` rule.
        """
        return await RedisTaskLedger(self._redis).read(task.name for task in SCHEDULED_TASKS)

    async def dlq_depths(self) -> dict[str, int]:
        """``XLEN`` of ``<stream>.dlq`` for every entry in
        ``DLQ_SOURCE_STREAMS``, keyed by the SOURCE stream name -- the
        ``ops.dlq`` module's own convention (never the ``.dlq`` suffix as the
        key, so a caller need not know the derivation to read the result)."""
        return {
            stream: int(await self._redis.xlen(f"{stream}.dlq")) for stream in DLQ_SOURCE_STREAMS
        }


# ── Redis reply helpers ────────────────────────────────────────────────────
# The shared client is built with `decode_responses=False` (`redis_cache.py`:
# the `CacheProvider` port's contract is bytes end to end), so every key and
# value that comes back from `XINFO` is `bytes`. These three keep that detail
# where it belongs -- at the wire -- instead of spreading `b"..."` literals
# through the method above.


def _as_text(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return None


def _field(reply: object, name: str) -> object:
    if not isinstance(reply, dict):
        return None
    if name in reply:
        return reply[name]
    return reply.get(name.encode("utf-8"))


def _stream_id_ms(raw: object) -> float | None:
    """The millisecond half of a `<ms>-<seq>` Redis stream id."""
    text_id = _as_text(raw)
    if text_id is None:
        return None
    head = text_id.split("-", 1)[0]
    try:
        return float(head)
    except ValueError:
        return None


async def queue_wait_seconds(redis: Redis, stream: str, group: str) -> float | None:
    """How long the oldest entry ``group`` has not yet been handed has waited
    on ``stream``; ``0.0`` when nothing is waiting, ``None`` when the stream
    or the group does not exist.

    ``TIME`` and ``XINFO GROUPS`` in one ``MULTI``, then one ``XRANGE (cursor
    COUNT 1``. The entry after the cursor carries its own millisecond
    timestamp and ``TIME`` is the clock that minted it, so -- as in
    ``stream_lag_seconds`` -- no clock of ours enters the subtraction. A
    function over a client rather than a method so the live suite can prove it
    on keys of its own instead of the platform's streams.
    """
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.time()
            pipe.xinfo_groups(stream)
            server_time, groups = await pipe.execute()
    except ResponseError:
        return None
    cursor = next(
        (
            _as_text(_field(row, "last-delivered-id"))
            for row in groups
            if _as_text(_field(row, "name")) == group
        ),
        None,
    )
    if cursor is None:
        return None
    seconds, micros = server_time
    now_ms = int(seconds) * 1000 + int(micros) // 1000
    rows = await redis.xrange(stream, min=f"({cursor}", max="+", count=1)
    oldest_ms = _stream_id_ms(rows[0][0]) if rows else None
    if oldest_ms is None:
        return 0.0
    return max(0.0, (now_ms - oldest_ms) / 1000)
