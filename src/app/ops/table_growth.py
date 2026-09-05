"""Whether autovacuum can actually keep up with the write-heavy ledgers --
capacity step 2.8 (``ح-16``, ``docs/capacity-plan.md`` §5, Wave 2).

**The question this answers, which nothing else in the repository could.**
``ح-16`` says the write-heavy tables have "no partitioning, no archival and no
autovacuum settings of their own", and that the damage arrives "gradually, with
no single event to warn". Step 0.2's metric list named an autovacuum-lag metric
and deferred it (``capacity-status.md`` §2: "و تأخّرُ ``autovacuum`` باقٍ
لـ``0.4``"), 0.4 shipped ``pg_stat_statements`` instead, and ``د-12`` records
that there is no ``postgres_exporter`` in the stack at all. So the one number
that says whether a ledger is being cleaned has never been readable from
anywhere. ``status`` is that number, on the ``app.ops.dlq``/``slow_queries``
footing: a verb an operator runs, no HTTP surface, no scheduling, no metric
export.

**Why "dead tuples" alone is not the answer, and what is printed instead.**
Autovacuum starts on a table when::

    n_dead_tup > autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * n_live_tup

A dead-tuple count read on its own cannot say whether that is close or hopeless
-- 300,000 dead rows is an emergency on a small table and idle on a large one,
because the *right-hand side moves with the table*. This report therefore
computes the right-hand side per table from that table's OWN effective settings
(``pg_class.reloptions`` where step 2.8's migrations set them, the server-wide
``pg_settings`` value where they did not) and prints the ratio. ``ح-16``'s
"practically never cleaned" is a number here, not an adjective.

**Three traps this tool was written around, all three measured on this stack
rather than assumed:**

1. ``n_dead_tup`` is not free of charge to read correctly. PostgreSQL 15+
   defaults ``stats_fetch_consistency`` to ``cache``, which pins every
   statistics read to the FIRST one in the transaction -- and this report reads
   five tables in one transaction, so four of them would be answered from a
   snapshot taken before they were consulted. ``growth`` sets
   ``stats_fetch_consistency = none`` with ``SET LOCAL``, scoped to its own
   transaction rather than changing a server-wide default for everyone else.

   The related trap, met while building this step and worth naming because it
   invalidated a whole measurement run: a backend flushes its pending statistics
   at the boundary of the OUTER command, not at a procedural ``COMMIT`` inside a
   ``DO`` block. A churn harness written as one ``DO`` block therefore reported
   ``n_dead_tup = 0`` for ten minutes and 1,000,000 the instant it finished --
   and autovacuum, which reads the same shared statistics, never fired either.
   That is a property of the harness, not of the server; the real measurement
   was redone over ``pgbench``, i.e. over real client transactions.

2. **The bloat an operator cares about is not visible in ``n_dead_tup`` at
   all.** VACUUM returns dead tuples to the table's free space map; it does not
   return pages to the filesystem (only trailing empty ones). So a table that
   was allowed to accumulate 400,000 dead rows before its first vacuum keeps
   the file it grew to, forever, and ``n_dead_tup`` reads near zero right after.
   Measured on ``platform.outbox`` at 2,000,000 rows, at a CONSTANT row count so
   every byte of growth is bloat and not data: with the shipped defaults heap
   went 1302 MB -> 1673 MB (+28.5%) and plateaued there rather than returning,
   while with step 2.8's absolute threshold it went 1302 MB -> 1406 MB (+8.0%)
   and stayed flat for ten consecutive samples. ``status`` therefore prints
   ``bytes/row`` beside the sizes: it is the only column that moves when a table
   is bloating and the row count is not.

3. A manual ``VACUUM`` is the obvious remedy and it does not work on a big
   table in a default Docker container -- parallel maintenance draws its
   segment from a 64 MB ``/dev/shm``. That is fixed in ``docker-compose.yml``
   (``shm_size``), and ``status`` reports the remaining headroom so the fix is
   visible rather than folklore.

Two verbs::

    python -m app.ops.table_growth status [--json] [--all]
    python -m app.ops.table_growth vacuum --table <name> [--yes]

``status`` reads only. ``vacuum`` is the operator's manual catch-up for a table
that has already fallen behind -- gated on ``--yes`` because on a large table it
takes a ``SHARE UPDATE EXCLUSIVE`` lock for its duration, and because a running
autovacuum on the same table is CANCELLED by it (measured: two cancellations
during the first churn run, both caused by the operator, not by the workload).

``DATABASE_URL`` for THIS process must be ``aizzak_owner``'s OWN DSN, pointed at
``postgres:5432`` -- ``slow_queries``' convention and for its reason: the report
is most useful during a load run, which is exactly when ``MAX_CLIENT_CONN`` is
the resource under test (``ح-3``), and a measuring tool that occupies one of the
slots it measures perturbs the measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine
from app.ops.retention import QUALIFIED

_logger = logging.getLogger(__name__)

#: The tables step 2.8 is about, in the order its own text names them. Taken
#: from `app.ops.retention.QUALIFIED` -- the sweep and the growth report must
#: never disagree about which tables have a policy -- plus `usage_rollups`,
#: which is deliberately NOT swept (it is the balance, not the detail; see
#: `sweep_usage_records`) and is therefore the one table here whose size is
#: bounded by rollup arithmetic rather than by a retention window. It is
#: watched precisely because nothing sweeps it.
WATCHED: tuple[str, ...] = (*QUALIFIED.values(), "usage.usage_rollups")


@dataclass(frozen=True, slots=True)
class TableGrowth:
    """One watched table's answer to "is autovacuum keeping up?"."""

    table: str
    live_tuples: int
    dead_tuples: int
    #: The right-hand side of the trigger inequality, computed from THIS
    #: table's effective settings -- the number `dead_tuples` is racing.
    vacuum_trigger_at: int
    heap_bytes: int
    index_bytes: int
    autovacuum_count: int
    last_autovacuum: datetime | None
    last_autoanalyze: datetime | None
    #: True when step 2.8's migration has been applied to this table. A table
    #: without it is running the proportional trigger `ح-16` describes.
    has_own_settings: bool

    @property
    def total_bytes(self) -> int:
        return self.heap_bytes + self.index_bytes

    @property
    def bytes_per_row(self) -> float:
        """The bloat column. At a steady row count this is the only figure
        that moves, and it moves in one direction until a vacuum completes."""
        return self.total_bytes / self.live_tuples if self.live_tuples else 0.0

    @property
    def trigger_ratio(self) -> float:
        """``dead / trigger``. Below 1.0 autovacuum has not started; at 1.0 it
        is due. Sustained values above 1.0 mean it is due and not finishing."""
        return self.dead_tuples / self.vacuum_trigger_at if self.vacuum_trigger_at else 0.0

    @property
    def never_vacuumed(self) -> bool:
        return self.autovacuum_count == 0


