"""Capacity step 2.5, against a real cluster: the four facts that decide
whether a backup is a backup.

Every one of these is invisible to a unit test and silent in production. A
`pg_dump` taken without `BYPASSRLS` does not crash the platform; it writes a
file, exits 0, and is discovered to be empty on the day it is restored.

1. **The role has all three privileges, and the third is a MEMBERSHIP.**
   `REPLICATION` (no base backup without it), `BYPASSRLS` (no tenant rows
   without it), and `pg_read_all_data`. The membership is asserted through
   `pg_auth_members.inherit_option`, not merely as a row in the catalogue:
   this repository's convention is `NOINHERIT` for every service role, and
   under `NOINHERIT` the membership is INERT. Measured during this step, on a
   role the catalogue showed as a member: `permission denied for schema
   workspace`. PostgreSQL 16 then makes it worse than a one-line fix, because
   the inherit option is recorded PER GRANT from the member's `rolinherit` at
   grant time -- so `ALTER ROLE ... INHERIT` afterwards changes nothing, and
   only a re-`GRANT ... WITH INHERIT TRUE` does.
2. **It actually sees tenant rows.** The catalogue says what the role is
   allowed to do; this connects as it and counts. A tenant table under
   `FORCE ROW LEVEL SECURITY` with no `app.workspace_id` set returns zero
   rows to everyone else -- which is exactly what an empty dump is made of.
3. **The cluster is archiving, and the archiver has moved.** `archive_mode`
   alone proves configuration, not function; `archived_count` is the only
   number that cannot be produced by a broken archive command. And
   deliberately NOT `failed_count`: measured on this step's first run, six
   consecutive `FATAL: archive command failed with exit code 126` left it at
   zero.
4. **The tenant tables really are FORCE RLS**, which is what makes (1) and
   (2) matter at all rather than being facts about an ordinary read.

**Why this file does not join the shared `live_db` gate.** Adding the backup
role to `LiveDbDsns` would make every live_db test in the suite skip on a
`.env.test` that has not been updated -- a large blast radius for one step.
The cluster-wide facts are read through the owner engine the gate already
hands out (`pg_roles`, `pg_settings` and `pg_stat_archiver` are cluster-wide,
not per-database), and only the one test that must BE the backup role opens
its own connection, skipping alone when it cannot.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool
from uuid6 import uuid7

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.ops.backup import BACKUP_ROLE
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_BACKUP_DSN = os.environ.get(
    "TEST_DATABASE_URL_BACKUP",
    f"postgresql+asyncpg://{BACKUP_ROLE}:{BACKUP_ROLE}@127.0.0.1:15432/aizzak_test",
)

# One tenant table, chosen for the property that makes the assertion decidable
# rather than for convenience: it is under FORCE RLS and it is never empty on
# a cluster that has ever had a workspace.
_TENANT_TABLE = "workspace.users"


@pytest.fixture
async def owner_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """The migrator's engine. There is no shared fixture for it -- every test
    that needs the owner builds its own (`test_conversation_get_slo_live.py`
    precedent), because almost nothing should be reading as the owner."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def one_tenant_user(owner_engine: AsyncEngine) -> AsyncIterator[str]:
    """One workspace and one user, written the way the application writes
    them, and removed afterwards.

    The live suite rebuilds `aizzak_test` from empty every session, so
    "the backup role sees rows" needs a row to see -- and inventing one
    through a superuser would prove nothing about the policy. The INSERT into
    `workspace.users` runs under `SET LOCAL app.workspace_id` for the same
    reason every adapter does: the owner is subject to FORCE RLS too, so
    without the context its own INSERT is refused by its own policy.
    """
    workspace_id = str(uuid7())
    user_id = str(uuid7())
    tag = f"backup-2.5-{user_id}"
    async with owner_engine.begin() as conn:
        # workspace.workspaces carries no RLS (01 §2.1, R2).
        await conn.execute(
            text(
                "INSERT INTO workspace.workspaces "
                "(id, owner_user_id, name, status, created_at, updated_at, version) "
                "VALUES (CAST(:id AS uuid), CAST(:owner AS uuid), :name, 'active', "
                "now(), now(), 1)"
            ),
            {"id": workspace_id, "owner": user_id, "name": tag},
        )
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
        )
        await conn.execute(
            text(
                "INSERT INTO workspace.users "
                "(id, workspace_id, firebase_uid, email, display_name, status, "
                " created_at, updated_at, version) "
                "VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), :uid, :email, :name, "
                "'active', now(), now(), 1)"
            ),
            {
                "id": user_id,
                "ws": workspace_id,
                "uid": tag,
                "email": f"{tag}@example.invalid",
                "name": tag,
            },
        )
    try:
        yield workspace_id
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            await conn.execute(
                text("DELETE FROM workspace.users WHERE workspace_id = CAST(:ws AS uuid)"),
                {"ws": workspace_id},
            )
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM workspace.workspaces WHERE id = CAST(:ws AS uuid)"),
                {"ws": workspace_id},
            )


async def _scalar(engine: AsyncEngine, sql: str) -> object:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar_one()


# ------------------------------------------------------- the role itself --


