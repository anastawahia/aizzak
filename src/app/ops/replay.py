"""Replay published events from ``platform.outbox`` back onto their streams
(capacity 5.6) -- the recovery for the one window the outbox cannot cover on
its own.

**The window.** The relay stamps ``published_at`` as soon as ``XADD``
returns. From then on the row is finished as far as Postgres knows, and the
event lives only in Redis until a consumer claims it. Redis can lose it there:
a restart from an AOF that had not reached the disk (``appendfsync everysec``,
up to a second of writes), a flush, a lost volume. Nothing sends it again --
the row says published, the consumer never saw it, and the aggregate waits
forever: a document ``pending``, a media job ``queued``, a summary that never
reaches its thread.

**Why sending it again is safe -- and only because of the ledger.** A
replayed event carries its original ``id``, and every durable handler claims
``(consumer_group, event_id)`` in ``platform.processed_events`` inside the
transaction of its effect (DD-09, 5.2-أ). An event that WAS processed comes
back as a duplicate and is acked without touching anything. Without that
ledger a replay would not be a recovery; it would apply every effect in the
range a second time. For the two summary deliveries (``summary.built`` /
``summary.build_failed``) the ledger is not one guard among several but the
only one: they append a message to a thread, and nothing else stops the same
message being appended twice.

**So the tool refuses what the ledger can no longer vouch for.** The ledger
forgets: ``app.ops.retention`` deletes claims older than
``PROCESSED_EVENTS_RETENTION``. A row created before that horizon may have
been processed and then had its claim swept, and replaying it would apply its
effect again with nothing left to stop it. Such rows are counted
(``beyond_ledger``) and never published. The horizon is the database's
``now()`` minus the same constant the sweep deletes by.

⚠️ **What it cannot see: a sweep run with a shorter window** (``retention
sweep --table processed_events --older-than-days N``). The sweep keeps no
record of its cutoff. The tool prints the ledger's oldest claim beside the
horizon and warns when rows it would replay were created before that claim.
In a young deployment that is only its age; after a shortened sweep it is
where the ledger's memory really ends. ``retention``'s docstring already
makes the operator who shortens the window answerable for what relies on it,
and replay is one more such thing.

**"Not claimed" is not "lost" -- so Redis is asked too.** An event a worker
has simply not reached yet is unclaimed as well, and a replay run during a
backlog (after an AOF restart that lost one second out of an hour's queue)
would send the whole queue twice. The ledger would absorb it, and every
duplicate would still cost its handler's work up to the claim -- for a
document, reading and parsing the file again. So before anything is called
lost, each unclaimed event is looked for on its stream: if a copy is still
there and every group that needs it has yet to finish it (the entry is past
the group's delivery cursor, or in its pending list), the group will be
handed it without help. One more place is checked: ``<stream>.dlq``. An
event a worker gave up on is quarantined, not lost, and requeueing it is
``app.ops.dlq``'s decision -- a replay that re-sent it would only bypass
that decision.

**Six verdicts per row; one is published** (``classify``, in this order):

* ``no_reader``     -- no durable group handles the type: the notify-only
  events (``document.indexed`` and friends) and all of ``stream.files``.
  Losing one costs a toast, not state
  (``framework/streaming/notifications.py``), and a replayed row makes its
  worker emit a fresh one anyway.
* ``processed``     -- every durable group that handles it has claimed it.
* ``in_stream``     -- still on its stream and still owed to every group that
  needs it.
* ``dead_lettered`` -- in the DLQ: ``python -m app.ops.dlq`` decides.
* ``beyond_ledger`` -- would be replayed, but was created before the horizon.
* ``replay``        -- none of the above: Redis no longer holds it for anyone.

A handler that finds nothing to do returns without claiming -- a summary with
no thread to deliver to, a document already terminal, a job cancelled before
it ran. Once its entry has been acked such a row counts as ``replay`` on every
run, and it is a no-op when it arrives, stopped by the same guard that
stopped it the first time.

**Groups first, then the rows.** A flushed Redis loses its consumer groups
with its entries, and ``ensure_group`` creates a missing group at ``$``, the
tail. A group created after the replay's ``XADD`` would start past every
replayed entry and never see one. So ``run`` provisions each durable group of
the target streams BEFORE it selects anything, and selects up to the
database's ``now()`` after that. The same rule is why "still on the stream"
means "past the cursor or pending" and not merely "present": the relay goes on
publishing after a flush, and a worker that restarts recreates its group at
``$`` -- past everything published in between. Those entries are present, and
no one will ever be handed them.

**Never past the backstop.** ``run`` publishes through the relay's own
publisher, with the same ``MAXLEN ~``. A replay larger than the room left
under the backstop would trim its own first entries before anyone read them,
so ``run`` refuses when ``XLEN`` plus a stream's replay count would exceed
``STREAM_MAXLEN``. Narrow the range, or trim first (``app.ops.stream_trim``).

**Two verbs, the ``app.ops.stream_trim`` shape:**

* ``plan`` -- reads only (``SELECT``\\ s; ``XINFO``/``XPENDING``/``XRANGE``/
  ``XLEN``): the counts per stream, verdict and type, the horizon, the oldest
  claim. ``--json`` prints the same as one document, for an acceptance record.
* ``run`` -- provisions the groups, selects again, and publishes the
  ``replay`` rows in their original order (``created_at, id``, the relay's
  order). Gated on ``--yes``. Running it twice publishes nothing the second
  time: what the first run sent is on the stream and owed, so ``in_stream``.

**Selection.** ``--since``/``--until`` bound ``published_at`` -- when the
event went into Redis, which is how a Redis incident is dated -- as ISO-8601
with an offset, or as a duration back from now (``90m``, ``2h``, ``3d``).
``--until`` defaults to now. ``--workspace`` narrows to one tenant and may
stand alone, in which case the range starts at the horizon. ``--stream``
narrows to named streams. One of ``--since``/``--workspace`` is required:
"everything the ledger still covers" is not what an omitted flag should mean.

**It runs as ``outbox_relay``**, the role that publishes, with the one read
it needs added (``app.ops.provision.OUTBOX_RELAY_GRANTS``). The relay's own
container already carries that DSN and the streams' Redis::

    docker compose exec outbox-relay python -m app.ops.replay plan --since 2h
    docker compose exec outbox-relay python -m app.ops.replay run --since 2h --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.errors import AppError
from app.framework.events.topology import (
    PUBLISHED_STREAMS,
    STATIC_CONSUMER_TOPOLOGY,
    ConsumerBinding,
)
from app.framework.settings.settings import DatabaseSettings
from app.framework.types import Json
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.redis_streams import (
    RedisStreamsConsumer,
    RedisStreamsPublisher,
)
from app.infrastructure.messaging.stream_retention import (
    format_stream_id,
    parse_stream_id,
    read_stream_snapshot,
)
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import outbox
from app.infrastructure.persistence.processed_events import processed_events
from app.ops.retention import PROCESSED_EVENTS_RETENTION

_logger = logging.getLogger(__name__)

# Ids per `IN (...)`: far below asyncpg's 32,767-parameter ceiling, and small
# enough that a payload page never holds much of a large replay in memory.
# Also the `XRANGE` page when a stream is searched for what it still holds.
_CHUNK = 1_000

# 04 §2: the one field a published (and dead-lettered) envelope lives under.
_CE = b"ce"

# One `XRANGE` reply under this codebase's fixed `decode_responses=False`
# client -- the `app.ops.dlq` precedent, restated.
_RangeResponse = Sequence[tuple[bytes, dict[bytes, bytes]]]

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class Verdict(StrEnum):
    """What ``classify`` decides for one published row. Order is the order
    ``plan`` prints them in; the module docstring lists them in the order they
    are decided."""

    REPLAY = "replay"
    IN_STREAM = "in_stream"
    DEAD_LETTERED = "dead_lettered"
    PROCESSED = "processed"
    NO_READER = "no_reader"
    BEYOND_LEDGER = "beyond_ledger"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One published outbox row in the range, and what the two stores say
    about it: the durable groups that have claimed it (Postgres), the groups
    its stream still owes a copy to, and whether a copy sits in the DLQ
    (Redis). No payload: ``plan`` never needs one, and ``run`` reads them a
    page at a time."""

    event_id: str
    stream: str
    event_type: str
    created_at: datetime
    claimed_by: frozenset[str] = frozenset()
    owed_by: frozenset[str] = frozenset()
    dead_lettered: bool = False


