"""Unit tests for ``stream-topology-plan.md`` §3 — the outbox relay must
provision every static consumer group (``STATIC_CONSUMER_TOPOLOGY``) BEFORE
its first publish, on every entrypoint that ever runs it
(``app.workers.outbox_relay.run``, which both the Compose ``outbox-relay``
service and RunPod's ``supervisord.conf`` execute unchanged — the plan's
§1-هـ, "no extra line needed in either").

Hermetic, the ``test_redis_blocking_timeouts.py`` precedent restated: every
client factory ``build_relay_from_env`` calls (``create_engine``/
``create_redis_client``) is lazy, so calling it for real here opens no
network connection at all, and disposing a never-connected client is a
documented no-op on the wire (``test_composition_root.py``'s own comment on
``dispose_all``). ``RedisStreamsConsumer`` — the one piece of this module
that would otherwise issue a real ``XGROUP CREATE`` — is monkeypatched to a
spy for every test below, so no Redis command is ever actually sent, and
``OutboxRelay.run_forever`` is monkeypatched per-instance so no Postgres
connection is ever attempted either.

Tests 1 and 2 go through ``outbox_relay.py::run`` itself, not just the
``ensure_topology`` closure in isolation: the sequencing this plan step
protects is at that ENTRYPOINT (build → provision → run → teardown), and a
test that only called the closure directly would keep passing even if
``run`` stopped calling it at all. Proof: deleting the
``await ensure_topology()`` line from ``outbox_relay.py`` fails test 1 —
docs/log/3.107.md carries the captured before/after ``pytest`` output.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from redis.asyncio import Redis

from app.framework.di.lifecycle import Disposable
from app.framework.errors import AppError
from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.workers import outbox_relay
from app.workers.bootstrap import build_relay_from_env


def _redis_client_of(disposables: Sequence[Disposable]) -> Redis:
    """The ``test_redis_blocking_timeouts.py`` lookup, restated here so this
    file stays self-contained: pick the one ``Redis`` client out of a
    builder's disposables list by bound-method ownership (``__self__``)."""
    for dispose in disposables:
        owner = getattr(dispose, "__self__", None)
        if isinstance(owner, Redis):
            return owner
    raise AssertionError("no Redis client found among the disposables")


class _SpyConsumer:
    """Stand-in for ``RedisStreamsConsumer``: records every ``ensure_group``
    call onto a list SHARED with whatever else a test wants ordered against
    it (a fake ``run_forever``'s own ``"publish"`` marker, in tests 1/2) —
    ordering across two different objects is exactly what those tests
    measure. ``fail_on`` (1-indexed call number) lets test 2 simulate a
    real ``ensure_group`` failure partway through the four pairs without
    touching Redis at all.
    """

    def __init__(
        self, client: object, *, calls: list[tuple[str, ...]], fail_on: int | None = None
    ) -> None:
        self.client = client
        self._calls = calls
        self._fail_on = fail_on
        self._call_count = 0

    async def ensure_group(self, stream: str, group: str) -> None:
        self._call_count += 1
        if self._fail_on is not None and self._call_count == self._fail_on:
            # The same message/code `RedisStreamsConsumer._translate_consume`
            # raises for a real failure — this test cares that AN `AppError`
            # escapes uncaught, not about which driver-level exception caused it.
            raise AppError("event consume failed", code="common.internal")
        self._calls.append(("ensure_group", stream, group))


def _install_spy_consumer(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, ...]], *, fail_on: int | None = None
) -> list[_SpyConsumer]:
    """Monkeypatch ``RedisStreamsConsumer`` where ``build_relay_from_env``
    looks it up, and return the list of spy instances it constructs (always
    exactly one, in practice — ``build_relay_from_env`` mounts one consumer
    per call) so a test can inspect what it was built with."""
    instances: list[_SpyConsumer] = []

    def _factory(client: object) -> _SpyConsumer:
        spy = _SpyConsumer(client, calls=calls, fail_on=fail_on)
        instances.append(spy)
        return spy

    monkeypatch.setattr("app.workers.bootstrap.RedisStreamsConsumer", _factory)
    return instances


# --------------------------------------------------------------------------- #
# Test 1: all four `ensure_group` calls happen before the relay's first      #
# publish -- through `outbox_relay.py::run` itself, not the closure alone.   #
# --------------------------------------------------------------------------- #
async def test_ensure_group_for_all_four_pairs_precedes_the_first_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    _install_spy_consumer(monkeypatch, calls)

    relay, ensure_topology, disposables = build_relay_from_env()

    async def _fake_run_forever() -> None:
        calls.append(("publish",))

    monkeypatch.setattr(relay, "run_forever", _fake_run_forever)
    monkeypatch.setattr(
        "app.workers.outbox_relay.build_relay_from_env",
        lambda: (relay, ensure_topology, disposables),
    )

    await outbox_relay.run()

    ensure_group_calls = [c for c in calls if c[0] == "ensure_group"]
    # Guard the guard (§0 bullet in the plan's coverage rule): an empty or
    # short list must not pass silently.
    assert len(ensure_group_calls) == 4
    assert {(stream, group) for _, stream, group in ensure_group_calls} == {
        (binding.stream, binding.group) for binding in STATIC_CONSUMER_TOPOLOGY
    }
    # `publish` really happened, AND every `ensure_group` call precedes it.
    assert calls[-1] == ("publish",)
    assert calls[:4] == ensure_group_calls


# --------------------------------------------------------------------------- #
# Test 2: a failing `ensure_group` blocks boot outright -- propagates, never #
# swallowed, and the relay never publishes on a half-provisioned topology.   #
# --------------------------------------------------------------------------- #
async def test_ensure_group_failure_propagates_and_blocks_the_first_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    _install_spy_consumer(monkeypatch, calls, fail_on=2)  # fails on the second pair

    relay, ensure_topology, disposables = build_relay_from_env()

    async def _fake_run_forever() -> None:
        calls.append(("publish",))

    monkeypatch.setattr(relay, "run_forever", _fake_run_forever)
    monkeypatch.setattr(
        "app.workers.outbox_relay.build_relay_from_env",
        lambda: (relay, ensure_topology, disposables),
    )

    with pytest.raises(AppError, match="event consume failed"):
        await outbox_relay.run()

    # Not swallowed anywhere along the way (`outbox_relay.run`'s `finally`
    # only disposes resources, it never catches), and the relay never
    # reached `run_forever` -- no publish on a half-provisioned topology.
    assert ("publish",) not in calls
    assert len(calls) == 1  # only the one successful `ensure_group` before the failure


# --------------------------------------------------------------------------- #
# Test 3: the topology consumer is mounted over the SAME Redis client the    #
# publisher uses -- no second connection.                                    #
# --------------------------------------------------------------------------- #
async def test_topology_consumer_shares_the_publisher_redis_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    instances = _install_spy_consumer(monkeypatch, calls)

    _, ensure_topology, disposables = build_relay_from_env()
    await ensure_topology()

    assert len(instances) == 1
    assert instances[0].client is _redis_client_of(disposables)