_STATUS_SQL = """
SELECT c.oid::regclass::text                                        AS qualified,
       s.n_live_tup, s.n_dead_tup, s.autovacuum_count,
       s.last_autovacuum, s.last_autoanalyze,
       pg_relation_size(c.oid)                                      AS heap_bytes,
       pg_indexes_size(c.oid)                                       AS index_bytes,
       -- The trigger's own arithmetic, per table: the reloption where step
       -- 2.8 set one, the server-wide value where it did not. current_setting
       -- is the fallback rather than a hard-coded 50/0.2, so a change to the
       -- server config is reflected instead of contradicted.
       (COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                   WHERE option_name = 'autovacuum_vacuum_threshold'),
                 current_setting('autovacuum_vacuum_threshold'))::numeric
        + COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                     WHERE option_name = 'autovacuum_vacuum_scale_factor'),
                   current_setting('autovacuum_vacuum_scale_factor'))::numeric
          * s.n_live_tup)::bigint                                   AS vacuum_trigger_at,
       (c.reloptions IS NOT NULL
        AND EXISTS (SELECT 1 FROM pg_options_to_table(c.reloptions)
                     WHERE option_name = 'autovacuum_vacuum_threshold'))
                                                                    AS has_own_settings
FROM pg_class c
JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE c.oid = ANY(CAST(:tables AS regclass[]))
ORDER BY pg_total_relation_size(c.oid) DESC
"""


