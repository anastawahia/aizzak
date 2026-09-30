"""Capacity 5.5 (``ح-17``, ``docs/capacity-plan.md``) -- the stream trim, as
a rule, as a loop, as a reading of Redis, and as a number the backstop has to
satisfy. Hermetic: the rule is a pure function, the reader and the trimmer run
over a scripted client, and the relay wiring never opens a connection (the
``test_relay_topology_provisioning.py`` precedent). The same rule against a
real Redis -- including the ``ح-17`` loss reproduced and then prevented -- is
``tests/integration/test_stream_retention_live.py``.

What each part pins:

* **The rule** (``trim_decision``): below the slowest reader, never below a
  pending entry, a stopped group holds exactly like a live one, and a stream
  nobody reads keeps only the margin.
* **Why the margin is not the safety** (5.5's own wording, tested): a pure
  time margin under an in-flight handler would have deleted the entry it is
  working on.
* **The reading**: one ``MULTI`` for ``TIME``/``XINFO STREAM``/``XINFO
  GROUPS``, ``XPENDING`` only where something is pending, ``XRANGE`` only
  where a group is behind and ages were asked for.
* **The loop**: ``XTRIM MINID ~``, never a stream outside the list, carries on
  after a failed pass, and warns once per window near the backstop.
* **The backstop**: ``STREAM_MAXLEN`` covers three times the peak arrival over
  the longest outage 5.5 calls acceptable, plus the margin.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence

import pytest
from redis.exceptions import ResponseError

from app.framework.di.composition_root import _CG_NOTIFY_PREFIX
from app.framework.events.topology import PUBLISHED_STREAMS
from app.framework.settings.settings import EventSettings
from app.infrastructure.config import env_settings
from app.infrastructure.messaging import stream_retention
from app.infrastructure.messaging.stream_retention import (
    NOTIFY_GROUP_FAMILY,
    GroupPosition,
    StreamSnapshot,
    StreamTrimmer,
    format_stream_id,
    parse_stream_id,
    read_stream_snapshot,
    trim_decision,
)
from app.ops import stream_trim
from app.workers import outbox_relay
from app.workers.bootstrap import build_relay_from_env

_MIN = 60_000  # ms
_NOW_MS = 1_790_000_000_000


def _group(
    name: str,
    *,
    cursor_ms: int,
    pending_from_ms: int | None = None,
    consumers: int = 1,
    entries_read: int | None = None,
    lag: int | None = 0,
    undelivered_from_ms: int | None = None,
) -> GroupPosition:
    return GroupPosition(
        name=name,
        consumers=consumers,
        pending=0 if pending_from_ms is None else 1,
        last_delivered_id=(cursor_ms, 0),
        oldest_pending_id=None if pending_from_ms is None else (pending_from_ms, 0),
        entries_read=entries_read,
        lag=lag,
        oldest_undelivered_id=None if undelivered_from_ms is None else (undelivered_from_ms, 0),
    )


def _snapshot(
    *groups: GroupPosition,
    first_ms: int | None = _NOW_MS - 120 * _MIN,
    head_ms: int = _NOW_MS,
    stream: str = "stream.knowledge",
    length: int = 1000,
    entries_added: int = 1000,
) -> StreamSnapshot:
    return StreamSnapshot(
        stream=stream,
        length=length,
        entries_added=entries_added,
        last_generated_id=(head_ms, 0),
        first_entry_id=None if first_ms is None else (first_ms, 0),
        groups=groups,
        now_ms=_NOW_MS,
    )


# --------------------------------------------------------------------------- #
# The rule                                                                    #
# --------------------------------------------------------------------------- #
def test_a_stopped_group_holds_the_trim_exactly_like_a_live_one() -> None:
    """5.5's acceptance scenario, as data: `worker-knowledge` stopped cleanly
    (it deregisters, so ZERO consumers) twenty minutes ago, while every
    notify group reads on. The trim must stop at the stopped group -- a rule
    that skipped consumer-less groups would cut through twenty minutes of
    unread index requests here."""
    stopped = _group("cg.knowledge", cursor_ms=_NOW_MS - 20 * _MIN, consumers=0)
    notify = _group(f"{_CG_NOTIFY_PREFIX}.host.7", cursor_ms=_NOW_MS)

    decision = trim_decision(_snapshot(stopped, notify), margin_ms=10 * _MIN)

    assert decision.holder == "cg.knowledge"
    assert decision.minid == format_stream_id((_NOW_MS - 30 * _MIN, 0))
    assert parse_stream_id(decision.minid) < stopped.last_delivered_id


def test_the_oldest_pending_entry_bounds_the_trim_not_the_cursor() -> None:
    """A delivered-but-unacked entry sits below the cursor for as long as its
    handler runs. The rule reads it from the PEL, so it survives."""
    in_flight = _group(
        "cg.knowledge", cursor_ms=_NOW_MS - 1 * _MIN, pending_from_ms=_NOW_MS - 30 * _MIN
    )

    decision = trim_decision(_snapshot(in_flight), margin_ms=0)

    assert decision.minid == format_stream_id((_NOW_MS - 30 * _MIN, 0))
    assert decision.boundary == in_flight.oldest_pending_id


def test_a_pure_time_margin_would_have_deleted_an_in_flight_entry() -> None:
    """Why the PEL term exists, and why the plan's "last-delivered-id minus a
    safety margin" could not be it. A summary build may hold its entry for
    1,800 s; the default margin is 600 s. Trimming at `cursor - margin` would
    cut below the entry the handler is still working on -- and a trimmed
    pending entry comes back from the recovery read with no payload."""
    margin_ms = int(EventSettings().stream_trim_margin_s * 1000)
    build_started = _NOW_MS - 30 * _MIN  # 1,800 s ago
    group = _group("cg.knowledge", cursor_ms=_NOW_MS, pending_from_ms=build_started)

    naive_cut = (group.last_delivered_id[0] - margin_ms, 0)
    assert naive_cut > group.oldest_pending_id, "the naive rule WOULD trim the in-flight entry"

    decision = trim_decision(_snapshot(group), margin_ms=margin_ms)
    assert decision.minid is not None
    assert parse_stream_id(decision.minid) <= (build_started, 0)


def test_a_stream_nobody_reads_keeps_only_the_margin() -> None:
    """`stream.files` in this deployment: published, read by nobody. Groups
    are born at `$`, so no future reader wants its past either."""
    decision = trim_decision(_snapshot(stream="stream.files"), margin_ms=10 * _MIN)

    assert decision.holder is None
    assert decision.minid == format_stream_id((_NOW_MS - 10 * _MIN, 0))


def test_nothing_is_trimmed_when_no_entry_sits_below_the_cut() -> None:
    fresh = _snapshot(_group("cg.memory", cursor_ms=_NOW_MS), first_ms=_NOW_MS - 1 * _MIN)
    assert trim_decision(fresh, margin_ms=10 * _MIN).minid is None


def test_an_empty_stream_is_never_trimmed() -> None:
    assert trim_decision(_snapshot(first_ms=None), margin_ms=0).minid is None


def test_a_group_that_has_read_nothing_holds_everything() -> None:
    """A group created at `0` (or by hand) with nothing delivered needs the
    whole stream; the cut falls before the epoch and nothing moves."""
    unread = _group("cg.backfill", cursor_ms=0)
    assert trim_decision(_snapshot(unread), margin_ms=0).minid is None


def test_a_cursor_set_past_the_head_never_licenses_a_deeper_trim() -> None:
    ahead = _group("cg.media", cursor_ms=_NOW_MS + 60 * _MIN)
    decision = trim_decision(_snapshot(ahead, head_ms=_NOW_MS), margin_ms=0)
    assert decision.boundary == (_NOW_MS, 0)


def test_the_slowest_of_several_groups_is_the_holder() -> None:
    groups = (
        _group("cg.knowledge", cursor_ms=_NOW_MS - 1 * _MIN),
        _group(f"{_CG_NOTIFY_PREFIX}.a.7", cursor_ms=_NOW_MS - 5 * _MIN),
        _group(f"{_CG_NOTIFY_PREFIX}.b.8", cursor_ms=_NOW_MS),
    )
    assert trim_decision(_snapshot(*groups), margin_ms=0).holder == f"{_CG_NOTIFY_PREFIX}.a.7"


# --------------------------------------------------------------------------- #
# GroupPosition's derived numbers                                             #
# --------------------------------------------------------------------------- #
def test_notify_groups_report_under_one_family_label() -> None:
    assert NOTIFY_GROUP_FAMILY == _CG_NOTIFY_PREFIX
    assert _group(f"{_CG_NOTIFY_PREFIX}.3f2a.9", cursor_ms=1).family == NOTIFY_GROUP_FAMILY
    assert _group("cg.knowledge", cursor_ms=1).family == "cg.knowledge"


def test_oldest_unconsumed_is_the_older_of_pending_and_undelivered() -> None:
    group = _group(
        "cg.knowledge",
        cursor_ms=_NOW_MS - 5 * _MIN,
        pending_from_ms=_NOW_MS - 9 * _MIN,
        undelivered_from_ms=_NOW_MS - 4 * _MIN,
        lag=3,
    )
    assert group.oldest_unconsumed_id == (_NOW_MS - 9 * _MIN, 0)
    assert _snapshot(group).age_s(group.oldest_unconsumed_id) == 540.0
    assert _group("cg.media", cursor_ms=_NOW_MS).oldest_unconsumed_id is None


def test_unread_trimmed_is_exact_and_unknown_when_redis_cannot_say() -> None:
    """entries-added - entries-read - lag: 1,000 added, the group read 100,
    300 of the 900 after its cursor remain -- 600 were deleted unread (the
    numbers of the live probe on Redis 7.4)."""
    behind = _group("cg.knowledge", cursor_ms=1, entries_read=100, lag=300)
    assert behind.unread_trimmed(1000) == 600
    assert _group("cg.knowledge", cursor_ms=1, entries_read=1000, lag=0).unread_trimmed(1000) == 0
    assert _group("cg.notify.h.7", cursor_ms=1, entries_read=None).unread_trimmed(1000) is None
    assert _group("cg.knowledge", cursor_ms=1, entries_read=5, lag=None).unread_trimmed(9) is None


def test_stream_ids_round_trip() -> None:
    assert parse_stream_id(b"1790622610859-3") == (1790622610859, 3)
    assert parse_stream_id("7") == (7, 0)
    assert parse_stream_id(None) is None
    assert parse_stream_id(b"nope") is None
    assert format_stream_id((12, 4)) == "12-4"


# --------------------------------------------------------------------------- #
# The reading                                                                 #
# --------------------------------------------------------------------------- #
class _ScriptedRedis:
    """Answers exactly the calls ``read_stream_snapshot``/``StreamTrimmer``
    make, from per-stream canned replies shaped like redis-py's own (str keys
    for XINFO, bytes ids), and records them."""

    def __init__(self, streams: Mapping[str, Mapping[str, object]]) -> None:
        self._streams = streams
        self.calls: list[tuple[object, ...]] = []

    def pipeline(self, transaction: bool = True) -> _ScriptedPipeline:
        assert transaction, "the snapshot must be one MULTI/EXEC"
        return _ScriptedPipeline(self)

    async def xpending(self, stream: str, group: str) -> dict[str, object]:
        self.calls.append(("xpending", stream, group))
        pending = self._streams[stream]["pending"]
        assert isinstance(pending, dict)
        return {"pending": 1, "min": pending[group], "max": pending[group], "consumers": []}

    async def xrange(
        self, stream: str, *, min: str, max: str, count: int
    ) -> list[tuple[bytes, dict[bytes, bytes]]]:
        self.calls.append(("xrange", stream, min, max, count))
        after = self._streams[stream]["after"]
        assert isinstance(after, dict)
        return [(after[min], {b"ce": b"{}"})] if min in after else []

    async def xtrim(self, stream: str, *, minid: str, approximate: bool) -> int:
        self.calls.append(("xtrim", stream, minid, approximate))
        trims = self._streams[stream].get("trims", 0)
        assert isinstance(trims, int)
        return trims


class _ScriptedPipeline:
    def __init__(self, client: _ScriptedRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, str | None]] = []

    async def __aenter__(self) -> _ScriptedPipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def time(self) -> None:
        self._queued.append(("time", None))

    def xinfo_stream(self, stream: str) -> None:
        self._queued.append(("xinfo_stream", stream))

    def xinfo_groups(self, stream: str) -> None:
        self._queued.append(("xinfo_groups", stream))

    async def execute(self) -> list[object]:
        self._client.calls.append(("multi", *[q[0] for q in self._queued]))
        stream = next(s for _, s in self._queued if s is not None)
        if stream not in self._client._streams:
            raise ResponseError(f"Command # 2 (XINFO STREAM {stream}) caused error: no such key")
        spec = self._client._streams[stream]
        return [(_NOW_MS // 1000, (_NOW_MS % 1000) * 1000), spec["info"], spec["groups"]]


def _info(first: bytes | None, head: bytes, *, length: int, added: int) -> dict[str, object]:
    return {
        "length": length,
        "entries-added": added,
        "last-generated-id": head,
        "first-entry": None if first is None else (first, {b"ce": b"{}"}),
    }


def _row(
    name: bytes, *, cursor: bytes, pending: int = 0, lag: int | None = 0, read: int | None = None
) -> dict[str, object]:
    return {
        "name": name,
        "consumers": 1,
        "pending": pending,
        "last-delivered-id": cursor,
        "entries-read": read,
        "lag": lag,
    }


_STREAMS: dict[str, dict[str, object]] = {
    "stream.knowledge": {
        "info": _info(b"1000-0", b"9000-0", length=40, added=40),
        "groups": [
            _row(b"cg.knowledge", cursor=b"5000-0", pending=1, lag=10, read=30),
            _row(b"cg.notify.h.7", cursor=b"9000-0", lag=0, read=40),
        ],
        "pending": {"cg.knowledge": b"4000-0"},
        "after": {"(5000-0": b"5001-0"},
        "trims": 12,
    },
}


async def test_the_snapshot_is_one_transaction_plus_only_the_lookups_it_needs() -> None:
    client = _ScriptedRedis(_STREAMS)

    snapshot = await read_stream_snapshot(client, "stream.knowledge")  # type: ignore[arg-type]

    assert snapshot is not None
    assert client.calls[0] == ("multi", "time", "xinfo_stream", "xinfo_groups")
    # XPENDING for the one group with something pending, no XRANGE unasked.
    assert client.calls[1:] == [("xpending", "stream.knowledge", "cg.knowledge")]
    assert snapshot.now_ms == _NOW_MS
    assert snapshot.first_entry_id == (1000, 0)
    worker, notify = snapshot.groups
    assert worker.needs_from == (4000, 0)
    assert worker.oldest_undelivered_id is None
    assert notify.family == NOTIFY_GROUP_FAMILY


async def test_ages_look_up_the_first_undelivered_entry_of_groups_behind() -> None:
    client = _ScriptedRedis(_STREAMS)

    snapshot = await read_stream_snapshot(
        client,  # type: ignore[arg-type]
        "stream.knowledge",
        with_undelivered=True,
    )

    assert snapshot is not None
    assert ("xrange", "stream.knowledge", "(5000-0", "+", 1) in client.calls
    # The caught-up notify group costs nothing extra.
    assert sum(1 for call in client.calls if call[0] == "xrange") == 1
    assert snapshot.groups[0].oldest_undelivered_id == (5001, 0)


async def test_a_stream_that_does_not_exist_reads_as_none() -> None:
    client = _ScriptedRedis({})
    assert await read_stream_snapshot(client, "stream.memory") is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The loop                                                                    #
# --------------------------------------------------------------------------- #
async def test_a_pass_trims_each_listed_stream_by_minid_approximately() -> None:
    client = _ScriptedRedis(_STREAMS)
    trimmer = StreamTrimmer(
        client,  # type: ignore[arg-type]
        streams=["stream.knowledge", "stream.memory"],
        interval_s=60,
        margin_s=0,
        backstop=100_000,
    )

    [report] = await trimmer.trim_once()  # stream.memory does not exist: skipped

    assert ("xtrim", "stream.knowledge", "4000-0", True) in client.calls
    assert (report.stream, report.trimmed, report.holder) == (
        "stream.knowledge",
        12,
        "cg.knowledge",
    )


def test_a_disabled_trimmer_is_never_built() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        StreamTrimmer(object(), streams=[], interval_s=0, margin_s=0, backstop=None)  # type: ignore[arg-type]


async def test_a_failed_pass_is_logged_and_the_loop_carries_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Trimming is housekeeping: a Redis hiccup must cost one pass, never the
    loop -- and never the relay it rides beside."""
    passes = 0

    async def _failing_pass(self: StreamTrimmer) -> list[object]:
        nonlocal passes
        passes += 1
        raise ConnectionError("redis went away")

    sleeps = 0

    async def _sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(StreamTrimmer, "trim_once", _failing_pass)
    monkeypatch.setattr(stream_retention.asyncio, "sleep", _sleep)
    trimmer = StreamTrimmer(object(), streams=[], interval_s=60, margin_s=0, backstop=None)  # type: ignore[arg-type]

    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await trimmer.run_forever()

    assert passes == 3
    assert sum(r.getMessage() == "stream_trim.failed" for r in caplog.records) == 3


