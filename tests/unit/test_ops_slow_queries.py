"""Hermetic tests for the slowest-query report (``app.ops.slow_queries``,
capacity step 0.4, ``docs/capacity-plan.md`` §5 Wave 0).

Everything here runs over ``_FakeConnection``, a duck-typed stand-in for
``sqlalchemy.ext.asyncio.AsyncConnection`` (the ``test_ops_retention.py``
precedent: stub the connection, never touch a real database). What this file
proves: which SQL each verb issues and that no caller string ever reaches it,
that the report's warnings fire on exactly the three conditions that make a
report less than it looks, and that the compose/initdb wiring the tool
depends on actually exists in the files it names.

The live round trip -- "``pg_stat_statements`` really answers this SQL, and
the ranking really contains a statement we just ran" -- is the one proof a
stub structurally cannot make, and lives in
``tests/integration/test_slow_queries_ops_live.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ops.slow_queries import (
    DEFAULT_LIMIT,
    ORDER_BY,
    QueryStat,
    StatsInfo,
    _is_unpreloaded,
    _render_json,
    _render_table,
    _warnings,
    extension_installed,
    reset,
    top_queries,
)
from app.ops.slow_queries import main as slow_queries_main

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_EXTENSIONS_SH = _REPO_ROOT / "deploy" / "postgres" / "initdb" / "20-extensions.sh"
_TESTDB_SH = _REPO_ROOT / "deploy" / "postgres" / "testdb" / "20-test-database.sh"
_RUNPOD_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_RUNPOD_BOOTSTRAP = _REPO_ROOT / "deploy" / "runpod" / "bootstrap.sh"

_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class _FakeRow:
    def __init__(self, values: dict[str, Any]) -> None:
        self.__dict__.update(values)


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def all(self) -> list[_FakeRow]:
        return self._rows

    def one(self) -> _FakeRow:
        return self._rows[0]


class _FakeConnection:
    """Records every ``execute``/``scalar`` call's SQL text + params and hands
    back whatever rows the test queued."""

    def __init__(self, *, rows: list[_FakeRow] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(self._rows)

    async def scalar(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(stmt), dict(params or {})))
        return self._scalar

    @property
    def sql(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _row(**overrides: Any) -> _FakeRow:
    values: dict[str, Any] = {
        "queryid": 42,
        "role": "app_rw",
        "database": "aizzak",
        "calls": 7,
        "total_exec_time": 12.3456789,
        "mean_exec_time": 1.7636684,
        "max_exec_time": 4.2,
        "rows": 14,
        "shared_blks_hit": 100,
        "shared_blks_read": 3,
        "query": "SELECT\n  conversations.id\nFROM conversations.conversations\nWHERE id = $1",
    }
    values.update(overrides)
    return _FakeRow(values)


def _stat(**overrides: Any) -> QueryStat:
    values: dict[str, Any] = {
        "rank": 1,
        "queryid": 42,
        "role": "app_rw",
        "database": "aizzak",
        "calls": 7,
        "total_exec_ms": 12.346,
        "mean_exec_ms": 1.764,
        "max_exec_ms": 4.2,
        "rows": 14,
        "shared_blks_hit": 100,
        "shared_blks_read": 3,
        "query": "SELECT conversations.id FROM conversations.conversations WHERE id = $1",
    }
    values.update(overrides)
    return QueryStat(**values)


# ---------------------------------------------------------------- the SQL --


@pytest.mark.asyncio
async def test_the_report_is_scoped_to_the_connected_database_by_default() -> None:
    """The cluster also carries ``aizzak_test`` and ``postgres``; ranking
    their statements alongside the application's own would read as if a test
    suite's cost were production cost."""
    conn = _FakeConnection(rows=[_row()])

    await top_queries(conn)  # type: ignore[arg-type]

    assert "WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())" in (
        conn.sql
    )


@pytest.mark.asyncio
async def test_all_databases_drops_the_scope_clause_entirely() -> None:
    conn = _FakeConnection(rows=[_row()])

    await top_queries(conn, all_databases=True)  # type: ignore[arg-type]

    assert "current_database()" not in conn.sql


