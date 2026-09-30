"""Hermetic tests for the outbox replay tool (``app.ops.replay``, capacity 5.6).

Everything that decides WHAT is replayed is pure and tested here: the four
verdicts, the ledger horizon, the backstop refusal, the order of ``run``'s
three steps, and the CLI's refusals. The round trip against real Postgres and
real Redis -- a batch published, the stream lost, the replay restoring every
effect exactly once -- is ``tests/integration/test_replay_ops_live.py``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.framework.errors import AppError
from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY, ConsumerBinding
from app.framework.types import Json
from app.ops import replay as replay_module
from app.ops.replay import (
    Candidate,
    ReplayPlan,
    ReplayRefused,
    Selection,
    Verdict,
    classify,
    parse_instant,
    publish_plan,
    readers_by_type,
    render_plan,
    run_replay,
)
from app.ops.retention import PROCESSED_EVENTS_RETENTION

_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
_HORIZON = _NOW - PROCESSED_EVENTS_RETENTION
_READERS = readers_by_type(STATIC_CONSUMER_TOPOLOGY)


def _candidate(
    event_type: str = "knowledge.document.registered.v1",
    *,
    stream: str = "stream.knowledge",
    created_at: datetime = _NOW - timedelta(hours=1),
    claimed_by: frozenset[str] = frozenset(),
    owed_by: frozenset[str] = frozenset(),
    dead_lettered: bool = False,
    event_id: str = "e1",
) -> Candidate:
    return Candidate(
        event_id=event_id,
        stream=stream,
        event_type=event_type,
        created_at=created_at,
        claimed_by=claimed_by,
        owed_by=owed_by,
        dead_lettered=dead_lettered,
    )


def _plan(
    rows: Sequence[tuple[Candidate, Verdict]],
    *,
    oldest_claim: datetime | None = _NOW - timedelta(days=40),
    backstop: int | None = 100_000,
    lengths: dict[str, int] | None = None,
) -> ReplayPlan:
    return ReplayPlan(
        since=_NOW - timedelta(hours=2),
        until=_NOW,
        workspace_id=None,
        streams=(),
        retention=PROCESSED_EVENTS_RETENTION,
        horizon=_HORIZON,
        oldest_claim=oldest_claim,
        rows=tuple(rows),
        backstop=backstop,
        stream_lengths=lengths or {},
    )


# --------------------------------------------------------------------------- #
# Who reads what                                                              #
# --------------------------------------------------------------------------- #
def test_readers_come_from_the_production_topology() -> None:
    """The durable readers of each handled type, and NO entry for the types
    only the notify family reads -- which is what makes them ``no_reader``."""
    assert _READERS[("stream.knowledge", "knowledge.document.registered.v1")] == {"cg.knowledge"}
    assert _READERS[("stream.knowledge", "knowledge.summary.built.v1")] == {"cg.knowledge"}
    assert _READERS[("stream.media", "media.job.requested.v1")] == {"cg.media"}
    assert _READERS[("stream.memory", "memory.item.stored.v1")] == {"cg.memory"}
    assert ("stream.knowledge", "knowledge.document.indexed.v1") not in _READERS
    assert ("stream.media", "media.job.generated.v1") not in _READERS
    assert not any(stream == "stream.files" for stream, _ in _READERS)


def test_two_groups_on_one_stream_are_both_readers() -> None:
    readers = readers_by_type(
        (
            ConsumerBinding(stream="s", group="g1", event_types=frozenset({"t"})),
            ConsumerBinding(stream="s", group="g2", event_types=frozenset({"t", "u"})),
        )
    )
    assert readers == {("s", "t"): {"g1", "g2"}, ("s", "u"): {"g2"}}


# --------------------------------------------------------------------------- #
# The four verdicts                                                           #
# --------------------------------------------------------------------------- #
def test_an_unclaimed_row_inside_the_horizon_is_replayed() -> None:
    assert classify(_candidate(), readers=_READERS, horizon=_HORIZON) is Verdict.REPLAY


def test_a_row_every_reader_claimed_is_processed() -> None:
    row = _candidate(claimed_by=frozenset({"cg.knowledge"}))
    assert classify(row, readers=_READERS, horizon=_HORIZON) is Verdict.PROCESSED


def test_notification_only_types_and_stream_files_have_no_reader() -> None:
    """Never claimed by design, so without this verdict they would look
    unprocessed forever and be re-published on every run."""
    for row in (
        _candidate("knowledge.document.indexed.v1"),
        _candidate("knowledge.document.indexing_failed.v1"),
        _candidate("media.job.generated.v1", stream="stream.media"),
        _candidate("files.file.uploaded.v1", stream="stream.files"),
    ):
        assert classify(row, readers=_READERS, horizon=_HORIZON) is Verdict.NO_READER


def test_an_unclaimed_row_older_than_the_ledger_is_never_replayed() -> None:
    """The ⚠️ of the plan made mechanical: the ledger may have swept its
    claim, and then nothing stops the effect applying twice."""
    old = _candidate(created_at=_HORIZON - timedelta(seconds=1))
    assert classify(old, readers=_READERS, horizon=_HORIZON) is Verdict.BEYOND_LEDGER
    at_the_edge = _candidate(created_at=_HORIZON)
    assert classify(at_the_edge, readers=_READERS, horizon=_HORIZON) is Verdict.REPLAY


def test_processed_and_no_reader_win_over_age() -> None:
    """Only a row that WOULD be replayed is held against the horizon, so an
    old row still says what it is rather than just "old"."""
    old = _HORIZON - timedelta(days=5)
    claimed = _candidate(created_at=old, claimed_by=frozenset({"cg.knowledge"}))
    unread = _candidate("knowledge.document.indexed.v1", created_at=old)
    assert classify(claimed, readers=_READERS, horizon=_HORIZON) is Verdict.PROCESSED
    assert classify(unread, readers=_READERS, horizon=_HORIZON) is Verdict.NO_READER


def test_a_row_its_stream_still_owes_is_left_to_the_stream() -> None:
    """ "Not claimed" is not "lost": a backlog is unclaimed too, and replaying
    it would send the whole queue twice."""
    row = _candidate(owed_by=frozenset({"cg.knowledge"}))
    assert classify(row, readers=_READERS, horizon=_HORIZON) is Verdict.IN_STREAM


def test_a_dead_lettered_row_is_left_to_the_dlq_tool() -> None:
    row = _candidate(dead_lettered=True)
    assert classify(row, readers=_READERS, horizon=_HORIZON) is Verdict.DEAD_LETTERED
    # ...unless a copy is owed again (a requeue): then the stream has it.
    requeued = _candidate(dead_lettered=True, owed_by=frozenset({"cg.knowledge"}))
    assert classify(requeued, readers=_READERS, horizon=_HORIZON) is Verdict.IN_STREAM


def test_in_stream_and_dead_lettered_win_over_age() -> None:
    old = _HORIZON - timedelta(days=1)
    owed = _candidate(created_at=old, owed_by=frozenset({"cg.knowledge"}))
    dead = _candidate(created_at=old, dead_lettered=True)
    assert classify(owed, readers=_READERS, horizon=_HORIZON) is Verdict.IN_STREAM
    assert classify(dead, readers=_READERS, horizon=_HORIZON) is Verdict.DEAD_LETTERED


def test_a_stream_must_owe_every_group_that_has_not_claimed() -> None:
    """Owed to one group, missing from the other: the other still needs a
    replay, and the one that is owed absorbs the extra copy in its ledger."""
    readers = {("s", "t"): frozenset({"g1", "g2"})}
    owed_to_one = _candidate("t", stream="s", owed_by=frozenset({"g1"}))
    claimed_one_owed_other = _candidate(
        "t", stream="s", claimed_by=frozenset({"g1"}), owed_by=frozenset({"g2"})
    )
    assert classify(owed_to_one, readers=readers, horizon=_HORIZON) is Verdict.REPLAY
    assert classify(claimed_one_owed_other, readers=readers, horizon=_HORIZON) is Verdict.IN_STREAM


def test_one_claim_is_not_enough_when_two_groups_read_the_type() -> None:
    readers = {("s", "t"): frozenset({"g1", "g2"})}
    half = _candidate("t", stream="s", claimed_by=frozenset({"g1"}))
    both = _candidate("t", stream="s", claimed_by=frozenset({"g1", "g2"}))
    assert classify(half, readers=readers, horizon=_HORIZON) is Verdict.REPLAY
    assert classify(both, readers=readers, horizon=_HORIZON) is Verdict.PROCESSED


# --------------------------------------------------------------------------- #
# The plan                                                                    #
# --------------------------------------------------------------------------- #
def test_the_backstop_refuses_a_replay_that_would_trim_itself() -> None:
    rows = [(_candidate(event_id=f"e{i}"), Verdict.REPLAY) for i in range(3)]
    assert _plan(rows, backstop=10, lengths={"stream.knowledge": 7}).over_backstop() == []
    assert _plan(rows, backstop=10, lengths={"stream.knowledge": 8}).over_backstop() == [
        "stream.knowledge"
    ]
    assert _plan(rows, backstop=None, lengths={"stream.knowledge": 10**9}).over_backstop() == []


def test_rows_that_are_not_replayed_do_not_count_against_the_backstop() -> None:
    rows = [(_candidate(event_id=f"e{i}"), Verdict.PROCESSED) for i in range(50)]
    assert _plan(rows, backstop=10, lengths={"stream.knowledge": 10}).over_backstop() == []


def test_unvouched_counts_replay_rows_older_than_the_oldest_claim() -> None:
    before = _candidate(event_id="a", created_at=_NOW - timedelta(days=3))
    after = _candidate(event_id="b", created_at=_NOW - timedelta(hours=1))
    skipped = _candidate(event_id="c", created_at=_NOW - timedelta(days=3))
    rows = [(before, Verdict.REPLAY), (after, Verdict.REPLAY), (skipped, Verdict.PROCESSED)]
    assert _plan(rows, oldest_claim=_NOW - timedelta(days=2)).unvouched() == 1
    assert _plan(rows, oldest_claim=_NOW - timedelta(days=40)).unvouched() == 0
    # An empty ledger vouches for nothing.
    assert _plan(rows, oldest_claim=None).unvouched() == 2


def test_counts_and_json_carry_every_verdict_by_stream_and_type() -> None:
    rows = [
        (_candidate(event_id="a"), Verdict.REPLAY),
        (_candidate(event_id="b", claimed_by=frozenset({"cg.knowledge"})), Verdict.PROCESSED),
        (_candidate("knowledge.document.indexed.v1", event_id="c"), Verdict.NO_READER),
        (_candidate("media.job.requested.v1", stream="stream.media", event_id="d"), Verdict.REPLAY),
    ]
    plan = _plan(rows, lengths={"stream.knowledge": 5, "stream.media": 0})
    assert plan.totals() == {
        Verdict.REPLAY: 2,
        Verdict.IN_STREAM: 0,
        Verdict.DEAD_LETTERED: 0,
        Verdict.PROCESSED: 1,
        Verdict.NO_READER: 1,
        Verdict.BEYOND_LEDGER: 0,
    }
    assert [c.event_id for c in plan.to_replay()] == ["a", "d"]
    document = plan.to_json()
    assert document["totals"] == {
        "replay": 2,
        "in_stream": 0,
        "dead_lettered": 0,
        "processed": 1,
        "no_reader": 1,
        "beyond_ledger": 0,
    }
    knowledge = document["by_stream"]["stream.knowledge"]
    assert knowledge["replay"] == {"knowledge.document.registered.v1": 1}
    assert knowledge["no_reader"] == {"knowledge.document.indexed.v1": 1}
    assert knowledge["stream_length"] == 5
    assert document["over_backstop"] == []


def test_render_shows_the_refusal_and_the_warning() -> None:
    rows = [(_candidate(event_id=f"e{i}"), Verdict.REPLAY) for i in range(3)]
    text = "\n".join(
        render_plan(_plan(rows, backstop=3, lengths={"stream.knowledge": 1}, oldest_claim=None))
    )
    assert "over -- run refuses" in text
    assert "warning: 3 row(s) to replay were created before the ledger's oldest claim" in text
    assert (
        "total: replay 3 | in_stream 0 | dead_lettered 0 | processed 0 | no_reader 0 "
        "| beyond_ledger 0"
    ) in text


def test_render_names_a_group_that_run_will_recreate() -> None:
    plan = replace(
        _plan([(_candidate(), Verdict.REPLAY)]),
        missing_groups=(("stream.knowledge", "cg.knowledge"),),
    )
    text = "\n".join(render_plan(plan))
    assert "group cg.knowledge does not exist -- run recreates it before publishing" in text
    assert plan.to_json()["missing_groups"] == ["stream.knowledge cg.knowledge"]


def test_render_of_an_empty_range_says_so() -> None:
    assert "no published rows in this range" in "\n".join(render_plan(_plan([])))


# --------------------------------------------------------------------------- #
# Publishing                                                                  #
# --------------------------------------------------------------------------- #
class _Publisher:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.sent: list[tuple[str, Json]] = []
        self._fail_at = fail_at

    async def publish(self, stream: str, event: Json) -> str:
        if self._fail_at is not None and len(self.sent) == self._fail_at:
            raise AppError("event publish failed", code="common.internal")
        self.sent.append((stream, event))
        return f"{len(self.sent)}-0"


def _stub_payloads(
    monkeypatch: pytest.MonkeyPatch, payloads: dict[str, Json]
) -> list[Sequence[str]]:
    asked: list[Sequence[str]] = []

    async def fake(engine: Any, event_ids: Sequence[str]) -> dict[str, Json]:
        asked.append(list(event_ids))
        return {i: payloads[i] for i in event_ids if i in payloads}

    monkeypatch.setattr(replay_module, "_payloads", fake)
    return asked


@pytest.mark.anyio
async def test_publish_sends_only_replay_rows_in_order_with_their_stored_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        (_candidate(event_id="a"), Verdict.REPLAY),
        (_candidate(event_id="b"), Verdict.PROCESSED),
        (_candidate("media.job.requested.v1", stream="stream.media", event_id="c"), Verdict.REPLAY),
    ]
    _stub_payloads(monkeypatch, {"a": {"id": "a"}, "b": {"id": "b"}, "c": {"id": "c"}})
    publisher = _Publisher()
    outcome = await publish_plan(object(), publisher, _plan(rows))  # type: ignore[arg-type]
    assert publisher.sent == [("stream.knowledge", {"id": "a"}), ("stream.media", {"id": "c"})]
    assert outcome.published == {"stream.knowledge": 1, "stream.media": 1}
    assert outcome.failure is None and outcome.vanished == 0


@pytest.mark.anyio
async def test_publish_stops_at_the_first_failure_and_says_how_far_it_got(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relay's head-of-line rule: a Redis failure hits every entry
    alike, so the run stops and reports rather than failing N more times."""
    rows = [(_candidate(event_id=f"e{i}"), Verdict.REPLAY) for i in range(4)]
    _stub_payloads(monkeypatch, {f"e{i}": {"id": f"e{i}"} for i in range(4)})
    outcome = await publish_plan(object(), _Publisher(fail_at=2), _plan(rows))  # type: ignore[arg-type]
    assert outcome.published == {"stream.knowledge": 2}
    assert outcome.failure is not None


