"""Redis Streams adapter for the ``EventPublisher`` port (02-port-contracts
§1.8 · D-18/20) AND the consumer-side ``XREADGROUP``/``XACK`` adapter
(5.1-ج · 04-event-catalog §2/§3).

**Publisher (5.1-ب, unchanged below) + Consumer (5.1-ج, added below).**
``RedisStreamsPublisher``'s SOLE caller is the ``outbox_relay``
(``infrastructure/messaging/outbox.py``) -- business modules never publish
directly, they write ``platform.outbox`` and the relay forwards it (02 §1.8:
"الوحدات لا تنشر مباشرة بل تكتب Outbox"). ``RedisStreamsConsumer``'s callers
are the generic engine (``infrastructure/messaging/consumers/engine.py``),
wired per worker process by ``workers/bootstrap.py`` -- never a business
module directly, mirroring the publisher's own indirection. Both adapters
share this one file because they are two faces of the same wire contract (04
§1/§2): the ``ce`` field name, the JSON encoding, and the error-translation
discipline below are common to both.

Mirrors the ``redis_cache.py`` split: the Composition Root / worker bootstrap
builds the raw ``redis.asyncio.Redis`` client via ``infrastructure/cache/
redis_cache.py::create_redis_client`` (reused, not duplicated -- Redis has
exactly one client factory in this codebase, keyed off the same
``RedisSettings``), and this module wraps it with a thin adapter class
(structural Protocol match against ``EventPublisher`` -- no inheritance, the
``RedisCache`` precedent).

**Wire format (04 §2):** ``XADD stream.<module> * ce <json>`` -- one field,
named ``ce`` (CloudEvents), holding the *complete* envelope as a JSON string.
``event`` is published **verbatim**: the envelope was already built and
validated at produce time (``framework/events/envelope.build_envelope``, run
through ``to_outbox_record`` in each producing module), so this adapter does
not re-validate or mutate it -- doing so here would duplicate a check the
producer already made and risk silently changing what a consumer receives
versus what was durably stored in ``platform.outbox.payload``.

Serialization uses compact separators (``json.dumps(..., separators=(",",
":"))``) -- there is no reader-facing reason to spend wire bytes on the
default ``", "``/``": "`` padding once an envelope is Streams-bound, unlike
``JsonFormatter`` (``framework/observability/logging.py``), which stays
default-spaced because *its* JSON is meant for a human tailing logs.
``ensure_ascii=False`` matches every other JSON-producing call site in this
codebase (``JsonFormatter``, the ``knowledge`` table-to-text parsers) --
non-ASCII user content (e.g. Arabic prompts) round-trips as UTF-8 rather than
``\\uXXXX``-escaped.

Error policy -- translate, never fail open, no retry (the ``RedisCache``
precedent, restated for Streams): every ``redis`` failure raised by ``xadd``
is mapped onto the shared framework hierarchy (``common.internal``) so no
``redis``-package exception type ever escapes this adapter. This adapter
NEVER retries internally -- ``OutboxRelay.run_once`` is the sole retry/backoff
policy owner (its own docstring), so a publisher that quietly retried here
would hide failed attempts from the relay's ``attempts`` bookkeeping and its
head-of-line semantics.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from redis import RedisError, ResponseError
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from app.framework.errors import AppError
from app.framework.ports.event_publisher import StreamEvent
from app.framework.types import Json

# 04 §2: the sole field name every consumer reads a published envelope from.
_CE_FIELD = b"ce"  # XADD stream.<module> * ce <json>


def _encode(event: Json) -> str:
    """Compact, non-ASCII-preserving JSON encoding for the ``ce`` field --
    see the module docstring's "Serialization" paragraph for why these
    particular ``json.dumps`` kwargs (and not ``JsonFormatter``'s)."""
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


class RedisStreamsPublisher:
    """``EventPublisher`` (02 §1.8) over ``redis.asyncio.Redis``."""

    def __init__(self, client: Redis, *, maxlen: int | None = None) -> None:
        self._client = client
        self._maxlen = maxlen

    async def publish(self, stream: str, event: Json) -> str:
        """``XADD stream MAXLEN ~ <n> * ce <json>``; returns the new entry id.

        The client is always built with ``decode_responses=False``
        (``create_redis_client``'s fixed contract), so ``xadd`` always hands
        back ``bytes`` here -- the same invariant ``RedisCache.get`` already
        relies on, and the same ``cast`` used there rather than a runtime
        ``isinstance`` branch for a case that cannot actually vary.

        **Trimming (7.3).** Without ``MAXLEN`` a stream grows forever, and not
        only while 7.2 leaves the consumers unable to boot: Redis Streams are
        an append-only log, and ``XACK`` clears a group's pending list without
        deleting anything. A fully healthy platform therefore accumulates
        every event it has ever published, on a Redis measured live at
        ``maxmemory 0`` / ``noeviction`` -- no ceiling anywhere in the stack.

        ``approximate=True`` (Redis's ``~``) trims on radix-node boundaries
        instead of walking to an exact length. Exact trimming makes every
        single ``XADD`` -- the hot path for every event this platform emits --
        pay an O(n) deletion, to hold a retention bound whose precise value
        nobody has an opinion about. "About 100k" and "exactly 100k" are the
        same operational statement at very different costs.

        ``maxlen=None`` omits the argument entirely rather than passing
        ``None`` through: identical wire bytes to the pre-7.3 ``XADD``.
        """
        payload = _encode(event)
        try:
            if self._maxlen is None:
                raw_id = await self._client.xadd(stream, {_CE_FIELD: payload})
            else:
                raw_id = await self._client.xadd(
                    stream, {_CE_FIELD: payload}, maxlen=self._maxlen, approximate=True
                )
        except RedisError as exc:
            raise _translate(exc) from exc
        return cast(bytes, raw_id).decode()

    async def publish_batch(self, items: Sequence[StreamEvent]) -> list[str]:
        """Sequential ``publish`` calls, in order -- deliberately NOT a
        Redis pipeline.

        A pipelined ``XADD`` batch fails or succeeds as one wire round trip,
        so a single bad entry could not be told apart from the rest -- there
        would be no way to know which entries actually landed on the stream.
        Sequential publishing keeps every entry independently attributable:
        each ``XADD`` either lands on the stream and returns its own entry
        id, or raises on its own, before the next one is even attempted.
        This is exactly the per-entry attribution ``OutboxRelay.run_once``'s
        own head-of-line handling depends on when it calls ``publish``
        directly in its loop; this method exists for ``EventPublisher``
        callers that want the whole-batch convenience instead.
        """
        return [await self.publish(item.stream, item.event) for item in items]


def _translate(exc: RedisError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- the ``RedisCache._translate`` precedent: every
    ``redis``-package failure (connection refused, timeout, OOM, ...) is an
    infrastructure fault the caller cannot meaningfully branch on, so it
    folds into the one 500-class ``common.internal``."""
    return AppError("event publish failed", code="common.internal")


# --------------------------------------------------------------------------- #
# Consumer half (5.1-ج)                                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StreamMessage:
    """One delivered Streams entry, as thin as ``RedisStreamsConsumer.read``
    can make it: the raw ``ce`` field bytes, un-decoded. ``raw`` is ``None``
    when the entry carries no ``ce`` field at all (a poison/malformed
    ``XADD`` from outside this codebase's own producer path) -- the engine
    (``infrastructure/messaging/consumers/engine.py``), never this adapter,
    decides what "malformed" means and how to react (module docstring:
    "keep this adapter THIN").

    ``delivery_count`` (5.2-ب) is Redis's own per-PEL-entry delivery
    counter, INCLUDING the delivery this message represents: ``1`` for a
    fresh ``>`` delivery (by definition -- Redis initializes the counter to
    1 on first delivery), the ``XPENDING``-reported value for a recovered
    entry. Verified empirically against live Redis (docs/log/3.48.md): a
    ``0`` recovery re-read increments the counter exactly like a ``>``
    delivery or an ``XCLAIM``, so the counter genuinely counts processing
    ATTEMPTS -- which is what the engine's N=5 DLQ threshold (04 §3) needs
    it to mean."""

    stream: str
    entry_id: str
    raw: bytes | None
    delivery_count: int = 1


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """One row of ``XINFO GROUPS``, thinned to what the ت-2 sweepers read:
    the group's name plus the two counters that decide whether anything is
    still using it (``consumers``) and whether destroying it would drop
    bookkeeping (``pending``)."""

    name: str
    consumers: int
    pending: int


@dataclass(frozen=True, slots=True)
class ConsumerInfo:
    """One row of ``XINFO CONSUMERS``: a consumer's name, how many delivered-
    but-unacked entries it still owns, and how long (ms) since it last
    interacted with the group.

    ``idle_ms`` is the ONLY liveness evidence available about a consumer from
    outside its own process, and it is evidence in one direction only: a
    small value proves something is reading right now (a blocking
    ``XREADGROUP`` resets it every ``consumer_block_ms``), while a large one
    means "nothing has read under this name for a while" -- which is a dead
    container's tombstone in every case measured so far, but is not by itself
    proof of death. The sweeper's threshold is sized against ``block_ms``
    accordingly (``consumers/sweeper.py``)."""

    name: str
    pending: int
    idle_ms: int


def _parse_autoclaim(reply: object) -> tuple[str, list[str]]:
    """``XAUTOCLAIM``'s reply, normalised. Redis 6.2 answers with two
    elements (next cursor, claimed) and Redis 7+ with three (a trailing list
    of ids that no longer exist and were dropped from the PEL); redis-py
    passes both shapes through as-is. Only the first two matter here, and
    reading them positionally keeps this adapter working across both server
    versions rather than pinning one.
    """
    parts = cast("Sequence[object]", reply or [])
    if not parts:
        return "0-0", []
    cursor_raw = parts[0]
    cursor = cursor_raw.decode() if isinstance(cursor_raw, bytes) else str(cursor_raw)
    ids: list[str] = []
    if len(parts) > 1:
        for entry in cast("Sequence[object]", parts[1] or []):
            # `JUSTID` yields bare ids; a non-`JUSTID` reply would yield
            # `(id, fields)` tuples -- tolerated so this helper cannot become
            # the reason a future non-JUSTID caller breaks.
            raw_id = entry[0] if isinstance(entry, tuple | list) else entry
            ids.append(raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id))
    return cursor, ids


class RedisStreamsConsumer:
    """``XREADGROUP``/``XACK`` consumer adapter (5.1-ج · 04-event-catalog
    §2/§3 · D-19/20).

    Thin by construction, the publisher's own precedent restated for the
    read side: this adapter returns raw bytes and entry ids, never decodes
    JSON, and never retries internally -- ``StreamConsumer`` (the engine) is
    the SOLE redelivery-policy owner, exactly as ``OutboxRelay`` is the sole
    retry/backoff owner on the publish side.

    **Deviation from a plain "one ``XREADGROUP '>'``" read -- reality over
    the 5.1-ج design brief's own inline comment, per this codebase's "the
    working code wins, note the deviation" rule.** Verified empirically
    against a live Redis 8.0.1: once an entry has been delivered to a
    consumer group via ``>``, the GROUP's single shared delivery cursor has
    moved past it for every consumer, so a plain ``>`` read issued again by
    the SAME (stable) consumer name never sees that entry again even if it
    was never ``XACK``\\ ed -- there is no such thing as ``>`` "redelivering".
    Redis's own supported way to see an own still-pending entry again is to
    read with an explicit id (``0`` = "my pending entries list from the
    start"); ``BLOCK`` has no effect on a non-``>`` read (verified: it
    returns immediately regardless of the ``block`` argument, since there is
    nothing to wait for), so this costs no extra latency. ``read`` therefore
    issues TWO ``XREADGROUP`` calls per invocation -- a ``0`` "recovery" pass
    (this consumer's own not-yet-``XACK``\\ ed entries, redelivered first)
    THEN a ``>`` "fresh" pass (brand-new entries, respecting ``block_ms``) --
    merging both. Without this, 04-event-catalog §3's core promise ("فشل
    عابر ⇒ لا XACK ⇒ يُعاد التسليم", "a transient failure ⇒ no XACK ⇒
    redelivered") would be FALSE for exactly the failure mode it exists to
    describe: a handler that raises on attempt 1 would simply never be
    retried by attempt 2 from the same long-lived worker process. This is
    still exactly ONE logical ``read()`` call from ``StreamConsumer``'s own
    perspective (unchanged public contract, unchanged call count at the
    engine boundary) -- only this adapter's internal Redis usage differs
    from the design brief's literal comment.
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def ensure_group(self, stream: str, group: str) -> None:
        """``XGROUP CREATE <stream> <group> $ MKSTREAM``.

        ``$`` starts the group at the tail -- only entries ``XADD``\\ ed
        AFTER this call are ever delivered to it (04 §2's topology table is
        silent on backfill, and a worker should not suddenly ingest a
        stream's entire history the first time it boots). ``MKSTREAM``
        creates the stream itself if absent, so a worker may start before
        any producer/relay has ever published to it. ``BUSYGROUP`` (the
        group already exists -- true on every redeploy/restart after the
        first) is swallowed; any OTHER failure translates like every other
        adapter in this codebase.
        """
        try:
            await self._client.xgroup_create(stream, group, id="$", mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc

    async def destroy_group(self, stream: str, group: str) -> None:
        """``XGROUP DESTROY <stream> <group>`` -- ``ensure_group``'s
        counterpart (5.3-هـ / 04-event-catalog §2, docs/log/3.81.md): removes
        a group and its pending-entries list entirely, run by a CLEAN
        shutdown for this process's own group (``consumers/engine.py``'s
        ``StreamConsumer.teardown``) and by the composition root's startup
        sweep for an orphan a SIGKILLed sibling left behind
        (``CompositionRoot.sweep_stale_notify_groups``).

        Two outcomes are silent no-ops, verified empirically against a live
        Redis 8.0.1 rather than assumed from the docs: ``XGROUP DESTROY`` on
        a group name that does not exist (the stream itself DOES) returns
        ``0``, no exception at all -- destroying twice, or destroying a name
        nobody ever created, is exactly as safe as ``ensure_group`` creating
        a group that already exists. The one exception this method itself
        swallows is the stream-missing case (``"...requires the key to
        exist..."`` -- the SAME message ``XGROUP CREATE`` raises without
        ``MKSTREAM``, minus the option that fixes it here): nothing to
        destroy is not a failure. Any OTHER failure translates like every
        other adapter in this codebase.
        """
        try:
            await self._client.xgroup_destroy(stream, group)
        except ResponseError as exc:
            if "requires the key to exist" not in str(exc):
                raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc

    async def list_groups(self, stream: str) -> list[str]:
        """``XGROUP LIST``'s underlying command, ``XINFO GROUPS <stream>``,
        thinned to just the names -- the ONLY thing the composition root's
        orphan sweep needs (docs/log/3.81.md). A stream that does not exist
        yet (nobody has ever ``ensure_group``\\ d against it on this host) has
        no groups by definition, verified live as its own ``ResponseError``
        (``"no such key"``) rather than an empty list -- folded to ``[]``
        here so a caller never needs to special-case "never created" versus
        "created, nothing left".
        """
        try:
            groups = await self._client.xinfo_groups(stream)
        except ResponseError as exc:
            if "no such key" in str(exc):
                return []
            raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc
        return [cast(bytes, group["name"]).decode() for group in groups]

    async def group_infos(self, stream: str) -> list[GroupInfo]:
        """``XINFO GROUPS <stream>`` with the two fields ``list_groups``
        throws away -- ``consumers`` and ``pending`` (ت-2,
        ``docs/operational-findings.md`` §2).

        Kept SEPARATE from ``list_groups`` rather than replacing it: the
        composition root's startup sweep genuinely needs nothing but names,
        and widening its return type would make every caller carry fields it
        has no use for. Same ``"no such key"`` -> ``[]`` folding, for the same
        reason (a stream nobody ever published to has no groups).

        redis-py normalises this reply's KEYS to ``str`` even under this
        codebase's fixed ``decode_responses=False`` client contract, while
        ``name``'s VALUE stays ``bytes`` -- the asymmetry ``list_groups``
        above already depends on, restated here rather than rediscovered.
        """
        try:
            rows = await self._client.xinfo_groups(stream)
        except ResponseError as exc:
            if "no such key" in str(exc):
                return []
            raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc
        return [
            GroupInfo(
                name=cast(bytes, row["name"]).decode(),
                consumers=int(row["consumers"]),
                pending=int(row["pending"]),
            )
            for row in rows
        ]

    async def list_consumers(self, stream: str, group: str) -> list[ConsumerInfo]:
        """``XINFO CONSUMERS <stream> <group>`` -- who is registered under a
        group, how much each still owns, and how long since each last spoke.

        **Why this exists (ت-2).** Redis NEVER removes a consumer entry when
        the process behind it dies; only an explicit ``XGROUP DELCONSUMER``
        does. So every container recreation leaves a permanent tombstone
        inside an otherwise healthy group, and ``consumers``/``idle`` -- the
        measurement this project now relies on to tell a LIVE worker from a
        wedged one (docs/log/3.134.md) -- silt up until they mean nothing.
        This method is the read half of the automatic cleanup
        (``consumers/sweeper.py``); ``delete_consumer`` below is the write
        half, and ``reclaim`` is the safety step that must precede it.

        Both "the stream does not exist" and "the group does not exist"
        (``NOGROUP``) fold to ``[]``: neither is a failure for a caller whose
        question is "who is registered here", and a sweep that runs before a
        worker has ever booted must not raise.
        """
        try:
            rows = await self._client.xinfo_consumers(stream, group)
        except ResponseError as exc:
            text = str(exc)
            if "no such key" in text or text.startswith("NOGROUP"):
                return []
            raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc
        return [
            ConsumerInfo(
                name=cast(bytes, row["name"]).decode(),
                pending=int(row["pending"]),
                idle_ms=int(row["idle"]),
            )
            for row in rows
        ]

    async def delete_consumer(self, stream: str, group: str, consumer: str) -> int:
        """``XGROUP DELCONSUMER`` -- returns the number of pending entries the
        deleted consumer still owned, which Redis DISCARDS along with it.

        That return value is the whole danger of this command and the reason
        it is exposed raw rather than wrapped in a decision: entries dropped
        this way are not redelivered to anyone -- they stay in the stream,
        unacked and unreachable, invisible to every future ``XPENDING``. The
        caller (``consumers/sweeper.py``) therefore ``reclaim``s first and
        only ever calls this on a consumer it has just observed at
        ``pending == 0``; a non-zero return here means that observation lost
        a race and is logged as the anomaly it is.

        A missing stream/group is not an error (nothing to delete): the same
        folding as ``list_consumers`` above, reported as ``0``.
        """
        try:
            return int(await self._client.xgroup_delconsumer(stream, group, consumer))
        except ResponseError as exc:
            text = str(exc)
            if "no such key" in text or text.startswith("NOGROUP"):
                return 0
            raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc

    async def reclaim(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int = 100,
        max_batches: int = 100,
    ) -> list[str]:
        """``XAUTOCLAIM ... JUSTID`` in a cursor loop: transfer every entry in
        ``group``'s pending list that has sat untouched for at least
        ``min_idle_ms`` to ``consumer``. Returns the transferred entry ids.

        **This is a recovery path, not only a cleanup one.** ``read``'s
        recovery pass (its own docstring) re-reads ``0`` for the CALLING
        consumer name -- which is ``<module>.<host>.<pid>`` and therefore
        changes on every restart. Entries a killed worker left pending are
        thus owned by a name no live process will ever read under again:
        without this command they are stuck forever, not merely untidy. The
        sweeper claims them to a live consumer, whose next ``read`` recovery
        pass then picks them up and processes them normally.

        ``JUSTID`` returns ids only -- the payload is never fetched, because
        the claimer's own next ``read`` will fetch it. It also does NOT
        increment the delivery counter (Redis's documented behaviour for
        ``JUSTID``), which is deliberate here: a message must not consume its
        N=5 DLQ budget merely by being moved between consumers.

        The cursor loop is bounded by ``max_batches`` so a pathologically
        large PEL cannot pin a worker's loop indefinitely -- whatever is left
        is simply claimed by the next sweep.
        """
        claimed: list[str] = []
        cursor = "0-0"
        try:
            for _ in range(max_batches):
                reply = await self._client.xautoclaim(
                    stream, group, consumer, min_idle_ms, start_id=cursor, count=count, justid=True
                )
                cursor, ids = _parse_autoclaim(reply)
                claimed.extend(ids)
                if cursor == "0-0" or not ids:
                    break
        except ResponseError as exc:
            text = str(exc)
            if "no such key" in text or text.startswith("NOGROUP"):
                return claimed
            raise _translate_consume(exc) from exc
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc
        return claimed

    async def read(
        self,
        *,
        streams: Sequence[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[StreamMessage]:
        """One logical read covering every stream in ``streams`` for
        ``group`` -- see the class docstring's "Deviation" paragraph for why
        this issues two ``XREADGROUP`` calls (recovery then fresh) rather
        than the literal single call the design brief's comment names, and
        why that is necessary for redelivery to be real rather than
        aspirational.

        Recovered messages carry their real ``XPENDING`` delivery counter
        (5.2-ب): the ``XPENDING RANGE`` runs AFTER the ``0`` recovery read,
        so the counter already includes the delivery this very call just
        performed (the recovery read increments it -- the
        ``StreamMessage.delivery_count`` docstring's verified semantics).
        Fresh ``>`` messages skip the extra round trip entirely: their
        counter is 1 by definition, so the idle-loop common case (recovery
        pass empty) costs exactly what it cost before 5.2-ب.
        """
        try:
            recovered = await self._client.xreadgroup(
                group, consumer, dict.fromkeys(streams, "0"), count=count
            )
            recovered_messages = _to_messages(recovered)
            if recovered_messages:
                counts = await self._delivery_counts(
                    recovered_messages, group=group, consumer=consumer
                )
                recovered_messages = [
                    StreamMessage(
                        stream=message.stream,
                        entry_id=message.entry_id,
                        raw=message.raw,
                        delivery_count=counts.get((message.stream, message.entry_id), 1),
                    )
                    for message in recovered_messages
                ]
            fresh = await self._client.xreadgroup(
                group, consumer, dict.fromkeys(streams, ">"), count=count, block=block_ms
            )
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc
        return recovered_messages + _to_messages(fresh)

    async def _delivery_counts(
        self, messages: Sequence[StreamMessage], *, group: str, consumer: str
    ) -> dict[tuple[str, str], int]:
        """``XPENDING RANGE`` per stream (consumer-filtered -- recovered
        entries are by construction this consumer's own PEL), keyed by
        ``(stream, entry_id)``. Runs inside ``read``'s translation ``try``."""
        by_stream: dict[str, list[str]] = {}
        for message in messages:
            by_stream.setdefault(message.stream, []).append(message.entry_id)
        counts: dict[tuple[str, str], int] = {}
        for stream, entry_ids in by_stream.items():
            rows = await self._client.xpending_range(
                stream,
                group,
                min=entry_ids[0],
                max=entry_ids[-1],
                count=len(entry_ids),
                consumername=consumer,
            )
            for row in rows:
                entry_id = cast(bytes, row["message_id"]).decode()
                counts[(stream, entry_id)] = int(cast(int, row["times_delivered"]))
        return counts

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        """``XACK`` -- one of exactly two ways an entry ever leaves a
        consumer's pending-entries list (the other being ``dead_letter``
        below, whose transfer ends in this same ``XACK``)."""
        try:
            await self._client.xack(stream, group, entry_id)
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc

    async def dead_letter(
        self,
        *,
        stream: str,
        group: str,
        entry_id: str,
        raw: bytes | None,
        reason: str,
        delivery_count: int,
    ) -> None:
        """Move one poison/exhausted entry to ``<stream>.dlq`` (04 §3:
        ``stream.<m>.dlq``) and ``XACK`` it out of the source stream -- the
        WIRE half of the DLQ (naming + field layout live here with the
        ``ce`` field-name constant; the DECISION to dead-letter is engine
        policy, exactly the module docstring's ownership split).

        The DLQ entry carries the original ``ce`` bytes VERBATIM when they
        exist (an operator re-injects by copying the field back onto the
        source stream) plus the failure bookkeeping 04 §3.1 asks for («نقل
        الحدث + سبب الفشل»): ``reason``, ``source_stream``,
        ``source_entry_id``, ``consumer_group``, ``deliveries``. A
        ``ce``-less malformed entry dead-letters as bookkeeping-only --
        there are no bytes to preserve.

        ``XADD`` + ``XACK`` run in one MULTI/EXEC (``transaction=True``), so
        a half-transfer (moved but still pending, or acked but never moved)
        cannot be produced by a crash BETWEEN the two commands. A crash
        before the MULTI executes leaves the entry pending -- it is simply
        redelivered, found over-threshold again, and re-transferred: the
        DLQ itself is at-least-once, which its human/ops consumers must
        (and trivially do) tolerate.
        """
        # `redis.typing`'s own aliases -- `xadd`'s field dict is invariant,
        # so a narrower `dict[bytes, bytes]` annotation fails mypy strict.
        fields: dict[FieldT, EncodableT] = {
            b"reason": reason.encode(),
            b"source_stream": stream.encode(),
            b"source_entry_id": entry_id.encode(),
            b"consumer_group": group.encode(),
            b"deliveries": str(delivery_count).encode(),
        }
        if raw is not None:
            fields[_CE_FIELD] = raw
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.xadd(f"{stream}.dlq", fields)
                pipe.xack(stream, group, entry_id)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            raise _translate_consume(exc) from exc


# One `XREADGROUP` response, always `bytes` keys/values under this codebase's
# fixed `decode_responses=False` client contract (the publisher's own
# invariant, restated): `[[stream_name, [(entry_id, {field: value, ...}), ...]], ...]`.
_ReadGroupResponse = Sequence[tuple[bytes, Sequence[tuple[bytes, dict[bytes, bytes]]]]]


def _to_messages(response: object) -> list[StreamMessage]:
    """Flatten one ``XREADGROUP`` response into ``StreamMessage``\\ s.
    ``fields.get(_CE_FIELD)`` is ``None`` when an entry has no ``ce`` field
    at all -- carried through as ``StreamMessage.raw``, never raised here
    (decode/validation of ``ce`` is the ENGINE's job, module docstring's
    "keep this adapter THIN"). A ``None``/empty response (nothing to read,
    the common case under ``BLOCK`` timing out) yields an empty list.
    """
    messages: list[StreamMessage] = []
    for stream_name, entries in cast("_ReadGroupResponse", response or []):
        stream = stream_name.decode()
        for entry_id, fields in entries:
            messages.append(
                StreamMessage(stream=stream, entry_id=entry_id.decode(), raw=fields.get(_CE_FIELD))
            )
    return messages


def _translate_consume(exc: Exception) -> AppError:
    """Map a driver-level failure from the consumer side onto the shared
    framework error hierarchy (03-api-spec §4) -- the same fold-to-
    ``common.internal`` policy as ``_translate`` above, worded for the
    consumer rather than the publisher so a log line names which side
    actually failed."""
    return AppError("event consume failed", code="common.internal")
