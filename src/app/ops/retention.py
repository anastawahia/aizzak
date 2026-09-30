"""Age-based retention sweep for the ledgers that grow without bound (P1-5,
``docs/p1-hardening-plan.md`` §3 step 8; extended by capacity step 2.8,
``ح-16``): published rows of ``platform.outbox``, all of
``platform.processed_events``, all of ``platform.idempotency_keys``, and —
added by 2.8 — all of ``usage.usage_records``. None of them has a cron job,
a sweep, or a runbook paragraph today (01-data-model §4.2/§4.2-ب name this
explicitly: "لا سياسة احتفاظٍ في v1"); growth is slow but strictly
one-directional.

**Capacity step 2.8 added the fourth target and the first floor.** 2.8 names
three write-heavy tables — ``outbox``, ``processed_events`` and ``usage`` —
and asks for "a retention policy executed by step 5.7". Two of the three were
already here; ``usage`` was not swept by anything, in any schema, by any role.
It is also the first target outside ``platform``, which is why it needed a
migration of its own rather than a GRANT: every ``usage`` table is under FORCE
ROW LEVEL SECURITY, so a cross-tenant age sweep is confined to one workspace
(i.e. to none) until a role-scoped policy says otherwise. See
``sweep_usage_records`` and ``processed_events_floor``.

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
* ``USAGE_RECORDS_RETENTION`` (90 days) — the append-only metering ledger.
  Three full ``Period.MONTH`` enforcement windows, which is what makes a
  disputed month reconstructible from the detail rows and not only from the
  rollup. It can expire at all only because the number quota enforcement reads
  lives in ``usage_rollups`` (2.7's ``reserve``), which is NOT swept: deleting
  a rollup row would hand a workspace back headroom it already spent, while
  deleting a ledger row loses only the itemisation. And it may expire at all
  only because of a decision already recorded in ``app.ops.purge``'s docstring
  (CONFIRMED 2026-08-12, human review): usage here is "a per-workspace meter
  reading, not a financial obligation (billing is out of v1 scope)". If a
  billing or tax retention duty is ever asserted, THAT wins and this number
  must be revisited — the same sentence ``purge`` already carries.
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

Four verbs, exactly ``app.ops.dlq``'s shape (no HTTP surface, no periodic
scheduling built here — metrics/scheduling are LATER steps):

* ``sweep`` (no ``--table``) — sweeps all four, each at its OWN default
  retention, each in its OWN transaction (independent tables, independent
  windows — no reason to couple their commits).
* ``sweep --table <name> [--older-than-days N]`` — sweeps exactly one table,
  optionally overriding ITS OWN window. ``--older-than-days`` REQUIRES
  ``--table``: there is deliberately no single override that would apply the
  same number to all four windows above, which would erase the very
  distinction this module's docstring just spent four paragraphs making. An
  override is additionally refused below a table's safe floor where one can be
  derived — see ``processed_events_floor``.
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

from app.framework.settings.settings import DatabaseSettings, EventSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine
from app.ops.provision import RETENTION_ROLE
from app.ops.role_guard import ROLE_MISMATCH_EXIT, RoleMismatchError, require_role

_logger = logging.getLogger(__name__)

# See the module docstring's per-table paragraph for why these four numbers
# differ, and why none of them is invented without a reason attached.
IDEMPOTENCY_KEYS_RETENTION = timedelta(days=2)
PROCESSED_EVENTS_RETENTION = timedelta(days=30)
OUTBOX_RETENTION = timedelta(days=90)
USAGE_RECORDS_RETENTION = timedelta(days=90)

_TABLES: tuple[str, ...] = (
    "outbox",
    "processed_events",
    "idempotency_keys",
    "usage_records",
)

#: Fully-qualified names, because two schemas are swept now and the table
#: argument is a bare word. `app.ops.table_growth` imports this mapping rather
#: than repeating either half.
QUALIFIED: dict[str, str] = {
    "outbox": "platform.outbox",
    "processed_events": "platform.processed_events",
    "idempotency_keys": "platform.idempotency_keys",
    "usage_records": "usage.usage_records",
}


def processed_events_floor(events: EventSettings | None = None) -> timedelta:
    """The shortest retention that CANNOT resurrect a duplicate effect --
    derived from the redelivery mechanics, never written as a number.

    Capacity step 2.8's warning ("no row is deleted from ``processed_events``
    before the retry window has fully elapsed -- deleting one early revives the
    duplicate effect the table exists to prevent") was, until now, a paragraph.
    A paragraph does not stop ``--older-than-days 0``, which is one keystroke
    from ``--older-than-days 30`` and deletes the entire ledger.

    Two spans compose the window the CODE guarantees, and both come out of
    ``EventSettings``:

    * ``consumer_stale_idle_s`` -- how long a message may sit pending against a
      dead consumer before ``consumers/sweeper.py`` reclaims it. Nothing has
      re-delivered it yet, and its dedup row must still be there when something
      does.
    * ``consumer_block_ms x max_retries_before_dlq`` -- the automatic retry
      ladder that follows, before the message is routed to the DLQ.

    **What this floor deliberately does NOT cover, stated rather than implied.**
    The 30-day default is not set by these mechanics -- it is set by the
    OPERATOR replay path (``python -m app.ops.dlq requeue``), which can re-
    deliver an event weeks after the original failure. That window has no
    mechanical end, and its length lives in Redis (the DLQ's own depth and age),
    which this process does not connect to. So the floor below is the provable
    half only: under it, a duplicate effect is certain; over it and under the
    default, it depends on whether the DLQ is empty. Lowering the window is
    therefore allowed above the floor and refused below it, and the operator
    who lowers it is the one who has to know the DLQ is drained.
    """
    settings = events or EventSettings()
    ladder = (settings.consumer_block_ms / 1000.0) * settings.max_retries_before_dlq
    return timedelta(seconds=settings.consumer_stale_idle_s + ladder)


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


async def sweep_usage_records(
    conn: AsyncConnection,
    *,
    retention: timedelta = USAGE_RECORDS_RETENTION,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SweepResult:
    """Deletes metering ledger rows older than ``retention``, across EVERY
    workspace at once -- reachable only because ``retention_sweeper`` holds the
    cross-tenant carve-out ``migrations/versions/usage/0004_usage_autovacuum.py``
    adds, the same mechanism ``0003_retention_sweep.py`` added for
    ``idempotency_keys``. Every ``usage`` table is under FORCE ROW LEVEL
    SECURITY, so the GRANT alone would leave this sweep confined to one
    workspace -- which for an age-based sweep means none.

    **``usage_rollups`` is not swept and must not be.** It is the aggregate the
    quota check reads (step 2.7's ``reserve``), so deleting a row there does not
    age out history -- it hands a workspace back headroom it already spent. The
    ledger is the detail and expires; the rollup is the balance and does not.
    The role has no GRANT on it either, so this is enforced by the database and
    not only by this module's choice of statement."""
    cutoff = (now or datetime.now(UTC)) - retention
    return await _sweep(
        conn,
        table="usage_records",
        delete_sql="DELETE FROM usage.usage_records WHERE created_at < :cutoff",
        count_sql="SELECT count(*) FROM usage.usage_records WHERE created_at < :cutoff",
        cutoff=cutoff,
        dry_run=dry_run,
    )


_SweepFn = Callable[..., Awaitable[SweepResult]]
_SWEEPERS: dict[str, _SweepFn] = {
    "outbox": sweep_outbox,
    "processed_events": sweep_processed_events,
    "idempotency_keys": sweep_idempotency_keys,
    "usage_records": sweep_usage_records,
}
_DEFAULT_RETENTION: dict[str, timedelta] = {
    "outbox": OUTBOX_RETENTION,
    "processed_events": PROCESSED_EVENTS_RETENTION,
    "idempotency_keys": IDEMPOTENCY_KEYS_RETENTION,
    "usage_records": USAGE_RECORDS_RETENTION,
}

#: Only one table has a floor the code can prove; the others' windows are
#: policy, and a policy this module cannot check is not one it pretends to.
_MIN_RETENTION: dict[str, Callable[[], timedelta]] = {
    "processed_events": processed_events_floor,
}


def check_retention_floor(table: str, retention: timedelta) -> None:
    """Raises ``ValueError`` when ``retention`` is short enough to break a
    guarantee, rather than letting the sweep quietly break it (step 2.8's
    warning, made mechanical). Tables with no provable floor pass unchanged."""
    floor_of = _MIN_RETENTION.get(table)
    if floor_of is None:
        return
    floor = floor_of()
    if retention < floor:
        raise ValueError(
            f"--older-than-days is below {table}'s safe floor: "
            f"{retention.total_seconds():.0f}s requested, {floor.total_seconds():.0f}s "
            "is the redelivery window derived from EventSettings "
            "(consumer_stale_idle_s + consumer_block_ms x max_retries_before_dlq). "
            "Deleting a dedup row inside that window revives the duplicate effect "
            "the table exists to prevent."
        )


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
        # Capacity 5.7 (`role_guard`'s docstring): under any other role RLS
        # turns two of the four tables into empty ones, and the sweep
        # "succeeds" on them. Checked before anything is counted.
        await require_role(engine, tool="app.ops.retention", expected=RETENTION_ROLE)
        if args.table is not None:
            retention = (
                timedelta(days=args.older_than_days)
                if args.older_than_days is not None
                else _DEFAULT_RETENTION[args.table]
            )
            # Checked BEFORE the engine does any work, so a refused sweep costs
            # a connection and not a transaction.
            check_retention_floor(args.table, retention)
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
            "for all four tables (module docstring's whole point)."
        )
    try:
        raise SystemExit(asyncio.run(_run_cli(args)))
    except RoleMismatchError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(ROLE_MISMATCH_EXIT) from exc


if __name__ == "__main__":
    main()
