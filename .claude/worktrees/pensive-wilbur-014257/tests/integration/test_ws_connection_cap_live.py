"""Live-Redis proof that the 07 §4 per-user WebSocket cap is GLOBAL, not
per process (P1-8 — ``docs/pre-release-review.md`` §3 · ``docs/log/3.90.md``).

``live_redis`` only. Reproduces the real production topology directly: TWO
independent ``ConnectionHub`` instances, each with its OWN
``RedisWsConnectionRegistry`` object, sharing ONE Redis — exactly what
``WEB_CONCURRENCY=2``'s two sibling gunicorn workers are, both inside one API
replica, and exactly the shape P0-2 taught us to test in
(``test_notify_bridge_per_process_group_live.py``).

**Fails against the pre-fix code, by the DEFECT'S OWN MECHANISM.** Before this
step ``ConnectionHub`` counted a user's sockets in ``self._user_counts``, a
plain ``dict`` on its own heap. Two hubs therefore held two private counters,
so a user could fill hub A to the ceiling and hub B would still admit five
more — the announced 5 was really 5 per worker per replica. The first test
below fills hub A and asserts hub B REFUSES; against a per-process counter
that assertion fails on its own terms, not on a signature or import error.
``tests/unit/test_streaming_hub.py::test_two_private_registries_do_not_enforce_
one_cap`` keeps the broken shape permanently in the suite so the defect stays
legible after the fix (the
``test_a_group_shared_between_two_processes_only_delivers_to_one``
precedent).

Every key is uniquely tagged (``ws:test:<uuid7>``) and deleted afterwards:
this file must never touch a production ``ws:conn:*`` counter, and the live
Redis is a shared server that is deliberately never flushed
(``conftest.live_redis``'s own docstring).
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from app.framework.identifiers import new_uuid7
from app.framework.streaming import ConnectionHub
from app.framework.types import Json, Uuid
from app.infrastructure.streaming.redis_connection_registry import RedisWsConnectionRegistry

pytestmark = [pytest.mark.live_redis]

# Short enough that the leak proof runs in seconds, still obeying the hub's own
# `renew_interval_s < entry_ttl_s` invariant. Production's numbers (90/20) hold
# the identical relationship -- this only scales the clock, not the mechanism.
_TTL_S = 2
_RENEW_S = 1


class _Session:
    def __init__(self) -> None:
        self.received: list[Json] = []

    async def send_json(self, payload: Json) -> None:
        self.received.append(payload)


def _sibling_hubs(
    redis_client: Redis, *, prefix: str, cap: int, ttl_s: int = 60
) -> tuple[ConnectionHub, ConnectionHub]:
    """Two hubs that share nothing but Redis -- one per simulated gunicorn
    worker. Each builds its OWN registry object (its own script cache, its own
    Python state), because two sibling PROCESSES could not share one."""
    return (
        ConnectionHub(
            max_connections_per_user=cap,
            registry=RedisWsConnectionRegistry(redis_client, key_prefix=prefix),
            entry_ttl_s=ttl_s,
            renew_interval_s=_RENEW_S,
        ),
        ConnectionHub(
            max_connections_per_user=cap,
            registry=RedisWsConnectionRegistry(redis_client, key_prefix=prefix),
            entry_ttl_s=ttl_s,
            renew_interval_s=_RENEW_S,
        ),
    )


@pytest.mark.anyio
async def test_the_cap_is_enforced_across_two_separate_hubs_not_within_one(
    redis_client: Redis,
) -> None:
    """THE exit criterion (``p1-hardening-plan.md`` §3 step 11): the ceiling
    holds across two SEPARATE registries, not inside one.

    Hub A (worker 1) takes every slot the user has; hub B (worker 2) -- which
    has never seen any of those sessions and whose own local map is empty --
    must still refuse. A pre-fix per-process counter admits here, which is the
    whole defect."""
    prefix = f"ws:test:{new_uuid7()}"
    user_id, workspace_id = new_uuid7(), new_uuid7()
    hub_a, hub_b = _sibling_hubs(redis_client, prefix=prefix, cap=3)
    try:
        for _ in range(3):
            assert await hub_a.try_register(
                workspace_id=workspace_id, user_id=user_id, session=_Session()
            )

        # Worker 2 holds NOTHING locally -- and must refuse anyway.
        assert hub_b.user_connection_count(user_id) == 0
        assert not await hub_b.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )
        # Both siblings agree on the one shared truth.
        assert await hub_a.global_user_connection_count(user_id) == 3
        assert await hub_b.global_user_connection_count(user_id) == 3
        # And a DIFFERENT user is untouched by the first one's ceiling.
        assert await hub_b.try_register(
            workspace_id=workspace_id, user_id=new_uuid7(), session=_Session()
        )
    finally:
        await _purge(redis_client, prefix)


@pytest.mark.anyio
async def test_simultaneous_registrations_never_overshoot_the_cap(redis_client: Redis) -> None:
    """The check-then-add race the design brief names: twelve registrations
    fired CONCURRENTLY across two sibling hubs against a ceiling of four.

    A two-round-trip implementation ("read ZCARD, then ZADD if there is room")
    would let several callers observe the same sub-cap count and all be
    admitted; the Lua script makes evict/check/add one indivisible step, so the
    admitted total is exactly the ceiling and Redis holds exactly that many
    members -- never more, not even transiently."""
    prefix = f"ws:test:{new_uuid7()}"
    user_id, workspace_id = new_uuid7(), new_uuid7()
    hub_a, hub_b = _sibling_hubs(redis_client, prefix=prefix, cap=4)
    try:
        results = await asyncio.gather(
            *(
                (hub_a if index % 2 == 0 else hub_b).try_register(
                    workspace_id=workspace_id, user_id=user_id, session=_Session()
                )
                for index in range(12)
            )
        )

        assert sum(results) == 4
        assert await redis_client.zcard(f"{prefix}:{user_id}") == 4
        assert await hub_a.global_user_connection_count(user_id) == 4
    finally:
        await _purge(redis_client, prefix)


@pytest.mark.anyio
async def test_a_crashed_processes_entry_ages_out_but_a_renewed_one_does_not(
    redis_client: Redis,
) -> None:
    """The leak the shared counter would otherwise have (the design brief's
    pitfall 3), proved in BOTH directions at once.

    ``crashed`` registers a session and then never releases and never renews it
    -- exactly what a SIGKILLed gunicorn worker leaves behind, since its
    ``finally`` never runs. ``live`` holds a session and keeps renewing it, as
    the real hub's ``start_renewal`` loop does for every socket it owns. After
    more than one full ``entry_ttl_s``:

    * the crashed worker's slot is back (otherwise the user is locked out of
      that slot forever, on a ceiling of five);
    * the live worker's slot is STILL held -- a long-lived socket must never be
      evicted merely for being long-lived, which is the failure mode a naive
      TTL introduces.

    Both ids are read back OUT of Redis rather than reached for through the
    hub, so the assertion names the exact member that survived instead of
    merely counting.
    """
    prefix = f"ws:test:{new_uuid7()}"
    key = f"{prefix}:{new_uuid7()}"
    user_id, workspace_id = key.rsplit(":", 1)[1], new_uuid7()
    crashed, live = _sibling_hubs(redis_client, prefix=prefix, cap=2, ttl_s=_TTL_S)
    try:
        assert await crashed.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )
        crashed_ids = await _members(redis_client, key)
        assert await live.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )
        live_ids = await _members(redis_client, key) - crashed_ids
        assert len(crashed_ids) == 1 and len(live_ids) == 1
        # Both slots spent: the cap genuinely bites before anything ages.
        assert not await live.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )

        # `crashed` is gone: no release, no renewal, ever again. `live` keeps
        # its own entry young exactly as its renewal loop would.
        await live.start_renewal()
        await asyncio.sleep(_TTL_S * 1.5)
        await live.stop_renewal()

        assert await _members(redis_client, key) == live_ids
        assert await live.global_user_connection_count(user_id) == 1
        # The freed slot is genuinely usable again -- the point of the bound.
        assert await live.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )
    finally:
        await live.stop_renewal()
        await _purge(redis_client, prefix)


@pytest.mark.anyio
async def test_unregister_frees_the_slot_for_the_other_sibling(redis_client: Redis) -> None:
    """A clean disconnect on worker 1 must free the slot for worker 2 --
    otherwise the global cap would be a one-way ratchet that only ever
    tightens."""
    prefix = f"ws:test:{new_uuid7()}"
    user_id, workspace_id = new_uuid7(), new_uuid7()
    hub_a, hub_b = _sibling_hubs(redis_client, prefix=prefix, cap=1)
    session = _Session()
    try:
        assert await hub_a.try_register(workspace_id=workspace_id, user_id=user_id, session=session)
        assert not await hub_b.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )

        await hub_a.unregister(workspace_id=workspace_id, session=session)
        await hub_a.unregister(workspace_id=workspace_id, session=session)  # teardown may repeat

        assert await hub_b.global_user_connection_count(user_id) == 0
        assert await hub_b.try_register(
            workspace_id=workspace_id, user_id=user_id, session=_Session()
        )
    finally:
        await _purge(redis_client, prefix)


@pytest.mark.anyio
async def test_renew_never_resurrects_a_released_entry(redis_client: Redis) -> None:
    """``ZADD XX``'s load-bearing half: a renewal that races a release must not
    re-create the slot it just gave back. Without ``XX`` the renewal loop would
    silently re-admit every socket the endpoint had already torn down, and the
    cap would fill with ghosts."""
    prefix = f"ws:test:{new_uuid7()}"
    user_id = new_uuid7()
    registry = RedisWsConnectionRegistry(redis_client, key_prefix=prefix)
    connection_id = new_uuid7()
    try:
        assert await registry.try_acquire(
            user_id=user_id, connection_id=connection_id, max_connections=1, ttl_s=60
        )
        await registry.release(user_id=user_id, connection_id=connection_id)
        await registry.release(user_id=user_id, connection_id=connection_id)  # idempotent

        await registry.renew(user_id=user_id, connection_ids=[connection_id], ttl_s=60)

        assert await registry.count(user_id=user_id, ttl_s=60) == 0
    finally:
        await _purge(redis_client, prefix)


async def _members(client: Redis, key: str) -> set[Uuid]:
    return {member.decode() for member in await client.zrange(key, 0, -1)}


async def _purge(client: Redis, prefix: str) -> None:
    """Delete only this test's own uniquely-prefixed keys -- never a FLUSHDB,
    never a production ``ws:conn:*`` key (the file docstring's own rule)."""
    keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
    if keys:
        await client.delete(*keys)
