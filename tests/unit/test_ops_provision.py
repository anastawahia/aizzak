"""The provisioning artifact must not drift from the migrations (7.1).

This is the guard the 7.1 defect argues for, in the shape 6.3 established for
`openapi.yaml`: compare TWO documents bidirectionally, rather than trusting
that whoever edits one remembers the other.

The failure it exists to catch is silent and only appears in a container. No
migration issues a GRANT (01 §6 makes seeding an operational step), so a
module chain added without a matching entry in `app.ops.provision` produces a
schema that migrates perfectly and then answers `permission denied` on the
first request that touches the new table -- and the live test harness, which
provisions through the same constants, would not notice either.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from app.ops.provision import (
    APP_RW_GRANTS,
    METRICS_GRANTS,
    MIGRATION_CHAINS,
    OUTBOX_RELAY_GRANTS,
    PURGE_GRANTS,
    RETENTION_GRANTS,
    TRANSIT_ROTATOR_GRANTS,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO_ROOT / "migrations" / "versions"

# `platform` tables are deliberately NOT full-CRUD, so they are excluded from
# the "every table is granted" sweep and asserted separately below.
# `platform.idempotency_keys` (3.79) is deliberately NOT in this set — it is
# the one platform table that legitimately gets full CRUD, asserted on its own
# below so the exception is stated rather than merely absent.
_PLATFORM_TABLES = frozenset({"platform.outbox", "platform.processed_events"})
# Granted nothing on purpose: 01 §4.3 calls stream_offsets optional
# monitoring, and no code in src/ reads or writes it.
_DELIBERATELY_UNGRANTED = frozenset({"platform.stream_offsets"})

_CREATE_TABLE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+\.[a-z_]+)")


def _tables_in_migrations() -> set[str]:
    found: set[str] = set()
    for path in _MIGRATIONS.rglob("*.py"):
        found.update(_CREATE_TABLE.findall(path.read_text(encoding="utf-8")))
    return found


def _granted_tables() -> set[str]:
    granted: set[str] = set()
    for statement in (*APP_RW_GRANTS, *OUTBOX_RELAY_GRANTS):
        match = re.search(r" ON ([a-z_]+\.[a-z_]+) TO ", statement)
        if match:
            granted.add(match.group(1))
    return granted


def test_the_regex_actually_finds_tables() -> None:
    """A guard built on a regex that matched nothing would pass forever while
    guarding nothing -- the 3.69 lesson."""
    tables = _tables_in_migrations()
    assert len(tables) >= 15, f"expected the v1 schema, found {tables}"
    assert "files.files" in tables


def test_every_migrated_table_is_granted_or_deliberately_not() -> None:
    missing = _tables_in_migrations() - _granted_tables() - _DELIBERATELY_UNGRANTED
    assert not missing, (
        f"tables created by a migration but granted to nobody: {sorted(missing)}. "
        "Add them to app.ops.provision._TENANT_TABLES, or to this test's "
        "_DELIBERATELY_UNGRANTED with a reason."
    )


def test_no_grant_names_a_table_that_does_not_exist() -> None:
    """The other direction: a grant left behind after a table is renamed
    fails the whole provisioning transaction on the next deploy."""
    phantom = _granted_tables() - _tables_in_migrations()
    assert not phantom, f"granted tables that no migration creates: {sorted(phantom)}"


def test_platform_tables_are_not_granted_full_crud() -> None:
    """The app is an outbox PRODUCER (D-18). A producer that could UPDATE
    published_at could make an event vanish unpublished."""
    for table in _PLATFORM_TABLES:
        app_grants = [s for s in APP_RW_GRANTS if f" ON {table} TO " in s]
        assert app_grants, f"{table} lost its app_rw grant"
        for statement in app_grants:
            assert "INSERT" in statement
            assert "UPDATE" not in statement, f"app_rw must not UPDATE {table}: {statement}"
            assert "DELETE" not in statement, f"app_rw must not DELETE {table}: {statement}"
            assert "SELECT" not in statement, f"app_rw must not SELECT {table}: {statement}"


def test_the_idempotency_ledger_is_the_one_platform_table_with_full_crud() -> None:
    """3.79, and it is an exception with a reason rather than an oversight:
    unlike the two ledgers above, this table exists to be READ BACK (the
    stored first response), UPDATEd (filled in after the operation) and
    DELETEd (an unfinished claim released when the operation raised). What
    confines it is RLS — it is the only ``platform`` table carrying a tenant
    policy — not the grant."""
    grants = [s for s in APP_RW_GRANTS if " ON platform.idempotency_keys TO " in s]
    assert len(grants) == 1
    for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert verb in grants[0]


def test_relay_holds_the_mirror_image_on_the_outbox() -> None:
    outbox = [s for s in OUTBOX_RELAY_GRANTS if " ON platform.outbox TO " in s]
    assert len(outbox) == 1
    assert "SELECT" in outbox[0] and "UPDATE" in outbox[0]
    assert "INSERT" not in outbox[0], "the relay must not be able to produce events"


def test_retention_sweeper_gets_select_and_delete_never_insert_or_update() -> None:
    """P1-5 (docs/p1-hardening-plan.md §3 step 8): the retention role must be
    able to shrink the three unbounded ledgers and nothing else -- never able
    to forge or alter a row, on any of the three."""
    for table in (
        "platform.outbox",
        "platform.processed_events",
        "platform.idempotency_keys",
    ):
        matches = [s for s in RETENTION_GRANTS if f" ON {table} TO " in s]
        assert len(matches) == 1, f"{table} missing (or duplicated) in RETENTION_GRANTS"
        assert "SELECT" in matches[0] and "DELETE" in matches[0]
        assert "INSERT" not in matches[0], f"retention_sweeper must not INSERT into {table}"
        assert "UPDATE" not in matches[0], f"retention_sweeper must not UPDATE {table}"


def test_retention_sweeper_does_not_widen_app_rws_own_outbox_or_ledger_grant() -> None:
    """The architectural tension this step must not paper over: adding a
    retention role must leave `app_rw`'s INSERT-only grant on `outbox`/
    `processed_events` completely untouched (`test_platform_tables_are_not_
    granted_full_crud` above already proves this for `APP_RW_GRANTS` alone;
    this asserts the two grant sets are actually disjoint by role name)."""
    for statement in RETENTION_GRANTS:
        assert " TO app_rw" not in statement
    for statement in APP_RW_GRANTS:
        assert " TO retention_sweeper" not in statement


def test_metrics_reader_gets_select_only_on_the_outbox_and_nothing_else() -> None:
    """P1-3 (docs/p1-hardening-plan.md §3 step 10): `/metrics`'s own role must
    be able to read `platform.outbox` and do nothing else -- never INSERT,
    UPDATE or DELETE, and never a reach on `processed_events`/
    `idempotency_keys` (this role has no business enumerating those two)."""
    outbox_grants = [s for s in METRICS_GRANTS if " ON platform.outbox TO " in s]
    assert len(outbox_grants) == 1, "platform.outbox missing (or duplicated) in METRICS_GRANTS"
    assert "SELECT" in outbox_grants[0]
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert verb not in outbox_grants[0], f"metrics_reader must not {verb} platform.outbox"

    for table in ("platform.processed_events", "platform.idempotency_keys"):
        assert not any(f" ON {table} TO " in s for s in METRICS_GRANTS), (
            f"metrics_reader has no business reaching {table} at all"
        )


def test_metrics_reader_does_not_widen_app_rws_own_outbox_grant() -> None:
    """The same architectural tension `RETENTION_GRANTS` resolves, restated for
    the metrics role: adding it must leave `app_rw`'s INSERT-only grant on
    `platform.outbox` completely untouched, and the two grant sets must never
    name each other's role."""
    for statement in METRICS_GRANTS:
        assert " TO app_rw" not in statement
    for statement in APP_RW_GRANTS:
        assert " TO metrics_reader" not in statement