@pytest.mark.anyio
async def test_a_row_gone_before_its_payload_was_read_is_counted_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [(_candidate(event_id="a"), Verdict.REPLAY), (_candidate(event_id="b"), Verdict.REPLAY)]
    _stub_payloads(monkeypatch, {"b": {"id": "b"}})
    outcome = await publish_plan(object(), _Publisher(), _plan(rows))  # type: ignore[arg-type]
    assert outcome.published == {"stream.knowledge": 1}
    assert outcome.vanished == 1


@pytest.mark.anyio
async def test_payloads_are_read_a_page_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [(_candidate(event_id=f"e{i}"), Verdict.REPLAY) for i in range(2_500)]
    asked = _stub_payloads(monkeypatch, {f"e{i}": {"id": f"e{i}"} for i in range(2_500)})
    outcome = await publish_plan(object(), _Publisher(), _plan(rows))  # type: ignore[arg-type]
    assert outcome.total == 2_500
    assert [len(page) for page in asked] == [1_000, 1_000, 500]


@pytest.mark.anyio
async def test_run_provisions_groups_before_it_selects_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module docstring's "groups first": a group created after the
    replay's XADD starts at `$` past every replayed entry and sees none."""
    calls: list[str] = []
    plan = _plan([(_candidate(), Verdict.REPLAY)])

    async def ensure_groups(client: Any, bindings: Any, streams: Any) -> list[ConsumerBinding]:
        calls.append("ensure_groups")
        return []

    async def build_plan(*args: Any, **kwargs: Any) -> ReplayPlan:
        calls.append("build_plan")
        return plan

    async def publish(*args: Any, **kwargs: Any) -> replay_module.ReplayOutcome:
        calls.append("publish")
        return replay_module.ReplayOutcome(published={"stream.knowledge": 1})

    monkeypatch.setattr(replay_module, "ensure_groups", ensure_groups)
    monkeypatch.setattr(replay_module, "build_plan", build_plan)
    monkeypatch.setattr(replay_module, "publish_plan", publish)
    await run_replay(object(), object(), Selection(since=timedelta(hours=2)))  # type: ignore[arg-type]
    assert calls == ["ensure_groups", "build_plan", "publish"]