async def growth(conn: AsyncConnection, tables: Sequence[str]) -> list[TableGrowth]:
    """Read each table's live growth picture.

    ``stats_fetch_consistency = none`` is set on this transaction and not
    globally: every row must be read fresh (module docstring, trap 1), and a
    reporting tool has no business changing a server-wide default for anyone
    else. ``SET LOCAL`` scopes it to this transaction alone.
    """
    await conn.execute(text("SET LOCAL stats_fetch_consistency = none"))
    rows = (await conn.execute(text(_STATUS_SQL), {"tables": list(tables)})).all()
    return [
        TableGrowth(
            table=str(row.qualified),
            live_tuples=int(row.n_live_tup),
            dead_tuples=int(row.n_dead_tup),
            vacuum_trigger_at=int(row.vacuum_trigger_at),
            heap_bytes=int(row.heap_bytes),
            index_bytes=int(row.index_bytes),
            autovacuum_count=int(row.autovacuum_count),
            last_autovacuum=row.last_autovacuum,
            last_autoanalyze=row.last_autoanalyze,
            has_own_settings=bool(row.has_own_settings),
        )
        for row in rows
    ]


async def shm_headroom(conn: AsyncConnection) -> tuple[int, int]:
    """``(maintenance_work_mem bytes, max_parallel_maintenance_workers)`` --
    the two numbers that decide whether a manual ``VACUUM`` can run at all in a
    container (module docstring, trap 3). The size of ``/dev/shm`` itself is not
    readable from SQL, so this reports what Postgres will ASK for and leaves the
    comparison to the operator and to ``docker-compose.yml``'s own comment."""
    mem = await conn.scalar(
        text("SELECT setting::bigint * 1024 FROM pg_settings WHERE name = 'maintenance_work_mem'")
    )
    workers = await conn.scalar(
        text("SELECT setting::int FROM pg_settings WHERE name = 'max_parallel_maintenance_workers'")
    )
    return int(mem or 0), int(workers or 0)


def _mb(value: int) -> str:
    return f"{value / 1048576:,.0f}M"


def _render_table(rows: Sequence[TableGrowth]) -> str:
    columns = (
        f"{'table':<32}  {'live':>12}  {'dead':>11}  {'trigger':>11}  "
        f"{'ratio':>6}  {'total':>8}  {'b/row':>7}  {'autovac':>7}  own"
    )
    lines = [columns, "-" * len(columns)]
    for row in rows:
        lines.append(
            f"{row.table:<32}  {row.live_tuples:>12,}  {row.dead_tuples:>11,}  "
            f"{row.vacuum_trigger_at:>11,}  {row.trigger_ratio:>6.2f}  "
            f"{_mb(row.total_bytes):>8}  {row.bytes_per_row:>7,.0f}  "
            f"{row.autovacuum_count:>7,}  {'yes' if row.has_own_settings else 'NO'}"
        )
    return "\n".join(lines)