def test_transit_rotator_gets_select_and_column_scoped_update_only() -> None:
    """P1-9 (docs/p1-hardening-plan.md §3 step 12): the rotation sweep's role
    must be able to read every row and overwrite ONLY its ciphertext column
    on each of the three Transit-ciphertext-bearing tables -- never a
    table-wide UPDATE (which would let it forge `status`/`label`/`scopes`),
    never INSERT or DELETE anywhere."""
    expectations = {
        "credentials.credentials": "ciphertext_ref",
        "integrations.connections": "token_ref",
        "integrations.mcp_servers": "auth_ref",
    }
    for table, column in expectations.items():
        matches = [s for s in TRANSIT_ROTATOR_GRANTS if f" ON {table} TO " in s]
        assert len(matches) == 1, f"{table} missing (or duplicated) in TRANSIT_ROTATOR_GRANTS"
        assert "SELECT" in matches[0]
        assert f"UPDATE ({column})" in matches[0], (
            f"transit_rotator's UPDATE on {table} must be scoped to {column} alone"
        )
        assert "INSERT" not in matches[0]
        assert "DELETE" not in matches[0]


def test_transit_rotator_does_not_widen_app_rws_own_grants() -> None:
    """The same architectural tension `RETENTION_GRANTS`/`METRICS_GRANTS`
    resolve, restated for the rotation role: adding it must leave `app_rw`'s
    own full-CRUD grant on the three tables completely untouched, and the two
    grant sets must never name each other's role."""
    for statement in TRANSIT_ROTATOR_GRANTS:
        assert " TO app_rw" not in statement
    for statement in APP_RW_GRANTS:
        assert " TO transit_rotator" not in statement


