"""Slowest-query report off ``pg_stat_statements`` -- the measurement half of
capacity step 0.4 (``docs/capacity-plan.md`` §5, Wave 0).

**What was missing.** The plan's own Wave 2 step 2.4 ("review the five most
expensive queries with ``EXPLAIN (ANALYZE, BUFFERS)``") names *this report*
as its input, and step 2.1 lists ``shared_preload_libraries=
pg_stat_statements`` among the Postgres settings it will compose. Until now
neither existed: the extension was not loaded, so there was nothing to read,
and no ``python -m app.ops.*`` entrypoint read it. An operator asking "which
statement is actually costing us the p95" had ``pg_stat_activity`` -- a
snapshot of what is running *this instant*, which under load is the query
that happens to be unlucky, not the query that is expensive.

**Why the extension belongs in Wave 0 and the rest of 2.1 does not.**
``docker-compose.yml``'s ``postgres`` service now starts with exactly three
``-c`` overrides, all three about *observing* the server, none about tuning
it: ``shared_preload_libraries``, ``pg_stat_statements.max``,
``pg_stat_statements.track``. ``shared_buffers``/``work_mem``/
``max_wal_size`` and the rest of 2.1's list stay unset deliberately -- the
plan's own risk ``م-8`` is "tuning starts before the baseline exists", and a
baseline measured on a *tuned* server cannot answer "did the tuning help".
The measuring instrument is installed first; the knobs it will justify are
turned later, in 2.1, where the composed config file replaces this
``command:``.

**Two verbs, the ``app.ops.dlq``/``app.ops.retention`` shape (no HTTP
surface, no scheduling, no metric export -- exporting the top-N as a metric
would be a per-query label, the unbounded-cardinality trap 0.2 was written
around):**

* ``top``   -- the ranked report. Reads only; ``pg_stat_statements`` is a
  view over shared memory and nothing here writes to it.
* ``reset`` -- zeroes the accumulated statistics, gated on an explicit
  ``--yes``. This is what makes a report *attributable to one load run*:
  ``reset`` before ``deploy/load/run.sh peak``, ``top`` after, and every
  millisecond in the report was spent by that run. Without it the numbers
  are "since the server last started", which mixes the run with every
  migration, smoke test and idle poll that preceded it.

**Pitfall 1 -- a row whose text is ``<insufficient privilege>`` is not a
report.** ``pg_stat_statements`` shows every role's rows to everyone, but
shows the *query text* only for statements the reading role ran itself,
unless that role holds ``pg_read_all_stats``. Since every request-path
statement is executed as ``app_rw``, a reader without that membership gets a
ranked list of anonymous rows: the exact shape of a working report, saying
nothing. ``deploy/postgres/initdb/20-extensions.sh`` therefore grants
``pg_read_all_stats`` to ``aizzak_owner``, and ``top`` *counts* the censored
rows it got back and says so on stderr rather than printing them as if they
were an answer.

  Granting that membership to ``aizzak_owner`` rather than minting an eighth
  login role is a deliberate departure from the ``retention_sweeper``/
  ``transit_rotator`` precedent, and the asymmetry is real: those roles exist
  because their tools *write* (``DELETE``, ``UPDATE``) and ``app_rw``'s own
  least-privilege grants must not be widened to let them. This tool only
  reads, and it reads *statistics about* tables whose every row
  ``aizzak_owner`` already owns. A role that can ``ALTER TABLE ... NO FORCE
  ROW LEVEL SECURITY`` on every table in the database gains nothing it did
  not have from being allowed to see a normalised query string. What it does
  gain is the ``provision.py`` footing -- one administrative, never-serves-a-
  request role -- instead of a new password in five files whose only privilege
  is one cluster-level membership and zero object grants.

**Pitfall 2 -- an evicted entry is a missing row, and a missing row is a
wrong ranking.** ``pg_stat_statements`` keeps at most
``pg_stat_statements.max`` entries and discards the least-executed ones when
it overflows, counting each such eviction in ``pg_stat_statements_info.
dealloc``. A report built after an overflow can silently omit the very
statement that caused it. Every ``top`` output therefore carries ``dealloc``
and ``stats_reset`` in its header (and ``--json``'s ``info`` object), and a
non-zero ``dealloc`` prints a warning naming the setting to raise: the
report stays honest about being partial instead of looking complete.

**Pitfall 3 -- the normalised text is not the query the tenant ran.**
Constants are replaced by ``$1`` placeholders, so one row aggregates every
workspace's execution of that statement -- which is exactly what makes it
safe to print (no tenant literal reaches this output) and exactly why 2.4
still has to run ``EXPLAIN`` itself, under ``SET LOCAL app.workspace_id``:
the plan under a tenant's RLS predicate is a different plan, and this report
cannot show it. Rows are also keyed by ``(userid, dbid, queryid, toplevel)``,
so the same SQL run by ``app_rw`` and by ``aizzak_owner`` is two rows, not
one -- ``role`` is a column of the report for that reason.

**Connect DIRECTLY to ``postgres:5432``, not through the pooler.** The
report is most useful *during* a load run, and that is precisely when
``MAX_CLIENT_CONN`` is the resource under test (``ح-3``): a measuring tool
that occupies one of the client slots it is measuring perturbs the
measurement. Direct is also simpler here -- nothing in this module needs a
pooled connection, it opens one, runs one query and closes it.

Usage::

    python -m app.ops.slow_queries top [--limit 20] [--order-by total|mean|calls]
                                       [--all-databases] [--json]
    python -m app.ops.slow_queries reset --yes

``DATABASE_URL`` for THIS process must be ``aizzak_owner``'s OWN DSN, pointed
at ``postgres:5432`` -- the same per-process convention ``provision.py`` and
``app.ops.retention`` document (one role per process, never one role wearing
another's hat).
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
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine

_logger = logging.getLogger(__name__)

EXTENSION = "pg_stat_statements"
DEFAULT_LIMIT = 20

#: What "expensive" is allowed to mean, as a closed set -- the value reaches
#: an ``ORDER BY`` clause, so it is looked up here and never interpolated
#: from the command line.
ORDER_BY: dict[str, str] = {
    # The plan's own wording ("أبطأ 20 استعلاماً بالزمن التراكميّ"): what the
    # server actually spent, calls x cost. A statement run a million times
    # for 2ms outranks one run twice for 3s, and it should -- the first is
    # where the capacity went.
    "total": "s.total_exec_time DESC",
    # The one a cumulative ranking hides: rare and slow. 2.4's EXPLAIN
    # candidates come off both lists, not just the first.
    "mean": "s.mean_exec_time DESC",
    # Not a cost ranking at all -- a chattiness ranking. It is what finds an
    # N+1 in the request path, where no single row looks expensive.
    "calls": "s.calls DESC",
}

_CENSORED = "<insufficient privilege>"


@dataclass(frozen=True, slots=True)
class QueryStat:
    """One ``pg_stat_statements`` row, already ranked. Times are milliseconds
    (the view's own unit), rounded to the microsecond -- the report is read
    against ``07 §2``'s budgets, which are written in milliseconds."""

    rank: int
    queryid: int | None
    role: str
    database: str
    calls: int
    total_exec_ms: float
    mean_exec_ms: float
    max_exec_ms: float
    rows: int
    shared_blks_hit: int
    shared_blks_read: int
    query: str

    @property
    def censored(self) -> bool:
        """``True`` when the reading role was not allowed to see this
        statement's text (module docstring, Pitfall 1)."""
        return self.query == _CENSORED


@dataclass(frozen=True, slots=True)
class StatsInfo:
    """``pg_stat_statements_info`` -- the two numbers that say whether the
    report above it can be trusted (module docstring, Pitfall 2)."""

    dealloc: int
    stats_reset: datetime | None


async def extension_installed(conn: AsyncConnection) -> bool:
    """Whether ``CREATE EXTENSION`` was ever run in THIS database -- asked of
    ``pg_catalog``, never by touching the extension's own view, so a missing
    extension surfaces as this tool's own sentence instead of
    ``UndefinedTable``.

    **The OTHER failure -- loaded extension, unpreloaded server -- is
    deliberately not asked here, and the reason was measured rather than
    assumed.** ``SHOW shared_preload_libraries`` raises
    ``InsufficientPrivilege`` for ``aizzak_owner`` ("only roles with
    privileges of pg_read_all_settings may examine this parameter"), and
    ``SELECT setting FROM pg_settings WHERE name = 'shared_preload_libraries'``
    silently returns ZERO ROWS for the same reason -- indistinguishable from
    a server that genuinely has no preloaded library. Granting
    ``pg_read_all_settings`` to buy a diagnostic nicety would be privilege
    spent on a question the extension already answers itself: an unpreloaded
    ``pg_stat_statements`` raises ``object_not_in_prerequisite_state`` naming
    the setting on the first read of its view, which ``_run`` translates
    (``_PRELOAD_HINT``). The check that cannot be made cheaply is not made at
    all; the error that carries the same information is caught instead.
    """
    return bool(
        await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = :name)"),
            {"name": EXTENSION},
        )
    )


