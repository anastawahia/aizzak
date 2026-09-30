"""Operator CLI over capacity 5.5's stream trim (``ح-17``) -- ``status`` shows
what each published stream holds and which group bounds its trim; ``run``
performs one trim pass now instead of waiting for ``outbox-relay``'s timer.

**Why this exists next to the automatic trim.** The trim can only be as short
as its slowest reader allows, and it cannot tell a stopped worker from an
abandoned group -- both are a group with zero consumers and an old cursor
(``infrastructure/messaging/stream_retention.py``'s docstring says why that
is not a guess worth automating). When a stream grows toward its ``MAXLEN``
backstop, ``AizzakStreamBackstopHigh`` fires, and the next question is always
"who is holding it, and since when" -- this answers it in one line per group.

**Two verbs, mirroring ``app.ops.notify_groups``' shape:**

* ``status`` -- per stream: length against the backstop, the trim point the
  rule would use, the group that holds it and for how long, then every group
  with its consumers, pending entries, lag, oldest unconsumed age and entries
  trimmed before it read them. Reads only (``MULTI``/``XPENDING``/``XRANGE``).
  ``--json`` prints the same as one document, for an acceptance record.
* ``run`` -- one ``XTRIM MINID ~`` pass with the deployment's own margin,
  gated on ``--yes``. It removes only what no group needs (the rule is the
  same function the relay runs), but it does delete history, and the ops
  tools here never delete by default.

**What it deliberately does NOT do: destroy a group.** An abandoned group is
the one thing that holds a stream forever, and removing it is the fix -- but
"abandoned" is a judgement about intent that Redis cannot answer, so the
command is printed for an operator to run, never run here (08 §4.21).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from app.framework.events.topology import PUBLISHED_STREAMS
from app.framework.types import Json
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.stream_retention import (
    NOTIFY_GROUP_FAMILY,
    GroupPosition,
    StreamSnapshot,
    StreamTrimmer,
    format_stream_id,
    read_stream_snapshot,
    trim_decision,
)


@dataclass(frozen=True, slots=True)
class GroupStatus:
    """One group, reduced to the plain values ``status`` prints."""

    name: str
    consumers: int
    pending: int
    lag: int | None
    oldest_unconsumed_age_s: float
    unread_trimmed: int | None


@dataclass(frozen=True, slots=True)
class StreamStatus:
    """One stream's ``status`` -- rendered as text or as JSON from the same
    record, so the two forms cannot say different things."""

    stream: str
    length: int
    backstop: int | None
    first_entry_id: str | None
    last_generated_id: str
    entries_added: int
    trim_to: str | None
    held_by: str | None
    boundary: str
    boundary_age_s: float
    groups: tuple[GroupStatus, ...]

    @property
    def backstop_used(self) -> float | None:
        return round(self.length / self.backstop, 4) if self.backstop else None

    def to_json(self) -> Json:
        return {
            "stream": self.stream,
            "length": self.length,
            "backstop": self.backstop,
            "backstop_used": self.backstop_used,
            "first_entry_id": self.first_entry_id,
            "last_generated_id": self.last_generated_id,
            "entries_added": self.entries_added,
            "trim_to": self.trim_to,
            "held_by": self.held_by,
            "boundary": self.boundary,
            "boundary_age_s": self.boundary_age_s,
            "groups": [
                {
                    "name": g.name,
                    "consumers": g.consumers,
                    "pending": g.pending,
                    "lag": g.lag,
                    "oldest_unconsumed_age_s": g.oldest_unconsumed_age_s,
                    "unread_trimmed": g.unread_trimmed,
                }
                for g in self.groups
            ],
        }


def _group_status(snapshot: StreamSnapshot, group: GroupPosition) -> GroupStatus:
    return GroupStatus(
        name=group.name,
        consumers=group.consumers,
        pending=group.pending,
        lag=group.lag,
        oldest_unconsumed_age_s=round(snapshot.age_s(group.oldest_unconsumed_id), 1),
        unread_trimmed=group.unread_trimmed(snapshot.entries_added),
    )


def stream_status(
    snapshot: StreamSnapshot, *, margin_ms: int, backstop: int | None
) -> StreamStatus:
    """What ``status`` reports for one stream, from the SAME rule the relay
    trims by (``trim_decision``) -- a preview that cannot disagree with it."""
    decision = trim_decision(snapshot, margin_ms=margin_ms)
    return StreamStatus(
        stream=snapshot.stream,
        length=snapshot.length,
        backstop=backstop,
        first_entry_id=(
            format_stream_id(snapshot.first_entry_id) if snapshot.first_entry_id else None
        ),
        last_generated_id=format_stream_id(snapshot.last_generated_id),
        entries_added=snapshot.entries_added,
        trim_to=decision.minid,
        held_by=decision.holder,
        boundary=format_stream_id(decision.boundary),
        boundary_age_s=round(snapshot.age_s(decision.boundary), 1),
        groups=tuple(_group_status(snapshot, g) for g in snapshot.groups),
    )


def _worst(groups: Sequence[GroupStatus], label: str) -> GroupStatus:
    """The notify family folded into one line: summed consumers, the worst of
    everything else. Twelve per-process lines on a three-replica stack say
    less than their maximum does."""
    lags = [g.lag for g in groups if g.lag is not None]
    trimmed = [g.unread_trimmed for g in groups if g.unread_trimmed is not None]
    return GroupStatus(
        name=label,
        consumers=sum(g.consumers for g in groups),
        pending=max(g.pending for g in groups),
        lag=max(lags) if lags else None,
        oldest_unconsumed_age_s=max(g.oldest_unconsumed_age_s for g in groups),
        unread_trimmed=max(trimmed) if trimmed else None,
    )


def _group_line(group: GroupStatus) -> str:
    return (
        f"  {group.name:<36} consumers {group.consumers!s:<3} pending {group.pending!s:<5} "
        f"lag {group.lag!s:<7} oldest unconsumed {group.oldest_unconsumed_age_s}s  "
        f"unread trimmed {group.unread_trimmed}"
    )


def render_status(status: StreamStatus) -> list[str]:
    """The text form. ``--json`` keeps every notify group; this folds them."""
    used = status.backstop_used
    cap = f"{status.backstop:,} ({(used or 0.0) * 100:.1f}% used)" if status.backstop else "off"
    holder = status.held_by or "no group -- the margin alone"
    lines = [
        f"{status.stream}  length {status.length:,}  backstop {cap}",
        f"  trim to {status.trim_to or '-- nothing to trim --'}  "
        f"(bounded by {holder} at {status.boundary}, {status.boundary_age_s}s old)",
    ]
    if not status.groups:
        lines.append("  (no consumer groups)")
    notify = [g for g in status.groups if g.name.startswith(f"{NOTIFY_GROUP_FAMILY}.")]
    for group in status.groups:
        if group in notify:
            continue
        lines.append(_group_line(group))
        if group.consumers == 0 and group.name == status.held_by:
            lines.append(
                "    ^ bounds the trim with no consumer: a stopped worker, or an abandoned "
                "group. If nothing will ever read it again (08 §4.21):\n"
                f"      redis-cli XGROUP DESTROY {status.stream} {group.name}"
            )
    if notify:
        lines.append(_group_line(_worst(notify, f"{NOTIFY_GROUP_FAMILY}.* ({len(notify)}, worst)")))
    return lines


async def _snapshots(streams: Sequence[str]) -> list[StreamSnapshot]:
    client = create_redis_client(load_settings().redis)
    try:
        found = []
        for stream in streams:
            snapshot = await read_stream_snapshot(client, stream, with_undelivered=True)
            if snapshot is not None:
                found.append(snapshot)
        return found
    finally:
        await client.aclose()


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    margin_ms = int(settings.events.stream_trim_margin_s * 1000)
    backstop = settings.events.stream_maxlen
    if args.action == "status":
        statuses = [
            stream_status(snapshot, margin_ms=margin_ms, backstop=backstop)
            for snapshot in await _snapshots(PUBLISHED_STREAMS)
        ]
        if args.json:
            print(json.dumps([st.to_json() for st in statuses], ensure_ascii=False, indent=2))
        else:
            for status in statuses:
                print("\n".join(render_status(status)))
        return 0

    # "run" -- `main` has already refused to reach here without --yes.
    client = create_redis_client(settings.redis)
    try:
        trimmer = StreamTrimmer(
            client,
            streams=PUBLISHED_STREAMS,
            # One pass, never looped here; any positive number satisfies the
            # constructor, which refuses to build a DISABLED trimmer.
            interval_s=max(settings.events.stream_trim_interval_s, 1.0),
            margin_s=settings.events.stream_trim_margin_s,
            backstop=backstop,
        )
        for report in await trimmer.trim_once():
            print(
                f"{report.stream:<18} trimmed {report.trimmed:>7,} of {report.length_before:>7,}"
                f"  minid {report.minid or '-'}  bounded by {report.holder or '-'}"
                f" ({report.boundary_age_s}s old)"
            )
        return 0
    finally:
        await client.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.stream_trim",
        description="Inspect or run capacity 5.5's stream trim (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    status = sub.add_parser("status", help="length, backstop, trim point and holder per stream")
    status.add_argument("--json", action="store_true", help="one JSON document instead of text")
    run = sub.add_parser("run", help="one XTRIM MINID ~ pass now, with the deployment's margin")
    run.add_argument(
        "--yes",
        action="store_true",
        help="required explicit confirmation -- trimmed history does not come back",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "run" and not args.yes:
        raise SystemExit(
            "run refused: pass --yes to confirm -- the pass removes history no group "
            "needs, and it does not come back. Run `status` first."
        )
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
