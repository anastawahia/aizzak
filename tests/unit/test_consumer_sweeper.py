"""Hermetic tests for the automatic Streams cleanup (ت-2 ·
``infrastructure/messaging/consumers/sweeper.py``).

Everything runs over ``_StubRedis``, a duck-typed stand-in implementing only
the commands the adapter issues for these paths (``XINFO GROUPS`` /
``XINFO CONSUMERS`` / ``XGROUP DESTROY`` / ``XGROUP DELCONSUMER`` /
``XAUTOCLAIM``) -- the ``test_ops_dlq.py`` precedent (stub the client, never
touch a real Redis), driven through the REAL ``RedisStreamsConsumer`` so the
reply-shape handling is exercised rather than mocked away.

What this file is FOR: both sweeps here delete things nothing else can
recreate, so the interesting cases are all the ones where they must REFUSE --
a consumer that is merely busy, a consumer that still owns messages, a group
whose bridge is mid-boot. Each refusal has its own test, and so does each
positive path, because a rule that refused everything would leave the leak in
place while every safety test still passed.

The notify-group half of this module was moved here verbatim from
``app.ops.notify_groups`` (docs/log/3.135.md) when the API process started
running it on a timer; its tests moved with it, so a monkeypatch of
``_pid_is_alive`` targets the module that now owns the rule.
"""

from __future__ import annotations

import os
import socket
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.framework.di.composition_root import _CG_NOTIFY_PREFIX as _ROOT_PREFIX
from app.framework.settings.settings import EventSettings
from app.infrastructure.messaging.consumers import sweeper as module
from app.infrastructure.messaging.consumers.sweeper import (
    NotifyGroup,
    deregister_consumer,
    destroy_orphan_notify_groups,
    find_orphan_notify_groups,
    is_orphan,
    read_notify_groups,
    sweep_stale_consumers,
)
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

_STREAM = "stream.knowledge"
_GROUP = "cg.knowledge"
_LIVE = "knowledge.livehost.1"
_GHOST = "knowledge.deadhost.1"


