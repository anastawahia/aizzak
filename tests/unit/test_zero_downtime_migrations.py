"""Capacity step 2.9 (``ح-18``) — the three things a rolling deploy needs
from a schema change, guarded at the source so none of them can be undone by
an edit that looks harmless.

`ح-18` names three elements and says none of them helps without the other two:
an advisory lock around `app.ops.provision`, a short `lock_timeout` for the
migration itself, and the expand/contract discipline. The measurements that
say each one is real live in `MigrationSettings`, `app.ops.provision`'s module
docstring and `app.ops.online_ddl`; what is here is only what can be checked
without a database.

The live half -- that the lock actually serialises three replicas, that a
blocked migration actually fails at its budget, and that
`create_index_concurrently` actually produces a VALID index -- is
`tests/integration/test_zero_downtime_migrations_live.py`.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from app.framework.settings.settings import MigrationSettings
from app.infrastructure.config.env_settings import _EnvSettings
from app.ops.online_ddl import MAX_BATCHES, backfill_in_batches, create_index_concurrently
from app.ops.provision import _PROVISION_LOCK_SQL, PROVISION_ROLES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
_ENV_EXAMPLE = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
_ENV_PY = (_REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
_VERSIONS = _REPO_ROOT / "migrations" / "versions"


def _code_only(path: Path) -> str:
    """The module's source with its docstrings and comments blanked out.

    A guard that forbids a construct must not be satisfiable -- or defeatable
    -- by a sentence that merely mentions it. `provision.py` explains at
    length why it does NOT use `pg_advisory_xact_lock`; a naive substring
    search reads that explanation as the violation.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            lines[row - 1] = lines[row - 1][:col]
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for row in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines[row - 1] = ""
    return "\n".join(lines)


_PROVISION_PY = _code_only(_REPO_ROOT / "src" / "app" / "ops" / "provision.py")
_ONLINE_DDL_PY = _code_only(_REPO_ROOT / "src" / "app" / "ops" / "online_ddl.py")


def _compose_value(key: str) -> str:
    """The default of a `KEY: ${KEY:-default}` line in docker-compose.yml."""
    match = re.search(rf"^\s*{key}:\s*\$\{{{key}:-([^}}]+)\}}", _COMPOSE, flags=re.MULTILINE)
    assert match is not None, f"{key} is not set in docker-compose.yml"
    return match.group(1)


# ───────────────────────── (1) the advisory lock ─────────────────────────


def test_the_lock_covers_all_three_phases_not_just_the_migrations() -> None:
    """Measured (module docstring of `app.ops.provision`): on a database with
    NOTHING left to migrate, one of three concurrent provisioners still died
    -- in `apply_grants`, with `tuple concurrently updated`. A lock that wraps
    only `run_migrations` would leave that case exactly as broken as it was,
    while looking like the fix."""
    body = _PROVISION_PY[_PROVISION_PY.index("async def _provision_locked") :]
    body = body[: body.index("\ndef provision(")]
    guarded = body[body.index("async with provision_lock(") :]
    for phase in ("_require_roles(", "run_migrations", "apply_grants("):
        assert phase in guarded, f"{phase} runs outside the provisioning lock"


def test_the_lock_is_session_scoped_not_transaction_scoped() -> None:
    """`pg_advisory_xact_lock` -- the form `AdvisoryQuotaLock` uses -- would
    be released by the first COMMIT, and the run it must cover spans a whole
    Alembic invocation."""
    statement = str(_PROVISION_LOCK_SQL.text)
    assert "pg_advisory_lock(" in statement
    assert "pg_advisory_xact_lock" not in statement


def test_the_lock_key_is_hashed_in_sql_never_in_python() -> None:
    """Python's `hash()` is randomised per process, so three replicas would
    compute three keys for one string and serialise against nobody -- the
    same trap `infrastructure/persistence/quota_lock.py` documents."""
    statement = str(_PROVISION_LOCK_SQL.text)
    assert "hashtextextended(:key, 0)" in statement, (
        "the key must be hashed by Postgres, from a bind parameter -- not in Python"
    )
    assert "hash(" not in _PROVISION_PY


