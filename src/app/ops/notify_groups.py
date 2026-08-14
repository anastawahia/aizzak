"""Operator CLI over the orphaned-notify-group sweep -- ``list`` what the
rule would act on, or ``sweep`` it now instead of waiting for the API's own
timer (docs/log/3.135.md, ت-2 in ``docs/operational-findings.md`` §2).

**The leak this addresses, and why it is not a bug in the startup sweep.**
``_sweep_stale_notify_groups`` (``framework/di/composition_root.py``) destroys
orphaned notify groups at API startup, and its FIRST safety guard is that it
only inspects groups whose name begins with *its own* ``socket.gethostname()``
-- deliberately, so one replica can never destroy another replica's group no
matter how pid numbers collide across hosts. That guard is correct and
nothing here weakens it. But it has an unavoidable consequence: under Compose
(and RunPod), a recreated container gets a NEW hostname, so the groups the OLD
container left behind are named after a host that will never boot again.
Nothing was permitted to sweep them, because the only process permitted to was
dead. They accumulated monotonically -- measured 2026-08-13 on the local
stack: **20 orphaned groups across two streams**, five dead hostnames' worth,
all with ``pending 0``.

**What changed under this file.** The rule and both verbs now live in
``infrastructure/messaging/consumers/sweeper.py``, and the API process runs
them on a timer of its own (``CompositionRoot.sweep_orphan_notify_groups_
forever``, wired from ``EventSettings.notify_group_sweep_interval_s``), so the
leak is closed automatically rather than by remembering to run this. This
module stays because automation and inspection are different needs: an
operator wants to SEE what the rule considers live before trusting it, wants
to force a sweep immediately after recreating containers rather than waiting
out the interval, and needs a way to clean a stack whose API is not running
at all. It is now a thin CLI -- printing and argument parsing -- over the
same functions the automatic sweep calls, so the two can never diverge.

**Two verbs, mirroring ``app.ops.dlq``'s shape:**

* ``list``  -- prints every notify group on the topology's streams, marking
  each LIVE / ORPHAN and saying why. Reads only; touches nothing.
* ``sweep`` -- ``XGROUP DESTROY``s the orphans, gated on an explicit
  ``--yes``. Irreversible, hence never the default.

**What this deliberately does NOT do.** It does not touch ``cg.knowledge`` /
``cg.media`` / ``cg.memory`` (the static topology's own groups, which must
outlive every process -- destroying one silently resets a module's delivery
position to the stream tail), and it does not remove stale *consumers* inside
a live group: that is a different measurement with a different safety rule
(a consumer holding ``pending > 0`` owns messages that must be
``XAUTOCLAIM``ed before it can be deleted), and it is now done automatically
by each worker over its own groups (``sweeper.sweep_stale_consumers``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.sweeper import (
    DEFAULT_SETTLE_SECONDS,
    NotifyGroup,
    destroy_orphan_notify_groups,
    find_orphan_notify_groups,
    is_orphan,
    read_notify_groups,
)
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer


def topology_streams() -> tuple[str, ...]:
    """The streams a notify group can exist on: every stream in the static
    topology. A superset of where the bridge subscribes today, on purpose --
    this tool must keep finding orphans if the bridge's subscription set
    changes, and scanning a stream with no notify groups costs one `XINFO`."""
    return tuple(dict.fromkeys(binding.stream for binding in STATIC_CONSUMER_TOPOLOGY))


def _print_groups(groups: Sequence[NotifyGroup]) -> None:
    if not groups:
        print("no cg.notify groups found on the topology's streams")
        return
    for group in sorted(groups, key=lambda g: (g.stream, g.name)):
        orphan, reason = is_orphan(group)
        print(f"{'ORPHAN' if orphan else 'LIVE  '}  {group.stream:<20} {group.name:<34} {reason}")


async def _run(args: argparse.Namespace) -> int:
    streams = topology_streams()
    client = create_redis_client(load_settings().redis)
    consumer = RedisStreamsConsumer(client)
    try:
        if args.action == "list":
            _print_groups(await read_notify_groups(consumer, streams))
            return 0

        # "sweep" -- `main` has already refused to reach here without --yes.
        orphans = await find_orphan_notify_groups(
            consumer, streams, settle_seconds=args.settle_seconds
        )
        if not orphans:
            print("nothing to sweep: no orphaned cg.notify groups")
            return 0
        _print_groups(orphans)
        destroyed = await destroy_orphan_notify_groups(consumer, orphans)
        print(f"destroyed {destroyed} orphaned cg.notify group(s)")
        return 0
    finally:
        await client.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.notify_groups",
        description="List/sweep orphaned cg.notify.<host>.<pid> groups (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="print every cg.notify group, marking LIVE vs ORPHAN")

    sweep_parser = sub.add_parser("sweep", help="XGROUP DESTROY the orphaned notify groups")
    sweep_parser.add_argument(
        "--yes",
        action="store_true",
        help="required explicit confirmation -- XGROUP DESTROY is irreversible",
    )
    sweep_parser.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help=(
            "gap between the two readings that must BOTH show a group unused "
            f"(default {DEFAULT_SETTLE_SECONDS}; 0 skips the second reading)"
        ),
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "sweep" and not args.yes:
        raise SystemExit(
            "sweep refused: pass --yes to confirm -- this XGROUP DESTROYs the "
            "orphaned groups permanently, and there is no undo. Run `list` first."
        )
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