class _StubRedis:
    """Replies in redis-py's own shapes: ``str`` keys with ``bytes`` ``name``
    values (the asymmetry ``RedisStreamsConsumer`` already depends on).

    Pending entries are modelled per consumer as a list of entry ids, which is
    the only way ``XAUTOCLAIM``'s effect (they MOVE, they are not deleted) can
    be asserted rather than assumed -- the difference between the sweep this
    module implements and one that loses messages.

    ``readings`` lets a test hand out a DIFFERENT ``XINFO GROUPS`` snapshot per
    call, which is the only way to exercise the settle window.
    """

    def __init__(self, *, claimable: bool = True) -> None:
        self.groups: dict[str, list[dict[str, Any]]] = {}
        self.pending: dict[tuple[str, str, str], list[str]] = {}
        self.idle: dict[tuple[str, str, str], int] = {}
        self.readings: list[dict[str, list[dict[str, Any]]]] = []
        self.destroyed: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str, str]] = []
        self.claims: list[tuple[str, str, str, int]] = []
        self.reads = 0
        # `False` models the one case the sweeper must refuse rather than
        # force: entries that survive the claim (another claimer won the race,
        # or Redis declined them).
        self._claimable = claimable

    # -- seeding ---------------------------------------------------------- #
    def seed_group(self, stream: str, name: str, *, consumers: int = 0, pending: int = 0) -> None:
        self.groups.setdefault(stream, []).append(
            {"name": name.encode(), "consumers": consumers, "pending": pending}
        )

    def seed_consumer(
        self, stream: str, group: str, name: str, *, idle_ms: int, entries: int = 0
    ) -> None:
        key = (stream, group, name)
        self.idle[key] = idle_ms
        self.pending[key] = [f"{i + 1}-0" for i in range(entries)]

    # -- commands --------------------------------------------------------- #
    async def xinfo_groups(self, name: str) -> list[dict[str, Any]]:
        snapshot = self.readings[min(self.reads, len(self.readings) - 1)] if self.readings else None
        self.reads += 1
        source = snapshot if snapshot is not None else self.groups
        if name not in source:
            # The real reply for a stream nobody ever created -- an error,
            # not an empty list.
            raise ResponseError("ERR no such key")
        return source[name]

    async def xgroup_destroy(self, name: str, groupname: str) -> int:
        self.destroyed.append((name, groupname))
        self.groups[name] = [
            g for g in self.groups.get(name, []) if g["name"].decode() != groupname
        ]
        return 1

    async def xinfo_consumers(self, name: str, groupname: str) -> list[dict[str, Any]]:
        rows = [
            {"name": consumer.encode(), "pending": len(self.pending[key]), "idle": self.idle[key]}
            for key in sorted(self.idle)
            for (stream, group, consumer) in [key]
            if stream == name and group == groupname
        ]
        if not rows and name not in self.groups:
            raise ResponseError("ERR no such key")
        return rows

    async def xgroup_delconsumer(self, name: str, groupname: str, consumername: str) -> int:
        self.deleted.append((name, groupname, consumername))
        key = (name, groupname, consumername)
        dropped = len(self.pending.pop(key, []))
        self.idle.pop(key, None)
        return dropped

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        count: int = 100,
        justid: bool = False,
    ) -> list[Any]:
        self.claims.append((name, groupname, consumername, min_idle_time))
        moved: list[str] = []
        if self._claimable:
            for key in list(self.pending):
                stream, group, consumer = key
                if stream != name or group != groupname or consumer == consumername:
                    continue
                if self.idle.get(key, 0) < min_idle_time:
                    continue
                moved.extend(self.pending[key])
                self.pending[key] = []
        destination = (name, groupname, consumername)
        self.pending.setdefault(destination, []).extend(moved)
        self.idle.setdefault(destination, 0)
        # Redis 7's three-element reply, `JUSTID` shape.
        return [b"0-0", [entry.encode() for entry in moved], []]


def _consumer(client: _StubRedis) -> RedisStreamsConsumer:
    return RedisStreamsConsumer(cast(Redis, client))


def _group(name: str, *, consumers: int = 0, pending: int = 0) -> NotifyGroup:
    return NotifyGroup(stream=_STREAM, name=name, consumers=consumers, pending=pending)


def _dead_host_group() -> str:
    """A notify group named after a host that is definitionally not this one."""
    return f"{module._CG_NOTIFY_PREFIX}.deadhost0000.7"


# --------------------------------------------------------------------------- #
# Stale consumers                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_sweeper_never_deletes_its_own_registration() -> None:
    """Rule 1. The caller's own name is the one entry that is certainly live,
    and deleting it would drop the caller's OWN pending entries -- the sweep's
    single most damaging possible mistake, and the cheapest to rule out."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=10_000_000, entries=3)

    swept = await sweep_stale_consumers(
        _consumer(client), pairs=[(_STREAM, _GROUP)], live_consumer=_LIVE, min_idle_ms=60_000
    )

    assert swept == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_a_consumer_idle_less_than_the_threshold_is_left_alone() -> None:
    """Rule 2, and the reason the threshold must stay far above
    `consumer_block_ms`: a working sibling resets its idle clock on every
    blocking read, so anything below the threshold is presumed alive."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=100)
    client.seed_consumer(_STREAM, _GROUP, "knowledge.sibling.1", idle_ms=4_000)

    swept = await sweep_stale_consumers(
        _consumer(client), pairs=[(_STREAM, _GROUP)], live_consumer=_LIVE, min_idle_ms=900_000
    )

    assert swept == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_an_idle_empty_ghost_is_deleted() -> None:
    """The whole point: Redis keeps a dead container's consumer entry forever,
    and every future `XINFO CONSUMERS` reading is buried under them."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=50)
    client.seed_consumer(_STREAM, _GROUP, _GHOST, idle_ms=3_600_000)

    swept = await sweep_stale_consumers(
        _consumer(client), pairs=[(_STREAM, _GROUP)], live_consumer=_LIVE, min_idle_ms=900_000
    )

    assert swept == [_GHOST]
    assert client.deleted == [(_STREAM, _GROUP, _GHOST)]
    assert client.claims == []  # Nothing pending -- no reason to claim.


@pytest.mark.asyncio
async def test_a_ghosts_pending_entries_are_claimed_before_it_is_deleted() -> None:
    """Rule 3, and the reason this sweep is a RECOVERY path rather than only a
    cleanup one: entries stuck under a dead consumer's name are redelivered to
    nobody (the recovery read is keyed to a name that died with its process),
    so they must move to a live consumer BEFORE the tombstone goes."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=50)
    client.seed_consumer(_STREAM, _GROUP, _GHOST, idle_ms=3_600_000, entries=2)

    swept = await sweep_stale_consumers(
        _consumer(client), pairs=[(_STREAM, _GROUP)], live_consumer=_LIVE, min_idle_ms=900_000
    )

    assert swept == [_GHOST]
    assert client.claims == [(_STREAM, _GROUP, _LIVE, 900_000)]
    # Moved, not dropped: the live consumer now owns both entries, and its own
    # next `read` recovery pass is what processes them.
    assert client.pending[(_STREAM, _GROUP, _LIVE)] == ["1-0", "2-0"]
    assert client.deleted == [(_STREAM, _GROUP, _GHOST)]