def test_the_lock_holder_connection_is_autocommit() -> None:
    """A lock held inside an open transaction pins the database's `xmin`
    horizon for the whole deploy, which is precisely what stops autovacuum
    from removing dead tuples -- the defect step 2.8 just finished removing
    from these same tables."""
    holder = _PROVISION_PY[_PROVISION_PY.index("async def provision_lock") :]
    holder = holder[: holder.index("async def _provision_locked")]
    assert 'isolation_level="AUTOCOMMIT"' in holder


def test_the_migrate_service_does_not_go_through_the_pooler() -> None:
    """A SESSION-level advisory lock taken through PgBouncer in transaction
    pooling rides on a server connection handed to another client at the next
    COMMIT: held by, and released by, the wrong session."""
    # Two spaces exactly: `depends_on:\n  migrate:` inside another service
    # matches a looser pattern, and that entry carries no DSN of its own.
    match = re.search(r"^  migrate:\s*$", _COMPOSE, flags=re.MULTILINE)
    assert match is not None, "docker-compose.yml has no top-level migrate service"
    service = _COMPOSE[match.end() : _COMPOSE.index("\n  # ─", match.end())]
    dsn = re.search(r"DATABASE_URL:\s*(\S+)", service)
    assert dsn is not None, "the migrate service has no DATABASE_URL"
    assert "@postgres:5432/" in dsn.group(1), (
        "app.ops.provision holds a session-level advisory lock; it must connect "
        f"directly to postgres, not through the pooler -- got {dsn.group(1)}"
    )


def test_provisioning_still_says_it_finished_after_alembic_reconfigures_logging() -> None:
    """`migrations/env.py` calls `fileConfig` on `alembic.ini`, whose
    `[logger_root] level = WARNING` REPLACES whatever `main` configured -- so
    every INFO after the first chain was swallowed, `provision.complete`
    included. Tolerable while provisioning was one process; not once a replica
    can legitimately sit silent waiting for the lock, because then "no output"
    would mean both "waiting" and "finished"."""
    run = _PROVISION_PY[_PROVISION_PY.index("def run_migrations") :]
    run = run[: run.index("async def _require_roles")]
    assert "logging.getLogger().setLevel(root_level)" in run


def test_the_checked_roles_are_reachable_as_one_constant() -> None:
    """`provision` names the six roles once so the locked path and
    `test_role_provisioning_wiring` cannot disagree about them."""
    assert len(PROVISION_ROLES) == 6
    assert len(set(PROVISION_ROLES)) == 6


# ───────────────────────────── (2) lock_timeout ───────────────────────────


def test_the_migration_connection_sets_lock_timeout_in_the_startup_packet() -> None:
    """Not a `SET`: Alembic opens the revision's transaction itself, so a
    `SET` here would either fight that transaction for the connection or be
    rolled back with the first revision that fails."""
    assert '"server_settings": {"lock_timeout"' in _ENV_PY


def test_the_offline_script_carries_the_same_guard() -> None:
    """`alembic upgrade --sql` emits a file an operator pipes into psql. A
    generated script that freezes a hot table is the same defect as a
    connection that does."""
    offline = _ENV_PY[_ENV_PY.index("def run_migrations_offline") :]
    offline = offline[: offline.index("def _do_run_migrations")]
    assert "SET lock_timeout" in offline


def test_the_freeze_budget_is_smaller_than_the_request_budget() -> None:
    """The derivation, not a preference. A DDL statement waiting for its lock
    parks every later reader behind it, so `MIGRATION_LOCK_TIMEOUT_MS` is how
    long a deploy may freeze a hot table -- measured at 6.57 s of blocked
    SELECT with no bound, 2.46 s with a 3 s one. A request stuck behind it
    must be released by the migration giving up, not by its own budget
    expiring: the first is a deploy that failed loudly, the second is a 500
    with no migration anywhere in the message."""
    freeze = int(_compose_value("MIGRATION_LOCK_TIMEOUT_MS"))
    request = int(_compose_value("DB_STATEMENT_TIMEOUT_MS"))
    assert 0 < freeze < request, (
        f"MIGRATION_LOCK_TIMEOUT_MS ({freeze}) must stay under DB_STATEMENT_TIMEOUT_MS ({request})"
    )


