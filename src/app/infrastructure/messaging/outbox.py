"""The Outbox Relay loop (5.1-ب · 01-data-model §4.1 · 04-event-catalog
§2/§3/§3.1 · D-18/19/20/26 · 10-code-standards §7).

The single-instance (D-26) process that turns durably-written ``platform.
outbox`` rows into published Redis Stream entries: fetch unpublished rows →
``XADD`` each in order → stamp ``published_at`` on whichever ones actually
made it. Nothing else in the platform is allowed to publish (10 §7: "لا نشر
مباشر إلى Redis من الوحدات — اكتب Outbox؛ المُرحّل ينشر") — this is that
"المُرحّل" in code.

**Head-of-line blocking is a deliberate, documented choice, not an
oversight.** ``run_once`` stops at the FIRST publish failure in a batch
rather than skipping past it: a Redis outage affects every entry equally, so
skipping ahead would just re-fail on the next one and the one after that,
burning attempts for no benefit; and a genuinely "poison" ROW — one that
fails to publish no matter how many times it is retried — cannot exist in
this pipeline, because every payload was already built and JSON-schema
validated at PRODUCE time (``framework/events/envelope.build_envelope``,
called from each producing module's ``event_mapping.py``) before it was ever
written to ``platform.outbox``. The only failures ``publish`` can raise are
therefore transport-level (Redis unreachable, timed out, OOM, ...), and those
apply to the whole batch, not to one unlucky row — so stopping and retrying
the WHOLE remaining suffix next cycle is strictly simpler than skip-and-
retry-individually, with no correctness cost.

**Why the store methods (fetch/mark/bump) are three separate short
transactions instead of one held across the whole cycle:** ``publish`` is an
external I/O call (a Redis round trip) sitting between the read and the
write half of each cycle. R2 (5.1-أ's hand-off to this sub-step) already
rules out a DB transaction spanning external I/O — the same reason applies
here a fortiori, since holding a transaction across N sequential Redis calls
(N = batch size) would pin a DB connection idle for the whole batch's network
time. ``SqlOutboxRelayStore``'s own docstring carries the matching half of
this note.

**``run_once``'s non-AppError decision (explicitly left open by the 5.1-ب
design brief — decided and documented here):** ``EventPublisher.publish``'s
contract is "raises a translated ``AppError``, nothing else" (``redis_streams
.py``'s module docstring). If an adapter ever violated that contract — a
genuine bug, not an operational failure — this loop does NOT reclassify it as
an ordinary publish failure (bump + break + keep returning normally): that
would silently launder a programming error into "Redis must be flaky today",
exactly the "لا ابتلاع صامت" (no silent swallowing) rule 10 §5 exists to
prevent. Instead: the failing entry's attempt is still recorded (bump —
without this, a deterministic bug would wedge the relay in an identical
crash loop forever, since ``attempts`` would never advance toward a future
DLQ threshold, 5.2), the successful prefix so far is still marked published
(via the ``finally`` below — those rows really did reach Redis, and failing
to record that would just cause harmless-but-wasteful re-publication next
cycle, not data loss), and then the ORIGINAL exception is re-raised
uncaught — "let it propagate after bump", the first of the two alternatives
the design brief offered. ``run_forever`` deliberately does not catch it
either (only ``AppError``), so an unexpected exception type crashes the
whole worker process loudly rather than being backed off and retried forever
by a mechanism built for transient outages, not for bugs.
"""

from __future__ import annotations

import asyncio

from app.framework.errors import AppError
from app.framework.observability import Heartbeat, NullHeartbeat, get_logger
from app.framework.ports.event_publisher import EventPublisher
from app.framework.types import Uuid
from app.infrastructure.persistence.outbox import SqlOutboxRelayStore

_logger = get_logger(__name__)


