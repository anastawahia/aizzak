"""Hermetic tests for the retention sweep tool (``app.ops.retention``, P1-5,
``docs/p1-hardening-plan.md`` §3 step 8).

Everything here runs over ``_FakeConnection``/``_FakeEngine``, minimal
duck-typed stand-ins for ``sqlalchemy.ext.asyncio``'s
``AsyncConnection``/``AsyncEngine`` (the ``test_ops_dlq.py``
``_StubRedis`` precedent: stub the client, never touch a real database).
What this file proves: which SQL each verb issues (``DELETE`` vs
``SELECT count(*)``, and which predicate column), the cutoff arithmetic, and
the CLI's own argument validation. The live round trip against real
Postgres -- the ONE proof a hermetic stub structurally cannot make, "deletes
what is past the cutoff and does not delete what is not" -- lives in
``tests/integration/test_retention_ops_live.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ops import retention as retention_module
from app.ops.retention import (
    IDEMPOTENCY_KEYS_RETENTION,
    OUTBOX_RETENTION,
    PROCESSED_EVENTS_RETENTION,
    sweep_all,
    sweep_idempotency_keys,
    sweep_outbox,
    sweep_processed_events,
)

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


class _FakeResult:
    def __init__(self, *, rowcount: int, scalar: int) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one(self) -> int:
        return self._scalar


class _FakeConnection:
    """Records every ``execute`` call's SQL text + params, and hands back a
    fixed rowcount (DELETE path) / scalar count (dry-run SELECT path)."""

    def __init__(self, *, rowcount: int = 0, scalar: int = 0) -> None:
        self._rowcount = rowcount
        self._scalar = scalar
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(rowcount=self._rowcount, scalar=self._scalar)


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeEngine:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self._conn)


def test_the_three_retention_windows_are_all_different() -> None:
    """The plan's own requirement, guarded directly: no single number quietly
    replacing all three (module docstring's whole per-table argument)."""
    windows = {IDEMPOTENCY_KEYS_RETENTION, PROCESSED_EVENTS_RETENTION, OUTBOX_RETENTION}
    assert len(windows) == 3


async def test_sweep_outbox_issues_a_delete_scoped_to_published_rows_only() -> None:
    conn = _FakeConnection(rowcount=3, scalar=99)
    result = await sweep_outbox(conn, retention=timedelta(days=90), now=_NOW)

    assert result.table == "outbox"
    assert result.dry_run is False
    assert result.affected == 3
    assert result.cutoff == _NOW - timedelta(days=90)
    [(sql, params)] = conn.calls
    assert sql.startswith("DELETE FROM platform.outbox")
    assert "published_at IS NOT NULL" in sql
    assert "published_at < :cutoff" in sql
    assert params == {"cutoff": _NOW - timedelta(days=90)}


async def test_sweep_outbox_dry_run_counts_instead_of_deleting() -> None:
    conn = _FakeConnection(rowcount=3, scalar=99)
    result = await sweep_outbox(conn, retention=timedelta(days=90), dry_run=True, now=_NOW)

    assert result.dry_run is True
    assert result.affected == 99  # the SELECT count(*) scalar, not rowcount
    [(sql, _)] = conn.calls
    assert sql.startswith("SELECT count(*) FROM platform.outbox")
    assert "DELETE" not in sql


async def test_sweep_processed_events_keys_on_processed_at() -> None:
    conn = _FakeConnection(rowcount=5, scalar=0)
    result = await sweep_processed_events(conn, retention=timedelta(days=30), now=_NOW)

    assert result.table == "processed_events"
    assert result.affected == 5
    assert result.cutoff == _NOW - timedelta(days=30)
    [(sql, _)] = conn.calls
    assert sql == "DELETE FROM platform.processed_events WHERE processed_at < :cutoff"


async def test_sweep_idempotency_keys_keys_on_created_at() -> None:
    conn = _FakeConnection(rowcount=2, scalar=0)
    result = await sweep_idempotency_keys(conn, retention=timedelta(days=2), now=_NOW)

    assert result.table == "idempotency_keys"
    assert result.affected == 2
    assert result.cutoff == _NOW - timedelta(days=2)
    [(sql, _)] = conn.calls
    assert sql == "DELETE FROM platform.idempotency_keys WHERE created_at < :cutoff"


async def test_sweep_all_sweeps_every_table_each_in_its_own_transaction() -> None:
    conn = _FakeConnection(rowcount=1, scalar=0)
    engine = _FakeEngine(conn)

    results = await sweep_all(engine, dry_run=False)

    assert [r.table for r in results] == ["outbox", "processed_events", "idempotency_keys"]
    assert all(r.affected == 1 for r in results)
    # Three DELETEs, one per table, each on the SAME (stubbed) connection.
    assert len(conn.calls) == 3


def test_cli_parses_table_and_override_and_dry_run_together() -> None:
    parser = retention_module._build_parser()
    args = parser.parse_args(["sweep", "--table", "outbox", "--older-than-days", "10", "--dry-run"])
    assert args.table == "outbox"
    assert args.older_than_days == 10
    assert args.dry_run is True


def test_cli_defaults_to_all_tables_and_no_override() -> None:
    parser = retention_module._build_parser()
    args = parser.parse_args(["sweep"])
    assert args.table is None
    assert args.older_than_days is None
    assert args.dry_run is False


def test_cli_rejects_an_unknown_table_name() -> None:
    parser = retention_module._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sweep", "--table", "bogus"])


def test_cli_refuses_older_than_days_without_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module docstring's core CLI rule: there is no single override for
    all three tables' windows at once."""
    monkeypatch.setattr("sys.argv", ["app.ops.retention", "sweep", "--older-than-days", "5"])
    with pytest.raises(SystemExit) as raised:
        retention_module.main()
    assert "--table" in str(raised.value)


def test_cli_requires_a_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.retention"])
    with pytest.raises(SystemExit):
        retention_module.main()