@pytest.mark.asyncio
async def test_a_ghost_whose_entries_survive_the_claim_is_refused() -> None:
    """The one case where tidiness must lose: entries still owned after
    `XAUTOCLAIM` would be DISCARDED by `XGROUP DELCONSUMER`, silently and
    unreachably. The ghost stays (and is re-attempted next sweep) instead."""
    client = _StubRedis(claimable=False)
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=50)
    client.seed_consumer(_STREAM, _GROUP, _GHOST, idle_ms=3_600_000, entries=2)

    swept = await sweep_stale_consumers(
        _consumer(client), pairs=[(_STREAM, _GROUP)], live_consumer=_LIVE, min_idle_ms=900_000
    )

    assert swept == []
    assert client.deleted == []
    assert client.pending[(_STREAM, _GROUP, _GHOST)] == ["1-0", "2-0"]


@pytest.mark.asyncio
async def test_a_group_that_does_not_exist_yet_sweeps_to_nothing() -> None:
    """A worker whose group was never created (nothing has ever been
    published) must not turn its first sweep into a boot failure."""
    swept = await sweep_stale_consumers(
        _consumer(_StubRedis()),
        pairs=[("stream.never.created", _GROUP)],
        live_consumer=_LIVE,
        min_idle_ms=1,
    )

    assert swept == []


