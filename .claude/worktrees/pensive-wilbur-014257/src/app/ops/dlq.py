"""DLQ inspect / requeue / purge — the missing operator entrypoint for
``RedisStreamsConsumer.dead_letter`` (P1-4, ``docs/p1-hardening-plan.md`` §3
step 7).

**The gap this closes.** ``dead_letter`` (``infrastructure/messaging/
redis_streams.py``) has moved poison/exhausted entries onto ``<stream>.dlq``
since 5.2-ب, and the engine (``infrastructure/messaging/consumers/engine.py``
``_dead_letter``) has decided WHEN since the same phase (``malformed_envelope``
· ``unroutable_envelope`` · ``handler_failed`` after ``max_retries_before_dlq``
deliveries). But nothing under ``python -m app.ops.*`` ever read that stream
back or copied an entry onto its source -- an operator's only tool was
``redis-cli`` by hand. This module is that missing artifact, one composition
root for a single one-shot operator invocation, exactly the ``provision.py``/
``revoke.py`` precedent: it may import ``app.infrastructure``, it builds the
one client it needs, and it closes it in a ``finally``.

**Three verbs, no more (the design brief's own limit — no HTTP surface, no
periodic scheduling, no dashboard, no metrics; metrics are a LATER step,
docs/p1-hardening-plan.md §1 "الأداة قبل المقياس"):**

* ``peek``    -- a non-consuming read of ``<stream>.dlq`` (plain ``XRANGE``,
  never ``XREADGROUP``/``XACK`` -- looking at the DLQ must never move
  anything).
* ``requeue`` -- copies one DLQ entry's ``ce`` field back onto its
  ``source_stream`` VERBATIM, exactly what ``dead_letter``'s own docstring
  names as the intended recovery path ("an operator re-injects by copying the
  field back onto the source stream").
* ``purge``   -- ``XDEL``s named DLQ entries, gated on an explicit ``--yes``:
  there is no bulk "clear everything" switch, deliberately (a mistyped
  invocation must not be able to erase a triage queue in one shot; an
  operator who genuinely wants to clear it all pipes ``peek``'s ids into
  ``purge --yes``).

**Pitfall 1 -- a ``ce``-less entry cannot be replayed, and this tool says so
instead of guessing.** ``dead_letter``'s own docstring: "A ``ce``-less
malformed entry dead-letters as bookkeeping-only -- there are no bytes to
preserve." That happens for the ``malformed_envelope`` reason specifically
when the original ``XADD`` never carried a ``ce`` field at all (poison from
outside this codebase's own producer path) -- see
``infrastructure/messaging/consumers/engine.py``'s ``_decode``. ``DlqEntry.
reinjectable`` surfaces this distinction to ``peek``'s own output, and
``requeue`` raises ``ValueError`` naming it rather than silently XADDing
nothing or crashing on a missing field.

**Pitfall 2 -- requeue is not blind to ``platform.processed_events``, and
this tool cannot see it either.** DD-09's ledger key is
``(consumer_group, event_id)``, claimed as the FIRST statement inside the
SAME ``uow.begin`` transaction that commits the handler's whole effect
(``workers/bootstrap.py``'s handler closures, every one of them) -- so the
claim and the effect always commit TOGETHER or roll back together. Two cases
follow, and this tool is honest about handling exactly one of them:

* **Failure BEFORE the claim ever durably committed** (the ONLY way a
  message can reach this DLQ via the ``handler_failed`` reason in THIS
  codebase's current handler shapes: every attempt that raised did so inside
  the same transaction as its own claim attempt, so the claim rolled back
  with it -- and ``malformed_envelope``/``unroutable_envelope`` entries never
  even reach a handler, so no claim was ever attempted for them either).
  ``requeue`` handles this case correctly by construction: the copied ``ce``
  is read fresh, the ledger has no row for ``(consumer_group, event_id)``,
  the claim inserts, and the full effect reprocesses exactly once.
* **Failure or re-delivery AFTER the claim already committed** -- e.g. the
  SAME DLQ entry is ``requeue``\\ d twice without an intervening ``purge``
  (an operator mistake this tool does not detect), or the underlying event
  was independently reprocessed through the normal pipeline before this
  ``requeue`` ever runs. ``ledger.claim`` returns ``False``, the handler
  treats it as a clean duplicate no-op (DD-09's «تكرار = تجاهُل»), and the
  engine ``XACK``\\ s it -- a "successful" requeue that actually did nothing.
  **This tool has no Postgres port and cannot distinguish this case from the
  first one**: it is a Streams-only adapter, structurally unable to query
  ``platform.processed_events``, and no such check is invented here. Naming
  this honestly (rather than pretending ``requeue``'s exit code proves
  reprocessing) is the point -- an operator who must be certain a specific
  event re-ran needs to check the domain's own effect (e.g. the aggregate's
  resulting state), not this tool's output.

Usage::

    python -m app.ops.dlq peek <stream> [--count N]
    python -m app.ops.dlq requeue <stream> <dlq-entry-id> [<dlq-entry-id> ...]
    python -m app.ops.dlq purge <stream> <dlq-entry-id> [<dlq-entry-id> ...] --yes

``<stream>`` is the SOURCE stream name (e.g. ``stream.knowledge``), never the
``.dlq`` suffix -- every verb below appends it itself, the same way
``dead_letter`` computes ``f"{stream}.dlq"`` rather than taking it as an
argument, so an operator cannot typo the derived name.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis

from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings

_logger = logging.getLogger(__name__)

# 04 §2/§3: the sole field name a published (and dead-lettered) envelope's
# bytes live under -- the `RedisStreamsConsumer`/`dead_letter` precedent.
_CE_FIELD = b"ce"

_DEFAULT_PEEK_COUNT = 100


@dataclass(frozen=True, slots=True)
class DlqEntry:
    """One dead-lettered entry, decoded from ``<stream>.dlq``'s raw ``XRANGE``
    fields -- the mirror image of ``dead_letter``'s own field layout
    (``reason``/``source_stream``/``source_entry_id``/``consumer_group``/
    ``deliveries`` always present; ``ce`` present unless the original entry
    was ``ce``-less bookkeeping, module docstring's Pitfall 1)."""

    entry_id: str
    reason: str
    source_stream: str
    source_entry_id: str
    consumer_group: str
    deliveries: int
    ce: bytes | None

    @property
    def reinjectable(self) -> bool:
        """``False`` iff this entry dead-lettered with no ``ce`` bytes to
        replay at all (module docstring's Pitfall 1) -- ``requeue`` refuses
        these with a named reason rather than XADDing an empty envelope."""
        return self.ce is not None


# One `XRANGE` response entry, always `bytes` keys/values under this
# codebase's fixed `decode_responses=False` client contract (the
# `RedisStreamsConsumer`/`_ReadGroupResponse` precedent, restated).
_RangeResponse = Sequence[tuple[bytes, dict[bytes, bytes]]]


def _decode_entry(entry_id: bytes, fields: dict[bytes, bytes]) -> DlqEntry:
    return DlqEntry(
        entry_id=entry_id.decode(),
        reason=fields[b"reason"].decode(),
        source_stream=fields[b"source_stream"].decode(),
        source_entry_id=fields[b"source_entry_id"].decode(),
        consumer_group=fields[b"consumer_group"].decode(),
        deliveries=int(fields[b"deliveries"].decode()),
        ce=fields.get(_CE_FIELD),
    )


async def peek(client: Redis, stream: str, *, count: int = _DEFAULT_PEEK_COUNT) -> list[DlqEntry]:
    """Non-consuming read of up to ``count`` OLDEST entries on
    ``<stream>.dlq`` -- plain ``XRANGE -  +``, never ``XREADGROUP``: an
    operator looking at the DLQ must never move anything (no group, no
    ``XACK``, no delivery-count bump)."""
    rows = cast("_RangeResponse", await client.xrange(f"{stream}.dlq", count=count) or [])
    return [_decode_entry(entry_id, fields) for entry_id, fields in rows]


async def _get_entry(client: Redis, stream: str, dlq_entry_id: str) -> DlqEntry:
    rows = cast(
        "_RangeResponse",
        await client.xrange(f"{stream}.dlq", min=dlq_entry_id, max=dlq_entry_id, count=1) or [],
    )
    if not rows:
        raise ValueError(f"no such entry {dlq_entry_id!r} on {stream}.dlq")
    entry_id, fields = rows[0]
    return _decode_entry(entry_id, fields)


async def requeue(client: Redis, stream: str, dlq_entry_id: str) -> str:
    """Copy ONE DLQ entry's ``ce`` bytes back onto ``stream`` verbatim --
    ``dead_letter``'s own documented recovery path -- and return the new
    entry id ``XADD`` assigns on the source stream (never the same id the
    entry carried on the DLQ; Streams ids are never reused).

    Raises ``ValueError`` (module docstring's Pitfall 1) when the entry has
    no ``ce`` to copy -- there is nothing this call could legitimately XADD.
    Does NOT touch ``<stream>.dlq`` itself -- the entry stays there until an
    explicit ``purge``, so a requeue can be verified before the DLQ copy is
    ever discarded.
    """
    entry = await _get_entry(client, stream, dlq_entry_id)
    if entry.ce is None:
        raise ValueError(
            f"DLQ entry {dlq_entry_id!r} (reason={entry.reason!r}) is bookkeeping-only -- "
            "it dead-lettered with no `ce` field at all (RedisStreamsConsumer.dead_letter's own "
            "docstring: a ce-less malformed entry has no bytes to preserve). Nothing to requeue; "
            "the underlying poison bytes never reached this codebase's own producer path."
        )
    raw_id = await client.xadd(stream, {_CE_FIELD: entry.ce})
    return raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)


