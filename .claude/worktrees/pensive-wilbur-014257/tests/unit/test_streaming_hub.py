"""Unit tests for the framework connection hub (``framework/streaming/hub.py``,
5.3-ج · P1-8 step 11).

What these pin: workspace-keyed routing (tenant isolation IS the routing),
the 07 §4 per-user cap counted across workspaces, idempotent unregistration
during teardown, snapshot fan-out that survives mid-broadcast mutation, and
the rule that one dying session never costs the others their notification.

**P1-8 adds the admission half's new home.** The cap is no longer counted on
this process's heap; it lives behind ``WsConnectionRegistry``. What that makes
testable here, hermetically: the registry-outage policy (fail CLOSED on
admission, swallow on release), the renewal loop that stops a crashed
process's entry from spending a slot forever, the ``renew_interval_s <
entry_ttl_s`` wiring invariant, and — kept deliberately — the DEFECT's own
shape, so "two private counters do not make one cap" stays legible in the
suite after the fix. The live, cross-registry proof is
``tests/integration/test_ws_connection_cap_live.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.framework.errors import AppError
from app.framework.streaming import ConnectionHub
from app.framework.streaming.hub import DEFAULT_ENTRY_TTL_S, DEFAULT_RENEW_INTERVAL_S
from app.framework.types import Json
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

_W1 = "018f0000-0000-7000-8000-00000000w001"
_W2 = "018f0000-0000-7000-8000-00000000w002"
_U1 = "018f0000-0000-7000-8000-00000000u001"
_U2 = "018f0000-0000-7000-8000-00000000u002"


class _Session:
    def __init__(self, *, fails: bool = False) -> None:
        self.received: list[Json] = []
        self._fails = fails

    async def send_json(self, payload: Json) -> None:
        if self._fails:
            raise RuntimeError("socket died")
        self.received.append(payload)


def _hub(cap: int = 5) -> ConnectionHub:
    return ConnectionHub(max_connections_per_user=cap, registry=InMemoryWsConnectionRegistry())


async def test_notify_reaches_only_the_named_workspace() -> None:
    hub = _hub()
    mine, theirs = _Session(), _Session()
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=mine)
    assert await hub.try_register(workspace_id=_W2, user_id=_U2, session=theirs)

    await hub.notify(_W1, "knowledge.document.indexed.v1", {"document_id": "d1"})

    assert mine.received == [
        {
            "type": "notification",
            "event": "knowledge.document.indexed.v1",
            "data": {"document_id": "d1"},
        }
    ]
    assert theirs.received == []


async def test_notify_for_a_workspace_with_nobody_connected_is_a_noop() -> None:
    await _hub().notify(_W1, "media.job.generated.v1", {"job_id": "j1"})


async def test_every_session_of_the_workspace_receives_the_push() -> None:
    hub = _hub()
    first, second = _Session(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=first)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=second)

    await hub.notify(_W1, "media.job.generated.v1", {"job_id": "j1"})

    assert len(first.received) == 1
    assert len(second.received) == 1


async def test_one_dying_socket_never_costs_the_others_their_notification() -> None:
    hub = _hub()
    dying, healthy = _Session(fails=True), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=dying)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=healthy)

    await hub.notify(_W1, "media.job.failed.v1", {"job_id": "j1", "reason": "boom"})

    assert len(healthy.received) == 1


async def test_the_per_user_cap_counts_across_workspaces() -> None:
    """07 §4 words the cap per USER ("اتصالات WS/مستخدم") — a user cannot
    dodge it by spreading sockets over workspaces."""
    hub = _hub(cap=2)
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    assert await hub.try_register(workspace_id=_W2, user_id=_U1, session=_Session())

    assert not await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    # A refused registration must not leak into the counts.
    assert hub.user_connection_count(_U1) == 2
    # And a DIFFERENT user is unaffected.
    assert await hub.try_register(workspace_id=_W1, user_id=_U2, session=_Session())


async def test_unregister_frees_the_user_slot_and_is_idempotent() -> None:
    hub = _hub(cap=1)
    session = _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=session)
    assert not await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())

    await hub.unregister(workspace_id=_W1, session=session)
    await hub.unregister(workspace_id=_W1, session=session)  # teardown may double-call

    assert hub.user_connection_count(_U1) == 0
    assert hub.workspace_session_count(_W1) == 0
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())


async def test_unregister_of_an_unknown_session_is_a_noop() -> None:
    hub = _hub()
    await hub.unregister(workspace_id=_W1, session=_Session())

    assert hub.workspace_session_count(_W1) == 0


async def test_fanout_iterates_a_snapshot_not_the_live_registry() -> None:
    """A session that unregisters ITSELF while being notified (teardown racing
    a broadcast) must not break the iteration for the rest."""
    hub = _hub()

    class _SelfRemoving(_Session):
        async def send_json(self, payload: Json) -> None:
            await hub.unregister(workspace_id=_W1, session=self)
            await super().send_json(payload)

    remover, bystander = _SelfRemoving(), _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=remover)
    await hub.try_register(workspace_id=_W1, user_id=_U2, session=bystander)

    await hub.notify(_W1, "knowledge.document.indexed.v1", {"document_id": "d1"})

    assert len(bystander.received) == 1
    assert hub.workspace_session_count(_W1) == 1  # only the bystander remains


# --------------------------------------------------------------------------- #
# P1-8 (step 11): the cap's home is the registry, not this process's heap     #
# --------------------------------------------------------------------------- #
async def test_one_shared_registry_enforces_one_cap_across_two_hubs() -> None:
    """The fix's own regression guard, hermetic twin of the live exit
    criterion: two hubs (two simulated gunicorn workers) over ONE registry
    share ONE ceiling — the second refuses what the first already spent, even
    though it holds nothing locally."""
    registry = InMemoryWsConnectionRegistry()
    worker_a = ConnectionHub(max_connections_per_user=2, registry=registry)
    worker_b = ConnectionHub(max_connections_per_user=2, registry=registry)

    assert await worker_a.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    assert await worker_b.try_register(workspace_id=_W2, user_id=_U1, session=_Session())

    assert worker_b.user_connection_count(_U1) == 1  # locally it sees only its own
    assert not await worker_b.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    assert await worker_b.global_user_connection_count(_U1) == 2


async def test_two_private_registries_do_not_enforce_one_cap() -> None:
    """The DEFECT's own shape, kept in the suite so it stays legible after the
    fix (the ``test_a_group_shared_between_two_processes_only_delivers_to_one``
    precedent, 3.81). Give each sibling hub its OWN in-process registry — a
    faithful stand-in for the pre-fix ``self._user_counts`` dict — and the very
    sequence the test above refuses is admitted instead: four sockets against a
    ceiling of two, i.e. "the announced cap x the number of workers". Nothing
    differs between the two tests except WHERE the count lives, which is
    precisely P1-8's claim."""
    worker_a = ConnectionHub(max_connections_per_user=2, registry=InMemoryWsConnectionRegistry())
    worker_b = ConnectionHub(max_connections_per_user=2, registry=InMemoryWsConnectionRegistry())

    for hub in (worker_a, worker_b):
        for _ in range(2):
            assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())

    assert worker_a.user_connection_count(_U1) + worker_b.user_connection_count(_U1) == 4


