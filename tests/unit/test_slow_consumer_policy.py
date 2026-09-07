"""Guards for the 3.6 slow-consumer policy: one bounded outbox and one sender
task per session, and one registry call per renewal tick.

**What was measured before any of this was written** (probes in
``docs/capacity-plan.md`` §3.6), on real sockets through the API's own
``_SocketSession`` adapter, three sessions in one workspace with one client
that completes the handshake and then never reads:

* 300 notifications reach all three sessions in **53 ms** when nobody is slow;
* with the non-reading client present, each HEALTHY session received **21 of
  300** and then nothing for 15 seconds — the fan-out was parked inside one
  ``await send_json``;
* and a reader in a **different workspace** received 21 of 300 too, because
  the 5.3-د bridge dispatches one event at a time and a parked ``notify``
  parks the consumer for every tenant, not just the stalled one's.

Every timing assertion below therefore uses a REAL stall (a session that waits
on an ``asyncio.Event`` nobody sets) rather than a mock, and asserts under
``asyncio.timeout``: a hub that fans out serially cannot pass them, which
``test_these_guards_can_actually_fail`` demonstrates by running the shipped-
before implementation against the same fixtures.

The renewal half is guarded by counting: what changed there is not behaviour
but the NUMBER of round trips one tick costs (489 ms at 1,500 sockets held by
1,500 users, against 16.5 ms for the same work batched), so the guard counts
registry calls and pipeline executes rather than measuring a clock.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.framework.settings.settings import EventSettings
from app.framework.streaming import ConnectionHub
from app.framework.streaming import hub as hub_module
from app.framework.streaming.hub import _OVERFLOW_CLOSE_CODE, DEFAULT_SESSION_QUEUE_SIZE
from app.framework.types import Json, Uuid
from app.infrastructure.streaming.redis_connection_registry import (
    _RENEW_CHUNK,
    RedisWsConnectionRegistry,
)
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

_W1 = "018f0000-0000-7000-8000-00000000w001"
_W2 = "018f0000-0000-7000-8000-00000000w002"
_U1 = "018f0000-0000-7000-8000-00000000u001"
_U2 = "018f0000-0000-7000-8000-00000000u002"

# Every wait below is bounded by this. Long enough that a loaded CI box cannot
# fail a HEALTHY delivery (which takes microseconds), short enough that a
# genuinely serial fan-out — which waits forever — always trips it.
_PATIENCE_S = 2.0


class _Session:
    """A session whose peer reads normally."""

    def __init__(self) -> None:
        self.received: list[Json] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, payload: Json) -> None:
        self.received.append(payload)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


class _StalledSession(_Session):
    """The plan's "عميلٌ لا يقرأ": the send never fails, it never RETURNS —
    which is what a TCP write to a peer that stopped reading does once the
    buffers fill."""

    def __init__(self, *, stall_close: bool = False) -> None:
        super().__init__()
        self.released = asyncio.Event()
        self.entered = asyncio.Event()
        self._stall_close = stall_close

    async def send_json(self, payload: Json) -> None:
        self.entered.set()
        await self.released.wait()
        await super().send_json(payload)

    async def close(self, *, code: int, reason: str) -> None:
        if self._stall_close:
            await self.released.wait()
        await super().close(code=code, reason=reason)


class _DyingSession(_Session):
    """A socket that ERRORS on every write — a different failure from a stall,
    and the one the pre-3.6 hub already handled."""

    async def send_json(self, payload: Json) -> None:
        raise RuntimeError("socket died")


def _hub(cap: int = 5, **kwargs: Any) -> ConnectionHub:
    return ConnectionHub(
        max_connections_per_user=cap, registry=InMemoryWsConnectionRegistry(), **kwargs
    )


async def _notify(hub: ConnectionHub, workspace_id: Uuid, count: int = 1) -> None:
    """Dispatch as the bridge does — one event after another — under the
    patience bound, so a hub that fans out serially FAILS here instead of
    hanging a CI run for as long as its stalled peer is stalled (which is
    forever: the stall in these fixtures is an ``Event`` nobody sets)."""
    async with asyncio.timeout(_PATIENCE_S):
        for i in range(count):
            await hub.notify(workspace_id, "knowledge.document.indexed.v1", {"seq": i})


async def _until(predicate: Any) -> None:
    """Wait for a condition the sender tasks satisfy, bounded by _PATIENCE_S."""
    async with asyncio.timeout(_PATIENCE_S):
        while not predicate():
            await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# The acceptance criterion: a stalled session costs nobody else               #
# --------------------------------------------------------------------------- #
async def test_a_stalled_session_never_delays_another_session_in_its_workspace() -> None:
    """The step's own acceptance sentence, hermetic: "جلسةٌ مُبطَّأةٌ صناعيّاً
    لا ترفع زمنَ التسليم لجلسةٍ أخرى في مساحة العمل نفسها"."""
    hub = _hub()
    stalled, healthy = _StalledSession(), _Session()
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)
    assert await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    await _notify(hub, _W1)

    await _until(lambda: len(healthy.received) == 1)
    assert stalled.received == []  # still parked, and that is fine
    stalled.released.set()


