"""Capacity step 2.5's deploy surface, guarded across the files that have to
agree with each other.

The tool in `app/ops/backup.py` is worth nothing on a stack that is not
archiving, whose spool nothing can write to, or whose role does not exist --
and every one of those is a file this module can read. Two of the assertions
here exist because the first run of this step failed on exactly them:

* the `archive_command` script is invoked through `sh` rather than executed,
  because a bind mount carries the HOST's permission bits (measured:
  `exit code 126`, and `pg_stat_archiver.failed_count` still reading 0 after
  six failures);
* the spool volume is chowned by a one-shot before the server starts,
  because a named volume's mount point is created root:root when the path
  does not exist in the image (measured: `archive_mode=on`, healthy
  container, not one byte archived).

Neither is reachable by reading the file that contains the bug.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ops.backup import BACKUP_ROLE

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_HBA = _REPO_ROOT / "deploy" / "postgres" / "pg_hba.conf"
_ARCHIVE_SH = _REPO_ROOT / "deploy" / "postgres" / "archive_wal.sh"
_ROLES_SH = _REPO_ROOT / "deploy" / "postgres" / "initdb" / "10-roles.sh"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_RUNPOD_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_MINIO_BOOTSTRAP = _REPO_ROOT / "deploy" / "minio" / "bootstrap.sh"
_DRILL = _REPO_ROOT / "deploy" / "backup" / "restore_drill.sh"

_PASSWORD_VAR = "BACKUP_OPERATOR_PASSWORD"
_SPOOL = "/var/lib/postgresql/wal-archive"


def _compose() -> str:
    return _COMPOSE.read_text(encoding="utf-8")


# --------------------------------------------------------------- the role --


def test_the_backup_role_exists_in_all_four_places() -> None:
    """The `test_role_provisioning_wiring.py` guard, applied to the one role
    it does NOT cover: `backup_operator` is deliberately absent from
    `_require_roles` (no migration names it in a policy), so nothing else
    compares these four sources for it."""
    missing = []
    if f"CREATE ROLE {BACKUP_ROLE} " not in _ROLES_SH.read_text(encoding="utf-8"):
        missing.append("deploy/postgres/initdb/10-roles.sh")
    if f"{_PASSWORD_VAR}: ${{{_PASSWORD_VAR}:?" not in _compose():
        missing.append("docker-compose.yml (postgres, with :?)")
    if f"{_PASSWORD_VAR}=" not in _ENV_EXAMPLE.read_text(encoding="utf-8"):
        missing.append(".env.example")
    if _PASSWORD_VAR not in _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8"):
        missing.append("deploy/runpod/entrypoint.sh")

    assert not missing, f"{BACKUP_ROLE} is not wired in: {missing}"


def test_the_role_is_created_with_both_attributes_the_tool_refuses_without() -> None:
    """REPLICATION for `pg_basebackup`, BYPASSRLS so the dump is not silently
    empty. `app.ops.backup.preflight` refuses on either; this is the half
    that makes a fresh cluster not need the refusal."""
    create = re.search(rf"CREATE ROLE {BACKUP_ROLE} ([^;]+);", _ROLES_SH.read_text("utf-8"))

    assert create is not None
    assert "REPLICATION" in create.group(1)
    assert "BYPASSRLS" in create.group(1)


def test_the_backup_role_is_the_one_role_that_inherits() -> None:
    """Every other role here is NOINHERIT on purpose. `pg_read_all_data` is a
    MEMBERSHIP, and under NOINHERIT a membership is inert until the session
    runs `SET ROLE` -- measured: `permission denied for schema workspace` on a
    role the catalogue shows as a member of a role that grants USAGE on every
    schema. The convention would have made the grant above decorative."""
    roles = _ROLES_SH.read_text("utf-8")
    create = re.search(rf"CREATE ROLE {BACKUP_ROLE} ([^;]+);", roles)

    assert create is not None
    assert "NOINHERIT" not in create.group(1)
    assert "INHERIT" in create.group(1)


def test_the_role_reads_every_table_through_a_predefined_role_not_a_grant_list() -> None:
    """A hand-maintained grant list stops covering tables added by later
    migrations, and it does so silently -- the dump succeeds and is short."""
    assert f"GRANT pg_read_all_data TO {BACKUP_ROLE}" in _ROLES_SH.read_text("utf-8")


def test_the_runpod_deployment_requires_the_password_before_it_boots() -> None:
    """`deploy/runpod/bootstrap.sh` runs the SAME `10-roles.sh` under
    `set -u`, so an unset variable there does not skip the role -- it aborts
    the whole cluster bootstrap."""
    entrypoint = _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8")
    required_block = entrypoint.split("for required in", 1)[1].split("do", 1)[0]

    assert _PASSWORD_VAR in required_block


# ------------------------------------------------------------- archiving --


def test_the_server_archives_wal() -> None:
    compose = _compose()

    assert "archive_mode=on" in compose
    assert "archive_command=" in compose


def test_the_archive_command_is_invoked_through_sh_and_not_executed() -> None:
    """Measured on this step's first run. A bind mount carries the host's
    permission bits; a checkout that lost the executable bit turns archiving
    off with `sh: 1: ...: Permission denied` / `exit code 126`, and
    `pg_stat_archiver.failed_count` stays at 0 through it."""
    command = next(line for line in _compose().splitlines() if "archive_command=" in line)

    assert "/bin/sh " in command, (
        "archive_command must invoke the script through an interpreter -- executing a "
        "bind-mounted script depends on the HOST's permission bits (exit code 126)"
    )


def test_the_spool_is_chowned_before_the_server_that_writes_it_starts() -> None:
    """A named volume's mount point is root:root when the path is not in the
    image. Measured: healthy container, `archive_mode=on`, zero bytes
    archived, and the only symptom in the log."""
    compose = _compose()

    assert "wal-archive-init:" in compose
    assert "chown 999:999 /var/lib/postgresql/wal-archive" in compose
    assert re.search(
        r"wal-archive-init:\s*\n\s*condition: service_completed_successfully", compose
    ), "postgres must depend on the chown one-shot completing, not merely starting"


def test_the_spool_volume_is_shared_by_exactly_the_two_services_that_need_it() -> None:
    """The server writes it and the shipper deletes from it. Any third mount
    is a copy of every tenant's transaction log somewhere nobody audited."""
    mounts = [line for line in _compose().splitlines() if f"- wal-archive:{_SPOOL}" in line]

    # postgres, wal-shipper, backup, wal-archive-init -- and no more.
    assert len(mounts) == 4