@pytest.mark.asyncio
async def test_the_limit_is_a_bound_parameter_not_string_interpolation() -> None:
    conn = _FakeConnection(rows=[_row()])

    await top_queries(conn, limit=5)  # type: ignore[arg-type]

    assert ":limit" in conn.sql
    assert conn.calls[0][1] == {"limit": 5}


@pytest.mark.parametrize("order_by", tuple(ORDER_BY))
@pytest.mark.asyncio
async def test_every_offered_ordering_reaches_the_order_by_clause(order_by: str) -> None:
    conn = _FakeConnection(rows=[_row()])

    await top_queries(conn, order_by=order_by)  # type: ignore[arg-type]

    assert f"ORDER BY {ORDER_BY[order_by]}" in conn.sql


@pytest.mark.asyncio
async def test_an_ordering_outside_the_closed_set_never_reaches_sql() -> None:
    """``order_by`` is looked up in a module constant, so an unknown value
    raises before a statement is built -- the difference between a closed set
    and an injection point. ``argparse``'s own ``choices`` is the first
    gate; this is the one that holds if a caller bypasses the CLI."""
    conn = _FakeConnection(rows=[_row()])

    with pytest.raises(KeyError):
        await top_queries(conn, order_by="1; DROP TABLE platform.outbox")  # type: ignore[arg-type]

    assert conn.calls == []


@pytest.mark.asyncio
async def test_extension_installed_asks_the_catalog_not_the_extensions_view() -> None:
    """A missing extension must surface as this tool's own sentence, not as
    ``UndefinedTable`` from touching ``pg_stat_statements`` itself."""
    conn = _FakeConnection(scalar=False)

    assert await extension_installed(conn) is False  # type: ignore[arg-type]
    assert "pg_extension" in conn.sql
    assert "FROM pg_stat_statements" not in conn.sql


@pytest.mark.asyncio
async def test_reset_calls_the_extensions_own_reset_function() -> None:
    conn = _FakeConnection(rows=[])

    await reset(conn)  # type: ignore[arg-type]

    assert "pg_stat_statements_reset()" in conn.sql


# ------------------------------------------------------------- projection --


@pytest.mark.asyncio
async def test_rows_are_ranked_in_the_order_the_server_returned_them() -> None:
    conn = _FakeConnection(rows=[_row(queryid=1), _row(queryid=2), _row(queryid=3)])

    stats = await top_queries(conn)  # type: ignore[arg-type]

    assert [stat.rank for stat in stats] == [1, 2, 3]
    assert [stat.queryid for stat in stats] == [1, 2, 3]


@pytest.mark.asyncio
async def test_query_text_is_flattened_to_one_line() -> None:
    """A report is a table; a row fourteen lines tall is not one. The stored
    text keeps whatever indentation SQLAlchemy emitted."""
    conn = _FakeConnection(rows=[_row()])

    (stat,) = await top_queries(conn)  # type: ignore[arg-type]

    assert "\n" not in stat.query
    assert stat.query.startswith("SELECT conversations.id FROM")


@pytest.mark.asyncio
async def test_times_are_milliseconds_rounded_to_the_microsecond() -> None:
    """The view's own unit, kept -- ``07 §2``'s budgets are written in
    milliseconds, so a report in seconds would need mental arithmetic at
    exactly the moment it is read against a budget."""
    conn = _FakeConnection(rows=[_row(total_exec_time=12.3456789)])

    (stat,) = await top_queries(conn)  # type: ignore[arg-type]

    assert stat.total_exec_ms == 12.346


def test_censored_is_a_pure_projection_of_the_query_text() -> None:
    assert _stat(query="<insufficient privilege>").censored is True
    assert _stat().censored is False


# --------------------------------------------------------------- warnings --


def test_censored_rows_are_reported_as_an_unusable_report_not_printed_as_data() -> None:
    """Pitfall 1: `<insufficient privilege>` rows have the exact shape of a
    working report and say nothing. The count and the remedy go to stderr."""
    stats = [_stat(query="<insufficient privilege>"), _stat(rank=2)]

    notes = _warnings(stats, StatsInfo(dealloc=0, stats_reset=_NOW))

    assert len(notes) == 1
    assert "1 of 2 rows" in notes[0]
    assert "pg_read_all_stats" in notes[0]