async def stats_info(conn: AsyncConnection) -> StatsInfo:
    row = (
        await conn.execute(text("SELECT dealloc, stats_reset FROM pg_stat_statements_info"))
    ).one()
    return StatsInfo(dealloc=int(row.dealloc), stats_reset=row.stats_reset)


async def top_queries(
    conn: AsyncConnection,
    *,
    limit: int = DEFAULT_LIMIT,
    order_by: str = "total",
    all_databases: bool = False,
) -> list[QueryStat]:
    """The ranked report itself.

    Scoped to the connected database by default: the cluster also carries
    ``aizzak_test`` (and the ``postgres`` maintenance database), whose rows
    would otherwise be ranked alongside the application's own and read as if
    they were production cost. ``all_databases=True`` is the deliberate
    exception, for an operator asking what the *server* is spending.
    """
    scope = (
        ""
        if all_databases
        else "WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())"
    )
    result = await conn.execute(
        text(
            f"""
            SELECT s.queryid,
                   COALESCE(r.rolname, '<dropped role ' || s.userid || '>') AS role,
                   COALESCE(d.datname, '<dropped db ' || s.dbid || '>')     AS database,
                   s.calls,
                   s.total_exec_time,
                   s.mean_exec_time,
                   s.max_exec_time,
                   s.rows,
                   s.shared_blks_hit,
                   s.shared_blks_read,
                   s.query
            FROM pg_stat_statements s
            LEFT JOIN pg_roles r ON r.oid = s.userid
            LEFT JOIN pg_database d ON d.oid = s.dbid
            {scope}
            ORDER BY {ORDER_BY[order_by]}
            LIMIT :limit
            """  # `scope`/ORDER_BY are module constants, never caller text
        ),
        {"limit": limit},
    )
    return [
        QueryStat(
            rank=rank,
            queryid=None if row.queryid is None else int(row.queryid),
            role=str(row.role),
            database=str(row.database),
            calls=int(row.calls),
            total_exec_ms=round(float(row.total_exec_time), 3),
            mean_exec_ms=round(float(row.mean_exec_time), 3),
            max_exec_ms=round(float(row.max_exec_time), 3),
            rows=int(row.rows),
            shared_blks_hit=int(row.shared_blks_hit),
            shared_blks_read=int(row.shared_blks_read),
            query=_normalise_whitespace(str(row.query)),
        )
        for rank, row in enumerate(result.all(), start=1)
    ]