def _render_json(rows: Sequence[TableGrowth]) -> str:
    payload = [
        {
            **asdict(row),
            "last_autovacuum": row.last_autovacuum.isoformat() if row.last_autovacuum else None,
            "last_autoanalyze": row.last_autoanalyze.isoformat() if row.last_autoanalyze else None,
            "total_bytes": row.total_bytes,
            "bytes_per_row": round(row.bytes_per_row, 1),
            "trigger_ratio": round(row.trigger_ratio, 3),
        }
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def warnings(rows: Sequence[TableGrowth], *, maintenance_bytes: int, workers: int) -> list[str]:
    """Everything the table above understates. Printed to stderr so ``--json``'s
    stdout stays a document worth archiving (``slow_queries``' split)."""
    notes: list[str] = []

    unmanaged = [row.table for row in rows if not row.has_own_settings]
    if unmanaged:
        verb = "use" if len(unmanaged) > 1 else "uses"
        notes.append(
            f"⚠️  {', '.join(unmanaged)} still {verb} the SERVER-WIDE autovacuum trigger, whose "
            "scale factor rises with the table: the bigger the backlog, the longer it is allowed "
            "to rot (`ح-16`). Capacity step 2.8's migrations set per-table thresholds -- "
            "`platform@head` and `-x vts=usage usage@head` (python -m app.ops.provision)."
        )

    behind = [row for row in rows if row.trigger_ratio > 1.0]
    if behind:
        notes.append(
            "⚠️  "
            + "; ".join(
                f"{row.table} is at {row.trigger_ratio:.1f}x its vacuum trigger "
                f"({row.dead_tuples:,} dead vs {row.vacuum_trigger_at:,})"
                for row in behind
            )
            + ". Autovacuum is due and has not finished. Every dead tuple above the trigger is "
            "file the table will not give back once a vacuum finally runs -- VACUUM returns space "
            "to the free space map, not to the filesystem."
        )

    never = [row.table for row in rows if row.never_vacuumed and row.live_tuples > 0]
    if never:
        have = "have" if len(never) > 1 else "has"
        notes.append(
            f"⚠️  {', '.join(never)} {have} never been autovacuumed. On a table that takes "
            "writes this is the `ح-16` failure itself, not a quiet table: check the ratio column."
        )

    if workers > 0:
        # Stated as a REQUIREMENT, not as a diagnosis: the size of /dev/shm is
        # not readable from SQL (see `shm_headroom`), so this cannot say whether
        # the ceiling is met -- only what will be asked for. Docker's default is
        # 64M, which is below the DEFAULT maintenance_work_mem, so the check is
        # worth printing even on a stack that has already been fixed.
        notes.append(
            f"NOTE: a manual VACUUM here needs /dev/shm > ~{_mb(maintenance_bytes)} "
            f"(maintenance_work_mem, with {workers} parallel maintenance workers enabled); "
            "below that it fails with `could not resize shared memory segment` on any table big "
            "enough to recruit a worker. Docker's default is 64M; docker-compose.yml's `postgres` "
            "service sets `shm_size` above it. Check with `docker compose exec postgres df -h "
            "/dev/shm` -- a cluster started before that setting was added still has the old "
            "ceiling until `docker compose up -d postgres`."
        )
    return notes


async def vacuum_table(engine: AsyncEngine, table: str) -> None:
    """``VACUUM (ANALYZE)`` one table, outside a transaction (VACUUM cannot run
    inside one), with parallel maintenance DISABLED for the duration.

    Disabling it is not a preference. Parallel maintenance draws its segment
    from ``/dev/shm``, and this is the tool an operator reaches for when a table
    has ALREADY bloated -- i.e. exactly when the table is large enough to
    recruit workers and the request is large enough to fail. Measured: with the
    default two workers the statement failed instantly on a 2,000,000-row table;
    with ``max_parallel_maintenance_workers = 0`` the identical VACUUM finished
    in 2.36 s. A catch-up that works everywhere beats one that is faster where
    it works.
    """
    # AUTOCOMMIT rather than a raw DBAPI connection: `engine.begin()` opens a
    # transaction and VACUUM refuses to run inside one ("VACUUM cannot run
    # inside a transaction block", measured while building this step).
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        await conn.execute(text("SET max_parallel_maintenance_workers = 0"))
        # `table` is constrained to `WATCHED` by the parser's own `choices=`,
        # so it is a module constant reaching SQL, never caller text.
        await conn.execute(text(f"VACUUM (ANALYZE) {table}"))


async def _run(args: argparse.Namespace) -> int:
    engine = create_engine(DatabaseSettings(url=load_settings().database.url), poolclass=NullPool)
    try:
        if args.action == "vacuum":
            if not args.yes:
                raise SystemExit(
                    f"refusing to VACUUM {args.table} without --yes: on a large table it holds a "
                    "SHARE UPDATE EXCLUSIVE lock for its duration and CANCELS any autovacuum "
                    "already running on the same table."
                )
            await vacuum_table(engine, args.table)
            _logger.info("ops.table_growth.vacuumed", extra={"table": args.table})
            print(f"VACUUM (ANALYZE) {args.table} -- done.")
            return 0

        async with engine.begin() as conn:
            rows = await growth(conn, WATCHED)
            maintenance_bytes, workers = await shm_headroom(conn)

        print(_render_json(rows) if args.json else _render_table(rows))
        for note in warnings(rows, maintenance_bytes=maintenance_bytes, workers=workers):
            print(note, file=sys.stderr)
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.table_growth",
        description="Whether autovacuum is keeping up with the write-heavy ledgers "
        "(capacity step 2.8 / ح-16).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    status = sub.add_parser("status", help="dead tuples vs each table's OWN vacuum trigger")
    status.add_argument("--json", action="store_true", help="machine-readable, for archiving")

    vac = sub.add_parser("vacuum", help="manual catch-up for a table that has fallen behind")
    vac.add_argument("--table", required=True, choices=WATCHED)
    vac.add_argument(
        "--yes", action="store_true", help="required: takes a lock, cancels autovacuum"
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(asyncio.run(_run(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