@pytest.mark.asyncio
async def test_deregister_removes_this_processs_own_entry() -> None:
    """The clean-exit half (`workers/lifecycle.py`): a graceful stop leaves no
    tombstone at all, so the timed sweep has nothing to find later."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=10)

    removed = await deregister_consumer(_consumer(client), pairs=[(_STREAM, _GROUP)], name=_LIVE)

    assert removed == [_LIVE]
    assert client.deleted == [(_STREAM, _GROUP, _LIVE)]


@pytest.mark.asyncio
async def test_deregister_refuses_while_this_process_still_owns_messages() -> None:
    """Shutting down with unacked entries must NOT delete them: leaving the
    tombstone is the lesser evil, because the next boot's sweep reclaims those
    entries to a live consumer and only then removes the name."""
    client = _StubRedis()
    client.seed_consumer(_STREAM, _GROUP, _LIVE, idle_ms=10, entries=1)

    removed = await deregister_consumer(_consumer(client), pairs=[(_STREAM, _GROUP)], name=_LIVE)

    assert removed == []
    assert client.deleted == []
    assert client.pending[(_STREAM, _GROUP, _LIVE)] == ["1-0"]


def test_the_stale_threshold_stays_far_above_the_blocking_read_interval() -> None:
    """The relation, not the number, is what makes the sweep safe under
    multiple replicas: a live consumer resets its idle clock every
    `consumer_block_ms`, so the default threshold must not be anywhere near
    it. Guarded because both values are independently editable."""
    events = EventSettings()

    assert events.consumer_stale_idle_s * 1000 > events.consumer_block_ms * 10
    assert events.consumer_sweep_interval_s > 0


# --------------------------------------------------------------------------- #
# Orphaned notify groups (moved here from `app.ops.notify_groups`)            #
# --------------------------------------------------------------------------- #
def test_the_prefix_agrees_with_the_composition_root() -> None:
    """The one constant this module copies rather than imports (module
    docstring), guarded exactly the way `topology.py` guards its four literal
    stream names: a rename on either side fails here by name instead of
    silently making the sweeper match nothing forever."""
    assert _ROOT_PREFIX == module._CG_NOTIFY_PREFIX


@pytest.mark.asyncio
async def test_the_static_topologys_own_groups_are_never_even_considered() -> None:
    """`cg.knowledge`/`cg.media`/`cg.memory` must outlive every process --
    destroying one resets a whole module's delivery position to the stream
    tail, silently dropping everything published in between. They do not carry
    the notify prefix, so they never reach the orphan rule at all."""
    client = _StubRedis()
    client.seed_group(_STREAM, "cg.knowledge", consumers=0, pending=0)
    client.seed_group(_STREAM, _dead_host_group(), consumers=0, pending=0)

    found = await read_notify_groups(_consumer(client), [_STREAM])

    assert [g.name for g in found] == [_dead_host_group()]


@pytest.mark.asyncio
async def test_a_missing_stream_contributes_nothing_rather_than_raising() -> None:
    """A stream nobody has ever created answers `XINFO GROUPS` with an error,
    not an empty list -- folded here the same way `list_groups` folds it."""
    client = _StubRedis()
    client.seed_group(_STREAM, _dead_host_group())

    found = await read_notify_groups(_consumer(client), [_STREAM, "stream.never.created"])

    assert [g.name for g in found] == [_dead_host_group()]


def test_a_group_with_a_registered_consumer_is_never_an_orphan() -> None:
    """Gate 1. A consumer entry means a process is reading under this group --
    or that a ghost consumer of a dead one is still registered inside a group
    that is legitimately in use (docs/log/3.134.md). Both are refusals."""
    orphan, reason = is_orphan(_group(_dead_host_group(), consumers=1))

    assert orphan is False
    assert "consumer" in reason


def test_a_group_holding_pending_entries_is_never_an_orphan() -> None:
    """Gate 2. Pending entries are delivered-but-unacked messages this group
    OWNS. Destroying it drops that bookkeeping with no trace, so the refusal
    names the recovery command instead."""
    orphan, reason = is_orphan(_group(_dead_host_group(), pending=3))

    assert orphan is False
    assert "XAUTOCLAIM" in reason


def test_this_hosts_own_live_process_is_never_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 3. Run inside a live API container, this rule can see one host's
    process table -- its own. A group named after a pid that is alive right now
    is refused even though it shows no consumer, which is precisely the
    bridge-mid-boot case the settle window also covers."""
    monkeypatch.setattr(module, "pid_is_alive", lambda pid: True)
    name = f"{module._CG_NOTIFY_PREFIX}.{socket.gethostname()}.{os.getpid()}"

    orphan, reason = is_orphan(_group(name))

    assert orphan is False
    assert "pid is alive" in reason