async def reset(conn: AsyncConnection) -> None:
    """Zero every accumulated statistic, for the whole cluster.

    Cluster-wide is not a choice this tool makes -- ``pg_stat_statements``
    keeps ONE shared hash table -- and it is why the CLI demands ``--yes``:
    an operator resetting to attribute their own load run also discards
    whatever another operator was accumulating on the same server.
    """
    await conn.execute(text("SELECT pg_stat_statements_reset()"))


def _normalise_whitespace(query: str) -> str:
    """One line per statement. The stored text keeps the newlines and
    indentation of whatever SQLAlchemy emitted; a report is a table, and a
    row that is fourteen lines tall is not one."""
    return " ".join(query.split())


#: What an operator does about a server that never loaded the library. The
#: two remedies are genuinely different -- a psql command creates a missing
#: extension, but only a CONTAINER RESTART adds a start-time setting -- so
#: they are two messages, never one "something is wrong with
#: pg_stat_statements".
_PRELOAD_HINT = (
    f"{EXTENSION} is not in shared_preload_libraries on this server, so it collects nothing and "
    "every read of its view raises. This is a SERVER START setting: docker-compose.yml's "
    f"`postgres` service passes it as `-c shared_preload_libraries={EXTENSION}` (capacity step "
    "0.4), so a cluster started before that change needs `docker compose up -d postgres` to pick "
    "it up -- restarting the container, not reloading the config."
)

_MISSING_EXTENSION_HINT = (
    f"the {EXTENSION} extension was never created in this database "
    "(deploy/postgres/initdb/20-extensions.sh does it, and initdb scripts run ONLY on a freshly "
    "initialised volume). On an existing cluster, once, as the superuser:\n"
    '  docker compose exec -T postgres psql -U "$POSTGRES_SUPERUSER" -d "$POSTGRES_DB" '
    "-c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements'"
)


def _is_unpreloaded(exc: DBAPIError) -> bool:
    """``ERROR: pg_stat_statements must be loaded via shared_preload_libraries``
    -- SQLSTATE ``55000`` (``object_not_in_prerequisite_state``), which the
    extension raises from every one of its functions when its hooks were
    never installed. Matched on the SQLSTATE, with the message text as a
    fallback for drivers that do not surface one."""
    sqlstate = getattr(exc.orig, "sqlstate", None) or exc.code
    return sqlstate == "55000" or "shared_preload_libraries" in str(exc.orig)


def _render_table(stats: Sequence[QueryStat], info: StatsInfo, *, query_chars: int) -> str:
    header = (
        f"pg_stat_statements: {len(stats)} row(s) "
        f"· since {info.stats_reset.isoformat() if info.stats_reset else 'unknown'} "
        f"· dealloc={info.dealloc}"
    )
    columns = (
        f"{'#':>3}  {'total_ms':>12}  {'calls':>9}  {'mean_ms':>9}  "
        f"{'max_ms':>9}  {'role':<18}  query"
    )
    lines = [header, columns, "-" * len(columns)]
    for stat in stats:
        query = (
            stat.query if len(stat.query) <= query_chars else stat.query[: query_chars - 1] + "…"
        )
        lines.append(
            f"{stat.rank:>3}  {stat.total_exec_ms:>12,.1f}  {stat.calls:>9,}  "
            f"{stat.mean_exec_ms:>9,.2f}  {stat.max_exec_ms:>9,.2f}  {stat.role:<18}  {query}"
        )
    return "\n".join(lines)


