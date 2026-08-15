"""Generic Redis Streams consumer engine (5.1-ج · 04-event-catalog §2/§3 ·
10-code-standards §7/§10 · D4/D5/R7).

**Module-agnostic by construction.** This file imports framework code only
-- NEVER ``app.modules.*`` -- so it stays reusable across every worker
(``knowledge_worker``/``media_worker``/``memory_worker``) without knowing any
module's shapes. Handlers arrive as opaque closures (``EventHandler``),
built by each worker's OWN per-process composition root
(``workers/bootstrap.py``, which — unlike this module — is exactly where
``app.modules.*`` imports belong). This mirrors ``RedisStreamsConsumer``
(the adapter this engine drives): thin, technology-facing, no business
knowledge.

**Ownership split with ``RedisStreamsConsumer``:** that adapter returns raw
``ce`` bytes and never decodes them (its own docstring: "keep this adapter
THIN"). THIS module owns JSON decoding, envelope-shape validation, and the
malformed/unroutable/no-handler/handler-failure policy below -- the adapter
must not swallow a decode failure before this engine ever sees it.

**Per-message policy (D4 final as of 5.2-ب — the DLQ exists now):**
1. ``ce`` missing or its bytes fail to decode as a JSON object -> log
   ``ERROR "malformed_envelope"`` (entry id + stream ONLY -- 10 §10, never
   payload bytes, which may carry user content) and **dead-letter
   IMMEDIATELY** (``RedisStreamsConsumer.dead_letter``: move to
   ``<stream>.dlq`` + XACK). No retry budget for permanently-broken bytes:
   attempt N would decode exactly what attempt 1 did, so 04 §3's ``N=5``
   (which governs «فشل عابر», transient failures) does not apply. 5.1-ج's
   interim policy XACK-dropped these with a loud log because the DLQ did
   not exist yet -- the poison pill now has a destination instead of
   vanishing.
2. ``type``, ``workspaceid``, or ``id`` missing/empty -> log ``ERROR
   "unroutable_envelope"`` and **dead-letter immediately** -- the same
   permanent-poison reasoning: nothing in this codebase's own producer path
   can ever build such an envelope (``framework/events/envelope.
   build_envelope`` requires all three), so this is corruption in transit,
   not a message this system chose to route. ``id`` JOINING this guard is
   a 5.2-ب bug fix (recorded in 5.2-أ, docs/log/3.47.md): the
   ``correlationid`` fallback below reads ``envelope["id"]``, so a
   type+workspaceid envelope WITHOUT an id previously escaped ``_dispatch``
   as a raw ``KeyError``, killed the worker loop, and -- being still
   pending -- was redelivered to the restarted worker: a crash loop on one
   poisoned payload.
3. No handler registered for ``type`` on the subscription owning this
   message's stream -> **XACK-skip**, no log at ERROR level (this is the
   NORMAL, expected case for e.g. ``knowledge.document.indexed.v1``/
   ``knowledge.document.indexing_failed.v1`` landing on ``cg.knowledge``:
   they belong to ``cg.notify``/5.3, not this consumer group's handler
   map -- 04 §2's own topology table names ``cg.notify`` as the OWNER of
   ``stream.knowledge`` even though ``cg.knowledge`` is ALSO a group on
   that same stream, docs/log/3.45.md's recorded catalog-sync gap). Not
   dead-lettered: an unclaimed type is a routing non-event, not poison.
4. Handler dispatch: build a job-scoped ``ExecutionContext`` from the
   envelope (``workspace_id``/``correlation_id`` -- decision R7: workers
   carry ``roles=frozenset()`` always, since RBAC is an API-boundary
   concern and tenant isolation here is ``workspace_id`` + RLS, not roles)
   and ``await handler(ctx, envelope)``.
   - success -> ``XACK``. (A redelivered-already-processed message ALSO
     lands here: the handler's own ``processed_events`` claim, 5.2-أ,
     returns cleanly on a duplicate -- this engine stays blind to
     Postgres.)
   - raises and ``delivery_count < max_deliveries`` -> log ``ERROR
     "handler_failed"`` (``exc_info=True``, never the envelope) and **NO
     ack** -- the entry stays pending and is redelivered
     (``RedisStreamsConsumer.read``'s own "recovery pass" docstring), its
     delivery counter climbing each attempt (verified semantics,
     ``StreamMessage.delivery_count``).
   - raises and ``delivery_count >= max_deliveries`` (04 §3 / NFR-04:
     ``N=5``, wired from ``EventSettings.max_retries_before_dlq``) ->
     ``handler_failed`` as above, THEN **dead-letter** with a reason naming
     the exception -- «بعد N=5 ⇒ يُنقل إلى stream.<m>.dlq مع سبب» verbatim.

Structured logging throughout (10 §10): every log line below carries the
Streams entry id and stream name, NEVER the decoded envelope or its
``data`` (which may carry workspace/user content) -- the ``OutboxRelay``
precedent ("Never the payload -- it may carry user content").
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import cast

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import Heartbeat, NullHeartbeat, get_logger
from app.framework.types import Json
from app.infrastructure.messaging.consumers.dlq_watch import report_dlq_backlog
from app.infrastructure.messaging.consumers.sweeper import (
    deregister_consumer,
    sweep_stale_consumers,
)
from app.infrastructure.messaging.redis_streams import (
    DlqBacklog,
    RedisStreamsConsumer,
    StreamMessage,
)

_logger = get_logger(__name__)

# (ctx, full CloudEvents envelope) -> None; raising means "no XACK, redeliver"
# (module docstring, policy 4). One handler per (subscription, event `type`).
EventHandler = Callable[[ExecutionContext, Json], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Subscription:
    """One consumer group's interest in one stream: which ``type``\\ s it
    knows how to handle, and with what. A single consumer GROUP may own
    several ``Subscription``\\ s across DIFFERENT streams (the knowledge
    worker's ``cg.knowledge`` on both ``stream.files`` and
    ``stream.knowledge``, 04 §4) -- ``StreamConsumer.run_once`` groups them
    back together so each group is read with exactly one ``XREADGROUP``
    call covering every one of its streams, never one call per stream.
    """

    stream: str
    group: str
    handlers: Mapping[str, EventHandler]


class StreamConsumer:
    """Drives ``RedisStreamsConsumer`` against a set of ``Subscription``\\ s
    -- the generic half of every worker's read loop (``knowledge_worker``/
    ``media_worker``/``memory_worker`` each supply their OWN subscriptions
    and handler closures; this class knows nothing about any of them)."""

    def __init__(
        self,
        consumer: RedisStreamsConsumer,
        *,
        consumer_name: str,
        block_ms: int,
        batch_count: int,
        max_deliveries: int,
        heartbeat: Heartbeat | None = None,
        sweep_interval_s: float = 0.0,
        stale_idle_ms: int = 0,
        dlq_watch_interval_s: float = 0.0,
    ) -> None:
        self._consumer = consumer
        self._consumer_name = consumer_name
        self._block_ms = block_ms
        self._batch_count = batch_count
        # ت-3 (`docs/operational-findings.md` §3). Optional with a no-op
        # default because this engine is generic infrastructure that also runs
        # under `pytest` and under a bare `python -m app.workers.memory_worker`
        # -- neither of which has a Docker healthcheck reading the file. The
        # `worker-*` Compose services always pass a real one
        # (`workers/bootstrap.py`).
        self._heartbeat: Heartbeat = NullHeartbeat() if heartbeat is None else heartbeat
        # 04 §3 / NFR-04's N (default 5) -- wired from
        # `EventSettings.max_retries_before_dlq` by every worker bootstrap.
        # Deliberately a REQUIRED parameter with no default here: the engine
        # is generic infrastructure, and the number's one authoritative home
        # is Settings (07 §4's limits table), not a second hardcoded copy.
        self._max_deliveries = max_deliveries
        # ت-2 (`docs/operational-findings.md` §2). BOTH default to "off", and
        # that is the honest default for a generic engine: the notify bridge
        # inside the API process builds a `StreamConsumer` too, and its
        # `cg.notify.*` family is swept at the GROUP level by a different rule
        # (`consumers/sweeper.py`'s second half) -- a consumer-level sweep
        # there could delete a live bridge's registration and make its own
        # group look orphaned to that other sweeper. Only the three `worker-*`
        # bootstraps pass real values.
        self._sweep_interval_s = sweep_interval_s
        self._stale_idle_ms = stale_idle_ms
        self._next_sweep_at = 0.0
        # ت-6 (`docs/operational-findings.md` §6). Off by default for the same
        # reason the sweep above is: this engine also drives the API's notify
        # bridge, and `cg.notify.*` has no DLQ of its own -- the streams it
        # reads are the WORKERS' streams, so a bridge that watched them would
        # report another process's backlog twice over (once per API worker
        # process, `WEB_CONCURRENCY`) while the worker that actually owns the
        # queue reports it once. Only the three `worker-*` bootstraps pass a
        # real value, which makes each DLQ the business of exactly the one
        # process that dead-letters into it.
        self._dlq_watch_interval_s = dlq_watch_interval_s
        self._next_dlq_watch_at = 0.0

    async def setup(self, subscriptions: Sequence[Subscription]) -> None:
        """``ensure_group`` for every ``(stream, group)`` pair named by
        ``subscriptions`` -- idempotent (``RedisStreamsConsumer.ensure_group``
        swallows ``BUSYGROUP``), so calling this once at ``run`` startup, or
        repeatedly across restarts, is always safe."""
        for subscription in subscriptions:
            await self._consumer.ensure_group(subscription.stream, subscription.group)

    async def teardown(self, subscriptions: Sequence[Subscription]) -> None:
        """``destroy_group`` for every DISTINCT ``(stream, group)`` pair named
        by ``subscriptions`` -- ``setup``'s counterpart (docs/log/3.81.md),
        meant for a CLEAN exit (a caller's own shutdown hook), never for
        crash recovery: an unclean kill (SIGKILL) never reaches this method
        at all, which is exactly why an orphan-sweep policy exists one layer
        up (``CompositionRoot.sweep_stale_notify_groups``) rather than being
        this engine's problem. Deduplicated because a single consumer group
        may own several subscriptions across different streams (the class
        docstring's own example) -- destroying the same ``(stream, group)``
        twice would be harmless (``destroy_group`` is itself idempotent) but
        wasteful. Idempotent the same way ``setup`` is: destroying an
        already-gone group, or a group that was never created, is a silent
        no-op all the way down to the adapter.
        """
        seen: set[tuple[str, str]] = set()
        for subscription in subscriptions:
            key = (subscription.stream, subscription.group)
            if key in seen:
                continue
            seen.add(key)
            await self._consumer.destroy_group(subscription.stream, subscription.group)

    async def run_once(self, subscriptions: Sequence[Subscription]) -> int:
        """One read-and-dispatch pass over every group named by
        ``subscriptions``; returns the count of messages that were
        successfully handled AND acked (module docstring policy 4's
        success path only -- a skip or a dead-letter transfer is bookkept
        in the logs and the DLQ, not in this count).

        Subscriptions are grouped by ``group`` first (module docstring's
        ``Subscription`` note): each DISTINCT group issues exactly ONE
        ``RedisStreamsConsumer.read`` call covering every stream that group
        subscribes to, so the knowledge worker's ``cg.knowledge`` (two
        streams) is one blocking read, never two sequential ones.

        **Heartbeat placement (ت-3).** Beats land after every completed
        ``read`` and after every dispatched message, and NOT once per
        ``run_once``: a returned ``XREADGROUP`` is the strongest liveness
        evidence this loop can produce cheaply (it proves a full Redis
        round-trip, not merely that Python is executing), and the per-message
        beat is what keeps a long, legitimate BATCH from reading like a wedged
        loop. What remains uncovered on purpose is a single handler that hangs
        forever -- which is exactly the failure the staleness threshold
        (``HealthSettings.heartbeat_max_age_s``) is sized to catch.
        """
        by_group: dict[str, dict[str, Subscription]] = {}
        for subscription in subscriptions:
            by_group.setdefault(subscription.group, {})[subscription.stream] = subscription

        handled = 0
        for group, by_stream in by_group.items():
            messages = await self._consumer.read(
                streams=list(by_stream),
                group=group,
                consumer=self._consumer_name,
                count=self._batch_count,
                block_ms=self._block_ms,
            )
            self._heartbeat.beat()
            for message in messages:
                if await self._dispatch(group, by_stream, message):
                    handled += 1
                self._heartbeat.beat()
        return handled

    async def run(self, subscriptions: Sequence[Subscription]) -> None:
        """``setup`` once, then loop ``run_once`` forever. No sleep between
        iterations: the blocking ``XREADGROUP`` inside ``read`` (``block_ms``)
        is the loop's own pacing, exactly as a blocking read should be.
        ``asyncio.CancelledError`` is a ``BaseException`` and is never caught
        here, so a graceful shutdown (SIGTERM under Compose, 08 §2) always
        propagates out of this loop rather than being swallowed.

        **The sweep rides this same loop (ت-2)** rather than living in a task
        of its own: it needs exactly what the loop already has (this
        process's consumer name and its subscriptions), it must never run
        concurrently with itself, and pacing it off ``run_once`` means it
        cannot fire while a handler is mid-flight. The cost of that choice is
        that a totally idle worker sweeps on the first tick after
        ``block_ms`` elapses past the deadline -- seconds of imprecision on a
        15-minute cadence, which is not worth a second task to remove.

        **The DLQ watch (ت-6) rides it too, with one difference: it fires on
        the FIRST pass, not after a full interval.** Its deadline starts at
        "now" rather than "now + interval" because a backlog is durable state
        that was already there before this process booted -- it is exactly
        what an operator restarting a worker wants told immediately, and the
        read costs two non-blocking Redis calls against a stream that is
        almost always empty. A stale-consumer sweep has the opposite shape (it
        acts on tombstones this run may itself be about to create, and it
        DELETES), so it keeps waiting out its first full interval.
        """
        await self.setup(subscriptions)
        self._next_sweep_at = monotonic() + self._sweep_interval_s
        self._next_dlq_watch_at = monotonic()
        while True:
            await self.run_once(subscriptions)
            if self._sweep_interval_s > 0 and monotonic() >= self._next_sweep_at:
                self._next_sweep_at = monotonic() + self._sweep_interval_s
                await self.sweep_stale(subscriptions)
            if self._dlq_watch_interval_s > 0 and monotonic() >= self._next_dlq_watch_at:
                self._next_dlq_watch_at = monotonic() + self._dlq_watch_interval_s
                await self.watch_dlq(subscriptions)

    async def sweep_stale(self, subscriptions: Sequence[Subscription]) -> list[str]:
        """Reclaim-then-delete the ghost consumers other processes left in
        THIS worker's groups (``consumers/sweeper.py`` owns the rule and the
        safety argument); returns the names deleted.

        Disabled unless both knobs were wired (the constructor's note).
        Never raises: a Redis failure while tidying bookkeeping must not cost
        the loop its next ``XREADGROUP``, so everything is caught here and
        logged. That is also why this is not folded into ``run_once`` -- a
        caller (and a test) can invoke the sweep on its own and see it fail
        loudly through the log rather than through the message path.
        """
        if self._sweep_interval_s <= 0 or self._stale_idle_ms <= 0:
            return []
        try:
            return await sweep_stale_consumers(
                self._consumer,
                pairs=_pairs(subscriptions),
                live_consumer=self._consumer_name,
                min_idle_ms=self._stale_idle_ms,
            )
        except Exception:
            _logger.error("consumer_sweep_failed", exc_info=True)
            return []

    async def watch_dlq(self, subscriptions: Sequence[Subscription]) -> list[DlqBacklog]:
        """Report what is parked on the DLQs of the streams THIS worker
        consumes (``consumers/dlq_watch.py`` owns the rule and the reason a
        log line is the right sink); returns the non-empty backlogs found.

        Disabled unless ``dlq_watch_interval_s`` was wired (the constructor's
        note). Never raises, for the same reason ``sweep_stale`` does not: a
        Redis hiccup while REPORTING a backlog must not cost the loop its next
        ``XREADGROUP``. Reporting is strictly less important than consuming,
        and the next tick tries again.
        """
        if self._dlq_watch_interval_s <= 0:
            return []
        try:
            return await report_dlq_backlog(
                self._consumer, streams=[s.stream for s in subscriptions]
            )
        except Exception:
            _logger.error("dlq_watch_failed", exc_info=True)
            return []

    async def deregister(self, subscriptions: Sequence[Subscription]) -> None:
        """Remove THIS process's own consumer registration from every group
        it read -- a clean exit's counterpart to ``setup`` (ت-2), and
        deliberately NOT ``teardown``: a worker's groups are the static
        topology's own (``cg.knowledge``/``cg.media``/``cg.memory``), which
        must outlive every process. Destroying one would reset a module's
        delivery position to the stream tail; removing a consumer entry from
        it removes only this process's tombstone.

        Skips a group where this process still owns pending entries
        (``sweeper.deregister_consumer``'s own rule -- those messages must be
        reclaimed by a live consumer, not dropped at shutdown). Never raises,
        for the same reason ``sweep_stale`` does not: this runs on the exit
        path, where an exception would mask whatever is actually shutting the
        process down.
        """
        try:
            await deregister_consumer(
                self._consumer, pairs=_pairs(subscriptions), name=self._consumer_name
            )
        except Exception:
            _logger.error("consumer_deregister_failed", exc_info=True)

    async def _dispatch(
        self, group: str, by_stream: Mapping[str, Subscription], message: StreamMessage
    ) -> bool:
        """Apply the module docstring's per-message policy to ONE delivered
        message; returns whether it was handled successfully (for
        ``run_once``'s return count)."""
        envelope = _decode(message)
        if envelope is None:
            _logger.error(
                "malformed_envelope", extra={"entry_id": message.entry_id, "stream": message.stream}
            )
            # Permanent poison, dead-lettered immediately (policy 1) -- no
            # retry budget can fix bytes that will decode the same way on
            # attempt N as on attempt 1.
            await self._dead_letter(group, message, reason="malformed_envelope")
            return False

        event_type = envelope.get("type")
        workspace_id = envelope.get("workspaceid")
        event_id = envelope.get("id")
        if not event_type or not workspace_id or not event_id:
            # `id` joining this guard is 5.2-ب's bug fix (module docstring
            # policy 2): it feeds the correlation fallback AND every
            # handler's DD-09 claim, and reading it unguarded previously
            # crash-looped the whole worker on one poisoned payload.
            _logger.error(
                "unroutable_envelope",
                extra={"entry_id": message.entry_id, "stream": message.stream},
            )
            await self._dead_letter(group, message, reason="unroutable_envelope")
            return False

        subscription = by_stream.get(message.stream)
        handler = subscription.handlers.get(event_type) if subscription is not None else None
        if handler is None:
            # Normal, expected case (policy 3) -- no ERROR log, just ack-away
            # a type this group's own subscriptions never claimed to handle.
            await self._consumer.ack(message.stream, group, message.entry_id)
            return False

        # `or` (not `.get`'s default) so an explicitly-empty `correlationid`
        # extension also falls back; `event_id` was guard-checked truthy
        # above, and a real envelope's `id` is always a `str`
        # (`build_envelope`'s own `event_id: Uuid` parameter).
        correlation_id = cast("str", envelope.get("correlationid") or event_id)
        ctx = ExecutionContext(
            workspace_id=workspace_id,
            user_id=None,
            correlation_id=correlation_id,
            roles=frozenset(),
            request_id=None,
        )
        try:
            # Idempotency (5.2-أ, DD-09) lives INSIDE the handler, not here:
            # each closure built by `workers/bootstrap.py` claims
            # (consumer_group, event_id) in `platform.processed_events` as
            # the first statement of its own effect transaction, and a
            # duplicate claim returns cleanly -- so to this engine a
            # redelivered-already-processed message is indistinguishable
            # from a success and is XACKed below. The engine stays blind to
            # Postgres by construction (module docstring).
            await handler(ctx, envelope)
        except Exception as exc:
            _logger.error(
                "handler_failed",
                extra={
                    "entry_id": message.entry_id,
                    "stream": message.stream,
                    "type": event_type,
                    "delivery_count": message.delivery_count,
                },
                exc_info=True,
            )
            if message.delivery_count >= self._max_deliveries:
                # 04 §3's «بعد N=5 ⇒ يُنقل إلى stream.<m>.dlq مع سبب»: this
                # was attempt N -- transfer instead of another redelivery.
                # The reason names the exception the way `handler_failed`'s
                # own exc_info already does (same exposure surface, 10 §10),
                # truncated so a pathological message cannot bloat the DLQ.
                reason = f"handler_failed: {type(exc).__name__}: {exc}"[:500]
                await self._dead_letter(group, message, reason=reason)
                return False
            return False  # NO ack -- policy 4's redelivery path.

        await self._consumer.ack(message.stream, group, message.entry_id)
        return True

    async def _dead_letter(self, group: str, message: StreamMessage, *, reason: str) -> None:
        """Transfer + XACK through the adapter (which owns the wire shape --
        DLQ stream naming and field layout), then log the transfer itself:
        an entry leaving the normal pipeline for the quarantine stream must
        never happen silently, whatever the reason."""
        await self._consumer.dead_letter(
            stream=message.stream,
            group=group,
            entry_id=message.entry_id,
            raw=message.raw,
            reason=reason,
            delivery_count=message.delivery_count,
        )
        _logger.error(
            "dead_lettered",
            extra={
                "entry_id": message.entry_id,
                "stream": message.stream,
                "dlq": f"{message.stream}.dlq",
                "reason": reason,
                "delivery_count": message.delivery_count,
            },
        )


def _pairs(subscriptions: Sequence[Subscription]) -> list[tuple[str, str]]:
    """The DISTINCT ``(stream, group)`` pairs a subscription set covers, in
    first-seen order -- ``teardown``'s own deduplication (a single group may
    own several subscriptions across different streams), reused by both ت-2
    sweeps so they cannot disagree with it."""
    return list(dict.fromkeys((s.stream, s.group) for s in subscriptions))


def _decode(message: StreamMessage) -> Json | None:
    """``ce`` missing, invalid JSON, or JSON that does not decode to an
    object are all "malformed" alike (module docstring policy 1) -- the
    third case matters because ``json.loads`` happily accepts any valid
    JSON document (``42``, ``"a string"``, ``[1, 2]``), not only objects,
    and this engine's envelope handling below assumes a ``dict``."""
    if message.raw is None:
        return None
    try:
        decoded = json.loads(message.raw)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded
