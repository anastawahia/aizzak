"""Monitoring plan phase 2, row 2, against a real cluster: what the
`metrics_exporter` role can and cannot do
(docs/delivery/monitoring-host-postgres/design.md §4, §5-ج).

The role is created by `deploy/postgres/initdb/15-metrics-exporter.sh`. On a
fresh volume it is born WITHOUT a password (its password is set by the same
script run by hand, 08-local-runbook §3.3-ج), so the first four tests read the
facts from the CATALOG through the owner engine the shared gate hands out,
using the functions Postgres itself decides with (`has_table_privilege`,
`pg_has_role`, `inherit_option`, `rolconfig`). The last three must BE the role:
they open their own connection with `TEST_DATABASE_URL_METRICS_EXPORTER` and
skip alone when it cannot log in -- the `test_backup_live.py` precedent, so the
shared `live_db` gate is not widened for one role.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_ROLE = "metrics_exporter"
_DSN = os.environ.get(
    "TEST_DATABASE_URL_METRICS_EXPORTER",
    f"postgresql+asyncpg://{_ROLE}:change-me-metrics-exporter@127.0.0.1:15432/aizzak_test",
)
_QUERIES = Path(__file__).resolve().parents[2] / "deploy" / "postgres-exporter" / "queries.yaml"
_MISSING = (
    f"role {_ROLE} does not exist on this cluster -- run "
    "docs/design/08-local-runbook.md §3.3-ج step 2 (15-metrics-exporter.sh)"
)


@pytest.fixture
async def owner_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _role_row(engine: AsyncEngine) -> Any:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreaterole, rolreplication, "
                    "rolcreatedb, rolcanlogin, rolconnlimit, rolconfig "
                    "FROM pg_roles WHERE rolname = :r"
                ),
                {"r": _ROLE},
            )
        ).one_or_none()
    assert row is not None, _MISSING
    return row


# ------------------------------------------------- from the catalog (CI) --


async def test_the_role_holds_no_dangerous_attribute(owner_engine: AsyncEngine) -> None:
    row = await _role_row(owner_engine)
    assert (row.rolsuper, row.rolbypassrls, row.rolcreaterole) == (False, False, False)
    assert (row.rolreplication, row.rolcreatedb) == (False, False)
    assert row.rolcanlogin is True
    assert row.rolconnlimit == 2


async def test_the_role_is_bounded_by_its_own_settings(owner_engine: AsyncEngine) -> None:
    row = await _role_row(owner_engine)
    config = set(row.rolconfig or [])
    assert {
        "statement_timeout=5s",
        "lock_timeout=1s",
        "default_transaction_read_only=on",
    } <= config


async def test_pg_monitor_is_its_only_membership_and_it_inherits(
    owner_engine: AsyncEngine,
) -> None:
    """The PG16 trap: under NOINHERIT a membership is inert unless the grant
    itself carries `inherit_option`. Without it `pg_stat_activity` shows the
    exporter `<insufficient privilege>` for every other session."""
    await _role_row(owner_engine)
    async with owner_engine.connect() as conn:
        members = (
            await conn.execute(
                text(
                    "SELECT g.rolname, m.inherit_option FROM pg_auth_members m "
                    "JOIN pg_roles g ON g.oid = m.roleid "
                    "JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = :r"
                ),
                {"r": _ROLE},
            )
        ).all()
        reads_stats = (
            await conn.execute(
                text("SELECT pg_has_role(:r, 'pg_read_all_stats', 'USAGE')"), {"r": _ROLE}
            )
        ).scalar_one()
    assert [(m.rolname, m.inherit_option) for m in members] == [("pg_monitor", True)]
    assert reads_stats is True


async def test_it_can_read_no_table_and_write_none(owner_engine: AsyncEngine) -> None:
    await _role_row(owner_engine)
    async with owner_engine.connect() as conn:
        offending = (
            (
                await conn.execute(
                    text(
                        "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relkind IN ('r', 'p') "
                        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                        "AND n.nspname NOT LIKE 'pg_toast%' "
                        "AND has_table_privilege(:r, c.oid, "
                        "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                    ),
                    {"r": _ROLE},
                )
            )
            .scalars()
            .all()
        )
        schema_usage = (
            await conn.execute(
                text("SELECT has_schema_privilege(:r, 'workspace', 'USAGE')"), {"r": _ROLE}
            )
        ).scalar_one()
    assert offending == [], f"{_ROLE} holds table privileges on {offending}"
    assert schema_usage is False


# ------------------------------------------- as the role itself (skips) --


@pytest.fixture
async def exporter_engine(owner_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    await _role_row(owner_engine)
    engine = create_engine(DatabaseSettings(url=_DSN), poolclass=NullPool)
    try:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            pytest.skip(f"{_ROLE} cannot log in with TEST_DATABASE_URL_METRICS_EXPORTER: {exc}")
        yield engine
    finally:
        await engine.dispose()


async def test_it_is_refused_every_tenant_table(exporter_engine: AsyncEngine) -> None:
    async with exporter_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(text("SELECT 1 FROM workspace.users"))
    async with exporter_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(text("INSERT INTO platform.outbox DEFAULT VALUES"))


async def test_it_sees_other_sessions_through_pg_stat_activity(
    owner_engine: AsyncEngine, exporter_engine: AsyncEngine
) -> None:
    async with owner_engine.connect() as owner:
        pid = (await owner.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        async with exporter_engine.connect() as conn:
            query = (
                await conn.execute(
                    text("SELECT query FROM pg_stat_activity WHERE pid = :p"), {"p": pid}
                )
            ).scalar_one()
    assert query != "<insufficient privilege>"


async def test_every_custom_exporter_query_runs_under_the_role(
    exporter_engine: AsyncEngine,
) -> None:
    queries = yaml.safe_load(_QUERIES.read_text(encoding="utf-8"))
    assert queries
    async with exporter_engine.connect() as conn:
        for name, spec in queries.items():
            rows = (await conn.execute(text(spec["query"]))).all()
            assert len(rows) == 1, f"{name} must yield one row, got {len(rows)}"
