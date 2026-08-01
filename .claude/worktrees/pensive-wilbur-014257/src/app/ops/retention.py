"""Age-based retention sweep for three ``platform`` ledgers that grow without
bound (P1-5, ``docs/p1-hardening-plan.md`` §3 step 8): published rows of
``platform.outbox``, all of ``platform.processed_events``, and all of
``platform.idempotency_keys``. None of the three has a cron job, a sweep, or
a runbook paragraph today (01-data-model §4.2/§4.2-ب name this explicitly:
"لا سياسة احتفاظٍ في v1"); growth is slow but strictly one-directional.

**The architectural tension this module is built around, resolved rather
than papered over.** ``app.ops.provision``'s own docstring grants ``app_rw``
only ``INSERT`` on ``outbox``/``processed_events`` — deliberately: "a role
that can only INSERT can neither enumerate the ledger nor un-process an
event to force a replay". Widening THAT grant to add ``DELETE`` so this
sweep could run as ``app_rw`` would undo exactly the guarantee that sentence
describes. The precedent for the alternative already lives in the same
module: ``outbox_relay`` is its OWN role, holding OWN least-privilege grants
(``SELECT, UPDATE`` on ``outbox`` alone), never a widened ``app_rw``. This
module follows that precedent instead of inventing a new one:
``retention_sweeper`` (``app.ops.provision.RETENTION_ROLE``) is a FOURTH
role, holding only ``SELECT``/``DELETE`` on the three named tables
(``RETENTION_GRANTS``) — enough to count and delete, nothing that can forge
or alter a row.

``platform.idempotency_keys`` needs one thing more: it is the ONE
``platform`` table under RLS, and its ``tenant_isolation`` policy confines
every role (no ``TO`` clause) to whatever ``app.workspace_id`` its own
session has set — nothing, for a sweep that runs once across every tenant.
``migrations/versions/platform/0003_retention_sweep.py`` adds two permissive
policies scoped ``TO retention_sweeper`` alone (``USING (true)``\\ , OR-
combined with ``tenant_isolation`` — 01 §3.1's own
``platform_credentials_read`` mechanism, here role-scoped instead of PUBLIC)
so this ONE role reaches every workspace's stale keys while ``app_rw``'s own
tenant confinement is completely untouched.

**Per-table retention windows are deliberately three different numbers, not
one** — each table's useful life is set by what actually reads it back, not
by a single "sane default" applied uniformly:

* ``IDEMPOTENCY_KEYS_RETENTION`` (2 days) — the ENTIRE purpose of a row is to
  answer a client's OWN retry of a request it already sent once
  (03-api-spec §0's "إعادة المحاولة آمنة"); a network timeout/reconnect
  resolves in minutes to hours, never weeks. ``response_body`` also stores
  actual tenant response data (01 §4.2-ب), so a short window is a data-
  minimisation win too, not merely a storage one.
* ``PROCESSED_EVENTS_RETENTION`` (30 days) — a bare dedup key, no payload.
  The real duplicate-delivery window this codebase's OWN retry mechanics
  produce is bounded by seconds (``EventSettings.consumer_block_ms=5000`` x
  ``max_retries_before_dlq=5``), but an OPERATOR replaying a DLQ entry by
  hand (``python -m app.ops.dlq requeue``, step 7) may do so long after the
  original failure — 30 days is a generous "the on-call operator got to the
  backlog eventually" margin, still finite.
* ``OUTBOX_RETENTION`` (90 days), published rows only — unlike the two
  above, a row here still carries the FULL CloudEvents payload plus
  ``correlation_id``/``causation_id`` (01 §4.1): real incident-forensics
  value ("what did we actually publish, and when") across a typical
  postmortem window. Kept longest of the three for that reason, still
  bounded at a quarter so growth still terminates.

**An unpublished outbox row is never a sweep candidate, at any age** — that
would be data loss for a message the relay has not gotten to yet, not
retention. ``sweep_outbox`` filters ``published_at IS NOT NULL`` before it
ever looks at the age at all.

Three verbs, exactly ``app.ops.dlq``'s shape (no HTTP surface, no periodic
scheduling built here — metrics/scheduling are LATER steps):

* ``sweep`` (no ``--table``) — sweeps all three, each at its OWN default
  retention, each in its OWN transaction (independent tables, independent
  windows — no reason to couple their commits).
* ``sweep --table <name> [--older-than-days N]`` — sweeps exactly one table,
  optionally overriding ITS OWN window. ``--older-than-days`` REQUIRES
  ``--table``: there is deliberately no single override that would apply the
  same number to all three windows above, which would erase the very
  distinction this module's docstring just spent three paragraphs making.
* ``--dry-run`` (either form) — counts what a real sweep WOULD delete
  (``SELECT count(*)`` under the identical predicate) and deletes nothing;
  the safe way to see the number before trusting it.

Usage::

    python -m app.ops.retention sweep [--dry-run]
    python -m app.ops.retention sweep --table outbox [--older-than-days 30] [--dry-run]

``DATABASE_URL`` for THIS process must be the ``retention_sweeper`` role's
OWN DSN — the same per-process convention ``provision.py``/
``workers/bootstrap.py`` already document for ``aizzak_owner``/
``outbox_relay``: one role per process, never one role wearing another's hat.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine

_logger = logging.getLogger(__name__)

# See the module docstring's per-table paragraph for why these three numbers
# differ, and why none of them is invented without a reason attached.
IDEMPOTENCY_KEYS_RETENTION = timedelta(days=2)
PROCESSED_EVENTS_RETENTION = timedelta(days=30)
OUTBOX_RETENTION = timedelta(days=90)

_TABLES: tuple[str, ...] = ("outbox", "processed_events", "idempotency_keys")


@dataclass(frozen=True, slots=True)
class SweepResult:
    """One table's sweep outcome. ``affected`` is a real ``DELETE`` row
    count when ``dry_run`` is ``False``; otherwise it is the count a real
    sweep WOULD have deleted (the identical predicate, ``SELECT count(*)``
    instead of ``DELETE``) -- never both in the same result."""

    table: str
    cutoff: datetime
    affected: int
    dry_run: bool


async def _sweep(
    conn: AsyncConnection,
    *,
    table: str,
    delete_sql: str,
    count_sql: str,
    cutoff: datetime,
    dry_run: bool,
) -> SweepResult:
    if dry_run:
        result = await conn.execute(text(count_sql), {"cutoff": cutoff})
        affected = int(result.scalar_one())
    else:
        result = await conn.execute(text(delete_sql), {"cutoff": cutoff})
        affected = result.rowcount
    return SweepResult(table=table, cutoff=cutoff, affected=affected, dry_run=dry_run)


async def sweep_outbox(
    conn: AsyncConnection,
    *,
    retention: timedelta = OUTBOX_RETENTION,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SweepResult:
    """Deletes PUBLISHED rows only (``published_at IS NOT NULL``) older than
    ``retention``. An unpublished row is never a candidate at any age --
    the relay has simply not reached it yet, and sweeping it would be data
    loss, not retention."""
    cutoff = (now or datetime.now(UTC)) - retention
    return await _sweep(
        conn,
        table="outbox",
        delete_sql=(
            "DELETE FROM platform.outbox WHERE published_at IS NOT NULL AND published_at < :cutoff"
        ),
        count_sql=(
            "SELECT count(*) FROM platform.outbox "
            "WHERE published_at IS NOT NULL AND published_at < :cutoff"
        ),
        cutoff=cutoff,
        dry_run=dry_run,
    )


async def sweep_processed_events(
    conn: AsyncConnection,
    *,
    retention: timedelta = PROCESSED_EVENTS_RETENTION,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SweepResult:
    """Deletes DD-09 ledger rows (``consumer_group``, ``event_id``) older
    than ``retention`` -- keyed on ``processed_at``, the only timestamp the
    table carries."""
    cutoff = (now or datetime.now(UTC)) - retention
    return await _sweep(
        conn,
        table="processed_events",
        delete_sql="DELETE FROM platform.processed_events WHERE processed_at < :cutoff",
        count_sql="SELECT count(*) FROM platform.processed_events WHERE processed_at < :cutoff",
        cutoff=cutoff,
        dry_run=dry_run,
    )


async def sweep_idempotency_keys(
    conn: AsyncConnection,
    *,
    retention: timedelta = IDEMPOTENCY_KEYS_RETENTION,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SweepResult:
    """Deletes ``Idempotency-Key`` rows older than ``retention``, across
    EVERY workspace at once -- reachable only because ``retention_sweeper``
    (unlike ``app_rw``) holds the cross-tenant RLS carve-out
    ``migrations/versions/platform/0003_retention_sweep.py`` adds
    specifically for this role."""
    cutoff = (now or datetime.now(UTC)) - retention
    return await _sweep(
        conn,
        table="idempotency_keys",
        delete_sql="DELETE FROM platform.idempotency_keys WHERE created_at < :cutoff",
        count_sql="SELECT count(*) FROM platform.idempotency_keys WHERE created_at < :cutoff",
        cutoff=cutoff,
        dry_run=dry_run,
    )


_SweepFn = Callable[..., Awaitable[SweepResult]]
_SWEEPERS: dict[str, _SweepFn] = {
    "outbox": sweep_outbox,
    "processed_events": sweep_processed_events,
    "idempotency_keys": sweep_idempotency_keys,
}
_DEFAULT_RETENTION: dict[str, timedelta] = {
    "outbox": OUTBOX_RETENTION,
    "processed_events": PROCESSED_EVENTS_RETENTION,
    "idempotency_keys": IDEMPOTENCY_KEYS_RETENTION,
}


async def sweep_all(engine: AsyncEngine, *, dry_run: bool = False) -> list[SweepResult]:
    """Sweep all three tables, each at its own default retention, each in
    its own transaction (module docstring: independent windows, no reason to
    couple their commits)."""
    results: list[SweepResult] = []
    for table in _TABLES:
        async with engine.begin() as conn:
            results.append(await _SWEEPERS[table](conn, dry_run=dry_run))
    return results


def _print_result(result: SweepResult) -> None:
    payload = {
        "table": result.table,
        "cutoff": result.cutoff.isoformat(),
        "affected": result.affected,
        "dry_run": result.dry_run,
    }
    print(json.dumps(payload, ensure_ascii=False))
    _logger.info("ops.retention.swept", extra=payload)


async def _run_cli(args: argparse.Namespace) -> int:
    engine = create_engine(DatabaseSettings(url=load_settings().database.url), poolclass=NullPool)
    try:
        if args.table is not None:
            retention = (
                timedelta(days=args.older_than_days)
                if args.older_than_days is not None
                else _DEFAULT_RETENTION[args.table]
            )
            async with engine.begin() as conn:
                result = await _SWEEPERS[args.table](
                    conn, retention=retention, dry_run=args.dry_run
                )
            _print_result(result)
        else:
            for result in await sweep_all(engine, dry_run=args.dry_run):
                _print_result(result)
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.retention",
        description="Age-based DELETE sweep for the three unbounded platform ledgers "
        "(module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sweep_parser = sub.add_parser(
        "sweep", help="delete (or count, with --dry-run) rows older than each table's window"
    )
    sweep_parser.add_argument(
        "--table",
        choices=_TABLES,
        default=None,
        help="sweep exactly ONE table (default: all three, each at its own default window)",
    )
    sweep_parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="override that ONE table's retention window, in days -- requires --table "
        "(module docstring: no single override applies to all three)",
    )
    sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count what WOULD be deleted; deletes nothing",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.older_than_days is not None and args.table is None:
        raise SystemExit(
            "--older-than-days requires --table -- there is no single retention window "
            "for all three tables (module docstring's whole point)."
        )
    raise SystemExit(asyncio.run(_run_cli(args)))


if __name__ == "__main__":
    main()
