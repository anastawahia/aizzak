"""Unit tests for ``OutboxRelay`` (5.1-ب, ``infrastructure/messaging/outbox.py``).

Hermetic: a fake store + a fake publisher stand in for
``SqlOutboxRelayStore``/``RedisStreamsPublisher``, so these tests never touch
Postgres or Redis. ``tests/integration/test_outbox_relay_live.py`` proves the
same seam end to end against real infrastructure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from app.framework.errors import AppError
from app.framework.types import Json
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.persistence.outbox import OutboxEntry


def _entry(event_id: str, *, stream: str = "stream.media") -> OutboxEntry:
    return OutboxEntry(event_id=event_id, stream=stream, payload={"id": event_id, "data": {}})


class _FakeStore:
    """Emulates ``SqlOutboxRelayStore``. ``batches`` is a QUEUE: one list is
    popped per ``fetch_unpublished`` call; once exhausted, every further
    call returns ``[]`` (realistic "no more work" behaviour for the
    ``run_forever`` tests below, which call this many times)."""

    def __init__(self, batches: Sequence[Sequence[OutboxEntry]] = ()) -> None:
        self._batches: list[list[OutboxEntry]] = [list(b) for b in batches]
        self.mark_published_calls: list[list[str]] = []
        self.bump_attempts_calls: list[list[str]] = []
        self._fetch_error: AppError | None = None
        self._mark_error: AppError | None = None
        self._bump_error: AppError | None = None

    def fail_next_fetch(self, exc: AppError) -> None:
        self._fetch_error = exc

    def fail_next_mark_published(self, exc: AppError) -> None:
        self._mark_error = exc

    def fail_next_bump_attempts(self, exc: AppError) -> None:
        self._bump_error = exc

    async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]:
        if self._fetch_error is not None:
            exc, self._fetch_error = self._fetch_error, None
            raise exc
        if not self._batches:
            return []
        return self._batches.pop(0)

    async def mark_published(self, event_ids: Sequence[str]) -> None:
        if self._mark_error is not None:
            exc, self._mark_error = self._mark_error, None
            raise exc
        self.mark_published_calls.append(list(event_ids))

    async def bump_attempts(self, event_ids: Sequence[str]) -> None:
        if self._bump_error is not None:
            exc, self._bump_error = self._bump_error, None
            raise exc
        self.bump_attempts_calls.append(list(event_ids))


class _FakePublisher:
    """Emulates ``EventPublisher``. ``attempted`` records EVERY call
    (success or failure); ``published`` records only the ones that actually
    succeeded -- keeping the two separate is what lets a test assert "entry
    k+1 was never even ATTEMPTED", not just "never counted as published"."""

    def __init__(self) -> None:
        self.attempted: list[str] = []
        self.published: list[tuple[str, Json]] = []
        self._fail_on_call: int | None = None
        self._always_fail = False
        self._error: Exception = AppError("publish failed", code="common.internal")

    def fail_on_call(self, call_number: int, exc: Exception | None = None) -> None:
        self._fail_on_call = call_number
        if exc is not None:
            self._error = exc

    def fail_always(self, exc: Exception | None = None) -> None:
        self._always_fail = True
        if exc is not None:
            self._error = exc

    async def publish(self, stream: str, event: Json) -> str:
        attempt = len(self.attempted) + 1
        self.attempted.append(str(event["id"]))
        if self._always_fail or attempt == self._fail_on_call:
            raise self._error
        self.published.append((stream, event))
        return f"{len(self.published)}-0"


def _relay(
    store: _FakeStore,
    publisher: _FakePublisher,
    *,
    batch_size: int = 10,
    poll_interval_ms: int = 100,
    max_backoff_ms: int = 1000,
) -> OutboxRelay:
    return OutboxRelay(
        store,  # type: ignore[arg-type]
        publisher,  # type: ignore[arg-type]
        batch_size=batch_size,
        poll_interval_ms=poll_interval_ms,
        max_backoff_ms=max_backoff_ms,
    )


# --------------------------------------------------------------------------- #
# run_once -- empty fetch                                                     #
# --------------------------------------------------------------------------- #
async def test_run_once_with_an_empty_fetch_returns_zero_and_writes_nothing() -> None:
    store = _FakeStore(batches=[[]])
    publisher = _FakePublisher()

    count = await _relay(store, publisher).run_once()

    assert count == 0
    assert store.mark_published_calls == []
    assert store.bump_attempts_calls == []
    assert publisher.attempted == []


# --------------------------------------------------------------------------- #
# run_once -- full success                                                    #
# --------------------------------------------------------------------------- #
async def test_run_once_full_success_publishes_in_order_and_marks_exactly_those_ids() -> None:
    entries = [_entry("evt-1"), _entry("evt-2"), _entry("evt-3")]
    store = _FakeStore(batches=[entries])
    publisher = _FakePublisher()

    count = await _relay(store, publisher).run_once()

    assert count == 3
    assert publisher.attempted == ["evt-1", "evt-2", "evt-3"]
    assert [e["id"] for _, e in publisher.published] == ["evt-1", "evt-2", "evt-3"]
    assert store.mark_published_calls == [["evt-1", "evt-2", "evt-3"]]
    assert store.bump_attempts_calls == []


# --------------------------------------------------------------------------- #
# run_once -- partial failure (head-of-line)                                  #
# --------------------------------------------------------------------------- #
async def test_run_once_stops_at_the_first_failure_marks_only_the_prefix() -> None:
    """Mutation-battery targets #1 (mark_published(batch) instead of the
    prefix) and #2 (remove the break -- entries after the failure would be
    attempted too)."""
    entries = [_entry("evt-1"), _entry("evt-2"), _entry("evt-3"), _entry("evt-4")]
    store = _FakeStore(batches=[entries])
    publisher = _FakePublisher()
    publisher.fail_on_call(3)  # evt-3 (the third publish attempt) fails

    with pytest.raises(AppError):
        await _relay(store, publisher).run_once()

    # evt-1, evt-2 succeeded; evt-3 was attempted and failed; evt-4 was
    # NEVER attempted at all -- not just "not published".
    assert publisher.attempted == ["evt-1", "evt-2", "evt-3"]
    assert [e["id"] for _, e in publisher.published] == ["evt-1", "evt-2"]
    assert store.mark_published_calls == [["evt-1", "evt-2"]]
    assert store.bump_attempts_calls == [["evt-3"]]
    for call in store.mark_published_calls:
        assert "evt-3" not in call
        assert "evt-4" not in call


async def test_run_once_failure_on_the_very_first_entry_marks_nothing_published() -> None:
    entries = [_entry("evt-1"), _entry("evt-2")]
    store = _FakeStore(batches=[entries])
    publisher = _FakePublisher()
    publisher.fail_on_call(1)

    with pytest.raises(AppError):
        await _relay(store, publisher).run_once()

    assert publisher.attempted == ["evt-1"]
    assert store.mark_published_calls == [[]]
    assert store.bump_attempts_calls == [["evt-1"]]


# --------------------------------------------------------------------------- #
# run_once -- a non-AppError from the publisher (the open decision, resolved) #
# --------------------------------------------------------------------------- #
async def test_run_once_with_a_non_apperror_bumps_marks_the_prefix_then_propagates() -> None:
    """The 5.1-ب design brief's explicitly open decision, resolved: 'let it
    propagate after bump' (``infrastructure/messaging/outbox.py``'s module
    docstring, "non-AppError decision" paragraph). A ``RuntimeError``
    violates ``EventPublisher``'s own contract -- treated as a bug, never
    silently reclassified into the ordinary bump+break+continue path."""
    entries = [_entry("evt-1"), _entry("evt-2")]
    store = _FakeStore(batches=[entries])
    publisher = _FakePublisher()
    publisher.fail_on_call(2, RuntimeError("a real bug, not an outage"))

    with pytest.raises(RuntimeError, match="a real bug"):
        await _relay(store, publisher).run_once()

    # The attempt IS still recorded (so a deterministic bug still advances
    # toward a future DLQ threshold instead of wedging the relay forever).
    assert store.bump_attempts_calls == [["evt-2"]]
    # evt-1 truly succeeded and is marked; evt-2 never appears in any
    # mark_published call -- "must NOT mark the entry published".
    assert store.mark_published_calls == [["evt-1"]]
    for call in store.mark_published_calls:
        assert "evt-2" not in call