def test_a_nonzero_dealloc_warns_that_the_ranking_may_be_missing_rows() -> None:
    """Pitfall 2: an overflow discards the least-executed entries silently,
    and the ranking that results looks complete."""
    notes = _warnings([_stat()], StatsInfo(dealloc=3, stats_reset=_NOW))

    assert len(notes) == 1
    assert "pg_stat_statements.max" in notes[0]


def test_a_null_stats_reset_warns_that_the_numbers_are_not_one_runs() -> None:
    notes = _warnings([_stat()], StatsInfo(dealloc=0, stats_reset=None))

    assert len(notes) == 1
    assert "reset --yes" in notes[0]


def test_a_clean_report_carries_no_warnings() -> None:
    assert _warnings([_stat()], StatsInfo(dealloc=0, stats_reset=_NOW)) == []


# ---------------------------------------------------------------- output --


def test_the_table_truncates_query_text_and_the_json_never_does() -> None:
    """``--json`` is what 0.5 archives next to a load run's own result file:
    a truncated statement there would be a report that cannot be re-read."""
    long_query = "SELECT " + "x" * 400
    stats = [_stat(query=long_query)]
    info = StatsInfo(dealloc=0, stats_reset=_NOW)

    table = _render_table(stats, info, query_chars=60)
    document = json.loads(_render_json(stats, info))

    assert long_query not in table
    assert document["queries"][0]["query"] == long_query


def test_the_report_header_carries_the_two_numbers_that_qualify_it() -> None:
    header = _render_table([_stat()], StatsInfo(dealloc=9, stats_reset=_NOW), query_chars=80)

    assert "dealloc=9" in header
    assert _NOW.isoformat() in header


def test_the_json_document_carries_info_alongside_the_rows() -> None:
    document = json.loads(_render_json([_stat()], StatsInfo(dealloc=0, stats_reset=_NOW)))

    assert document["info"] == {"dealloc": 0, "stats_reset": _NOW.isoformat()}
    assert document["queries"][0]["rank"] == 1


# -------------------------------------------------------------------- CLI --


def test_cli_refuses_reset_without_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reset is cluster-wide -- one shared hash table for every database
    -- so it can discard another operator's in-flight measurement."""
    monkeypatch.setattr("sys.argv", ["app.ops.slow_queries", "reset"])

    with pytest.raises(SystemExit) as raised:
        slow_queries_main()

    assert "--yes" in str(raised.value)


def test_cli_requires_a_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.slow_queries"])

    with pytest.raises(SystemExit):
        slow_queries_main()


def test_the_default_limit_is_the_plans_own_twenty() -> None:
    """`0.4`'s wording: "أبطأ 20 استعلاماً بالزمن التراكميّ"."""
    assert DEFAULT_LIMIT == 20
    assert ORDER_BY["total"].startswith("s.total_exec_time")


# ------------------------------------------------------- error translation --


