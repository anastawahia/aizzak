"""Unit tests for the notification bridge (``framework/streaming/
notifications.py`` + ``build_notification_consumer`` in the Composition Root,
5.3-د) — hermetic.

What these pin: the handler routes a global envelope to the event's workspace
via the hub (reading the workspace from the engine-built ``ctx``, the type and
data from the envelope), degrades a malformed ``data`` to ``{}`` instead of
raising inside a stateless push, and that the ``cg.notify`` wiring covers
exactly the 04 §4 notify catalog across the two 04 §2 streams under one group.
"""

from __future__ import annotations

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.composition_root import (
    _NOTIFY_STREAMS,
    build_notification_consumer,
)
from app.framework.streaming import (
    NOTIFY_EVENT_TYPES,
    ConnectionHub,
    make_notification_handler,
)
from app.framework.types import Json
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

_W1 = "018f0000-0000-7000-8000-00000000w001"
_U1 = "018f0000-0000-7000-8000-00000000u001"


class _Session:
    def __init__(self) -> None:
        self.received: list[Json] = []

    async def send_json(self, payload: Json) -> None:
        self.received.append(payload)


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset(),
        request_id=None,
    )


def _envelope(event_type: str, workspace_id: str, data: object) -> Json:
    # The engine has already validated `type`/`workspaceid`/`id` present and
    # built `ctx` from `workspaceid` before the handler runs; this mirrors the
    # shape it hands over (`build_envelope`'s output, trimmed to what's read).
    return {"type": event_type, "workspaceid": workspace_id, "id": "evt-1", "data": data}


async def test_the_handler_pushes_to_the_events_workspace() -> None:
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session = _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=session)
    handler = make_notification_handler(hub)

    await handler(
        _ctx(_W1),
        _envelope("knowledge.document.indexed.v1", _W1, {"document_id": "d1", "chunk_count": 3}),
    )

    assert session.received == [
        {
            "type": "notification",
            "event": "knowledge.document.indexed.v1",
            "data": {"document_id": "d1", "chunk_count": 3},
        }
    ]


async def test_routing_uses_the_ctx_workspace_not_a_second_envelope_read() -> None:
    """The engine's own parse of ``workspaceid`` is authoritative — the handler
    routes by ``ctx.workspace_id``, so an event only reaches the workspace the
    engine already isolated it to."""
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    mine = _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=mine)
    handler = make_notification_handler(hub)

    # Nobody is connected for this OTHER workspace, so the push is a no-op and
    # `mine` (a different workspace) must not receive it.
    other = "018f0000-0000-7000-8000-00000000w002"
    await handler(_ctx(other), _envelope("media.job.generated.v1", other, {"job_id": "j1"}))

    assert mine.received == []


async def test_a_malformed_data_degrades_to_empty_rather_than_raising() -> None:
    """A stateless push must not raise on a bad ``data`` (the engine would then
    redeliver a push that never improves); the client still learns the event
    happened from the ``type``."""
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session = _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=session)
    handler = make_notification_handler(hub)

    for bad in (None, "a string", [1, 2], 42):
        session.received.clear()
        await handler(_ctx(_W1), _envelope("media.job.failed.v1", _W1, bad))
        assert session.received == [
            {"type": "notification", "event": "media.job.failed.v1", "data": {}}
        ]


async def test_missing_data_key_entirely_still_pushes_empty() -> None:
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session = _Session()
    await hub.try_register(workspace_id=_W1, user_id=_U1, session=session)
    handler = make_notification_handler(hub)

    await handler(_ctx(_W1), {"type": "media.job.generated.v1", "workspaceid": _W1, "id": "e"})

    assert session.received[0]["data"] == {}


# --------------------------------------------------------------------------- #
# The cg.notify wiring (build_notification_consumer)                          #
# --------------------------------------------------------------------------- #
def test_the_bridge_covers_exactly_the_notify_catalog() -> None:
    """Guards drift between the 04 §2 per-stream split and the 04 §4 catalog:
    the handler types wired across BOTH streams must equal ``NOTIFY_EVENT_TYPES``
    — no notify type left unrouted, no stray type invented."""
    wired = {t for types in _NOTIFY_STREAMS.values() for t in types}
    assert wired == set(NOTIFY_EVENT_TYPES)


def test_build_notification_consumer_wires_the_given_group_over_two_streams() -> None:
    """``group`` is a caller-supplied parameter (3.81, P0-2's fix: one group
    PER PROCESS, never a hardcoded shared literal) — every subscription wired
    by one call carries exactly the group that call was given."""
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    _consumer, subscriptions = build_notification_consumer(
        hub,
        redis_client=object(),  # type: ignore[arg-type]  # never touched by wiring
        group="cg.notify.host-a.4242",
        consumer_name="notify.test",
        block_ms=1000,
        batch_count=16,
        max_deliveries=5,
    )

    by_stream = {sub.stream: sub for sub in subscriptions}
    assert set(by_stream) == {"stream.knowledge", "stream.media"}
    for sub in subscriptions:
        assert sub.group == "cg.notify.host-a.4242"
    assert set(by_stream["stream.knowledge"].handlers) == {
        "knowledge.document.indexed.v1",
        "knowledge.document.indexing_failed.v1",
    }
    assert set(by_stream["stream.media"].handlers) == {
        "media.job.generated.v1",
        "media.job.failed.v1",
    }


def test_two_calls_with_different_groups_stay_independent() -> None:
    """The whole point of 3.81's fix: two `build_notification_consumer` calls
    (one per simulated sibling process) never share a group even though both
    wire the SAME two streams."""
    hub_a, hub_b = (
        ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
        ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
    )
    _consumer_a, subs_a = build_notification_consumer(
        hub_a,
        redis_client=object(),  # type: ignore[arg-type]
        group="cg.notify.host.111",
        consumer_name="notify.host.111",
        block_ms=1000,
        batch_count=16,
        max_deliveries=5,
    )
    _consumer_b, subs_b = build_notification_consumer(
        hub_b,
        redis_client=object(),  # type: ignore[arg-type]
        group="cg.notify.host.222",
        consumer_name="notify.host.222",
        block_ms=1000,
        batch_count=16,
        max_deliveries=5,
    )

    assert {sub.group for sub in subs_a} == {"cg.notify.host.111"}
    assert {sub.group for sub in subs_b} == {"cg.notify.host.222"}


def test_all_notify_types_share_the_one_handler_closure() -> None:
    """One ``make_notification_handler(hub)`` closure serves every type — the
    push is identical regardless of which worker result arrived."""
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    _consumer, subscriptions = build_notification_consumer(
        hub,
        redis_client=object(),  # type: ignore[arg-type]
        group="cg.notify.test",
        consumer_name="notify.test",
        block_ms=1000,
        batch_count=16,
        max_deliveries=5,
    )

    handlers = {h for sub in subscriptions for h in sub.handlers.values()}
    assert len(handlers) == 1