async def test_a_stalled_session_never_delays_a_DIFFERENT_workspace() -> None:
    """The blast radius the measurement found and the plan did not claim: the
    bridge dispatches one event at a time, so a parked send used to park the
    consumer itself — a second tenant's reader went silent for a stall it had
    no connection to."""
    hub = _hub()
    stalled, other_tenant = _StalledSession(), _Session()
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)
    assert await hub.try_register(workspace_id=_W2, user_id=_U2, session=other_tenant)

    await _notify(hub, _W1)  # the blocked tenant's event, dispatched first
    await _notify(hub, _W2)  # the next event off the same stream

    await _until(lambda: len(other_tenant.received) == 1)
    stalled.released.set()


async def test_a_dying_socket_still_never_costs_the_others_their_notification() -> None:
    """Unchanged from before the queues (the pre-3.6 guard, re-pinned): an
    erroring send is swallowed, and its session keeps draining rather than
    filling — an error is not a stall."""
    hub = _hub()
    dying, healthy = _DyingSession(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=dying)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    await _notify(hub, _W1, count=DEFAULT_SESSION_QUEUE_SIZE * 3)

    await hub.deliver_pending()
    assert len(healthy.received) == DEFAULT_SESSION_QUEUE_SIZE * 3
    assert hub.session_backlog(_W1, dying) == 0
    assert hub.workspace_session_count(_W1) == 2  # nobody was evicted


# --------------------------------------------------------------------------- #
# The bound: what a stalled session may cost this process                     #
# --------------------------------------------------------------------------- #
async def test_a_stalled_sessions_backlog_is_bounded_by_the_queue_size() -> None:
    """ "⚠️ والحدُّ هو الفكرة كلُّها": an unbounded queue does not fix a slow
    consumer, it moves the growth onto this heap. Ten times the queue's depth
    is pushed at a peer that never reads; what the process holds for it is the
    depth, not the ten."""
    hub = _hub(session_queue_size=8)
    stalled = _StalledSession()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)

    await _notify(hub, _W1, count=80)

    assert hub.session_backlog(_W1, stalled) <= 8
    stalled.released.set()


async def test_the_queue_is_deeper_than_one_consumer_batch() -> None:
    """The floor under the bound: the notify bridge dispatches a whole stream
    read one event after another, so a queue at or below ``consumer_batch_count``
    would evict a session that is merely scheduled late rather than stopped."""
    assert EventSettings().consumer_batch_count < DEFAULT_SESSION_QUEUE_SIZE


async def test_a_session_that_is_merely_slow_loses_nothing() -> None:
    """The other side of that floor, behaviourally: a peer that reads late —
    released after a full consumer batch has piled up — still receives every
    one of them, in order."""
    batch = EventSettings().consumer_batch_count
    hub = _hub()
    slow = _StalledSession()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=slow)

    await _notify(hub, _W1, count=batch)
    await slow.entered.wait()
    slow.released.set()

    await hub.deliver_pending()
    assert [message["data"]["seq"] for message in slow.received] == list(range(batch))


# --------------------------------------------------------------------------- #
# The overflow policy: 1013, not silence                                      #
# --------------------------------------------------------------------------- #
async def test_an_overflowing_session_is_told_to_come_back() -> None:
    """The policy the plan offers second and this hub takes, because the first
    has no referent here (there is no criticality axis on a notification):
    close 1013 "Try Again Later", so the client reconnects and refetches rather
    than showing a wrong world forever."""
    hub = _hub(session_queue_size=4)
    stalled = _StalledSession()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)

    await _notify(hub, _W1, count=40)

    await _until(lambda: stalled.closed is not None)
    assert stalled.closed == (_OVERFLOW_CLOSE_CODE, "notification backlog")