def test_purge_role_gets_select_delete_on_every_tenant_table_it_empties() -> None:
    """BE-ADM-014: the purge sweep must be able to read and delete every row
    of every table §3.2 names, and nothing else on those tables (no INSERT,
    no forging a row)."""
    tables = (
        "spaces.spaces",
        "workspace.user_presence",
        "access.role_assignments",
        "credentials.credentials",
        "conversations.conversations",
        "conversations.messages",
        "conversations.conversation_files",
        "memory.memory_items",
        "files.files",
        "knowledge.documents",
        "knowledge.chunks",
        "knowledge.reindex_jobs",
        "knowledge.reindex_job_items",
        "knowledge.summaries",
        "knowledge.summary_jobs",
        "media.media_jobs",
        "integrations.connections",
        "integrations.mcp_servers",
        "usage.usage_records",
        "usage.usage_rollups",
        "usage.limits",
    )
    for table in tables:
        matches = [s for s in PURGE_GRANTS if f" ON {table} TO " in s]
        assert len(matches) == 1, f"{table} missing (or duplicated) in PURGE_GRANTS"
        assert "SELECT" in matches[0] and "DELETE" in matches[0]
        assert "INSERT" not in matches[0], f"workspace_purger must not INSERT into {table}"
        assert "UPDATE" not in matches[0], f"workspace_purger must not UPDATE {table}"


def test_purge_role_gets_column_scoped_reach_on_workspaces_and_users_only() -> None:
    """Never a table-wide UPDATE on `workspace.workspaces` (which could
    rename/resurrect a workspace) and never any column of `workspace.users`
    beyond bare state -- this role must never read `email`/`display_name`/
    `firebase_uid`, and must never UPDATE/DELETE `users` at all (the
    tombstone stays `SqlPlatformAccountManager.delete`'s alone)."""
    [workspaces_grant] = [s for s in PURGE_GRANTS if " ON workspace.workspaces TO " in s]
    assert "SELECT (id, owner_user_id, status, purged_at)" in workspaces_grant
    assert "UPDATE (status, purged_at)" in workspaces_grant
    assert "DELETE" not in workspaces_grant

    [users_grant] = [s for s in PURGE_GRANTS if " ON workspace.users TO " in s]
    assert users_grant == (
        "GRANT SELECT (id, workspace_id, status, deleted_at) ON workspace.users TO workspace_purger"
    )
    for leaked in ("email", "display_name", "firebase_uid", "UPDATE", "DELETE", "INSERT"):
        assert leaked not in users_grant


def test_purge_role_gets_select_and_insert_only_on_the_admin_audit_log() -> None:
    [audit_grant] = [s for s in PURGE_GRANTS if " ON platform.admin_audit_log TO " in s]
    assert "SELECT" in audit_grant and "INSERT" in audit_grant
    assert "UPDATE" not in audit_grant and "DELETE" not in audit_grant


def test_purge_role_has_no_reach_on_the_three_unbounded_ledgers() -> None:
    """No grant at all on outbox/processed_events/idempotency_keys -- this
    role has no business enumerating those (module docstring)."""
    for table in ("platform.outbox", "platform.processed_events", "platform.idempotency_keys"):
        assert not any(f" ON {table} TO " in s for s in PURGE_GRANTS)


def test_purge_role_does_not_widen_app_rws_own_grants() -> None:
    """The same architectural tension every other least-privilege role in
    this module resolves: adding `workspace_purger` must leave `app_rw`'s own
    grants completely untouched, and the two grant sets must never name each
    other's role."""
    for statement in PURGE_GRANTS:
        assert " TO app_rw" not in statement
    for statement in APP_RW_GRANTS:
        assert " TO workspace_purger" not in statement


def test_every_alembic_chain_has_a_migration_step() -> None:
    """`alembic upgrade head` is not a command this repo has: eleven chains
    means eleven heads. MIGRATION_CHAINS is the real invocation order, and a
    chain added to alembic.ini without an entry here would simply never be
    applied in a container."""
    # RawConfigParser: alembic.ini uses `%(here)s`, which is Alembic's own
    # substitution, not configparser's -- the interpolating parser rejects it.
    parser = configparser.RawConfigParser()
    parser.read(_REPO_ROOT / "alembic.ini")
    locations = {
        Path(line).name
        for line in parser["alembic"]["version_locations"].splitlines()
        if line.strip()
    }
    stepped = {revision.split("@")[0] for revision, _ in MIGRATION_CHAINS}
    assert locations == stepped, (
        f"alembic.ini and MIGRATION_CHAINS disagree -- "
        f"only in alembic.ini: {sorted(locations - stepped)}; "
        f"only in provision: {sorted(stepped - locations)}"
    )


def test_the_platform_baseline_runs_first_and_without_a_version_schema() -> None:
    """The baseline creates every schema the module chains then populate, and
    it is the one chain that passes no `-x vts=` (its version table lands in
    `public` -- which is why the migrator needs CREATE there)."""
    first_revision, first_vts = MIGRATION_CHAINS[0]
    assert first_revision == "platform@head"
    assert first_vts is None
    assert all(vts is not None for _, vts in MIGRATION_CHAINS[1:])