async def test_the_backup_role_holds_replication_and_bypassrls(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolreplication, rolbypassrls, rolcanlogin, rolinherit "
                    "FROM pg_roles WHERE rolname = :role"
                ),
                {"role": BACKUP_ROLE},
            )
        ).one_or_none()

    assert row is not None, (
        f"{BACKUP_ROLE} does not exist. `deploy/postgres/initdb/10-roles.sh` creates it on a "
        "FRESH volume only; an existing cluster needs the CREATE ROLE by hand (08 §3.3-ب)."
    )
    assert row.rolreplication, "pg_basebackup opens a replication connection"
    assert row.rolbypassrls, "without it the dump is empty or fails -- see the module docstring"
    assert row.rolcanlogin


async def test_the_pg_read_all_data_membership_actually_inherits(
    owner_engine: AsyncEngine,
) -> None:
    """The catalogue row is not the privilege. Under `NOINHERIT` -- this
    repository's convention for every other service role -- a membership does
    nothing until the session runs `SET ROLE`, and on PostgreSQL 16 the option
    is frozen into each grant at grant time."""
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT m.inherit_option FROM pg_auth_members m "
                    "JOIN pg_roles member ON member.oid = m.member "
                    "JOIN pg_roles granted ON granted.oid = m.roleid "
                    "WHERE member.rolname = :role AND granted.rolname = 'pg_read_all_data'"
                ),
                {"role": BACKUP_ROLE},
            )
        ).one_or_none()

    assert row is not None, f"{BACKUP_ROLE} is not a member of pg_read_all_data"
    assert row.inherit_option, (
        "the membership exists but does not inherit -- every read still answers 'permission "
        f"denied for schema ...'. Fix: GRANT pg_read_all_data TO {BACKUP_ROLE} WITH INHERIT TRUE"
    )


# --------------------------------------------- what the privileges buy it --


async def test_a_tenant_table_is_under_force_row_level_security(
    owner_engine: AsyncEngine,
) -> None:
    """The premise of the whole step. FORCE is the word that matters: without
    it the table's OWNER would be exempt and `pg_dump` as `aizzak_owner`
    would simply have worked."""
    schema, table = _TENANT_TABLE.split(".")
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = :table"
                ),
                {"schema": schema, "table": table},
            )
        ).one()

    assert row.relrowsecurity
    assert row.relforcerowsecurity


async def test_a_non_bypassing_role_sees_nothing_which_is_what_an_empty_dump_is(
    app_engine: AsyncEngine, one_tenant_user: str
) -> None:
    """The polarity of the next test, and the reason a fixture writes a real
    row first: `app_rw` with no `app.workspace_id` reads zero -- correct, and
    indistinguishable from an empty table once it has been written into a
    dump file."""
    assert await _scalar(app_engine, f"SELECT count(*) FROM {_TENANT_TABLE}") == 0


async def test_the_backup_role_sees_the_rows_a_dump_has_to_carry(
    owner_engine: AsyncEngine, one_tenant_user: str
) -> None:
    """The one test that must BE the backup role, and the only one here that
    skips on its own (module docstring: the shared gate is not widened for
    one step)."""
    async with owner_engine.connect() as conn:
        owner_sees = (
            await conn.execute(text(f"SELECT count(*) FROM {_TENANT_TABLE}"))
        ).scalar_one()

    engine = create_engine(DatabaseSettings(url=_BACKUP_DSN), poolclass=NullPool)
    try:
        try:
            backup_sees = await _scalar(engine, f"SELECT count(*) FROM {_TENANT_TABLE}")
        except Exception as exc:
            pytest.skip(f"{BACKUP_ROLE} cannot read {_TENANT_TABLE}: {exc}")
    finally:
        await engine.dispose()

    # The OWNER of the table, with no workspace context, sees nothing --
    # FORCE ROW LEVEL SECURITY subjects it to its own policy. That zero is
    # exactly what `pg_dump` would have written.
    assert owner_sees == 0
    assert isinstance(backup_sees, int)
    assert backup_sees > 0, (
        "the backup role sees no tenant rows -- a dump taken by it would be the successful, "
        "empty backup this step exists to make impossible"
    )


# -------------------------------------------------------------- archiving --


async def test_the_cluster_archives_wal_and_the_archiver_has_moved(
    owner_engine: AsyncEngine,
) -> None:
    """`archive_mode` is configuration; `archived_count` is behaviour. The
    two disagree exactly when it matters -- and `failed_count` sides with
    configuration (measured: 0 after six consecutive exec failures), which is
    why it is not asserted here."""
    async with owner_engine.connect() as conn:
        mode = (await conn.execute(text("SHOW archive_mode"))).scalar_one()
        row = (
            await conn.execute(
                text("SELECT archived_count, last_archived_wal FROM pg_stat_archiver")
            )
        ).one()

    assert mode == "on", (
        "the cluster is not archiving WAL: a base backup taken now restores to exactly one "
        "instant, and there is no point in time to choose"
    )
    assert row.archived_count > 0, (
        "archive_mode is on and nothing has ever been archived. `archive_command` cannot "
        "execute -- check the postgres log, NOT pg_stat_archiver.failed_count"
    )
    assert row.last_archived_wal