async def test_an_overflowing_session_stops_being_routed_to_immediately() -> None:
    """Eviction is synchronous inside ``notify``: the very next event must not
    queue behind the session that just overflowed, and the workspace's healthy
    sessions must be unaffected."""
    hub = _hub(session_queue_size=4)
    stalled, healthy = _StalledSession(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    await _notify(hub, _W1, count=40)

    assert hub.workspace_session_count(_W1) == 1
    assert hub.session_backlog(_W1, stalled) == 0  # its queue is gone, not held
    await _until(lambda: len(healthy.received) == 40)


async def test_an_evicted_session_still_releases_its_registry_slot() -> None:
    """The trap under the eviction: routing and REGISTRATION are two maps, and
    the endpoint's ``finally`` runs after the hub has already stopped routing
    to the session. If idempotence were keyed off the routing map, that
    ``finally`` would be a no-op and the slot would stay spent against the cap
    until it aged out — a slot held for nobody."""
    hub = _hub(cap=1, session_queue_size=4)
    stalled = _StalledSession()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)

    await _notify(hub, _W1, count=40)
    assert hub.workspace_session_count(_W1) == 0  # evicted from routing

    await hub.unregister(workspace_id=_W1, session=stalled)  # the endpoint's finally

    assert hub.user_connection_count(_U1) == 0
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())


async def test_the_close_of_a_stalled_peer_never_blocks_the_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer that will not drain a notification will not necessarily drain a
    close frame either. The eviction's close is therefore a task of its own,
    and the guard is deliberately set so that AWAITING it would be visible:
    the close bound is thirty seconds here, the fan-out is given two — and the
    workspace's healthy session must still have every one of its 40 events."""
    monkeypatch.setattr(hub_module, "_CLOSE_TIMEOUT_S", 30.0)
    hub = _hub(session_queue_size=4)
    unclosable, healthy = _StalledSession(stall_close=True), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=unclosable)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    await _notify(hub, _W1, count=40)

    assert unclosable.closed is None  # still parked inside `close`, unwaited
    await _until(lambda: len(healthy.received) == 40)


async def test_a_close_that_never_returns_is_given_up_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """And it is bounded rather than left in flight forever: past
    ``_CLOSE_TIMEOUT_S`` the attempt is abandoned and SAID so — the endpoint's
    own teardown owns the registry slot either way."""
    monkeypatch.setattr(hub_module, "_CLOSE_TIMEOUT_S", 0.05)
    hub = _hub(session_queue_size=4)
    unclosable = _StalledSession(stall_close=True)
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=unclosable)

    with caplog.at_level("WARNING"):
        await _notify(hub, _W1, count=40)
        await _until(lambda: "hub.slow_session_close_failed" in caplog.text)