class _StubOrig(Exception):
    def __init__(self, sqlstate: str | None, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class _StubDbapiError(Exception):
    def __init__(self, orig: Exception, code: str | None = None) -> None:
        super().__init__(str(orig))
        self.orig = orig
        self.code = code


def test_an_unpreloaded_server_is_recognised_by_its_sqlstate() -> None:
    """``55000``/``object_not_in_prerequisite_state`` -- what every
    ``pg_stat_statements`` function raises when the library was never loaded
    at server start."""
    error = _StubDbapiError(_StubOrig("55000", "pg_stat_statements must be loaded via ..."))

    assert _is_unpreloaded(error) is True  # type: ignore[arg-type]


def test_an_unpreloaded_server_is_still_recognised_without_a_sqlstate() -> None:
    error = _StubDbapiError(
        _StubOrig(None, 'ERROR: pg_stat_statements must be loaded via "shared_preload_libraries"')
    )

    assert _is_unpreloaded(error) is True  # type: ignore[arg-type]


def test_an_unrelated_database_error_is_not_swallowed_as_a_preload_problem() -> None:
    """The translation must be narrow: a connection refused, a permission
    error or a syntax error has a different remedy, and printing the preload
    hint for them would send an operator to restart a healthy container."""
    error = _StubDbapiError(_StubOrig("42501", "permission denied for table outbox"))

    assert _is_unpreloaded(error) is False  # type: ignore[arg-type]


# ------------------------------------------------------------ the wiring --


def test_compose_starts_postgres_with_the_extension_preloaded() -> None:
    """The tool is useless against a server that never loaded the library,
    and ``shared_preload_libraries`` is a START-time setting -- so the
    enabling lives in the `postgres` service's own command, not in a
    migration or an init script."""
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert "shared_preload_libraries=pg_stat_statements" in compose
    assert "pg_stat_statements.max=" in compose


def test_compose_does_not_smuggle_wave_2_tuning_into_wave_0() -> None:
    """Risk ``م-8``, guarded mechanically: a baseline measured on an
    already-tuned server cannot answer whether the tuning helped. Step 2.1
    sets these from a composed config file, and this guard is what makes
    adding one early a failing test rather than a quiet head start."""
    compose = _COMPOSE.read_text(encoding="utf-8")
    premature = [
        knob
        for knob in ("shared_buffers=", "work_mem=", "effective_cache_size=", "max_wal_size=")
        if knob in compose
    ]

    assert not premature, (
        f"{premature} appear in docker-compose.yml, but Postgres tuning is capacity step 2.1 "
        "(Wave 2) and the 0.5 baseline must be measured on an UNTUNED server -- otherwise no "
        "later wave can prove it improved anything."
    )


def test_initdb_creates_the_extension_and_grants_the_reader_its_stats() -> None:
    """Both halves matter and each fails differently: without the extension
    the view does not exist, and without ``pg_read_all_stats`` every
    request-path row comes back as `<insufficient privilege>`."""
    script = _EXTENSIONS_SH.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in script
    assert "GRANT pg_read_all_stats TO aizzak_owner" in script


def test_initdb_extension_script_sorts_after_the_roles_script() -> None:
    """Load-bearing ordering, not tidiness: the GRANT names ``aizzak_owner``,
    which ``10-roles.sh`` creates, and the postgres entrypoint sources
    ``/docker-entrypoint-initdb.d/*`` in sort order."""
    assert _EXTENSIONS_SH.name > "10-roles.sh"


def test_the_test_database_gets_the_extension_too() -> None:
    """The statistics are one cluster-wide hash table but the VIEW is
    per-database, so a connection to ``aizzak_test`` cannot read it unless it
    was created there -- which is what the live proof connects over."""
    assert "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in _TESTDB_SH.read_text(
        encoding="utf-8"
    )


def test_the_test_database_is_not_allowed_to_reset_the_servers_statistics() -> None:
    """A suite that could call ``pg_stat_statements_reset`` would erase an
    operator's in-flight measurement on the same server -- the reset is
    cluster-wide, so this grant is deliberately absent here while
    ``20-extensions.sh`` does make it for the operator's own database."""
    assert "GRANT EXECUTE ON FUNCTION pg_stat_statements_reset" not in _TESTDB_SH.read_text(
        encoding="utf-8"
    )


def test_the_runpod_deployment_enables_the_extension_the_same_way() -> None:
    """RunPod is one container that initialises its own cluster -- it never
    reads ``/docker-entrypoint-initdb.d`` and never sees Compose's
    ``command:``. Without both halves said again in its own idiom, ``python
    -m app.ops.slow_queries`` would work on the Compose stack and fail on a
    Pod, which is the kind of asymmetry that is only ever discovered while
    trying to diagnose something else."""
    entrypoint = _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8")
    bootstrap = _RUNPOD_BOOTSTRAP.read_text(encoding="utf-8")

    assert "shared_preload_libraries = 'pg_stat_statements'" in entrypoint
    assert "20-extensions.sh" in bootstrap


def test_the_runpod_bootstrap_creates_the_extension_after_the_roles() -> None:
    """Load-bearing order, the same one the initdb filenames encode: the
    extension script grants ``pg_read_all_stats`` to ``aizzak_owner``, which
    ``10-roles.sh`` has to have created first."""
    bootstrap = _RUNPOD_BOOTSTRAP.read_text(encoding="utf-8")

    assert bootstrap.index("10-roles.sh") < bootstrap.index("20-extensions.sh")