def test_the_two_deploy_waits_are_not_one_number() -> None:
    """Measured: `lock_timeout` DOES cancel a waiting `pg_advisory_lock`
    (55P03 after 2.44 s on a 2 s budget), so one process setting one
    `lock_timeout` for everything would either make the second replica give up
    in three seconds or put the DDL convoy back. A replica waits out another
    replica's ENTIRE migration run; a DDL statement may freeze a table for
    milliseconds."""
    settings = MigrationSettings()
    assert settings.provision_lock_wait_ms > settings.lock_timeout_ms * 100
    assert int(_compose_value("PROVISION_LOCK_WAIT_MS")) == settings.provision_lock_wait_ms
    assert int(_compose_value("MIGRATION_LOCK_TIMEOUT_MS")) == settings.lock_timeout_ms


def test_neither_wait_can_be_configured_to_wait_forever() -> None:
    """`0` is Postgres's spelling of "no limit" on both GUCs, and "no limit"
    is the behaviour `ح-18` exists to remove."""
    for field in ("migration_lock_timeout_ms", "provision_lock_wait_ms"):
        info = _EnvSettings.model_fields[field]
        assert any(getattr(m, "gt", None) == 0 for m in info.metadata), (
            f"{field} accepts 0, which means 'wait forever'"
        )


def test_both_knobs_are_declared_in_the_example_env() -> None:
    for key in ("MIGRATION_LOCK_TIMEOUT_MS", "PROVISION_LOCK_WAIT_MS"):
        assert re.search(rf"^{key}=", _ENV_EXAMPLE, flags=re.MULTILINE), (
            f"{key} is read by the migrate service but missing from .env.example"
        )


# ─────────────────────────── (3) expand/contract ──────────────────────────

#: DDL that a rolling deploy cannot survive in one release, and what each one
#: breaks. Every entry is a CONTRACT-phase operation: correct only once no
#: running code reads the old shape, which during a rolling deploy is never
#: true of the release that introduces it.
_CONTRACT_DDL: dict[str, re.Pattern[str]] = {
    # The old replica still SELECTs the column by name.
    "drop-column": re.compile(r"\bDROP\s+COLUMN\b", re.I),
    "rename-column": re.compile(r"\bRENAME\s+COLUMN\b", re.I),
    "rename-table": re.compile(r"\bALTER\s+TABLE\s+[\w.\"]+\s+RENAME\s+TO\b", re.I),
    "drop-table": re.compile(r"\bDROP\s+TABLE\b", re.I),
    # A rewrite under ACCESS EXCLUSIVE for the length of the table.
    "retype-column": re.compile(r"\bALTER\s+COLUMN\s+[\w\"]+\s+(SET\s+DATA\s+)?TYPE\b", re.I),
    # The old replica still INSERTs without the column, and gets 23502.
    "set-not-null": re.compile(r"\bALTER\s+COLUMN\s+[\w\"]+\s+SET\s+NOT\s+NULL\b", re.I),
}

_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", re.I)
_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY)"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\w.\"]+\s+ON\s+([\w.\"]+)",
    re.I,
)
_ADD_COLUMN = re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?[\w\"]+[^,;)]*", re.I)

#: Revisions that take a blocking `CREATE INDEX` on a table that already
#: existed, and were APPLIED before capacity 2.9 gave this repository an
#: online path (`app.ops.online_ddl.create_index_concurrently`).
#: `knowledge/0009_hot_path_indexes.py` measured the price it could not avoid
#: -- 2.1 s of blocked writes on a million chunks -- and named 2.9 as the way
#: out. Rewriting applied migrations would change nothing about the databases
#: they already ran on, so they are recorded instead.
#:
#: This list may only SHRINK. A new entry means a new migration took a lock a
#: rolling deploy would have felt, and `test_the_grandfathered_list_has_no_
#: stale_entries` fails the moment one of these stops being a violation, so
#: the list cannot quietly outlive the code it excuses.
_BLOCKING_INDEX_GRANDFATHERED: frozenset[str] = frozenset(
    {
        "conversations/0004_conversation_space.py",
        "conversations/0006_space_count_covering.py",
        "credentials/0003_platform_admin_keys.py",
        "files/0002_file_space.py",
        "files/0003_file_name_lookup.py",
        "files/0004_space_quota_covering.py",
        "knowledge/0004_document_space.py",
        "knowledge/0009_hot_path_indexes.py",
        "platform/0003_retention_sweep.py",
        "usage/0004_usage_autovacuum.py",
        "workspace/0009_workspace_purge.py",
    }
)


def _upgrade_sql(path: Path) -> str:
    """Every string literal inside the revision's ``upgrade()``.

    `upgrade()` and not the file: nearly every `downgrade()` in this tree
    drops the column its `upgrade()` added, which is correct and is not a
    contract-phase hazard -- a downgrade is not something a rolling deploy
    runs against live traffic."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if fn is None:
        return ""
    return "\n".join(
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _revisions() -> list[Path]:
    return sorted(_VERSIONS.rglob("0*.py"))


def _blocking_index_tables(sql: str) -> list[str]:
    """`CREATE INDEX` (no `CONCURRENTLY`) on a table this revision did not
    also create. Indexing a table born three statements earlier locks an
    empty table for microseconds and is not a hazard; indexing a live one
    blocks its writes for as long as the build takes."""
    born = {name.lower().strip('"') for name in _CREATE_TABLE.findall(sql)}
    return [t for t in _CREATE_INDEX.findall(sql) if t.lower().strip('"') not in born]


def _not_null_without_default(sql: str) -> list[str]:
    """`ADD COLUMN ... NOT NULL` with no `DEFAULT` fails outright on a
    non-empty table (23502). WITH a constant default it is metadata-only on
    PostgreSQL 11+, so it is not flagged."""
    return [
        clause.strip()
        for clause in _ADD_COLUMN.findall(sql)
        if re.search(r"NOT\s+NULL", clause, re.I) and not re.search(r"DEFAULT", clause, re.I)
    ]


def test_the_revision_scanner_actually_finds_revisions() -> None:
    """A guard iterating an empty list passes forever while checking
    nothing."""
    revisions = _revisions()
    assert len(revisions) > 40
    assert any("CREATE TABLE" in _upgrade_sql(path).upper() for path in revisions)


@pytest.mark.parametrize("path", _revisions(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_upgrade_performs_contract_phase_ddl(path: Path) -> None:
    """The window a rolling deploy opens is one in which two code versions run
    against one schema. Dropping, renaming, retyping or tightening a column in
    the SAME release that stops using it closes that window on the old
    replica's fingers: it is still selecting the column, or still inserting
    without it. Each of these belongs in a LATER release, once nothing reads
    the old shape -- phase 4 of `app.ops.online_ddl`'s docstring."""
    sql = _upgrade_sql(path)
    found = sorted(name for name, pattern in _CONTRACT_DDL.items() if pattern.search(sql))
    assert not found, (
        f"{path.parent.name}/{path.name} performs {found} in upgrade(). "
        "Split it: expand now, contract in a later release (app.ops.online_ddl)."
    )