class OutboxRelay:
    """Fetch → publish → mark-published, once (``run_once``) or forever
    (``run_forever``) — the single ``outbox_relay`` process (D-26)."""

    def __init__(
        self,
        store: SqlOutboxRelayStore,
        publisher: EventPublisher,
        *,
        batch_size: int,
        poll_interval_ms: int,
        max_backoff_ms: int,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._batch_size = batch_size
        self._poll_interval_ms = poll_interval_ms
        self._max_backoff_ms = max_backoff_ms
        # ت-3 (`docs/operational-findings.md` §3) -- the same optional-with-a-
        # no-op-default shape `StreamConsumer` carries, for the same reason.
        self._heartbeat: Heartbeat = NullHeartbeat() if heartbeat is None else heartbeat

    async def run_once(self) -> int:
        """One fetch/publish/mark-published cycle; returns the count
        actually published.

        On a clean cycle (every fetched row published, or nothing was
        fetched at all) this returns normally. On a publish failure it
        raises the (bumped, logged) failure AFTER marking whatever DID
        publish before it — see the module docstring for why a plain
        ``-> int`` return still needs an exception here at all: it is the
        only signal ``run_forever`` has to tell "an error interrupted this
        cycle" apart from "there was simply nothing left to do", and that
        distinction is exactly what decides normal polling versus backoff.
        """
        entries = await self._store.fetch_unpublished(self._batch_size)
        if not entries:
            return 0

        succeeded: list[Uuid] = []
        failure: AppError | None = None
        try:
            for entry in entries:
                try:
                    await self._publisher.publish(entry.stream, entry.payload)
                except Exception as exc:
                    # Never the payload -- it may carry user content (10 §10).
                    await self._store.bump_attempts([entry.event_id])
                    _logger.warning(
                        "outbox_relay.publish_failed",
                        extra={
                            "event_id": entry.event_id,
                            "stream": entry.stream,
                            "attempts_bumped": True,
                        },
                    )
                    if isinstance(exc, AppError):
                        # The expected shape (redis_streams.py's contract):
                        # head-of-line -- stop, let the `finally` below mark
                        # whatever succeeded, and re-raise so `run_forever`
                        # can tell this cycle apart from an idle one.
                        failure = exc
                        break
                    else:
                        # An unexpected exception TYPE is a bug, not an
                        # outage -- see the module docstring's "non-AppError
                        # decision". `else` (not a trailing unconditional
                        # `raise`) is deliberate: it keeps this branch
                        # mutually exclusive with the `break` above, so a
                        # mutant that drops JUST the `break` is observable
                        # (falls through to `succeeded.append` below,
                        # wrongly marking a FAILED entry as published)
                        # instead of accidentally self-healing through this
                        # `raise`.
                        raise
                succeeded.append(entry.event_id)
        finally:
            # ALWAYS the prefix that actually reached Redis -- never the
            # whole fetched batch, and unconditionally (clean finish, a
            # caught AppError break, or an unexpected exception unwinding
            # through this block all reach this line exactly once).
            await self._store.mark_published(succeeded)
            if succeeded:
                _logger.info("outbox_relay.batch_published", extra={"count": len(succeeded)})

        if failure is not None:
            raise failure
        return len(succeeded)

    async def run_forever(self) -> None:
        """Poll ``run_once`` until cancelled (``asyncio.CancelledError``
        propagates uncaught -- it is a ``BaseException``, not caught by the
        ``except AppError`` below regardless, but stated explicitly since a
        graceful shutdown depends on it not being swallowed here).

        Backoff starts at ``poll_interval_ms`` and doubles per consecutive
        failed cycle, capped at ``max_backoff_ms``; ANY clean cycle
        (published or not) resets it back to ``poll_interval_ms`` before
        deciding whether to sleep at all -- a failure right after a healthy
        stretch must not inherit an elevated backoff from a past, unrelated
        outage. A FULL batch (``published == batch_size``) skips the sleep
        entirely: more rows may still be waiting, so the next poll happens
        immediately to drain the backlog instead of idling for
        ``poll_interval_ms`` for no reason.

        **The heartbeat is on the CLEAN-cycle path only (ت-3), deliberately.**
        A relay that cannot reach Postgres or Redis stays ``Up`` and keeps
        looping here forever, doubling its backoff and logging
        ``cycle_failed`` into a stream nobody watches -- the precise shape of
        "running but accomplishing nothing" that ت-3 says nothing observes
        today. Beating inside the ``except`` branch would report that state as
        healthy; not beating there lets the stamp go stale and turns a
        wedged relay into an unhealthy container.
        """
        backoff_ms = self._poll_interval_ms
        while True:
            try:
                published = await self.run_once()
            except AppError:
                # Both origins land here: a store failure (`fetch_unpublished`
                # /`mark_published`/`bump_attempts` -- the relay must survive
                # a PG restart) and a publish `AppError` `run_once` already
                # bumped/logged and re-raised. Either way this IS the "the
                # relay is unhealthy right now" signal backoff exists for.
                _logger.error(
                    "outbox_relay.cycle_failed", extra={"backoff_ms": backoff_ms}, exc_info=True
                )
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms = min(backoff_ms * 2, self._max_backoff_ms)
                continue

            backoff_ms = self._poll_interval_ms  # any clean cycle clears the penalty
            self._heartbeat.beat()
            if published == self._batch_size:
                continue  # more may be waiting -- drain without sleeping
            await asyncio.sleep(self._poll_interval_ms / 1000)
