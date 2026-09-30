"""Safe stream trimming, and the numbers that watch it -- capacity 5.5
(``ح-17``, ``docs/capacity-plan.md``).

**The defect this replaces.** ``XADD ... MAXLEN ~ N`` (7.3,
``RedisStreamsPublisher.publish``) trims by LENGTH. It knows nothing about who
has read what, so when one consumer group falls behind -- a stopped worker, a
rolling deploy -- the entries it has not read are the OLDEST on the stream, and
they are exactly the ones the cap deletes first. Nothing reports it: no error,
no log line, no alert. The outbox row was stamped ``published`` long before,
so the event is not redelivered either. It is simply gone.

**The rule.** Trim each stream below the position of its slowest reader:

* for every consumer group on the stream, the first entry it may still need is
  its oldest PENDING entry (delivered, not yet acked) if it has one, and
  otherwise the entry after its ``last-delivered-id``;
* the trim point is the smallest of those across ALL groups, minus a margin;
* ``XTRIM <stream> MINID ~ <id>`` removes only whole radix nodes whose every
  entry is below ``<id>`` -- it may keep a little more, it never keeps less.

A stream with no group at all has no reader to protect: everything older than
the margin goes. Groups are always created at ``$`` in this codebase
(``RedisStreamsConsumer.ensure_group``), so a group born later never asks for
what was there before it.

**The PEL term is what makes this safe, not the margin.** The plan's wording
("the minimum ``last-delivered-id`` minus a safety margin") would be unsafe as
a pure time margin: a delivered-but-unacked entry sits BELOW
``last-delivered-id`` for as long as its handler runs, and a summary build may
run for ``summarize_job_max_duration_s`` (1,800 s). A margin shorter than that
trims an in-flight entry; one that long keeps half an hour of history on every
stream for nothing. Measured on Redis 7.4: a pending entry that was trimmed
comes back from the recovery read (``XREADGROUP ... 0``) with NO fields --
the handler never sees it again. So the oldest pending id enters the rule
exactly, and the margin is only what it looks like: a window of recent,
already-consumed history kept for inspection.

**Every group holds the trim, including one with zero consumers.** A worker
that stops cleanly removes its own consumer entry (``StreamConsumer.
deregister``), so a stopped ``worker-knowledge`` leaves ``cg.knowledge`` with
zero consumers -- which is 5.5's acceptance scenario itself. "No consumers"
therefore cannot mean "abandoned". An abandoned group (the live stack has one:
``cg.knowledge`` on ``stream.files``, unread since 2026-08-17) holds its stream
forever. That is surfaced, not guessed at: the stream grows toward the
backstop, ``AizzakStreamBackstopHigh`` fires at 70%, and ``python -m
app.ops.stream_trim status`` names the group. Destroying it is an operator's
decision, never this module's.

**Why ``MAXLEN`` stays, as a backstop.** Without it, one stalled group on
``redis-stream`` (``noeviction``, ``maxmemory 2gb`` since 5.2) grows its stream
until the instance refuses EVERY write -- the WebSocket registry, the rate-limit
windows and the session denylist with it. With it, the worst case is bounded
loss on the one stream whose reader stalled, announced at 70% of the cap and
counted if it happens (``unread_trimmed``). The cap is sized so it cannot bite
inside any outage this platform calls acceptable (08 §4.21 has the arithmetic).

**Why the snapshot is one ``MULTI``.** The loss count subtracts numbers from
two replies (``XINFO STREAM``'s ``entries-added``, ``XINFO GROUPS``'
``entries-read`` and ``lag``), and ``TIME`` dates the ids against the clock
that minted them. Read in separate round trips, an ``XADD`` landing between
them would skew the subtraction; inside one transaction the three replies
describe a single instant.

**Which streams.** ``PUBLISHED_STREAMS`` (``framework/events/topology.py``),
an explicit list, never discovered by ``SCAN``: the same Redis holds the
dead-letter queues -- quarantine, which must never be trimmed -- and, on a
development stack, test streams that are not this platform's to touch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.framework.observability import get_logger
from app.framework.observability.scheduled_tasks import STREAM_TRIM_TASK, TaskReport
from app.framework.ports.task_ledger import TaskLedger

_logger = get_logger(__name__)

# The per-process notify family (`composition_root._CG_NOTIFY_PREFIX`). Its
# members are named `cg.notify.<host>.<pid>` and change with every deploy, so
# the METRICS collapse them into one label value; the trim rule itself treats
# each one as the reader it is. Written out for the reason `sweeper.py` writes
# its own copy, and guarded against drift the same way (a test compares both).
NOTIFY_GROUP_FAMILY = "cg.notify"

# The backstop warning's threshold and cadence. 70% is 5.5's own number ("مع
# تنبيهٍ عند 70%"), the same one `AizzakStreamBackstopHigh` uses; the log line
# says it where no Prometheus is deployed, at most once per stream per window.
BACKSTOP_WARN_RATIO = 0.7
_BACKSTOP_WARN_EVERY_S = 900.0

_NO_SUCH_KEY = "no such key"


# --------------------------------------------------------------------------- #
# Stream ids                                                                  #
# --------------------------------------------------------------------------- #
def parse_stream_id(raw: object) -> tuple[int, int] | None:
    """``b"<ms>-<seq>"`` (or ``str``) as a comparable ``(ms, seq)`` pair;
    ``None`` for anything that is not one."""
    text = raw.decode() if isinstance(raw, bytes) else raw
    if not isinstance(text, str):
        return None
    ms, _, seq = text.partition("-")
    try:
        return int(ms), int(seq or 0)
    except ValueError:
        return None


def format_stream_id(stream_id: tuple[int, int]) -> str:
    return f"{stream_id[0]}-{stream_id[1]}"


# --------------------------------------------------------------------------- #
# The snapshot                                                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GroupPosition:
    """One consumer group, as the trim rule and the gauges need it.

    ``oldest_pending_id`` is ``None`` when nothing is pending.
    ``oldest_undelivered_id`` is only looked up when a caller asks for ages
    (``read_stream_snapshot(..., with_undelivered=True)``) and the group is
    not caught up; ``None`` otherwise.
    """

    name: str
    consumers: int
    pending: int
    last_delivered_id: tuple[int, int]
    oldest_pending_id: tuple[int, int] | None
    entries_read: int | None
    lag: int | None
    oldest_undelivered_id: tuple[int, int] | None = None

    @property
    def family(self) -> str:
        """The label this group is reported under: the notify family collapses
        to one value, every other group keeps its own name."""
        if self.name.startswith(f"{NOTIFY_GROUP_FAMILY}."):
            return NOTIFY_GROUP_FAMILY
        return self.name

    @property
    def needs_from(self) -> tuple[int, int]:
        """The lowest id this group may still need: its oldest pending entry,
        else its delivery cursor. Every pending entry was delivered, so it is
        never above the cursor; ``min`` still guards the case where the PEL
        was read a moment after the cursor (``read_stream_snapshot``)."""
        if self.oldest_pending_id is None:
            return self.last_delivered_id
        return min(self.oldest_pending_id, self.last_delivered_id)

    @property
    def oldest_unconsumed_id(self) -> tuple[int, int] | None:
        """The oldest entry this group has not finished: pending or not yet
        delivered. ``None`` when it has finished everything on the stream."""
        candidates = [
            entry_id
            for entry_id in (self.oldest_pending_id, self.oldest_undelivered_id)
            if entry_id is not None
        ]
        return min(candidates) if candidates else None

    def unread_trimmed(self, entries_added: int) -> int | None:
        """Entries removed from the stream before this group read them.

        ``entries-added`` minus ``entries-read`` is everything added after the
        group's cursor; ``lag`` is how many of those still exist. The
        difference was deleted unread. Exact inside one snapshot, and zero by
        construction while nothing but ``MINID`` trims.

        ⚠️ **Transient, measured on Redis 7.4:** once the group reads past the
        gap, Redis re-estimates ``entries-read`` and the difference returns to
        zero. So the number is visible while the group is still behind the
        gap -- which, for the stalled group a backstop trims past, is until it
        resumes. ``None`` when Redis cannot say (a group that has not read
        since it was created, or tombstones from ``XDEL``).
        """
        if self.entries_read is None or self.lag is None:
            return None
        return max(0, entries_added - self.entries_read - self.lag)


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """One stream and every group on it, read in one transaction."""

    stream: str
    length: int
    entries_added: int
    last_generated_id: tuple[int, int]
    first_entry_id: tuple[int, int] | None
    groups: tuple[GroupPosition, ...]
    # Redis's own clock at the moment of the snapshot, in milliseconds -- the
    # clock that minted every id above, so ages need no clock of ours.
    now_ms: int

    def age_s(self, entry_id: tuple[int, int] | None) -> float:
        """Seconds since ``entry_id`` was added; ``0.0`` for ``None``."""
        if entry_id is None:
            return 0.0
        return max(0.0, (self.now_ms - entry_id[0]) / 1000)


def _field(reply: Mapping[object, object], name: str) -> object:
    if name in reply:
        return reply[name]
    return reply.get(name.encode())


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    return int(cast("int | str | bytes", value))


async def read_stream_snapshot(
    client: Redis, stream: str, *, with_undelivered: bool = False
) -> StreamSnapshot | None:
    """``TIME`` + ``XINFO STREAM`` + ``XINFO GROUPS`` in one ``MULTI``, then
    ``XPENDING`` for each group with anything pending -- and, when
    ``with_undelivered``, one ``XRANGE (cursor + COUNT 1`` per group that is
    behind. ``None`` when the stream does not exist.

    The ``XPENDING`` calls run after the transaction. That is safe for the
    trim rule: a group's pending list only loses entries (acks) or gains ids
    ABOVE the cursor the transaction read, so a later read can only make the
    rule keep more (``GroupPosition.needs_from``).
    """
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.time()
            pipe.xinfo_stream(stream)
            pipe.xinfo_groups(stream)
            server_time, info, group_rows = await pipe.execute()
    except ResponseError as exc:
        if _NO_SUCH_KEY in str(exc):
            return None
        raise

    seconds, micros = cast("tuple[int, int]", server_time)
    info_map = cast("Mapping[object, object]", info)
    last_generated = parse_stream_id(_field(info_map, "last-generated-id")) or (0, 0)
    first_entry = _field(info_map, "first-entry")
    first_entry_id = (
        parse_stream_id(cast("Sequence[object]", first_entry)[0]) if first_entry else None
    )

    groups: list[GroupPosition] = []
    for row in cast("Sequence[Mapping[object, object]]", group_rows):
        name_raw = _field(row, "name")
        name = name_raw.decode() if isinstance(name_raw, bytes) else str(name_raw)
        pending = _as_int(_field(row, "pending")) or 0
        cursor = parse_stream_id(_field(row, "last-delivered-id")) or (0, 0)
        lag = _as_int(_field(row, "lag"))
        oldest_pending: tuple[int, int] | None = None
        if pending > 0:
            summary = cast("Mapping[str, object]", await client.xpending(stream, name))
            oldest_pending = parse_stream_id(summary.get("min"))
        oldest_undelivered: tuple[int, int] | None = None
        if with_undelivered and lag != 0:
            rows = await client.xrange(stream, min=f"({format_stream_id(cursor)}", max="+", count=1)
            if rows:
                oldest_undelivered = parse_stream_id(rows[0][0])
        groups.append(
            GroupPosition(
                name=name,
                consumers=_as_int(_field(row, "consumers")) or 0,
                pending=pending,
                last_delivered_id=cursor,
                oldest_pending_id=oldest_pending,
                entries_read=_as_int(_field(row, "entries-read")),
                lag=lag,
                oldest_undelivered_id=oldest_undelivered,
            )
        )

    return StreamSnapshot(
        stream=stream,
        length=_as_int(_field(info_map, "length")) or 0,
        entries_added=_as_int(_field(info_map, "entries-added")) or 0,
        last_generated_id=last_generated,
        first_entry_id=first_entry_id,
        groups=tuple(groups),
        now_ms=seconds * 1000 + micros // 1000,
    )


# --------------------------------------------------------------------------- #
# The rule                                                                    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TrimDecision:
    """Where one stream may be trimmed, and who decided it.

    ``minid`` is ``None`` when nothing may go: the slowest reader (or the
    margin) reaches back past the stream's first entry. ``holder`` is the
    group that bounds the trim -- ``None`` when no group reads the stream at
    all, so only the margin does. ``boundary`` is the holder's position before
    the margin is subtracted, which is what an operator asks about.
    """

    stream: str
    minid: str | None
    holder: str | None
    boundary: tuple[int, int]


def trim_decision(snapshot: StreamSnapshot, *, margin_ms: int) -> TrimDecision:
    """The module docstring's rule, as a pure function of one snapshot."""
    head = snapshot.last_generated_id
    boundary, holder = head, None
    for group in snapshot.groups:
        needs = group.needs_from
        if holder is None or needs < boundary:
            boundary, holder = needs, group.name
    # A cursor set past the head by hand (`XGROUP SETID`) must not license a
    # trim beyond what the stream has actually produced.
    boundary = min(boundary, head)
    cut = (boundary[0] - margin_ms, 0)
    first = snapshot.first_entry_id
    # Nothing to trim when the stream is empty, when the margin reaches back
    # before the epoch, or when no entry sits below the cut.
    minid = format_stream_id(cut) if first is not None and cut[0] > 0 and first < cut else None
    return TrimDecision(stream=snapshot.stream, minid=minid, holder=holder, boundary=boundary)


# --------------------------------------------------------------------------- #
# The trimmer                                                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TrimReport:
    """What one pass did to one stream."""

    stream: str
    length_before: int
    trimmed: int
    minid: str | None
    holder: str | None
    # How old the holder's position is -- NOT a lag: on a quiet stream whose
    # readers are all caught up it is simply the age of the last entry. It
    # climbs without bound only when the holder stops reading.
    boundary_age_s: float


class StreamTrimmer:
    """Trims ``streams`` on a timer by ``trim_decision`` -- one instance, in
    the single ``outbox-relay`` process (D-26), which is the only producer on
    these streams (``workers/bootstrap.build_relay_from_env``)."""

    def __init__(
        self,
        client: Redis,
        *,
        streams: Iterable[str],
        interval_s: float,
        margin_s: float,
        backstop: int | None,
        ledger: TaskLedger | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0; a disabled trimmer is not built at all")
        self._client = client
        self._streams = tuple(dict.fromkeys(streams))
        self._interval_s = interval_s
        self._margin_ms = int(margin_s * 1000)
        self._backstop = backstop
        self._next_warn_at: dict[str, float] = {}
        # Capacity 5.7. 5.5 shipped this loop with "no metric for the last
        # successful pass" written down as a gap and 5.7 named as its home: a
        # dead trimmer returns the platform to `MAXLEN` alone, and nothing says
        # so until a stream reaches 70% -- hours at peak, days at the real
        # rate. Measured on the live relay on 2026-09-30: an hour and a half
        # of passes and not one log line after the boot line, because a pass
        # that trims nothing is silent. So each pass is recorded, and the ledger says what silence
        # could not.
        self._report = TaskReport(ledger, STREAM_TRIM_TASK, interval_s=interval_s)

    async def trim_once(self) -> list[TrimReport]:
        """One pass over every stream: read, decide, ``XTRIM MINID ~``.

        Raises what Redis raises -- ``run_forever`` owns the policy of
        carrying on, and a caller (the ops tool, a test) sees the failure.
        """
        reports: list[TrimReport] = []
        for stream in self._streams:
            snapshot = await read_stream_snapshot(self._client, stream)
            if snapshot is None:
                continue
            decision = trim_decision(snapshot, margin_ms=self._margin_ms)
            trimmed = 0
            if decision.minid is not None:
                trimmed = int(
                    await self._client.xtrim(stream, minid=decision.minid, approximate=True)
                )
            report = TrimReport(
                stream=stream,
                length_before=snapshot.length,
                trimmed=trimmed,
                minid=decision.minid,
                holder=decision.holder,
                boundary_age_s=round(snapshot.age_s(decision.boundary), 1),
            )
            reports.append(report)
            if trimmed:
                _logger.info(
                    "stream_trim.trimmed",
                    extra={
                        "stream": stream,
                        "trimmed": trimmed,
                        "length": snapshot.length - trimmed,
                        "minid": decision.minid,
                        "holder": decision.holder,
                        "boundary_age_s": report.boundary_age_s,
                    },
                )
            self._warn_near_backstop(report)
        return reports

    def _warn_near_backstop(self, report: TrimReport) -> None:
        """A stream at 70% of the ``MAXLEN`` backstop is one whose reader has
        stalled (or one this trimmer cannot trim): past 100%, ``XADD`` starts
        deleting entries that reader has not read. Said once per stream per
        window -- the alert is the durable signal, this line is for a stack
        with no Prometheus."""
        if self._backstop is None:
            return
        length = report.length_before - report.trimmed
        if length < BACKSTOP_WARN_RATIO * self._backstop:
            return
        now = monotonic()
        if now < self._next_warn_at.get(report.stream, 0.0):
            return
        self._next_warn_at[report.stream] = now + _BACKSTOP_WARN_EVERY_S
        _logger.warning(
            "stream_trim.backstop_near",
            extra={
                "stream": report.stream,
                "length": length,
                "backstop": self._backstop,
                "holder": report.holder,
                "boundary_age_s": report.boundary_age_s,
                "triage": "python -m app.ops.stream_trim status",
            },
        )

    async def run_forever(self) -> None:
        """``trim_once`` every ``interval_s`` until cancelled. A failed pass
        is logged and the next one tries again: trimming is housekeeping, and
        the backstop still bounds every stream while it is not happening.

        Every pass lands in the task ledger (capacity 5.7), the failures as
        well as the successes -- a trimmer failing every pass must read as
        failing, not merely as quiet."""
        await self._report.arm()
        while True:
            started_at = self._report.now()
            try:
                await self.trim_once()
            except Exception as exc:
                _logger.error("stream_trim.failed", exc_info=True)
                await self._report.failed(started_at=started_at, error=exc)
            else:
                await self._report.succeeded(started_at=started_at)
            await asyncio.sleep(self._interval_s)