@pytest.mark.parametrize("path", _revisions(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_new_revision_takes_a_blocking_index_on_a_live_table(path: Path) -> None:
    """`CREATE INDEX` without `CONCURRENTLY` holds a `SHARE` lock that blocks
    every write to the table for the length of the build -- measured at 2.1 s
    for two indexes on a million chunks (`knowledge/0009_hot_path_indexes`).
    `app.ops.online_ddl.create_index_concurrently` is the way out, and it did
    not exist until 2.9; the revisions that predate it are listed above."""
    key = f"{path.parent.name}/{path.name}"
    tables = _blocking_index_tables(_upgrade_sql(path))
    if key in _BLOCKING_INDEX_GRANDFATHERED:
        return
    assert not tables, (
        f"{key} builds a blocking index on pre-existing table(s) {tables}. "
        "Use app.ops.online_ddl.create_index_concurrently."
    )


@pytest.mark.parametrize("path", _revisions(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_upgrade_adds_a_not_null_column_without_a_default(path: Path) -> None:
    """It fails outright on a non-empty table, and a deploy is not the place
    to discover that a table had rows. `files/0002_file_space.py` shows the
    shape that works: the column is born NULLable and the `SET NOT NULL`
    moves to its own later step."""
    offenders = _not_null_without_default(_upgrade_sql(path))
    assert not offenders, f"{path.parent.name}/{path.name}: {offenders}"


def test_the_grandfathered_list_has_no_stale_entries() -> None:
    """An excuse that outlives the thing it excuses is how a guard rots into
    decoration. Every listed revision must still exist AND still be a
    violation -- so the day one is rewritten onto the online path, this test
    demands the entry be deleted."""
    for key in sorted(_BLOCKING_INDEX_GRANDFATHERED):
        path = _VERSIONS / key
        assert path.exists(), f"{key} is grandfathered but no longer exists"
        assert _blocking_index_tables(_upgrade_sql(path)), (
            f"{key} no longer takes a blocking index -- remove it from "
            "_BLOCKING_INDEX_GRANDFATHERED"
        )


def test_the_contract_ddl_guard_can_actually_fail(tmp_path: Path) -> None:
    """The 2.8 lesson: a guard nobody has ever seen fail is a guard nobody has
    checked. Each pattern is fed the statement it exists to catch."""
    samples = {
        "drop-column": "ALTER TABLE files.files DROP COLUMN space_id",
        "rename-column": "ALTER TABLE files.files RENAME COLUMN a TO b",
        "rename-table": 'ALTER TABLE files.files RENAME TO "files_old"',
        "drop-table": "DROP TABLE files.files",
        "retype-column": "ALTER TABLE files.files ALTER COLUMN size TYPE bigint",
        "set-not-null": "ALTER TABLE files.files ALTER COLUMN space_id SET NOT NULL",
    }
    assert samples.keys() == _CONTRACT_DDL.keys()
    for name, statement in samples.items():
        assert _CONTRACT_DDL[name].search(statement), f"{name} does not match its own example"
        other = [n for n, p in _CONTRACT_DDL.items() if n != name and p.search(statement)]
        assert not other or name == "rename-table", f"{name}'s example also matched {other}"


def test_the_blocking_index_scanner_can_actually_fail() -> None:
    """A `CREATE INDEX` on a table born in the same revision must NOT be
    flagged, and one on a pre-existing table must be -- otherwise the guard
    is either noise or nothing."""
    born = "CREATE TABLE a.b (id int);\nCREATE INDEX ix_b ON a.b (id);"
    live = "CREATE INDEX ix_b ON a.b (id);"
    online = "CREATE INDEX CONCURRENTLY ix_b ON a.b (id);"
    assert _blocking_index_tables(born) == []
    assert _blocking_index_tables(live) == ["a.b"]
    assert _blocking_index_tables(online) == []


def test_the_not_null_scanner_can_actually_fail() -> None:
    assert _not_null_without_default("ALTER TABLE a.b ADD COLUMN c int NOT NULL")
    assert not _not_null_without_default("ALTER TABLE a.b ADD COLUMN c int NOT NULL DEFAULT 0")
    assert not _not_null_without_default("ALTER TABLE a.b ADD COLUMN c int")


# ─────────────────────── the online-DDL helpers' contract ─────────────────


def test_the_index_helper_refuses_to_trust_if_not_exists_alone() -> None:
    """Measured: `CREATE INDEX CONCURRENTLY IF NOT EXISTS` against an INVALID
    namesake left it invalid -- the retry "succeeded" and the table went on
    paying maintenance for an index the planner will never use. The helper
    must drop an invalid namesake first."""
    assert "NOT i.indisvalid" in _ONLINE_DDL_PY
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in _ONLINE_DDL_PY


def test_both_helpers_run_outside_the_revision_transaction() -> None:
    """`CONCURRENTLY` cannot run inside a transaction block and Alembic wraps
    every revision in one; a batched backfill that never commits is one long
    transaction wearing a loop."""
    assert _ONLINE_DDL_PY.count("autocommit_block()") == 2


def test_the_backfill_helper_rejects_a_non_identifier_table() -> None:
    with pytest.raises(ValueError, match="table name"):
        backfill_in_batches("files.files; DROP TABLE x", "a = 1", "a IS NULL")


def test_the_backfill_loop_is_bounded() -> None:
    """Its only natural exit is the predicate ceasing to match, so an
    assignment that does not falsify the predicate would spin forever -- while
    holding the provisioning lock, with `migrate` never exiting and no service
    starting behind it. The ceiling turns that into a message."""
    assert MAX_BATCHES > 0
    source = _ONLINE_DDL_PY[_ONLINE_DDL_PY.index("def backfill_in_batches") :]
    assert "while True" not in source, "the backfill loop must be bounded"
    assert "raise RuntimeError" in source


def test_the_index_helper_rejects_a_schema_qualified_index_name() -> None:
    """The index lands in the TABLE's schema; a qualified name here would read
    as a second, silently ignored, opinion about where it goes."""
    with pytest.raises(ValueError, match="bare"):
        create_index_concurrently("public.ix_a", "files.files", "(id)")
