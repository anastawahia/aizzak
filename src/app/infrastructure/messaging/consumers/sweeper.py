"""Automatic cleanup of Redis Streams bookkeeping nothing else reclaims --
the code half of ت-2 (``docs/operational-findings.md`` §2).

**The one fact both halves of this module exist for.** Redis never forgets a
reader. A consumer entry inside a group, and a consumer GROUP on a stream,
both outlive the process that created them by design: only an explicit
``XGROUP DELCONSUMER`` / ``XGROUP DESTROY`` removes them. Every container
recreation therefore leaves permanent tombstones behind, and they accumulate
monotonically -- measured live on 2026-08-13: two ghost consumers inside
healthy groups, and twenty orphaned ``cg.notify.<host>.<pid>`` groups from
five hostnames that will never boot again.

**Why that is worth code rather than a runbook line.** ``pending``, ``lag``
and ``consumers`` are the ONLY signals that distinguish a worker that is
"running" from one that is actually consuming (docs/log/3.134.md's own
lesson: ``docker ps`` cannot tell those apart). A measurement that must first
be hand-filtered against a list of which hostnames are currently alive is not
a monitoring signal at all. And in the consumer case the damage is not merely
cosmetic: entries left pending under a dead consumer's name are unreachable
forever (``RedisStreamsConsumer.reclaim``'s docstring), because the recovery
read that would redeliver them is keyed to a consumer name that died with its
process.

**Two sweeps, deliberately different in shape, because the decidable question
is different in each case.**

* ``sweep_stale_consumers`` runs INSIDE a live worker, over that worker's own
  groups, on a timer. A worker knows its own consumer name, so "not me" plus
  "idle far beyond the blocking-read interval" is a local, cheap, and safe
  judgement -- and the destructive step is preceded by ``XAUTOCLAIM``, so no
  message is ever dropped by it.
* ``find_orphan_notify_groups`` / ``destroy_orphan_notify_groups`` handle the
  ``cg.notify.<host>.<pid>`` family, whose owning process may be on a host
  that no longer exists. Liveness of a pid on ANOTHER host is not decidable
  from here (which is exactly why ``composition_root._sweep_stale_notify_
  groups`` restricts itself to its own hostname, and why this module does not
  weaken that guard). What IS decidable is whether anything has read under a
  group RECENTLY, and that -- confirmed by two readings a settle window apart
  -- is what these key on.

**⚠️ And the second bullet was written keying on the wrong thing (capacity
5.4).** It asked whether a consumer entry EXISTS, which the first paragraph
above already says is unanswerable: Redis never forgets a reader, so the
entry exists for every process that ever booted. The rule was therefore
structurally unable to fire on the exact leak this module was written to
close, and it did not fire -- measured on the live stack, 25 notify groups on
one stream from seven hostnames, four of them dead for 6-11 hours, every one
of them reported LIVE. The two layers had ended up covering each other's
blind spot everywhere except the one family they were both written for: a
notify group has exactly ONE member, the process it is named after, so
``sweep_stale_consumers`` -- which never touches ``live_consumer`` and only
ever runs over a live process's own groups -- can never reach it either.
``is_orphan`` now asks the answerable question, ``idle`` beyond the same
death threshold the consumer sweep already uses. Its docstring carries the
numbers.

**Who calls this.** The three ``worker-*`` processes via ``StreamConsumer``
(``engine.py``, on its own loop timer and once more at a clean exit), the API
process via ``CompositionRoot.sweep_orphan_notify_groups_forever``, and the
operator tool ``app.ops.notify_groups`` (which is now a CLI over the two
functions below rather than a second implementation of the rule).
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.framework.observability import get_logger
from app.infrastructure.messaging.redis_streams import ConsumerInfo, RedisStreamsConsumer

_logger = get_logger(__name__)

# Must equal `composition_root._CG_NOTIFY_PREFIX`. Written out rather than
# imported for the same reason `topology.py` writes its four strings out
# (its note (a)): `app.framework` must not import `app.infrastructure`, so
# the import could only go the other way and would drag the whole composition
# root into every worker. Guarded the same way instead -- a test imports both
# and asserts they are equal, so a rename fails by name.
_CG_NOTIFY_PREFIX = "cg.notify"

# A booting bridge creates its group (`ensure_group`) a moment BEFORE it
# registers a consumer (its first `XREADGROUP`), so a group can legitimately
# show zero consumers for that instant. Two observations this far apart must
# BOTH show zero before anything is destroyed -- see `find_orphan_notify_groups`.
DEFAULT_SETTLE_SECONDS = 5.0

# Mirrors `EventSettings.consumer_stale_idle_s` (15 min), for the same reason
# and with the same relation to `consumer_block_ms` that field's own comment
# argues: it is a DEATH threshold, not a cadence. Both callers pass the
# setting; this is the fallback for a direct call and for `app.ops.
# notify_groups`' default, so the two can never disagree by accident.
DEFAULT_STALE_IDLE_SECONDS = 900.0


# --------------------------------------------------------------------------- #
# Stale consumers inside a live group (ت-2, first layer)                      #
# --------------------------------------------------------------------------- #
async def sweep_stale_consumers(
    consumer: RedisStreamsConsumer,
    *,
    pairs: Iterable[tuple[str, str]],
    live_consumer: str,
    min_idle_ms: int,
) -> list[str]:
    """Reclaim-then-delete every consumer in ``pairs``' groups that is not
    ``live_consumer`` and has been idle for at least ``min_idle_ms``.
    Returns the names actually deleted, so a caller or a test can assert on
    what happened rather than on a call count.

    **The safety rule, and why it is in this order.**

    1. **Never itself.** ``live_consumer`` is the caller's own name; deleting
       it would drop the caller's OWN pending entries, and it would
       re-register on the next read anyway -- pure loss, zero gain.
    2. **Idle beyond the threshold.** A live consumer blocked in
       ``XREADGROUP`` resets its idle clock every ``consumer_block_ms`` (5 s
       by default), so a threshold two orders of magnitude above that
       (``EventSettings.consumer_stale_idle_s``, 15 min) cannot mistake a
       working sibling for a corpse. This is what makes the sweep safe under
       MULTIPLE replicas of the same worker, which is the case the
       hostname-based rules elsewhere in this codebase cannot cover.
    3. **``XAUTOCLAIM`` before ``XGROUP DELCONSUMER``, always.** A consumer
       holding pending entries owns messages; deleting it discards them
       silently and unreachably (``delete_consumer``'s own docstring). So
       anything still pending is first transferred to ``live_consumer``,
       whose next ``read`` recovery pass then processes it normally. Only a
       consumer OBSERVED at ``pending == 0`` afterwards is deleted; one that
       somehow still holds entries is left alone and logged, never forced.

    Every failure mode here is a no-op plus a log line, never an exception
    that could take a worker's loop down: this is housekeeping, and a Redis
    hiccup during it must not cost message processing. The caller
    (``StreamConsumer.run``) relies on that.
    """
    swept: list[str] = []
    for stream, group in dict.fromkeys(pairs):
        for info in await consumer.list_consumers(stream, group):
            if info.name == live_consumer or info.idle_ms < min_idle_ms:
                continue

            pending = info.pending
            if pending:
                claimed = await consumer.reclaim(
                    stream=stream,
                    group=group,
                    consumer=live_consumer,
                    min_idle_ms=min_idle_ms,
                )
                _logger.warning(
                    "consumers.sweep_reclaimed",
                    extra={
                        "stream": stream,
                        "group": group,
                        "from_consumer": info.name,
                        "to_consumer": live_consumer,
                        "entries": len(claimed),
                    },
                )
                pending = await _pending_of(consumer, stream, group, info.name)
                if pending:
                    # Not deletable: something is still owned. Left for the
                    # next sweep (or an operator), never forced.
                    _logger.warning(
                        "consumers.sweep_refused",
                        extra={
                            "stream": stream,
                            "group": group,
                            "consumer": info.name,
                            "pending": pending,
                            "reason": "entries survived XAUTOCLAIM",
                        },
                    )
                    continue

            dropped = await consumer.delete_consumer(stream, group, info.name)
            swept.append(info.name)
            if dropped:
                # Rule 3 said this could not happen: the consumer was
                # observed empty a moment ago. Loud rather than swallowed --
                # `dropped` entries are gone from every future XPENDING.
                _logger.error(
                    "consumers.sweep_dropped_pending",
                    extra={
                        "stream": stream,
                        "group": group,
                        "consumer": info.name,
                        "dropped": dropped,
                    },
                )
            else:
                _logger.info(
                    "consumers.sweep_deleted",
                    extra={
                        "stream": stream,
                        "group": group,
                        "consumer": info.name,
                        "idle_ms": info.idle_ms,
                    },
                )
    return swept


async def deregister_consumer(
    consumer: RedisStreamsConsumer, *, pairs: Iterable[tuple[str, str]], name: str
) -> list[str]:
    """Delete ``name``'s own consumer entry from each group in ``pairs`` --
    the CLEAN-exit counterpart of the timed sweep above, so a graceful
    shutdown leaves no tombstone for anyone to find later.

    Refuses (logs, does not delete) when this process still owns pending
    entries: those are messages it accepted and never acked, and dropping
    them at shutdown would be the one way this whole module could lose data.
    Leaving the tombstone instead is deliberately the lesser evil -- the next
    boot's ``sweep_stale_consumers`` reclaims those entries to a live
    consumer and only then deletes the name, which is exactly the two
    mechanisms covering each other's blind spot.
    """
    removed: list[str] = []
    for stream, group in dict.fromkeys(pairs):
        pending = await _pending_of(consumer, stream, group, name)
        if pending:
            _logger.warning(
                "consumers.deregister_refused",
                extra={"stream": stream, "group": group, "consumer": name, "pending": pending},
            )
            continue
        await consumer.delete_consumer(stream, group, name)
        removed.append(name)
        _logger.info(
            "consumers.deregistered", extra={"stream": stream, "group": group, "consumer": name}
        )
    return removed


async def _pending_of(consumer: RedisStreamsConsumer, stream: str, group: str, name: str) -> int:
    """How many entries ``name`` currently owns in ``(stream, group)``; ``0``
    when it is not registered at all (never created, or already swept)."""
    for info in await consumer.list_consumers(stream, group):
        if info.name == name:
            return info.pending
    return 0


# --------------------------------------------------------------------------- #
# Orphaned `cg.notify.<host>.<pid>` groups (ت-2, second layer)                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NotifyGroup:
    """One `cg.notify.*` group as `XINFO GROUPS` reports it, plus the
    ``XINFO CONSUMERS`` rows registered inside it.

    ``readers`` is not decoration and not an optimisation: without it this
    dataclass could only ever answer "does a consumer entry exist", and that
    question is the one this module's own opening paragraph says is
    unanswerable -- *Redis never forgets a reader*, so the entry exists for
    every process that ever booted, dead or alive. See ``is_orphan``.
    """

    stream: str
    name: str
    consumers: int
    pending: int
    readers: tuple[ConsumerInfo, ...] = ()

    @property
    def process_tag(self) -> str:
        """The ``<host>.<pid>`` suffix, or ``""`` for the legacy shared
        ``cg.notify`` group that predates the per-process split
        (docs/log/3.81.md) and therefore names no process at all."""
        return self.name[len(_CG_NOTIFY_PREFIX) :].lstrip(".")

    @property
    def is_this_host(self) -> bool:
        return self.process_tag.startswith(f"{socket.gethostname()}.")


async def read_notify_groups(
    consumer: RedisStreamsConsumer, streams: Sequence[str]
) -> list[NotifyGroup]:
    """Every `cg.notify*` group across `streams`, each carrying the consumer
    rows registered inside it. A stream that does not exist contributes
    nothing (``RedisStreamsConsumer.group_infos``' own folding of the
    ``"no such key"`` reply).

    **The second call is the whole point, and it is one per notify group.**
    ``XINFO GROUPS`` reports how MANY consumers a group has and nothing about
    them; ``XINFO CONSUMERS`` reports each one's ``idle``, which is the only
    evidence from outside a process that it is still reading. A group is only
    asked about when its name carries the notify prefix, so the static
    topology's own groups cost nothing here, and the whole census is
    ``len(streams)`` group reads plus one consumer read per notify group --
    at the shipped fleet (12 processes x 2 streams) 26 round trips per sweep,
    once every ``notify_group_sweep_interval_s``. Measured against the
    fan-out this same fleet already costs Redis, that is noise; the number is
    in ``08 §4.20``.
    """
    found: list[NotifyGroup] = []
    for stream in streams:
        for info in await consumer.group_infos(stream):
            if not info.name.startswith(_CG_NOTIFY_PREFIX):
                continue
            found.append(
                NotifyGroup(
                    stream=stream,
                    name=info.name,
                    consumers=info.consumers,
                    pending=info.pending,
                    readers=tuple(await consumer.list_consumers(stream, info.name)),
                )
            )
    return found


def is_orphan(
    group: NotifyGroup, *, min_idle_ms: int = int(DEFAULT_STALE_IDLE_SECONDS * 1000)
) -> tuple[bool, str]:
    """The safety rule, in one place, returning its own reason so a caller
    can print exactly what a sweep would act on.

    **⚠️ Gate 1 used to be "no consumer is registered", and that rule could
    not fire.** It contradicted this module's own opening sentence -- *Redis
    never forgets a reader* -- in the one family it was written for. A bridge
    that dies uncleanly leaves its consumer entry behind exactly as a worker
    does, so its group reports ``consumers: 1`` forever and "zero consumers"
    is never observed again. The consumer-level sweep does not rescue it
    either, and could not: ``sweep_stale_consumers`` runs inside a live
    process over its OWN groups, and a notify group has exactly one member --
    the process it is named after. So the tombstone consumer sits in a group
    no live process reads, and each layer's blind spot is the other layer's
    only job. Measured on the live stack 2026-09-10, seventeen minutes after
    a full recreate: **25 notify groups on ``stream.knowledge`` from seven
    hostnames, four of them dead for 6-11 hours, and the operator tool
    reported every single one "LIVE: 1 consumer(s) registered"**. The timed
    sweep fired on schedule and collected exactly one group -- the only one
    that happened to hold no consumer entry at all.

    So gate 1 asks the answerable question instead. Four gates, each
    necessary:

    1. **No reader that could still be alive.** A live bridge blocked in
       ``XREADGROUP`` resets its ``idle`` clock every ``consumer_block_ms``
       (5 s), so any consumer idle beyond ``min_idle_ms`` -- 15 min, the same
       ``EventSettings.consumer_stale_idle_s`` death threshold
       ``sweep_stale_consumers`` already relies on, and for the identical
       reason -- is a corpse. **The separation is four orders of magnitude,
       measured not assumed:** live bridge consumers on the shipped stack
       read ``idle`` 432 ms and 4,815 ms; the dead hosts' read 21,693,214 ms
       and 41,113,211 ms. The threshold sits 180x above the live ceiling and
       24x below the observed dead floor.
    2. **Nothing is pending.** A group holding delivered-but-unacked entries
       owns messages. Destroying it drops that bookkeeping silently, so this
       refuses regardless of anything else -- and it refuses on the group's
       own counter AND on any reader's, since a reader that owns entries is
       the thing ``XAUTOCLAIM`` exists to rescue.
    3. **No unexplained consumer.** ``consumers > 0`` with no reader rows to
       show for it means the two reads disagreed (a consumer registered or
       was deleted between them). Undecidable is a refusal, never a licence.
    4. **Not this host's live pid.** If the sweep runs INSIDE a live API
       container, that container's own groups are excluded by asking the OS
       -- the same ``os.kill(pid, 0)`` question ``_sweep_stale_notify_groups``
       asks, for the one host where it can be asked at all.

    ``min_idle_ms <= 0`` restores the pre-fix gate 1 exactly (any registered
    consumer refuses), because that is what ``consumer_stale_idle_s = 0``
    means everywhere else in this codebase: the idle half is switched off,
    and a switched-off threshold must never read as "everything is dead".

    **⚠️ ``inactive`` is NOT the field to use here, and it looks like it is.**
    Redis 7.2 added it alongside ``idle`` as "ms since the last successful
    read", which sounds like the sharper liveness signal. It is not: a
    healthy notify bridge on an idle stream has never successfully read
    anything, and Redis reports ``inactive: -1`` for it (measured on the same
    live consumers above). Keying on it would mark every quiet bridge dead.
    ``ConsumerInfo`` reads ``idle`` and only ``idle``.
    """
    if group.pending > 0:
        return False, f"holds {group.pending} pending entr(ies) -- XAUTOCLAIM before deleting"
    owed = sum(reader.pending for reader in group.readers)
    if owed > 0:
        return False, f"a reader still owns {owed} entr(ies) -- XAUTOCLAIM before deleting"
    if living := _living_reader(group, min_idle_ms):
        return False, living
    if group.consumers > 0 and not group.readers:
        return False, f"reports {group.consumers} consumer(s) but lists none -- undecidable"
    if group.is_this_host:
        pid_text = group.process_tag.rsplit(".", 1)[-1]
        if pid_text.isdigit() and pid_is_alive(int(pid_text)):
            return False, "this host, and its pid is alive"
    idle = [reader.idle_ms for reader in group.readers]
    return True, (
        f"nothing pending, every reader idle >= {max(idle)} ms"
        if idle
        else "no consumer, nothing pending"
    )


def _living_reader(group: NotifyGroup, min_idle_ms: int) -> str:
    """Gate 1 alone: why this group still counts as read, or ``""``.

    Split out so ``is_orphan`` reads as the flat list of gates its docstring
    describes, and so the ``min_idle_ms <= 0`` fallback -- the pre-5.4 rule,
    restored exactly when the idle threshold is switched off -- sits next to
    the rule it replaces rather than inside a branch of it.
    """
    if min_idle_ms <= 0:
        return f"live: {len(group.readers)} consumer(s) registered" if group.readers else ""
    fresh = [reader.idle_ms for reader in group.readers if reader.idle_ms < min_idle_ms]
    return f"live: read {min(fresh)} ms ago (threshold {min_idle_ms} ms)" if fresh else ""


def pid_is_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` -- signal 0 checks for existence without delivering
    anything. `PermissionError` means the pid exists but belongs to another
    user, which is still ALIVE. Mirrors `composition_root._pid_is_alive`."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def find_orphan_notify_groups(
    consumer: RedisStreamsConsumer,
    streams: Sequence[str],
    *,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    min_idle_ms: int = int(DEFAULT_STALE_IDLE_SECONDS * 1000),
) -> list[NotifyGroup]:
    """Orphans confirmed by TWO readings `settle_seconds` apart.

    One reading is not enough, and the gap is the whole reason this function
    exists rather than a filter expression at the call site. A bridge that has
    just called `ensure_group` but has not yet issued its first `XREADGROUP`
    shows zero consumers for that instant -- and destroying its group then is
    materially worse than the startup sweep's own accepted pid-reuse race,
    because that process is already past `setup()` and will never call
    `ensure_group` again: its next read fails `NOGROUP` instead of quietly
    recreating anything. A group that shows zero consumers in two readings
    seconds apart is not a bridge mid-boot; registration follows creation by
    milliseconds.

    `settle_seconds=0` skips the second reading. It exists for tests, and for
    an operator who has already stopped everything that could be booting.

    **The window did not become redundant when gate 1 learned to read
    ``idle``.** The two guards cover different states: ``idle`` answers for a
    group that HAS a reader, and the window answers for one that has none
    yet. A bridge between ``ensure_group`` and its first ``XREADGROUP`` is
    precisely the case with no consumer row to be idle, so it is still the
    window alone that spares it.
    """
    first = {(g.stream, g.name): g for g in await read_notify_groups(consumer, streams)}
    candidates = {key: g for key, g in first.items() if is_orphan(g, min_idle_ms=min_idle_ms)[0]}
    if not candidates or settle_seconds <= 0:
        return list(candidates.values())

    await asyncio.sleep(settle_seconds)
    second = {(g.stream, g.name): g for g in await read_notify_groups(consumer, streams)}
    confirmed = []
    for key in candidates:
        again = second.get(key)
        if again is None:
            continue  # Vanished between readings -- somebody else swept it.
        orphan, reason = is_orphan(again, min_idle_ms=min_idle_ms)
        if orphan:
            confirmed.append(again)
        else:
            _logger.info(
                "notify_groups.settled_live",
                extra={"stream": key[0], "group": key[1], "reason": reason},
            )
    return confirmed


async def destroy_orphan_notify_groups(
    consumer: RedisStreamsConsumer, orphans: Sequence[NotifyGroup]
) -> int:
    """``XGROUP DESTROY`` each orphan, through ``RedisStreamsConsumer`` so
    this shares the adapter's error translation and its idempotence
    (destroying an already-gone group is a silent no-op there). Returns the
    count."""
    for group in orphans:
        await consumer.destroy_group(group.stream, group.name)
        _logger.info(
            "notify_groups.destroyed",
            extra={"stream": group.stream, "group": group.name},
        )
    return len(orphans)