# --------------------------------------------------------------------------- #
# run_once -- store failures propagate as AppError too                        #
# --------------------------------------------------------------------------- #
async def test_run_once_propagates_a_fetch_failure() -> None:
    store = _FakeStore()
    store.fail_next_fetch(AppError("db unreachable", code="common.internal"))
    publisher = _FakePublisher()

    with pytest.raises(AppError):
        await _relay(store, publisher).run_once()

    assert publisher.attempted == []


# --------------------------------------------------------------------------- #
# run_forever -- backoff grows on failure, resets on any clean cycle          #
# --------------------------------------------------------------------------- #
async def test_run_forever_backs_off_exponentially_then_resets_after_a_clean_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation-battery target #6."""
    store = _FakeStore(batches=[[_entry("a")], [_entry("b")], [], [_entry("c")]])
    publisher = _FakePublisher()
    publisher.fail_always()
    relay = _relay(store, publisher, batch_size=5, poll_interval_ms=100, max_backoff_ms=1000)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr("app.infrastructure.messaging.outbox.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await relay.run_forever()

    # fail(0.1) -> fail(0.2, doubled) -> clean/empty(reset to 0.1, poll) ->
    # fail(0.1, proves the reset actually happened, NOT 0.4).
    assert sleeps == [0.1, 0.2, 0.1, 0.1]


async def test_run_forever_backs_off_on_repeated_store_failures_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_forever`` must treat a STORE failure (``fetch_unpublished``
    itself raising) the same as a publish failure -- 'the relay must survive
    a PG restart' (module docstring)."""
    store = _FakeStore()
    publisher = _FakePublisher()
    relay = _relay(store, publisher, batch_size=5, poll_interval_ms=100, max_backoff_ms=1000)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError()
        store.fail_next_fetch(AppError("db unreachable", code="common.internal"))

    store.fail_next_fetch(AppError("db unreachable", code="common.internal"))
    monkeypatch.setattr("app.infrastructure.messaging.outbox.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await relay.run_forever()

    # Doubling (not a flat repeat of the plain poll interval) proves the
    # BACKOFF path was taken, not the ordinary no-error poll path.
    assert sleeps == [0.1, 0.2]


# --------------------------------------------------------------------------- #
# run_forever -- full batch drains without sleeping                           #
# --------------------------------------------------------------------------- #
async def test_run_forever_does_not_sleep_while_draining_a_full_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(batches=[[_entry("a"), _entry("b")], [_entry("c"), _entry("d")], []])
    publisher = _FakePublisher()
    relay = _relay(store, publisher, batch_size=2, poll_interval_ms=100, max_backoff_ms=1000)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr("app.infrastructure.messaging.outbox.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await relay.run_forever()

    # Two FULL batches (size == batch_size) drained back to back with no
    # sleep at all; the sleep only happens once the third (empty) fetch
    # comes back and the loop finally has nothing left to drain.
    assert sleeps == [0.1]
    assert len(store.mark_published_calls) == 2


# --------------------------------------------------------------------------- #
# run_forever -- cancellation propagates                                      #
# --------------------------------------------------------------------------- #
async def test_run_forever_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore(batches=[[]])
    publisher = _FakePublisher()
    relay = _relay(store, publisher)

    async def fake_sleep(seconds: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr("app.infrastructure.messaging.outbox.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await relay.run_forever()