def test_both_backup_containers_run_as_the_postgres_uid() -> None:
    """Deleting a shipped segment needs write on the DIRECTORY. The
    alternative to matching the uid was widening the mode of live WAL."""
    compose = _compose()
    for service in ("wal-shipper:", "backup:"):
        block = compose.split(service, 1)[1][:900]
        assert 'user: "999:999"' in block, f"{service} must run as the spool's owner"


def test_neither_backup_connection_goes_through_the_pooler() -> None:
    """`pg_basebackup` speaks the replication protocol, which PgBouncer does
    not implement; `pg_dump` holds one snapshot across hundreds of
    statements, which transaction pooling breaks."""
    for line in _compose().splitlines():
        if "BACKUP_DATABASE_URL:" in line:
            assert "@postgres:5432" in line
            assert "pgbouncer" not in line


# -------------------------------------------------------------- pg_hba --


def test_the_replication_line_names_the_backup_role_and_not_all() -> None:
    """Without it `pg_basebackup` from any other container answers "no
    pg_hba.conf entry for replication connection" -- `all` in the database
    column does not match a physical replication connection."""
    hba = _HBA.read_text(encoding="utf-8")
    rules = [line.split() for line in hba.splitlines() if line and not line.startswith("#")]
    replication = [r for r in rules if len(r) >= 4 and r[1] == "replication"]
    from_network = [r for r in replication if r[0] == "host" and r[3] == "all"]

    assert len(from_network) == 1
    assert from_network[0][2] == BACKUP_ROLE, "only the backup role may replicate"


def test_the_mounted_hba_preserves_every_rule_initdb_produces() -> None:
    """This file REPLACES the generated one wholesale (`hba_file`), so a rule
    it forgets is a rule the cluster loses -- including the loopback trust
    every initdb script and `docker compose exec psql` depends on."""
    hba = _HBA.read_text(encoding="utf-8")
    for inherited in (
        ("local", "all", "all", "trust"),
        ("host", "all", "all", "127.0.0.1/32", "trust"),
        ("local", "replication", "all", "trust"),
        ("host", "all", "all", "all", "scram-sha-256"),
    ):
        assert any(line.split() == list(inherited) for line in hba.splitlines()), (
            f"the generated rule {inherited} is missing from the replacement"
        )