async def purge(client: Redis, stream: str, dlq_entry_ids: Sequence[str]) -> int:
    """``XDEL`` the named entries off ``<stream>.dlq``. Irreversible -- the
    CLI (``main`` below) only reaches this after an explicit ``--yes``, and
    there is deliberately no "purge everything" switch (module docstring).
    Returns the count Redis actually deleted (silently fewer than requested
    if an id was already gone -- ``XDEL`` itself is idempotent per id)."""
    return await client.xdel(f"{stream}.dlq", *dlq_entry_ids)


def _print_entries(entries: Sequence[DlqEntry]) -> None:
    for entry in entries:
        print(
            json.dumps(
                {
                    "dlq_entry_id": entry.entry_id,
                    "reason": entry.reason,
                    "source_stream": entry.source_stream,
                    "source_entry_id": entry.source_entry_id,
                    "consumer_group": entry.consumer_group,
                    "deliveries": entry.deliveries,
                    "reinjectable": entry.reinjectable,
                },
                ensure_ascii=False,
            )
        )


async def _run(args: argparse.Namespace) -> int:
    client = create_redis_client(load_settings().redis)
    try:
        if args.action == "peek":
            _print_entries(await peek(client, args.stream, count=args.count))
            return 0

        if args.action == "requeue":
            exit_code = 0
            for dlq_entry_id in args.dlq_entry_id:
                try:
                    new_entry_id = await requeue(client, args.stream, dlq_entry_id)
                except ValueError as exc:
                    exit_code = 1
                    _logger.error(
                        "ops.dlq.requeue_skipped",
                        extra={
                            "stream": args.stream,
                            "dlq_entry_id": dlq_entry_id,
                            "reason": str(exc),
                        },
                    )
                    print(f"SKIPPED {dlq_entry_id}: {exc}", file=sys.stderr)
                    continue
                _logger.info(
                    "ops.dlq.requeued",
                    extra={
                        "stream": args.stream,
                        "dlq_entry_id": dlq_entry_id,
                        "new_entry_id": new_entry_id,
                    },
                )
                print(f"requeued {dlq_entry_id} -> {args.stream}:{new_entry_id}")
            return exit_code

        # "purge" -- `main` has already refused to reach here without --yes.
        deleted = await purge(client, args.stream, args.dlq_entry_id)
        _logger.info(
            "ops.dlq.purged",
            extra={"stream": args.stream, "requested": len(args.dlq_entry_id), "deleted": deleted},
        )
        print(
            f"purged {deleted} of {len(args.dlq_entry_id)} requested entries from {args.stream}.dlq"
        )
        return 0
    finally:
        await client.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.dlq",
        description="Inspect/requeue/purge a Redis Streams dead-letter queue (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    peek_parser = sub.add_parser("peek", help="non-consuming read of <stream>.dlq")
    peek_parser.add_argument("stream", help="the SOURCE stream, e.g. stream.knowledge (not .dlq)")
    peek_parser.add_argument("--count", type=int, default=_DEFAULT_PEEK_COUNT)

    requeue_parser = sub.add_parser(
        "requeue", help="copy one or more DLQ entries' `ce` bytes back onto the source stream"
    )
    requeue_parser.add_argument(
        "stream", help="the SOURCE stream, e.g. stream.knowledge (not .dlq)"
    )
    requeue_parser.add_argument("dlq_entry_id", nargs="+", metavar="DLQ_ENTRY_ID")

    purge_parser = sub.add_parser("purge", help="XDEL named entries off <stream>.dlq")
    purge_parser.add_argument("stream", help="the SOURCE stream, e.g. stream.knowledge (not .dlq)")
    purge_parser.add_argument("dlq_entry_id", nargs="+", metavar="DLQ_ENTRY_ID")
    purge_parser.add_argument(
        "--yes", action="store_true", help="required explicit confirmation -- purge is irreversible"
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "purge" and not args.yes:
        raise SystemExit(
            "purge refused: pass --yes to confirm -- this XDELs the named entries "
            "off the DLQ permanently, and there is no undo."
        )
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