async def test_the_backstop_warning_fires_once_per_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    near = {
        "stream.files": {
            "info": _info(b"1-0", b"9000-0", length=75_000, added=80_000),
            "groups": [_row(b"cg.knowledge", cursor=b"1-0", lag=74_999, read=5)],
            "pending": {},
            "after": {},
        }
    }
    trimmer = StreamTrimmer(
        _ScriptedRedis(near),  # type: ignore[arg-type]
        streams=["stream.files"],
        interval_s=60,
        margin_s=0,
        backstop=100_000,
    )

    with caplog.at_level(logging.WARNING):
        await trimmer.trim_once()
        await trimmer.trim_once()

    warnings = [r for r in caplog.records if r.getMessage() == "stream_trim.backstop_near"]
    assert len(warnings) == 1
    assert warnings[0].__dict__["holder"] == "cg.knowledge"


# --------------------------------------------------------------------------- #
# The relay: where the trimmer runs, and the م-8 switch                        #
# --------------------------------------------------------------------------- #
@pytest.fixture
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(env_settings._EnvSettings.model_config, "env_file", None)


@pytest.mark.usefixtures("_no_dotenv")
async def test_the_relay_builds_a_trimmer_over_every_published_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STREAM_TRIM_INTERVAL_S", raising=False)
    _, _, trimmer, disposables = build_relay_from_env()
    try:
        assert isinstance(trimmer, StreamTrimmer)
        assert trimmer._streams == PUBLISHED_STREAMS
        assert trimmer._backstop == EventSettings().stream_maxlen
    finally:
        for dispose in disposables:
            await dispose()


@pytest.mark.usefixtures("_no_dotenv")
async def test_zero_interval_restores_the_pre_5_5_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    """`م-8`: `STREAM_TRIM_INTERVAL_S=0` must bring back MAXLEN-only, with
    no trimmer built at all -- not a trimmer that happens to sleep forever."""
    monkeypatch.setenv("STREAM_TRIM_INTERVAL_S", "0")
    _, _, trimmer, disposables = build_relay_from_env()
    try:
        assert trimmer is None
    finally:
        for dispose in disposables:
            await dispose()


async def test_the_entrypoint_runs_the_trimmer_beside_the_relay_and_stops_it_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relay's loop is what the process IS; the trimmer is a background
    task that is cancelled before the client it uses is closed."""
    events: list[str] = []

    class _Relay:
        async def run_forever(self) -> None:
            events.append("relay")
            await asyncio.sleep(0)  # let the trimmer task start
            await asyncio.sleep(0)

    class _Trimmer:
        async def run_forever(self) -> None:
            events.append("trimmer started")
            try:
                await asyncio.Event().wait()
            finally:
                events.append("trimmer stopped")

    async def _ensure_topology() -> None:
        events.append("topology")

    async def _dispose() -> None:
        events.append("disposed")

    monkeypatch.setattr(
        outbox_relay,
        "build_relay_from_env",
        lambda: (_Relay(), _ensure_topology, _Trimmer(), [_dispose]),
    )

    await outbox_relay.run()

    assert events == ["topology", "relay", "trimmer started", "trimmer stopped", "disposed"]


async def test_a_relay_failure_still_ends_the_process_as_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug in the relay must crash the process loudly with ITS exception
    (the relay module's own "non-AppError decision"), not with whatever the
    trimmer's teardown raised on the way out."""

    class _BrokenRelay:
        async def run_forever(self) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("relay bug")

    class _Trimmer:
        async def run_forever(self) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                raise OSError("teardown noise")  # must not replace the relay's error

    async def _ensure_topology() -> None:
        return None

    monkeypatch.setattr(
        outbox_relay,
        "build_relay_from_env",
        lambda: (_BrokenRelay(), _ensure_topology, _Trimmer(), []),
    )

    with pytest.raises(RuntimeError, match="relay bug"):
        await outbox_relay.run()