async def test_a_registry_outage_refuses_the_connection_rather_than_admitting_it() -> None:
    """Fail CLOSED (``hub.py``'s "Redis failure policy"): failing OPEN would
    delete the cap for the duration of any Redis degradation — the P1-8 defect
    restored on demand by anyone who can disturb Redis. The refusal must be the
    ordinary ``False`` the endpoint already turns into close 1008, never an
    exception escaping into the WS handshake."""
    registry = InMemoryWsConnectionRegistry()
    hub = ConnectionHub(max_connections_per_user=5, registry=registry)
    registry.fail_with = AppError("redis is down", code="common.internal")

    assert not await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    # And nothing was recorded locally either -- a refused socket leaves no
    # half-registered residue behind for `notify` to target.
    assert hub.user_connection_count(_U1) == 0
    assert hub.workspace_session_count(_W1) == 0


async def test_a_registry_outage_during_teardown_never_raises() -> None:
    """``unregister`` runs in the endpoint's ``finally``; raising there would
    abort the rest of teardown (cancelling the run tasks is what BILLS an
    abandoned run). The local registry is still cleaned, so ``notify`` stops
    targeting the dying socket even when the shared release could not be
    written -- that entry ages out on its own."""
    registry = InMemoryWsConnectionRegistry()
    hub = ConnectionHub(max_connections_per_user=5, registry=registry)
    session = _Session()
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=session)
    registry.fail_with = AppError("redis is down", code="common.internal")

    await hub.unregister(workspace_id=_W1, session=session)  # must not raise

    assert hub.user_connection_count(_U1) == 0
    assert hub.workspace_session_count(_W1) == 0