async def test_disconnect_user_is_not_parked_by_a_session_that_will_not_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same policy applied to the admin path: an account disabled while one
    of its sockets is parked mid-write must still evict the OTHERS promptly."""
    monkeypatch.setattr(hub_module, "_CLOSE_TIMEOUT_S", 0.05)
    hub = _hub()
    unclosable, second = _StalledSession(stall_close=True), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=unclosable)
    await hub.try_register(workspace_id=_W2, user_id=_U1, session=second)

    async with asyncio.timeout(_PATIENCE_S):
        await hub.disconnect_user(_U1)

    assert second.closed == (1008, "account disabled")


# --------------------------------------------------------------------------- #
# The renewal half: one tick, one conversation with the registry              #
# --------------------------------------------------------------------------- #
class _CountingRegistry(InMemoryWsConnectionRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.renew_calls = 0
        self.renewed_users: list[Uuid] = []

    async def renew(self, *, entries: Any, ttl_s: int) -> None:
        self.renew_calls += 1
        self.renewed_users.extend(entries)
        await super().renew(entries=entries, ttl_s=ttl_s)


async def test_one_renewal_tick_is_one_call_however_many_users_are_connected() -> None:
    """The measured cost this replaces: one awaited round trip per user, 1,500
    of them per tick at 1,500 connected users (489 ms), for work the registry
    does in 16.5 ms when it is handed the tick."""
    registry = _CountingRegistry()
    hub = ConnectionHub(max_connections_per_user=5, registry=registry)
    users = [f"018f0000-0000-7000-8000-{index:012d}" for index in range(300)]
    for user_id in users:
        assert await hub.try_register(workspace_id=_W1, user_id=user_id, session=_Session())

    await hub.renew_once()

    assert registry.renew_calls == 1
    assert sorted(registry.renewed_users) == sorted(users)


async def test_a_tick_with_nothing_to_renew_asks_the_registry_nothing() -> None:
    registry = _CountingRegistry()
    await ConnectionHub(max_connections_per_user=5, registry=registry).renew_once()

    assert registry.renew_calls == 0


async def test_the_tick_still_keeps_every_users_entry_young() -> None:
    """Batching changed the number of calls, not the semantics: every live
    entry is renewed, and one that was released is not resurrected."""
    now = [1000.0]
    registry = InMemoryWsConnectionRegistry(now=lambda: now[0])
    hub = ConnectionHub(
        max_connections_per_user=5, registry=registry, entry_ttl_s=90, renew_interval_s=20
    )
    kept, released = _Session(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=kept)
    await hub.try_register(workspace_id=_W2, user_id=_U2, session=released)
    await hub.unregister(workspace_id=_W2, session=released)

    now[0] += 60
    await hub.renew_once()
    now[0] += 60  # past the ttl for anything not renewed at t+60

    assert await hub.global_user_connection_count(_U1) == 1
    assert await hub.global_user_connection_count(_U2) == 0


# --------------------------------------------------------------------------- #
# The adapter: batched, but never in one unbounded burst                      #
# --------------------------------------------------------------------------- #
class _FakePipeline:
    def __init__(self, log: list[int]) -> None:
        self._log = log
        self.queued = 0

    async def execute(self) -> list[int]:
        self._log.append(self.queued)
        return [1] * self.queued


class _FakeRedis:
    """Enough of the client for the adapter's renew path: scripts that queue
    onto a pipeline, and pipelines that record how much rode each execute."""

    def __init__(self) -> None:
        self.executes: list[int] = []
        self._open: _FakePipeline | None = None

    def register_script(self, body: str) -> Any:
        async def _script(*, keys: list[str], args: list[Any], client: Any = None) -> int:
            assert client is not None, "renew must ride a pipeline, not a round trip each"
            client.queued += 1
            return 1

        return _script

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        assert transaction is False, "renewal needs batching, not a MULTI/EXEC transaction"
        self._open = _FakePipeline(self.executes)
        return self._open


async def test_the_adapter_renews_a_whole_tick_in_bounded_pipelines() -> None:
    """ "pipeline واحد" as the plan words it is measurably worse for everyone
    else on this Redis than a chunked one (see ``_RENEW_CHUNK``'s table), so
    the guard is two-sided: never one round trip per user, and never one
    unbounded burst either."""
    client = _FakeRedis()
    registry = RedisWsConnectionRegistry(client, key_prefix="ws:test")  # type: ignore[arg-type]
    users = {f"user-{index}": [f"conn-{index}"] for index in range(_RENEW_CHUNK * 2 + 1)}

    await registry.renew(entries=users, ttl_s=90)

    assert len(client.executes) == 3
    assert client.executes == [_RENEW_CHUNK, _RENEW_CHUNK, 1]
    assert max(client.executes) <= _RENEW_CHUNK


async def test_the_adapter_spends_no_round_trip_on_a_user_holding_nothing() -> None:
    client = _FakeRedis()
    registry = RedisWsConnectionRegistry(client, key_prefix="ws:test")  # type: ignore[arg-type]

    await registry.renew(entries={"user-1": [], "user-2": []}, ttl_s=90)

    assert client.executes == []


# --------------------------------------------------------------------------- #
# And the guards can fail                                                     #
# --------------------------------------------------------------------------- #
async def test_these_guards_can_actually_fail() -> None:
    """The timing assertions above are only worth their runtime if the shape
    they forbid trips them. This runs the fan-out EXACTLY as it shipped before
    3.6 — awaiting each session's send in a loop over a snapshot — against the
    same fixtures, and pins that the healthy session gets nothing while the
    stalled one is parked."""
    hub = _hub()
    stalled, healthy = _StalledSession(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=stalled)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    async def serial_notify() -> None:  # the pre-3.6 body, verbatim in shape
        payload: Json = {"type": "notification", "event": "e", "data": {}}
        for session in [stalled, healthy]:
            await session.send_json(payload)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await serial_notify()

    assert healthy.received == []  # the defect, reproduced
    stalled.released.set()