# --------------------------------------------------------------------------- #
# The backstop, as arithmetic                                                 #
# --------------------------------------------------------------------------- #
# §0: 100 heavy indexing jobs a minute at peak, and each puts two events on
# `stream.knowledge` -- the busiest stream (`registered`, then `indexed` or
# `indexing_failed`; 04 §4).
_PEAK_EVENTS_PER_MIN = 100 * 2
# 5.5's acceptance names the outage it calls acceptable: `worker-knowledge`
# fully stopped for twenty minutes.
_LONGEST_ACCEPTABLE_OUTAGE_MIN = 20
# 5.5: «يغطّي ذروةَ وصولٍ كاملةً طوال أطول انقطاعٍ مقبولٍ لعامل» -- times three.
_COVERAGE_FACTOR = 3


def test_the_backstop_covers_three_peak_outages_plus_the_margin() -> None:
    """The plan's condition for keeping MAXLEN at all, re-done here so a
    change to either side fails by name: 3 x 200/min x 20 min = 12,000
    entries of backlog, plus the trim's own margin of consumed history (600 s
    at 200/min = 2,000), against a cap of 100,000 -- 7.1x the requirement.
    The same cap is what 5.2's memory budget for `redis-stream` assumes
    (four streams at STREAM_MAXLEN, 08 §4.19), so raising it is not free."""
    events = EventSettings()
    assert events.stream_maxlen is not None
    backlog = _COVERAGE_FACTOR * _PEAK_EVENTS_PER_MIN * _LONGEST_ACCEPTABLE_OUTAGE_MIN
    margin = _PEAK_EVENTS_PER_MIN * events.stream_trim_margin_s / 60
    assert events.stream_maxlen >= backlog + margin, (
        f"STREAM_MAXLEN={events.stream_maxlen} no longer covers {_COVERAGE_FACTOR} x "
        f"{_PEAK_EVENTS_PER_MIN}/min x {_LONGEST_ACCEPTABLE_OUTAGE_MIN} min + the margin "
        f"({backlog + margin:,.0f}) -- 08 §4.21"
    )