@pytest.mark.anyio
async def test_run_refuses_before_publishing_anything_when_over_the_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    rows = [(_candidate(event_id=f"e{i}"), Verdict.REPLAY) for i in range(3)]
    over = _plan(rows, backstop=2, lengths={"stream.knowledge": 0})

    async def ensure_groups(client: Any, bindings: Any, streams: Any) -> list[ConsumerBinding]:
        return []

    async def build_plan(*args: Any, **kwargs: Any) -> ReplayPlan:
        return over

    async def publish(*args: Any, **kwargs: Any) -> replay_module.ReplayOutcome:
        published.append("publish")
        return replay_module.ReplayOutcome(published={})

    monkeypatch.setattr(replay_module, "ensure_groups", ensure_groups)
    monkeypatch.setattr(replay_module, "build_plan", build_plan)
    monkeypatch.setattr(replay_module, "publish_plan", publish)
    with pytest.raises(ReplayRefused):
        await run_replay(object(), object(), Selection(since=timedelta(hours=2)))  # type: ignore[arg-type]
    assert published == []


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def test_since_accepts_a_duration_or_an_instant_with_an_offset() -> None:
    assert parse_instant("90m") == timedelta(minutes=90)
    assert parse_instant("2h") == timedelta(hours=2)
    assert parse_instant("3d") == timedelta(days=3)
    assert parse_instant("2026-09-30T10:00Z") == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert parse_instant("2026-09-30T13:00+03:00") == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


def test_since_refuses_a_bare_local_time_and_nonsense() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="no UTC offset"):
        parse_instant("2026-09-30T10:00")
    with pytest.raises(argparse.ArgumentTypeError, match="neither a duration"):
        parse_instant("yesterday")


def _main_with(monkeypatch: pytest.MonkeyPatch, *argv: str) -> SystemExit:
    monkeypatch.setattr("sys.argv", ["app.ops.replay", *argv])
    monkeypatch.setattr(replay_module, "_run", _must_not_run)
    with pytest.raises(SystemExit) as raised:
        replay_module.main()
    return raised.value


async def _must_not_run(args: argparse.Namespace) -> int:  # pragma: no cover - a failure path
    raise AssertionError("the CLI reached the database without its guard")


def test_cli_refuses_run_without_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    exited = _main_with(monkeypatch, "run", "--since", "2h")
    assert "--yes" in str(exited.code)


def test_cli_refuses_a_replay_with_neither_range_nor_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for verb in ("plan", "run"):
        exited = _main_with(monkeypatch, verb, *(["--yes"] if verb == "run" else []))
        assert "--since" in str(exited.code) and "--workspace" in str(exited.code)


def test_cli_refuses_an_unknown_stream_and_a_malformed_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argparse exits 2 before anything is built -- a typo'd stream name
    would otherwise select nothing and report a clean, empty plan."""
    assert _main_with(monkeypatch, "plan", "--since", "2h", "--stream", "stream.nope").code == 2
    assert _main_with(monkeypatch, "plan", "--workspace", "not-a-uuid").code == 2