def test_this_hosts_dead_process_is_an_orphan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of gate 3 -- without this, the rule could refuse
    everything and every other test here would still pass."""
    monkeypatch.setattr(module, "pid_is_alive", lambda pid: False)
    name = f"{module._CG_NOTIFY_PREFIX}.{socket.gethostname()}.4194305"

    orphan, _ = is_orphan(_group(name))

    assert orphan is True


def test_a_dead_hosts_group_is_an_orphan_without_asking_any_process_table() -> None:
    """The whole reason this rule exists: the startup sweep cannot judge
    another host's pid, so it never touches these and they accumulate forever
    (module docstring). Here they are decidable -- via the consumer registry,
    not via liveness."""
    orphan, reason = is_orphan(_group(_dead_host_group()))

    assert orphan is True
    assert "no consumer" in reason


def test_the_legacy_shared_group_is_sweepable() -> None:
    """`cg.notify` with no `<host>.<pid>` suffix at all is the pre-§3.81
    shared group. It names no process, so no liveness question applies; it is
    judged by the same consumer/pending gates as everything else."""
    group = _group(module._CG_NOTIFY_PREFIX)

    assert group.process_tag == ""
    assert group.is_this_host is False
    assert is_orphan(group)[0] is True


def test_pid_liveness_answers_yes_for_this_very_process() -> None:
    """`pid_is_alive` is the one place this module asks the OS anything, and a
    version that always answered `False` would make gate 3 vanish while every
    mocked test above still passed."""
    assert module.pid_is_alive(os.getpid()) is True


@pytest.mark.asyncio
async def test_a_group_that_registers_between_the_two_readings_is_spared() -> None:
    """The settle window, which is the difference between this rule and a
    one-line filter. A bridge that has just created its group but has not yet
    issued its first `XREADGROUP` shows zero consumers for that instant -- and
    it is already past `setup()`, so destroying its group now means its next
    read fails `NOGROUP` rather than quietly recreating anything."""
    booting = f"{module._CG_NOTIFY_PREFIX}.otherhost0001.9"
    client = _StubRedis()
    client.readings = [
        {_STREAM: [{"name": booting.encode(), "consumers": 0, "pending": 0}]},
        {_STREAM: [{"name": booting.encode(), "consumers": 1, "pending": 0}]},
    ]

    orphans = await find_orphan_notify_groups(_consumer(client), [_STREAM], settle_seconds=0.01)

    assert orphans == []
    assert client.reads == 2


@pytest.mark.asyncio
async def test_a_group_unused_in_both_readings_survives_confirmation() -> None:
    """The positive path through the same window: still unused seconds later,
    so it is confirmed rather than merely suspected."""
    client = _StubRedis()
    client.seed_group(_STREAM, _dead_host_group())

    orphans = await find_orphan_notify_groups(_consumer(client), [_STREAM], settle_seconds=0.01)

    assert [g.name for g in orphans] == [_dead_host_group()]
    assert client.reads == 2


@pytest.mark.asyncio
async def test_destroy_removes_exactly_the_orphans_and_nothing_else() -> None:
    """End to end over the stub: two orphans, one live group, one group with
    pending entries, and the static topology's own group -- one survivor set,
    asserted by name rather than by count."""
    client = _StubRedis()
    client.seed_group(_STREAM, "cg.knowledge")
    client.seed_group(_STREAM, _dead_host_group())
    client.seed_group(_STREAM, f"{module._CG_NOTIFY_PREFIX}.deadhost0000.8")
    client.seed_group(_STREAM, f"{module._CG_NOTIFY_PREFIX}.livehost0002.5", consumers=1)
    client.seed_group(_STREAM, f"{module._CG_NOTIFY_PREFIX}.deadhost0003.6", pending=2)

    orphans = await find_orphan_notify_groups(_consumer(client), [_STREAM], settle_seconds=0)
    destroyed = await destroy_orphan_notify_groups(_consumer(client), orphans)

    assert destroyed == 2
    assert client.destroyed == [
        (_STREAM, _dead_host_group()),
        (_STREAM, f"{module._CG_NOTIFY_PREFIX}.deadhost0000.8"),
    ]
    assert {g["name"].decode() for g in client.groups[_STREAM]} == {
        "cg.knowledge",
        f"{module._CG_NOTIFY_PREFIX}.livehost0002.5",
        f"{module._CG_NOTIFY_PREFIX}.deadhost0003.6",
    }