def test_the_70_percent_warning_still_fires_before_the_backstop_bites() -> None:
    """The warning ratio must leave room: 70% of the cap is still more than
    the whole three-fold requirement, so the alert is not a false alarm during an
    acceptable outage, and it fires with 30% of the cap to spare."""
    events = EventSettings()
    assert events.stream_maxlen is not None
    requirement = _COVERAGE_FACTOR * _PEAK_EVENTS_PER_MIN * _LONGEST_ACCEPTABLE_OUTAGE_MIN
    assert stream_retention.BACKSTOP_WARN_RATIO * events.stream_maxlen > requirement


# --------------------------------------------------------------------------- #
# The operator tool                                                           #
# --------------------------------------------------------------------------- #
def test_status_names_the_holder_and_prints_the_destroy_command_for_a_silent_one() -> None:
    abandoned = _group("cg.knowledge", cursor_ms=_NOW_MS - 42 * 24 * 60 * _MIN, consumers=0)
    snapshot = _snapshot(abandoned, stream="stream.files")

    status = stream_trim.stream_status(snapshot, margin_ms=10 * _MIN, backstop=100_000)
    lines = stream_trim.render_status(status)

    assert status.held_by == "cg.knowledge"
    assert status.backstop_used == 0.01
    assert any("XGROUP DESTROY stream.files cg.knowledge" in line for line in lines)