def test_the_catch_all_is_last_because_pg_hba_is_first_match_wins() -> None:
    hba = [line.split() for line in _HBA.read_text("utf-8").splitlines() if line and line[0] != "#"]
    catch_all = [i for i, r in enumerate(hba) if r[:3] == ["host", "all", "all"] and r[3] == "all"]

    assert catch_all == [len(hba) - 1]


def test_the_server_reads_that_file_and_not_the_one_in_its_volume() -> None:
    assert "hba_file=/etc/postgresql/pg_hba.conf" in _compose()
    assert "./deploy/postgres/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro" in _compose()


# ------------------------------------------------------ the archive script --


def test_the_archive_script_refuses_to_overwrite_a_different_segment() -> None:
    """Postgres may re-run the command for a segment it already archived, and
    the documented contract is: identical file => success, different file =>
    failure. Overwriting silently is how a recoverable archive stops being
    one."""
    script = _ARCHIVE_SH.read_text(encoding="utf-8")

    assert "cmp -s" in script
    assert "already archived with DIFFERENT contents" in script


def test_the_archive_script_writes_atomically() -> None:
    """A half-written segment that the shipper picks up is a corrupt object
    with a perfectly good name."""
    script = _ARCHIVE_SH.read_text(encoding="utf-8")

    assert 'staging="${WAL_ARCHIVE_DIR}/.${segment_name}.$$"' in script
    assert 'mv "${staging}" "${target}"' in script
    assert "sync" in script


# ------------------------------------------------- the bucket and the image --


def test_the_backup_bucket_is_separate_from_the_file_bucket_and_versioned() -> None:
    bootstrap = _MINIO_BOOTSTRAP.read_text(encoding="utf-8")

    assert 'backup_bucket="${BACKUP_BUCKET:-aizzak-backups}"' in bootstrap
    assert 'mc version enable "aizzak/${backup_bucket}"' in bootstrap
    assert "noncurrent-expire-days" in bootstrap


def test_the_client_major_tracks_the_server_major() -> None:
    """`pg_dump` refuses to dump a server newer than itself, and a base
    backup taken by a mismatched `pg_basebackup` is a data directory the
    server may reject. Debian bookworm ships 15, which is why the image
    installs from PGDG at all."""
    server = re.search(r"image: postgres:(\d+)", _compose())
    # The INSTALL line, not the paragraph above it that explains why bookworm's
    # own `postgresql-client-15` is the wrong package -- a guard that reads its
    # own rationale as evidence proves nothing.
    installed = [
        line
        for line in _DOCKERFILE.read_text("utf-8").splitlines()
        if "postgresql-client-" in line and not line.lstrip().startswith("#")
    ]

    assert server is not None
    assert len(installed) == 1
    client = re.search(r"postgresql-client-(\d+)", installed[0])
    assert client is not None
    assert server.group(1) == client.group(1)


def test_the_image_carries_the_live_proofs_the_drill_and_the_runbook_run() -> None:
    """`08-local-runbook §5.1` and `stack_smoke.py`'s own header both document
    `docker compose exec app python /app/deploy/smoke/stack_smoke.py`, and
    until step 2.5 the image copied ONE file out of `deploy/` -- so that
    command had never worked from a build. Found only because 2.5's acceptance
    criterion made something finally run it."""
    dockerfile = _DOCKERFILE.read_text("utf-8")

    assert "COPY deploy/smoke/ ./deploy/smoke/" in dockerfile


def test_the_drill_is_executable_because_an_operator_runs_it_by_name() -> None:
    """Unlike `archive_wal.sh`, nothing invokes this through an interpreter
    for you -- `deploy/load/run.sh` and `smoke.sh` are 0755 for the same
    reason."""
    assert _DRILL.exists()
    assert _DRILL.stat().st_mode & 0o111, "deploy/backup/restore_drill.sh must be executable"


def test_the_drill_proves_the_point_in_time_and_not_merely_that_it_booted() -> None:
    """The one assertion that separates a restore drill from a container that
    started: a marker written BEFORE the target must be there and one written
    AFTER must not."""
    drill = _DRILL.read_text(encoding="utf-8")

    assert "restore_target_time" in drill or "recovery_target_time" in drill
    assert "this is not a point-in-time restore" in drill