async def test_a_renewed_entry_survives_its_ttl_and_an_abandoned_one_does_not() -> None:
    """The anti-leak mechanism, driven deterministically over a fake clock
    instead of by sleeping: ``renew_once`` is what the background loop calls,
    and it keeps only the entries THIS hub still holds young."""
    clock = _Clock()
    registry = InMemoryWsConnectionRegistry(now=clock)
    crashed = ConnectionHub(
        max_connections_per_user=2, registry=registry, entry_ttl_s=10, renew_interval_s=1
    )
    live = ConnectionHub(
        max_connections_per_user=2, registry=registry, entry_ttl_s=10, renew_interval_s=1
    )
    assert await crashed.try_register(workspace_id=_W1, user_id=_U1, session=_Session())
    assert await live.try_register(workspace_id=_W2, user_id=_U1, session=_Session())
    assert not await live.try_register(workspace_id=_W1, user_id=_U1, session=_Session())

    # `crashed` never renews again (its process is gone); `live` does, twice.
    for _ in range(2):
        clock.advance(6)
        await live.renew_once()

    assert await live.global_user_connection_count(_U1) == 1
    assert await live.try_register(workspace_id=_W1, user_id=_U1, session=_Session())


async def test_the_renewal_loop_starts_stops_and_is_idempotent() -> None:
    """``start_renewal``/``stop_renewal`` are lifespan hooks: a lifespan
    re-entered in tests must not leave two loops racing, and shutdown must
    cancel AND reap the task before the Redis client under it closes."""
    registry = InMemoryWsConnectionRegistry()
    hub = ConnectionHub(
        max_connections_per_user=2, registry=registry, entry_ttl_s=1, renew_interval_s=0
    )
    assert await hub.try_register(workspace_id=_W1, user_id=_U1, session=_Session())

    await hub.start_renewal()
    first = hub._renewal
    await hub.start_renewal()
    assert hub._renewal is first  # not a second, racing loop
    await asyncio.sleep(0)  # let it run at least one pass

    await hub.stop_renewal()
    await hub.stop_renewal()  # idempotent, like every other teardown hook here

    assert hub._renewal is None
    assert first is not None and first.done()


async def test_renewing_no_faster_than_entries_expire_is_refused_at_construction() -> None:
    """A live socket that outlives its own entry would silently stop counting,
    loosening the cap with no symptom -- so the wiring bug is caught where it
    is written."""
    with pytest.raises(ValueError, match="renew_interval_s"):
        ConnectionHub(
            max_connections_per_user=5,
            registry=InMemoryWsConnectionRegistry(),
            entry_ttl_s=30,
            renew_interval_s=30,
        )


def test_the_default_renewal_period_leaves_room_for_missed_renewals() -> None:
    """Not a tautology: the defaults must hold the invariant with MARGIN, so a
    Redis blip or a stalled loop costs a live socket a renewal, not its slot."""
    assert DEFAULT_RENEW_INTERVAL_S * 3 < DEFAULT_ENTRY_TTL_S


class _Clock:
    """A monotonic clock a test drives by hand (the ``InMemoryWsConnectionRegistry``
    ``now`` seam), so entry ageing is proved without sleeping."""

    def __init__(self) -> None:
        self._t = 1_000.0

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds
