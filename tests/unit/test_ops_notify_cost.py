"""Hermetic tests for the fan-out cost meter (capacity 5.4 ·
``app/ops/notify_cost.py``).

A measurement tool's own arithmetic is the one thing a live run cannot check:
if the pacing drifts or a delta is computed against the wrong baseline, the
number still prints and still looks plausible. So the pacing, the deltas and
the derived ratios are pinned here, and the live stack is left to supply the
inputs rather than to validate the maths.

The ``test_ops_dlq.py`` precedent throughout: stub the client, never touch a
real Redis.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.framework.di.composition_root import _NOTIFY_STREAMS
from app.infrastructure.messaging.redis_streams import GroupInfo
from app.ops import notify_cost as module
from app.ops.notify_cost import (
    NOTIFY_STREAM_TYPES,
    Census,
    RedisSample,
    await_drain,
    publish_at_rate,
)


class _RecordingPublisher:
    """``RedisStreamsPublisher``'s one method, remembering what it was asked
    to publish and when."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any], float]] = []

    async def publish(self, stream: str, event: dict[str, Any]) -> str:
        self.published.append((stream, event, time.monotonic()))
        return f"{len(self.published)}-0"


def _sample(**overrides: Any) -> RedisSample:
    base: dict[str, Any] = {
        "at": 0.0,
        "cpu_sys_s": 0.0,
        "cpu_user_s": 0.0,
        "ops_per_sec": 0,
        "net_in_bytes": 0,
        "net_out_bytes": 0,
        "used_memory": 0,
        "commands": {},
    }
    return RedisSample(**{**base, **overrides})


def test_the_stream_map_agrees_with_the_composition_roots() -> None:
    """The one table this module copies rather than imports, guarded the way
    ``sweeper.py``'s prefix is: a stream added to (or renamed in) the bridge's
    subscription set without being added here would make this tool measure a
    fan-out narrower than the one that is actually running, and report the
    difference as a saving."""
    assert [stream for stream, _ in NOTIFY_STREAM_TYPES] == list(_NOTIFY_STREAMS)
    for stream, event_type in NOTIFY_STREAM_TYPES:
        assert event_type in _NOTIFY_STREAMS[stream]


@pytest.mark.asyncio
async def test_the_rate_is_paced_against_a_deadline_not_a_sleep_interval() -> None:
    """A loop that sleeps ``1/rate`` after each publish drifts by the publish
    latency every iteration and lands under the requested rate -- which would
    make every per-event number wrong in the flattering direction. Absolute
    due times do not drift, so the achieved rate tracks the request."""
    publisher = _RecordingPublisher()

    published, achieved = await publish_at_rate(
        publisher,  # type: ignore[arg-type]
        rate=200.0,
        seconds=0.25,
        workspace_id="w",
    )

    assert published == 50
    assert 150.0 < achieved < 260.0
    assert len(publisher.published) == 50


@pytest.mark.asyncio
async def test_the_two_notify_streams_get_equal_shares() -> None:
    """The bridge reads both streams under one group, so a run that loaded
    only one of them would measure half the subscription set and none of the
    multi-stream read path."""
    publisher = _RecordingPublisher()

    await publish_at_rate(
        publisher,  # type: ignore[arg-type]
        rate=500.0,
        seconds=0.1,
        workspace_id="w",
    )

    counts = {stream for stream, _, _ in publisher.published}
    assert counts == {stream for stream, _ in NOTIFY_STREAM_TYPES}


@pytest.mark.asyncio
async def test_rate_zero_publishes_nothing_but_still_holds_the_window_open() -> None:
    """The idle-cost baseline. Returning immediately would make the window
    ~0 s long and every "per process per second" figure divide by noise; the
    point of ``--rate 0`` is to hold twelve bridges under observation while
    they do nothing."""
    publisher = _RecordingPublisher()
    started = time.monotonic()

    published, achieved = await publish_at_rate(
        publisher,  # type: ignore[arg-type]
        rate=0.0,
        seconds=0.2,
        workspace_id="w",
    )

    assert (published, achieved) == (0, 0.0)
    assert publisher.published == []
    assert time.monotonic() - started >= 0.2


def test_the_delta_reports_growth_not_totals() -> None:
    """Every counter Redis exposes here is monotonic since server start, so a
    report that printed ``after`` would describe the server's whole life
    instead of the window. The commands table drops anything that did not
    move, for the same reason."""
    census = Census(groups={"s": 2}, hosts={"h": 1}, live_hosts=("h",), dead_hosts=(), orphans=())
    before = _sample(
        at=10.0, cpu_sys_s=1.0, cpu_user_s=2.0, net_out_bytes=500, commands={"xadd": (10, 100.0)}
    )
    after = _sample(
        at=20.0,
        cpu_sys_s=1.5,
        cpu_user_s=2.5,
        net_out_bytes=1_500,
        commands={"xadd": (60, 400.0), "ping": (3, 9.0)},
    )

    delta = module._delta(before, after, census=census)

    assert delta["window_s"] == 10.0
    assert delta["redis_cpu_s"] == 1.0
    assert delta["redis_cpu_pct"] == 10.0
    assert delta["net_out_bytes"] == 1_000
    assert delta["commands"]["xadd"] == {"calls": 50, "usec": 300.0}
    # `ping` is not one of the fan-out commands, so it lands in the tail
    # rather than being dropped -- a command that appeared out of nowhere
    # during the window has to remain visible somewhere.
    assert delta["other_commands"] == {"calls": 3, "usec": 9.0}


def test_a_command_that_did_not_move_is_not_reported_as_zero() -> None:
    """Redis reports every command the server has ever run. Listing the
    unmoved ones would bury the six that matter under forty that did
    nothing."""
    census = Census(groups={}, hosts={}, live_hosts=(), dead_hosts=(), orphans=())
    sample = _sample(at=0.0, commands={"xack": (7, 70.0)})

    delta = module._delta(sample, _sample(at=1.0, commands={"xack": (7, 70.0)}), census=census)

    assert delta["commands"] == {}
    assert delta["other_commands"] == {"calls": 0, "usec": 0.0}


def test_the_per_event_ratios_divide_by_events_and_the_idle_floor_does_not() -> None:
    """The distinction 5.4's own sentence misses. Per-event cost is
    ``(events x processes)``-shaped and vanishes at zero events; the idle
    floor is ``processes``-shaped and does not. They must not be the same
    number computed twice."""
    record = {
        "published": 100,
        "census": {"processes": 12, "live_processes": 12},
        "delta": {
            "window_s": 10.0,
            "redis_cpu_s": 1.2,
            "net_out_bytes": 120_000,
            "commands": {"xreadgroup": {"calls": 600, "usec": 6_000.0}},
        },
    }

    derived = module._derive(record)

    assert derived["redis_us_per_event"] == pytest.approx(12_000.0)
    assert derived["redis_us_per_event_process"] == pytest.approx(1_000.0)
    assert derived["reads_per_event"] == pytest.approx(6.0)
    assert derived["out_bytes_per_event"] == pytest.approx(1_200.0)
    assert derived["redis_us_per_process_s"] == pytest.approx(10_000.0)
    assert derived["reads_per_process_s"] == pytest.approx(5.0)


def test_the_idle_floor_survives_a_window_with_no_events() -> None:
    """``--rate 0`` divides by ``published == 0`` in three places. A
    ``ZeroDivisionError`` here would mean the baseline run -- the one this
    tool exists to be able to take -- is the one run it cannot complete."""
    record = {
        "published": 0,
        "census": {"processes": 12, "live_processes": 12},
        "delta": {
            "window_s": 60.0,
            "redis_cpu_s": 0.6,
            "net_out_bytes": 0,
            "commands": {"xreadgroup": {"calls": 2_880, "usec": 30_000.0}},
        },
    }

    derived = module._derive(record)

    assert derived["redis_us_per_event"] == 0.0
    assert derived["reads_per_event"] == 0.0
    assert derived["redis_us_per_process_s"] == pytest.approx(833.33, rel=1e-3)
    assert derived["reads_per_process_s"] == pytest.approx(4.0)


def test_a_census_counts_processes_not_groups() -> None:
    """One process holds one group on EACH notify stream, so counting groups
    reports twice the fleet -- and "12 processes" is the number 5.4 is
    written against."""
    census = Census(
        groups={"stream.knowledge": 12, "stream.media": 12},
        hosts={"hostA": 4, "hostB": 4, "hostC": 4},
        live_hosts=("hostA", "hostB", "hostC"),
        dead_hosts=(),
        orphans=(),
        live_processes=12,
    )

    assert census.total_groups == 24
    assert census.processes == 12


def test_the_ratios_divide_by_live_processes_not_by_tombstones() -> None:
    """Found on the first live run: 25 tagged processes of which 13 were
    tombstones. A tombstoned group issues no `XREADGROUP` and costs nothing
    per second, so dividing by the total would halve every per-process figure
    -- an error in the direction of "cheaper than it is"."""
    record = {
        "published": 0,
        "census": {"processes": 25, "live_processes": 12},
        "delta": {
            "window_s": 60.0,
            "redis_cpu_s": 0.6,
            "net_out_bytes": 0,
            "commands": {"xreadgroup": {"calls": 2_880, "usec": 30_000.0}},
        },
    }

    derived = module._derive(record)

    assert derived["reads_per_process_s"] == pytest.approx(4.0)  # 2880 / 12 / 60, not / 25


class _StubGroups:
    """``RedisStreamsConsumer.group_infos`` alone, handing out a different
    snapshot per call so a drain can be watched converging."""

    def __init__(self, snapshots: list[dict[str, list[GroupInfo]]]) -> None:
        self._snapshots = snapshots
        self.calls = 0

    async def group_infos(self, stream: str) -> list[GroupInfo]:
        snapshot = self._snapshots[
            min(self.calls // len(NOTIFY_STREAM_TYPES), len(self._snapshots) - 1)
        ]
        self.calls += 1
        return snapshot.get(stream, [])


def _only_knowledge(*groups: GroupInfo) -> dict[str, list[GroupInfo]]:
    return {"stream.knowledge": list(groups)}


@pytest.mark.asyncio
async def test_the_drain_waits_on_lag_not_on_pending() -> None:
    """⚠️ The flaw this test pins. ``pending`` is delivered-and-unacked, so a
    group that has read NOTHING reports ``pending == 0`` -- a drain keyed on
    it returns "caught up" before a single bridge has read, closes the window
    early, and reports a fan-out cost near zero. The first snapshot below is
    exactly that state."""
    stub = _StubGroups(
        [
            _only_knowledge(GroupInfo(name="cg.notify.h.7", consumers=1, pending=0, lag=100)),
            _only_knowledge(GroupInfo(name="cg.notify.h.7", consumers=1, pending=0, lag=0)),
        ]
    )

    drained, _ = await await_drain(stub, skip=frozenset(), timeout_s=5.0)  # type: ignore[arg-type]

    assert drained is True
    assert stub.calls > len(NOTIFY_STREAM_TYPES)  # it did NOT return on the first reading


@pytest.mark.asyncio
async def test_an_orphaned_group_is_not_waited_for() -> None:
    """A tombstone's lag grows forever because nothing reads it. Waiting for
    it would time out every run on any stack carrying one -- and on the stack
    this tool was first run against, 25 groups were in that state."""
    orphan = GroupInfo(name="cg.notify.dead.7", consumers=1, pending=0, lag=999)
    stub = _StubGroups([_only_knowledge(orphan)])

    drained, waited = await await_drain(
        stub,  # type: ignore[arg-type]
        skip=frozenset({"stream.knowledge/cg.notify.dead.7"}),
        timeout_s=5.0,
    )

    assert drained is True
    assert waited < 1.0


@pytest.mark.asyncio
async def test_a_group_that_never_catches_up_times_out_rather_than_hanging() -> None:
    """And says so, so the report can mark its own read counts as
    undercounted instead of presenting them as final."""
    stuck = GroupInfo(name="cg.notify.h.7", consumers=1, pending=0, lag=5)
    stub = _StubGroups([_only_knowledge(stuck)])

    drained, waited = await await_drain(stub, skip=frozenset(), timeout_s=0.4)  # type: ignore[arg-type]

    assert drained is False
    assert waited >= 0.4


def test_an_undeterminable_lag_is_skipped_rather_than_read_as_zero() -> None:
    """Redis reports nil when a group's lag cannot be computed. Reading that
    as 0 would be "caught up"; reading it as blocking would hang. It is
    neither -- `GroupInfo.lag` keeps it as `None` all the way through."""
    assert GroupInfo(name="g", consumers=1, pending=0).lag is None