def test_status_folds_the_notify_family_into_one_line() -> None:
    groups: Sequence[GroupPosition] = [
        _group(f"{_CG_NOTIFY_PREFIX}.h.{pid}", cursor_ms=_NOW_MS) for pid in (7, 8, 9)
    ]
    lines = stream_trim.render_status(
        stream_trim.stream_status(_snapshot(*groups), margin_ms=0, backstop=None)
    )
    notify_lines = [line for line in lines if NOTIFY_GROUP_FAMILY in line and "consumers" in line]
    assert len(notify_lines) == 1
    assert "(3, worst)" in notify_lines[0]


def test_run_refuses_without_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["stream_trim", "run"])
    with pytest.raises(SystemExit, match="--yes"):
        stream_trim.main()


class _PassLedger:
    """Collects what the trimmer's `TaskReport` writes (capacity 5.7)."""

    def __init__(self) -> None:
        self.armed: list[float] = []
        self.outcomes: list[str | None] = []

    async def arm(self, task: str, *, interval_s: float, max_runtime_s: float, at: float) -> None:
        assert task == "stream_trim"
        self.armed.append(interval_s)

    async def record(self, task: str, **kwargs: object) -> None:
        assert task == "stream_trim"
        self.outcomes.append(kwargs["error"])  # type: ignore[arg-type]


async def test_every_pass_lands_in_the_task_ledger_failures_included(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5.5 wrote "no metric for the last successful pass" as a gap and named
    5.7 its home: a pass that trims nothing is silent, so silence was both the
    healthy trimmer and the dead one. Each pass is recorded -- and a failing
    one as a failure, so a trimmer failing every pass reads as failing, not
    merely as quiet."""
    results = iter([None, ConnectionError("redis went away"), None])

    async def _pass(self: StreamTrimmer) -> list[object]:
        outcome = next(results)
        if outcome is not None:
            raise outcome
        return []

    sleeps = 0

    async def _sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(StreamTrimmer, "trim_once", _pass)
    monkeypatch.setattr(stream_retention.asyncio, "sleep", _sleep)
    ledger = _PassLedger()
    trimmer = StreamTrimmer(
        object(),  # type: ignore[arg-type]
        streams=[],
        interval_s=60,
        margin_s=0,
        backstop=None,
        ledger=ledger,  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError):
        await trimmer.run_forever()

    assert ledger.armed == [60]
    assert ledger.outcomes == [None, "ConnectionError: redis went away", None]