@dataclass(frozen=True, slots=True)
class Selection:
    """The operator's range, before it is resolved against the database's
    clock. A ``timedelta`` means "this long before now"; ``None`` takes the
    default (``since``: the ledger's horizon, ``until``: now)."""

    since: datetime | timedelta | None = None
    until: datetime | timedelta | None = None
    workspace_id: str | None = None
    streams: tuple[str, ...] = ()


def readers_by_type(bindings: Iterable[ConsumerBinding]) -> dict[tuple[str, str], frozenset[str]]:
    """``(stream, event_type) -> the durable groups that handle it``. A pair
    that is absent has no durable reader at all."""
    readers: dict[tuple[str, str], set[str]] = {}
    for binding in bindings:
        for event_type in binding.event_types:
            readers.setdefault((binding.stream, event_type), set()).add(binding.group)
    return {key: frozenset(groups) for key, groups in readers.items()}


def classify(
    candidate: Candidate,
    *,
    readers: Mapping[tuple[str, str], frozenset[str]],
    horizon: datetime,
) -> Verdict:
    """The module docstring's six verdicts, in the order that keeps each one
    informative: a row nobody reads is ``no_reader`` and a row every reader
    claimed is ``processed``, however old. Only the groups that have NOT
    claimed it matter after that -- a row is ``in_stream`` when its stream
    still owes a copy to each of them -- and only a row that would otherwise
    be replayed is held against the horizon."""
    groups = readers.get((candidate.stream, candidate.event_type), frozenset())
    if not groups:
        return Verdict.NO_READER
    missing = groups - candidate.claimed_by
    if not missing:
        return Verdict.PROCESSED
    if missing <= candidate.owed_by:
        return Verdict.IN_STREAM
    if candidate.dead_lettered:
        return Verdict.DEAD_LETTERED
    if candidate.created_at < horizon:
        return Verdict.BEYOND_LEDGER
    return Verdict.REPLAY


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    """Everything ``plan`` prints and ``run`` acts on -- one record, so the
    two cannot disagree. ``rows`` keep the relay's order (``created_at, id``).
    ``stream_lengths`` hold ``XLEN`` for each stream with rows to replay."""

    since: datetime
    until: datetime
    workspace_id: str | None
    streams: tuple[str, ...]
    retention: timedelta
    horizon: datetime
    oldest_claim: datetime | None
    rows: tuple[tuple[Candidate, Verdict], ...]
    backstop: int | None
    stream_lengths: Mapping[str, int] = field(default_factory=dict)
    # Durable groups a row needed that did not exist when Redis was read, as
    # `(stream, group)` -- after a flush, every one of them. `run` recreates
    # them before it publishes; `plan` only says so.
    missing_groups: tuple[tuple[str, str], ...] = ()

    def to_replay(self) -> list[Candidate]:
        return [row for row, verdict in self.rows if verdict is Verdict.REPLAY]

    def counts(self) -> dict[str, dict[Verdict, Counter[str]]]:
        """``stream -> verdict -> Counter(event_type)``, streams in the order
        they first appear."""
        out: dict[str, dict[Verdict, Counter[str]]] = {}
        for row, verdict in self.rows:
            by_verdict = out.setdefault(row.stream, {v: Counter() for v in Verdict})
            by_verdict[verdict][row.event_type] += 1
        return out

    def totals(self) -> dict[Verdict, int]:
        totals = dict.fromkeys(Verdict, 0)
        for _, verdict in self.rows:
            totals[verdict] += 1
        return totals

    def replay_count(self, stream: str) -> int:
        return sum(1 for row in self.to_replay() if row.stream == stream)

    def over_backstop(self) -> list[str]:
        """Streams where the replay would push ``XLEN`` past ``MAXLEN`` and
        so trim its own first entries (module docstring)."""
        if self.backstop is None:
            return []
        replaying = sorted({row.stream for row in self.to_replay()})
        return [
            stream
            for stream in replaying
            if self.stream_lengths.get(stream, 0) + self.replay_count(stream) > self.backstop
        ]

    def unvouched(self) -> int:
        """Rows to replay that were created before the ledger's oldest claim
        -- the ones only a shortened sweep could make unsafe (module
        docstring's ⚠️). With an empty ledger that is every one of them."""
        replay = self.to_replay()
        if self.oldest_claim is None:
            return len(replay)
        oldest = self.oldest_claim
        return sum(1 for row in replay if row.created_at < oldest)

    def to_json(self) -> Json:
        counts = self.counts()
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "workspace_id": self.workspace_id,
            "streams": list(self.streams),
            "ledger_retention_s": self.retention.total_seconds(),
            "ledger_horizon": self.horizon.isoformat(),
            "ledger_oldest_claim": self.oldest_claim.isoformat() if self.oldest_claim else None,
            "unvouched": self.unvouched(),
            "backstop": self.backstop,
            "totals": {verdict.value: n for verdict, n in self.totals().items()},
            "by_stream": {
                stream: {
                    "stream_length": self.stream_lengths.get(stream),
                    **{
                        verdict.value: dict(counter.most_common())
                        for verdict, counter in by_verdict.items()
                    },
                }
                for stream, by_verdict in counts.items()
            },
            "over_backstop": self.over_backstop(),
            "missing_groups": [f"{stream} {group}" for stream, group in self.missing_groups],
        }


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """What ``run`` did. ``failure`` is the error that stopped it part-way,
    ``None`` on a clean finish; ``vanished`` counts rows that were gone by the
    time their payload was read (swept between selection and publish)."""

    published: Mapping[str, int]
    vanished: int = 0
    failure: str | None = None

    @property
    def total(self) -> int:
        return sum(self.published.values())


# --------------------------------------------------------------------------- #
# Reading                                                                     #
# --------------------------------------------------------------------------- #
def _resolve(value: datetime | timedelta | None, *, now: datetime, default: datetime) -> datetime:
    if value is None:
        return default
    if isinstance(value, timedelta):
        return now - value
    return value


def _chunks[T](items: Sequence[T], size: int = _CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _select_candidates(
    conn: AsyncConnection,
    *,
    since: datetime,
    until: datetime,
    workspace_id: str | None,
    streams: Sequence[str],
) -> list[Candidate]:
    """Published rows with ``since <= published_at < until`` in the relay's
    order. ``ix_outbox_published`` (``0003_retention_sweep.py``) serves the
    range."""
    stmt = (
        select(outbox.c.id, outbox.c.stream, outbox.c.event_type, outbox.c.created_at)
        .where(
            outbox.c.published_at.is_not(None),
            outbox.c.published_at >= since,
            outbox.c.published_at < until,
        )
        .order_by(outbox.c.created_at, outbox.c.id)
    )
    if workspace_id is not None:
        stmt = stmt.where(outbox.c.workspace_id == workspace_id)
    if streams:
        stmt = stmt.where(outbox.c.stream.in_(streams))
    result = await conn.execute(stmt)
    return [
        Candidate(
            event_id=row.id,
            stream=row.stream,
            event_type=row.event_type,
            created_at=row.created_at,
        )
        for row in result
    ]


async def _attach_claims(
    conn: AsyncConnection,
    candidates: Sequence[Candidate],
    readers: Mapping[tuple[str, str], frozenset[str]],
) -> list[Candidate]:
    """Fill ``claimed_by`` -- asked per GROUP, never per event id alone:
    the ledger's key is ``(consumer_group, event_id)``, so a lookup that names
    both walks the primary key, while one by ``event_id`` alone would scan."""
    wanted: dict[str, list[str]] = {}
    for candidate in candidates:
        for group in readers.get((candidate.stream, candidate.event_type), frozenset()):
            wanted.setdefault(group, []).append(candidate.event_id)

    claimed: dict[str, set[str]] = {}
    for group, event_ids in wanted.items():
        for chunk in _chunks(event_ids):
            result = await conn.execute(
                select(processed_events.c.event_id).where(
                    processed_events.c.consumer_group == group,
                    processed_events.c.event_id.in_(chunk),
                )
            )
            for (event_id,) in result:
                claimed.setdefault(event_id, set()).add(group)

    return [replace(c, claimed_by=frozenset(claimed.get(c.event_id, ()))) for c in candidates]


def _event_id(raw: bytes | None) -> str | None:
    """The envelope ``id`` in a ``ce`` field; ``None`` for bytes that are not
    an envelope. A malformed entry is the engine's to dead-letter, not this
    tool's to interpret."""
    if raw is None:
        return None
    try:
        envelope = json.loads(raw)
    except ValueError:
        return None
    event_id = envelope.get("id") if isinstance(envelope, dict) else None
    return event_id if isinstance(event_id, str) else None


async def _scan(client: Redis, key: str) -> AsyncIterator[tuple[tuple[int, int], str]]:
    """Every entry on ``key`` as ``(entry id, event id)``, a page at a time.
    The start of each page is exclusive (``(<id>``), so no entry is read
    twice. A key that does not exist is simply empty."""
    start = "-"
    while True:
        rows = cast("_RangeResponse", await client.xrange(key, min=start, max="+", count=_CHUNK))
        for raw_id, fields in rows:
            entry_id = parse_stream_id(raw_id)
            event_id = _event_id(fields.get(_CE))
            if entry_id is not None and event_id is not None:
                yield entry_id, event_id
        last = parse_stream_id(rows[-1][0]) if rows else None
        if len(rows) < _CHUNK or last is None:
            return
        start = f"({format_stream_id(last)}"


@dataclass(frozen=True, slots=True)
class _GroupState:
    """Where one group stands on its stream: its delivery cursor and its
    pending entries -- together, what it will still be handed."""

    cursor: tuple[int, int]
    pending: frozenset[tuple[int, int]]

    def owes(self, entry_id: tuple[int, int]) -> bool:
        """Past the cursor: not yet delivered. Pending: delivered and not yet
        finished, so recovered by the worker's own recovery read. Anything
        else was acked -- or lies behind a cursor a recreated group started
        at, which is the same thing to the group: it will never see it."""
        return entry_id > self.cursor or entry_id in self.pending


async def _group_states(
    client: Redis, stream: str, groups: Iterable[str]
) -> dict[str, _GroupState]:
    """The state of each of ``groups`` that exists on ``stream``. A missing
    stream, or a missing group, is absent from the result."""
    snapshot = await read_stream_snapshot(client, stream)
    if snapshot is None:
        return {}
    wanted = set(groups)
    states: dict[str, _GroupState] = {}
    for group in snapshot.groups:
        if group.name not in wanted:
            continue
        pending: set[tuple[int, int]] = set()
        if group.pending:
            rows = await client.xpending_range(
                stream, group.name, min="-", max="+", count=group.pending
            )
            for row in rows:
                entry_id = parse_stream_id(row["message_id"])
                if entry_id is not None:
                    pending.add(entry_id)
        states[group.name] = _GroupState(cursor=group.last_delivered_id, pending=frozenset(pending))
    return states


async def _look_in_redis(
    client: Redis,
    candidates: Sequence[Candidate],
    readers: Mapping[tuple[str, str], frozenset[str]],
) -> tuple[list[Candidate], tuple[tuple[str, str], ...]]:
    """Fill ``owed_by`` and ``dead_lettered`` for every row some reader has
    not claimed -- the only rows whose fate Redis can change -- and name the
    durable groups those rows need that do not exist. Reads only: ``XINFO``,
    ``XPENDING`` and ``XRANGE`` over each stream and its ``.dlq``."""
    wanted: dict[str, set[str]] = {}
    needed_groups: dict[str, set[str]] = {}
    for candidate in candidates:
        groups = readers.get((candidate.stream, candidate.event_type), frozenset())
        if groups - candidate.claimed_by:
            wanted.setdefault(candidate.stream, set()).add(candidate.event_id)
            needed_groups.setdefault(candidate.stream, set()).update(groups)

    owed: dict[str, set[str]] = {}
    dead: set[str] = set()
    missing: list[tuple[str, str]] = []
    for stream in sorted(wanted):
        states = await _group_states(client, stream, needed_groups[stream])
        missing.extend((stream, g) for g in sorted(needed_groups[stream] - set(states)))
        if states:
            async for entry_id, event_id in _scan(client, stream):
                if event_id not in wanted[stream]:
                    continue
                for group, state in states.items():
                    if state.owes(entry_id):
                        owed.setdefault(event_id, set()).add(group)
        async for _, event_id in _scan(client, f"{stream}.dlq"):
            if event_id in wanted[stream]:
                dead.add(event_id)

    looked = [
        replace(
            c,
            owed_by=frozenset(owed.get(c.event_id, ())),
            dead_lettered=c.event_id in dead,
        )
        for c in candidates
    ]
    return looked, tuple(missing)


async def build_plan(
    engine: AsyncEngine,
    client: Redis,
    selection: Selection,
    *,
    bindings: Sequence[ConsumerBinding] = STATIC_CONSUMER_TOPOLOGY,
    retention: timedelta = PROCESSED_EVENTS_RETENTION,
    backstop: int | None = None,
) -> ReplayPlan:
    """Select, classify and measure -- reads only. One transaction, so the
    range, the horizon and the claims are all read against one ``now()``."""
    readers = readers_by_type(bindings)
    async with engine.begin() as conn:
        now: datetime = (await conn.execute(select(func.now()))).scalar_one()
        horizon = now - retention
        since = _resolve(selection.since, now=now, default=horizon)
        until = _resolve(selection.until, now=now, default=now)
        candidates = await _select_candidates(
            conn,
            since=since,
            until=until,
            workspace_id=selection.workspace_id,
            streams=selection.streams,
        )
        candidates = await _attach_claims(conn, candidates, readers)
        oldest_claim: datetime | None = (
            await conn.execute(select(func.min(processed_events.c.processed_at)))
        ).scalar_one()

    # After the transaction, never inside it: Redis round trips have no
    # business holding a Postgres connection open.
    candidates, missing_groups = await _look_in_redis(client, candidates, readers)
    rows = tuple(
        (candidate, classify(candidate, readers=readers, horizon=horizon))
        for candidate in candidates
    )
    replaying = sorted({c.stream for c, verdict in rows if verdict is Verdict.REPLAY})
    lengths = {stream: int(await client.xlen(stream)) for stream in replaying}
    return ReplayPlan(
        since=since,
        until=until,
        workspace_id=selection.workspace_id,
        streams=selection.streams,
        retention=retention,
        horizon=horizon,
        oldest_claim=oldest_claim,
        rows=rows,
        backstop=backstop,
        stream_lengths=lengths,
        missing_groups=missing_groups,
    )


# --------------------------------------------------------------------------- #
# Publishing                                                                  #
# --------------------------------------------------------------------------- #
async def ensure_groups(
    client: Redis, bindings: Sequence[ConsumerBinding], streams: Sequence[str]
) -> list[ConsumerBinding]:
    """Provision every durable group of the target streams (all of them when
    ``streams`` is empty) -- the module docstring's "groups first". Returns
    the bindings it touched. ``ensure_group`` is a no-op for a group that
    exists, so a healthy Redis pays one ``BUSYGROUP`` reply per group."""
    consumer = RedisStreamsConsumer(client)
    targets = [b for b in bindings if not streams or b.stream in streams]
    for binding in targets:
        await consumer.ensure_group(binding.stream, binding.group)
    return targets


async def _payloads(engine: AsyncEngine, event_ids: Sequence[str]) -> dict[str, Json]:
    async with engine.begin() as conn:
        result = await conn.execute(
            select(outbox.c.id, outbox.c.payload).where(outbox.c.id.in_(event_ids))
        )
        return {row.id: row.payload for row in result}


async def publish_plan(
    engine: AsyncEngine, publisher: RedisStreamsPublisher, plan: ReplayPlan
) -> ReplayOutcome:
    """``XADD`` every ``replay`` row, in order, with its stored envelope
    verbatim -- the relay's own publisher and the relay's own bytes, so a
    consumer cannot tell a replayed entry from the original. Stops at the
    first publish failure (the relay's head-of-line rule) and says how far it
    got; the rest is picked up by running it again."""
    published: Counter[str] = Counter()
    vanished = 0
    for chunk in _chunks(plan.to_replay()):
        payloads = await _payloads(engine, [row.event_id for row in chunk])
        for row in chunk:
            payload = payloads.get(row.event_id)
            if payload is None:
                vanished += 1
                continue
            try:
                await publisher.publish(row.stream, payload)
            except AppError as exc:
                return ReplayOutcome(published=dict(published), vanished=vanished, failure=str(exc))
            published[row.stream] += 1
    return ReplayOutcome(published=dict(published), vanished=vanished)


class ReplayRefused(Exception):
    """``run`` would have trimmed its own entries (module docstring's
    backstop paragraph). Carries the plan so the caller can print it."""

    def __init__(self, plan: ReplayPlan) -> None:
        super().__init__(f"replay would pass the MAXLEN backstop on {plan.over_backstop()}")
        self.plan = plan


async def run_replay(
    engine: AsyncEngine,
    client: Redis,
    selection: Selection,
    *,
    bindings: Sequence[ConsumerBinding] = STATIC_CONSUMER_TOPOLOGY,
    retention: timedelta = PROCESSED_EVENTS_RETENTION,
    backstop: int | None = None,
) -> tuple[ReplayPlan, ReplayOutcome]:
    """Groups, then the plan, then the rows -- in that order, for the reason
    the module docstring gives. Raises ``ReplayRefused`` before publishing
    anything if the backstop would bite."""
    await ensure_groups(client, bindings, selection.streams)
    plan = await build_plan(
        engine, client, selection, bindings=bindings, retention=retention, backstop=backstop
    )
    if plan.over_backstop():
        raise ReplayRefused(plan)
    publisher = RedisStreamsPublisher(client, maxlen=backstop)
    outcome = await publish_plan(engine, publisher, plan)
    _logger.info(
        "ops.replay.published",
        extra={
            "published": dict(outcome.published),
            "vanished": outcome.vanished,
            "failed": outcome.failure is not None,
        },
    )
    return plan, outcome


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def parse_instant(value: str) -> datetime | timedelta:
    """``90m``/``2h``/``3d`` back from now, or ISO-8601 WITH an offset. A
    bare local time is refused: on a machine three hours off UTC it is a
    three-hour mistake that looks correct."""
    match = _DURATION.match(value)
    if match:
        return timedelta(**{_UNITS[match[2]]: int(match[1])})
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is neither a duration (90m, 2h, 3d) nor an ISO-8601 time"
        ) from exc
    if instant.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} has no UTC offset -- write 2026-09-30T10:00Z or 2026-09-30T13:00+03:00"
        )
    return instant


def _workspace(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a workspace id") from exc


def _days(span: timedelta) -> str:
    return f"{span.total_seconds() / 86_400:g} days"


def _iso(instant: datetime | None) -> str:
    return instant.isoformat(timespec="seconds") if instant is not None else "none"


def render_plan(plan: ReplayPlan) -> list[str]:
    """The text form of ``plan``. ``--json`` prints ``to_json`` instead."""
    lines = [
        f"published {_iso(plan.since)} .. {_iso(plan.until)}"
        f"   workspace {plan.workspace_id or 'any'}"
        f"   streams {', '.join(plan.streams) or 'all'}",
        f"ledger: claims are kept {_days(plan.retention)}, so nothing created before "
        f"{_iso(plan.horizon)} is replayed; oldest claim held {_iso(plan.oldest_claim)}",
    ]
    counts = plan.counts()
    if not counts:
        lines.append("no published rows in this range")
    for stream, by_verdict in counts.items():
        total = sum(sum(c.values()) for c in by_verdict.values())
        lines.append(f"{stream}  {total:,} published in range")
        for verdict, counter in by_verdict.items():
            if not counter:
                continue
            detail = ", ".join(f"{t} x{n:,}" for t, n in counter.most_common())
            lines.append(f"  {verdict.value:<14}{sum(counter.values()):>7,}  {detail}")
        for missing_stream, group in plan.missing_groups:
            if missing_stream == stream:
                lines.append(
                    f"  group {group} does not exist -- run recreates it before publishing"
                )
        to_replay = plan.replay_count(stream)
        if to_replay and plan.backstop is not None:
            length = plan.stream_lengths.get(stream, 0)
            verdict_text = "over -- run refuses" if stream in plan.over_backstop() else "fits"
            lines.append(
                f"  backstop: XLEN {length:,} + {to_replay:,} to replay of "
                f"{plan.backstop:,} -- {verdict_text}"
            )
    totals = plan.totals()
    lines.append(
        "total: " + " | ".join(f"{verdict.value} {totals[verdict]:,}" for verdict in Verdict)
    )
    unvouched = plan.unvouched()
    if unvouched:
        lines.append(
            f"warning: {unvouched:,} row(s) to replay were created before the ledger's oldest "
            "claim. Safe unless processed_events was swept with a window shorter than "
            f"{_days(plan.retention)} -- in that case those events may apply twice."
        )
    return lines


def _render_outcome(outcome: ReplayOutcome) -> list[str]:
    per_stream = ", ".join(f"{s} {n:,}" for s, n in sorted(outcome.published.items()))
    lines = [f"replayed {outcome.total:,}" + (f" ({per_stream})" if per_stream else "")]
    if outcome.vanished:
        lines.append(f"{outcome.vanished:,} row(s) were gone before they could be read")
    if outcome.failure is not None:
        lines.append(
            f"stopped by a publish failure: {outcome.failure}. Running it again is safe -- "
            "what already reached the stream counts as in_stream and is not sent again."
        )
    return lines


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    selection = Selection(
        since=args.since,
        until=args.until,
        workspace_id=args.workspace,
        streams=tuple(args.stream or ()),
    )
    backstop = settings.events.stream_maxlen
    engine = create_engine(DatabaseSettings(url=settings.database.url), poolclass=NullPool)
    client = create_redis_client(settings.redis)
    try:
        if args.action == "plan":
            plan = await build_plan(engine, client, selection, backstop=backstop)
            if args.json:
                print(json.dumps(plan.to_json(), ensure_ascii=False, indent=2))
            else:
                print("\n".join(render_plan(plan)))
            return 0

        # "run" -- `main` has already refused to reach here without --yes.
        try:
            plan, outcome = await run_replay(engine, client, selection, backstop=backstop)
        except ReplayRefused as refused:
            print("\n".join(render_plan(refused.plan)))
            print(
                "refused: the replay would pass the MAXLEN backstop and trim its own entries "
                "before anyone read them. Narrow the range, or trim first "
                "(python -m app.ops.stream_trim).",
                file=sys.stderr,
            )
            return 1
        print("\n".join(render_plan(plan)))
        print("\n".join(_render_outcome(outcome)))
        return 0 if outcome.failure is None else 1
    finally:
        await client.aclose()
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.replay",
        description="Replay published outbox events onto their streams (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("plan", "what a replay of this range would publish, and why -- reads only"),
        ("run", "provision the groups, then publish the rows no durable group has claimed"),
    ):
        verb = sub.add_parser(name, help=help_text)
        verb.add_argument(
            "--since",
            type=parse_instant,
            help="published at or after: ISO-8601 with an offset, or 90m / 2h / 3d back from now",
        )
        verb.add_argument(
            "--until", type=parse_instant, help="published before (default: now), same forms"
        )
        verb.add_argument("--workspace", type=_workspace, help="only this workspace's events")
        verb.add_argument(
            "--stream",
            action="append",
            choices=PUBLISHED_STREAMS,
            help="only this stream (repeatable; default: every stream)",
        )
        if name == "plan":
            verb.add_argument("--json", action="store_true", help="one JSON document")
        else:
            verb.add_argument(
                "--yes",
                action="store_true",
                help="required explicit confirmation -- the workers act on what is published",
            )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.since is None and args.workspace is None:
        raise SystemExit(
            f"{args.action} refused: name a range (--since) or a workspace (--workspace). "
            "Replaying everything the ledger still covers is not a default."
        )
    if args.action == "run" and not args.yes:
        raise SystemExit(
            "run refused: pass --yes to confirm -- the workers will act on every event it "
            "publishes. Run `plan` with the same arguments first."
        )
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