def _render_json(stats: Sequence[QueryStat], info: StatsInfo) -> str:
    payload: dict[str, Any] = {
        "info": {
            "dealloc": info.dealloc,
            "stats_reset": info.stats_reset.isoformat() if info.stats_reset else None,
        },
        "queries": [asdict(stat) for stat in stats],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _warnings(stats: Sequence[QueryStat], info: StatsInfo) -> list[str]:
    """Everything that makes the report above less than it appears. Printed
    to stderr so `--json`'s stdout stays a valid document to archive."""
    notes: list[str] = []
    censored = sum(1 for stat in stats if stat.censored)
    if censored:
        notes.append(
            f"⚠️  {censored} of {len(stats)} rows say `{_CENSORED}` instead of their SQL: this "
            "role may only read the text of statements it ran itself, and every request-path "
            "statement runs as `app_rw`. Grant it `pg_read_all_stats` (20-extensions.sh does "
            "this for aizzak_owner) or run this tool as a role that holds it -- the ranking is "
            "real, the "
            "report is not usable."
        )
    if info.dealloc:
        notes.append(
            f"⚠️  pg_stat_statements has evicted entries {info.dealloc} time(s) since the last "
            "reset (pg_stat_statements_info.dealloc): the table overflowed "
            "`pg_stat_statements.max` and the least-executed statements were discarded, so this "
            "ranking may be missing rows entirely. Raise `pg_stat_statements.max` in "
            "docker-compose.yml's `postgres` command and restart it before trusting a baseline."
        )
    if info.stats_reset is None:
        notes.append(
            "⚠️  stats_reset is NULL -- these counters accumulate since the server started, not "
            "since a load run began. For a report attributable to one run: "
            "`python -m app.ops.slow_queries reset --yes` first."
        )
    return notes


async def _run(args: argparse.Namespace) -> int:
    engine: AsyncEngine = create_engine(
        DatabaseSettings(url=load_settings().database.url), poolclass=NullPool
    )
    try:
        async with engine.begin() as conn:
            if not await extension_installed(conn):
                raise SystemExit(_MISSING_EXTENSION_HINT)

            try:
                if args.action == "reset":
                    await reset(conn)
                    _logger.info("ops.slow_queries.reset", extra={"scope": "cluster"})
                    print("pg_stat_statements reset -- every counter on this SERVER is now zero.")
                    return 0

                stats = await top_queries(
                    conn,
                    limit=args.limit,
                    order_by=args.order_by,
                    all_databases=args.all_databases,
                )
                info = await stats_info(conn)
            except DBAPIError as exc:
                if _is_unpreloaded(exc):
                    raise SystemExit(_PRELOAD_HINT) from exc
                raise

        print(
            _render_json(stats, info)
            if args.json
            else _render_table(stats, info, query_chars=args.query_chars)
        )
        for note in _warnings(stats, info):
            print(note, file=sys.stderr)
        _logger.info(
            "ops.slow_queries.reported",
            extra={
                "rows": len(stats),
                "order_by": args.order_by,
                "dealloc": info.dealloc,
                "censored": sum(1 for stat in stats if stat.censored),
            },
        )
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.slow_queries",
        description="Rank statements by what they actually cost, off pg_stat_statements "
        "(capacity step 0.4 -- module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    top_parser = sub.add_parser("top", help="the ranked report (reads only)")
    top_parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"rows to rank (default {DEFAULT_LIMIT})"
    )
    top_parser.add_argument(
        "--order-by",
        choices=tuple(ORDER_BY),
        default="total",
        help="total = cumulative time (the plan's own ranking, default) · mean = rare-and-slow · "
        "calls = chattiness",
    )
    top_parser.add_argument(
        "--all-databases",
        action="store_true",
        help="rank every database's statements, not just the connected one (aizzak_test included)",
    )
    top_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full report as JSON on stdout -- untruncated query text, for archiving "
        "next to a load run's own result file (0.5)",
    )
    top_parser.add_argument(
        "--query-chars",
        type=int,
        default=120,
        help="truncate query text at N characters in table output (default 120; --json is never "
        "truncated)",
    )

    reset_parser = sub.add_parser(
        "reset", help="zero every counter on the SERVER (run before a load run, not after)"
    )
    reset_parser.add_argument(
        "--yes",
        action="store_true",
        help="required explicit confirmation -- the reset is cluster-wide and irreversible",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "reset" and not args.yes:
        raise SystemExit(
            "reset refused: pass --yes to confirm -- pg_stat_statements keeps ONE hash table for "
            "the whole server, so this discards every statistic every database has accumulated, "
            "including any another operator is mid-measurement on. There is no undo."
        )
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
