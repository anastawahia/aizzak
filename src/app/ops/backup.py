"""Backup and point-in-time restore for the four stores this platform keeps
state in (capacity step ``2.5``, bottleneck ``ح-13``: "``src/app/ops/`` --
nine tools, and not one of them backs anything up").

Run as its own short-lived process, the ``app.ops.retention`` footing:

    docker compose --profile backup run --rm backup python -m app.ops.backup full
    docker compose --profile backup run --rm backup python -m app.ops.backup status

**What the step's own wording asks for, and the one thing it cannot
deliver.** ``capacity-plan §5`` step 2.5 reads: "a daily LOGICAL backup +
continuous WAL to MinIO", and its acceptance criterion reads "a restore,
documented by time, TO A CHOSEN POINT IN TIME". Those two sentences cannot
both be satisfied by the artifacts the first one names, and the reason is not
a matter of taste:

* ``pg_dump`` produces a LOGICAL snapshot -- SQL/TOC entries reconstructing
  rows as of one instant. Restoring it runs ``initdb`` semantics on a fresh
  cluster: new system identifier, new timeline, WAL starting at segment one.
* An archived WAL segment is a PHYSICAL change log -- block images and
  offsets, addressed by relation filenode inside ONE cluster's storage. The
  recovering server refuses to replay a segment whose system identifier does
  not match its own control file ("WAL file is from a different database
  system").

So a dump plus a shelf of WAL is not a point-in-time restore; it is one point
in time plus files nothing can consume. The measurement is in
``tests/integration/test_backup_live.py::test_a_dump_restore_cannot_consume_
this_cluster_s_wal``, which restores the dump into a clean cluster and reads
back both system identifiers: they differ, so every archived segment is
un-replayable there BY IDENTITY, not by accident of configuration.

**What this module does instead: both, for their own reasons.**

* ``base`` -- ``pg_basebackup``, the PHYSICAL anchor. This is the artifact
  WAL replays onto, and therefore the only artifact that makes
  ``recovery_target_time`` mean anything.
* ``dump`` -- ``pg_dump -Fc``, kept for what a base backup cannot do: restore
  into a DIFFERENT major version, restore ONE table, and survive a corruption
  that a physical copy would faithfully reproduce. It is not the PITR path
  and this module never claims it is.
* ``wal`` -- ships the archive spool to object storage, which is what makes
  the window between two base backups recoverable.
* ``qdrant`` -- per-collection snapshots. Derived state (a vector is
  recomputable from the chunk text Postgres holds), but "recomputable" for
  the ``§0`` corpus means re-embedding a million vectors, so it is backed up
  rather than shrugged at.

**The defect this step found before it wrote a line of the tool, measured on
the live stack.** ``pg_dump`` run as ``aizzak_owner`` -- the closest thing to
a privileged role this repository has -- FAILS on the first tenant table:

    pg_dump: error: query failed: ERROR:  query would be affected by
    row-level security policy for table "users"
    HINT:  To disable the policy for the table's owner, use
           ALTER TABLE NO FORCE ROW LEVEL SECURITY.

Every tenant table is under ``FORCE ROW LEVEL SECURITY`` (01 §3.1), which
subjects the OWNER to the policy as well, and ``pg_dump`` sets
``row_security = off``, which the server answers with an error rather than a
filtered read. Loud, and therefore fine.

What is not fine is the flag that silences it. With ``--enable-row-security``
-- the first hit for that error message, and a flag several backup guides
recommend without qualification -- the same command exits **0** and writes a
dump in which ``workspace.workspaces`` carries 202 rows, ``workspace.users``
carries **zero** (the real table: 202), and ``workspace.user_presence``
carries **zero** (the real table: 2). A successful backup of an empty
database. The HINT is worse still: ``ALTER TABLE ... NO FORCE ROW LEVEL
SECURITY`` would turn every tenant boundary in the schema off to make a
backup work.

So the dump needs a role that BYPASSES the policy rather than one that
negotiates with it, and this module refuses to run ``pg_dump`` at all unless
``pg_roles.rolbypassrls`` is true for the connected role (``_preflight``).
``BYPASSRLS`` is an attribute, not a grant: ``pg_read_all_data`` -- which
this role also holds -- explicitly does NOT bypass RLS, so both are needed
and each for its own reason (read every table; see every row in it).

And because "the role had the attribute" is a statement about the role rather
than about the file that was written, the dump is verified by READING IT
BACK: ``pg_restore --data-only`` for one canary table, row count compared
against the live count in the same run (``_verify_dump``). A dump that
carries zero rows for a table that has 202 fails the command that produced
it.

**``backup_operator`` is an eighth role, not a widened seventh.** The
precedent is unbroken here (``outbox_relay``, ``retention_sweeper``,
``metrics_reader``, ``transit_rotator``, ``workspace_purger``): a job gets
its own least-privilege role rather than a bigger grant on someone else's.
It holds ``REPLICATION`` (``pg_basebackup`` opens a replication connection),
``BYPASSRLS`` (above) and ``pg_read_all_data`` (SELECT on every table,
including ones added by migrations that have not been written yet -- a
backup role built from a hand-maintained grant list silently stops covering
new tables, which is the same class of failure as the empty dump). It holds
no INSERT, UPDATE or DELETE anywhere.

**Two connections this tool cannot make through PgBouncer, and why the
failure is loud in both cases.** ``pg_basebackup`` speaks the replication
protocol, which the pooler does not implement; ``pg_dump`` holds ONE
repeatable-read snapshot across hundreds of statements, which transaction
pooling breaks by design. Both fail with their own error rather than
producing a truncated artifact, so this module states the requirement
(``postgres:5432``, direct) and does not add a guess about whether the host
it was handed is a pooler.

**The archive path, and the trap in it.** ``archive_command`` runs inside the
``postgres`` container, which carries neither an S3 client nor this code, so
it copies to a spool volume (``deploy/postgres/archive_wal.sh``) and this
tool ships the spool. That split has one failure mode worth naming: a shipper
that stops does not fail an archive, it fills a disk -- Postgres keeps every
unarchived segment in ``pg_wal`` forever, and a full data volume is an outage
of the whole platform, not a backup problem. ``wal --follow`` is therefore
wired as a standing Compose service rather than an operator habit, ``status``
prints the spool depth and the age of the oldest spooled segment, and the
shipper deletes a segment only after re-reading its size back from object
storage.

**Retention windows are four different numbers and one rule that is not a
number**, the ``app.ops.retention`` shape:

* ``BASE_RETENTION`` (7 days) -- a full copy of the cluster; seven of them is
  a week of point-in-time coverage.
* ``DUMP_RETENTION`` (30 days) -- small, and the only artifact that survives
  a corruption or a major-version move. Kept longest for that reason.
* ``QDRANT_RETENTION`` (7 days) -- derived state, recomputable at the cost of
  re-embedding the corpus.
* WAL is **not** aged out. A segment is retained until the OLDEST surviving
  base backup no longer needs it, because "delete WAL older than N days" is
  precisely how a point-in-time restore dies quietly: the base backup is
  still on the shelf, the recovery starts, and it stops at the first missing
  segment with a target it can never reach. ``prune`` computes the floor from
  the surviving base manifests and never from the clock, and refuses to leave
  zero base backups at any age.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import io
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from minio import Minio
from minio.deleteobjects import DeleteObject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.observability.heartbeat import build_heartbeat
from app.framework.settings.settings import DatabaseSettings, MinioSettings
from app.infrastructure.config import load_settings
from app.infrastructure.config.vault_auth import load_vault_auth
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.secrets.vault_secrets import VaultSecrets, create_vault_client
from app.infrastructure.storage.minio_storage import create_minio_client

_logger = logging.getLogger(__name__)

# The role this tool authenticates as. Named here so
# `tests/unit/test_backup_wiring.py` can compare it against the four places a
# role has to exist in -- the same drift guard `test_role_provisioning_
# wiring.py` runs for the six roles `provision._require_roles` checks.
BACKUP_ROLE = "backup_operator"

# Object layout. Four prefixes because they have four different lifetimes and
# `prune` treats each on its own terms; `wal/` is flat because
# `restore_command` is handed a bare segment name (`%f`) and nothing else.
BASE_PREFIX = "base/"
DUMP_PREFIX = "dump/"
WAL_PREFIX = "wal/"
QDRANT_PREFIX = "qdrant/"
MANIFEST_NAME = "manifest.json"

# See the module docstring's retention paragraph for why these are three
# numbers and not one, and why WAL is not among them.
BASE_RETENTION = timedelta(days=7)
DUMP_RETENTION = timedelta(days=30)
QDRANT_RETENTION = timedelta(days=7)

# Never prune the last one, at any age. A retention sweep that can empty the
# shelf is not a retention policy.
MIN_KEPT_SETS = 1

_DEFAULT_BUCKET = "aizzak-backups"
_DEFAULT_SPOOL = "/var/lib/postgresql/wal-archive"
_DEFAULT_FOLLOW_INTERVAL_S = 30.0

# One canary table, read back out of every dump this tool writes. `workspace.
# users` is the cheapest tenant-scoped table that is never empty on a live
# deployment (a workspace cannot exist without its owner, 01 §2), which is
# what makes "zero rows here" decidable rather than ambiguous.
_CANARY_SCHEMA = "workspace"
_CANARY_TABLE = "users"

# A completed WAL segment is 24 hex characters (timeline, log id, segment).
# `.history` and `.backup` files are shipped too but are not segments and take
# no part in the continuity arithmetic.
_SEGMENT_RE = re.compile(r"^[0-9A-F]{24}$")

# Segments per log id at the default 16MB `wal_segment_size`: 2^32 / 2^24.
# Named rather than inlined because it is the ONE number `segment_sequence`
# turns on, and getting it wrong reports a gap at every rollover of a healthy
# archive.
_SEGMENTS_PER_LOG_ID = 0x100

# `pg_basebackup --format=tar --gzip` writes exactly these three names for a
# cluster with no extra tablespaces; a fourth would be a tablespace map this
# tool has never seen and must not silently drop.
_BASE_ARTIFACTS = ("base.tar.gz", "pg_wal.tar.gz", "backup_manifest")


class BackupError(RuntimeError):
    """Refusal or failure of one action. Carries an operator-readable
    sentence; `main` prints it and exits non-zero rather than raising a
    traceback at somebody at 3am."""


# ---------------------------------------------------------------- config --


@dataclass(frozen=True, slots=True)
class BackupConfig:
    """Everything the tool needs that is NOT part of the application's
    settings contract (05 §2).

    These knobs are module/env-level rather than `Settings` fields on
    purpose, and the precedent is `app.ops.retention`'s three windows: the
    settings contract describes what the PLATFORM reads at boot, and a
    manually-invoked operations tool's bucket name is not that. What IS taken
    from `Settings` is what the platform itself already owns -- the MinIO
    endpoint, the Qdrant URL, the database DSN.
    """

    dsn: str
    """libpq DSN for `backup_operator`, WITHOUT the password (which travels
    in `PGPASSWORD`; `/proc/<pid>/cmdline` is world-readable)."""

    password: str
    """Empty when no backup DSN was supplied. That is a legal configuration
    for exactly one action -- see `require_database`."""
    bucket: str
    spool: Path
    minio: MinioSettings
    qdrant_url: str

    def require_database(self) -> None:
        """Refuse an action that needs Postgres when no DSN was supplied.

        ``wal`` is the action that does not: shipping the spool is a
        filesystem read and an object-store write, and NOTHING else. That is
        why `wal-shipper` -- the one standing service here -- is given no
        database credentials at all in docker-compose.yml, and it is not
        tidiness: the shipper's whole reason to exist is the hour Postgres is
        in trouble and its WAL must still reach object storage. A shipper that
        could not start without a database password would be unavailable in
        precisely that hour, and would be holding a credential it never uses
        in every other one.
        """
        if not self.password:
            raise BackupError(
                "this action needs a database connection and no backup DSN was supplied. Set "
                f"BACKUP_DATABASE_URL to postgresql://{BACKUP_ROLE}:$BACKUP_OPERATOR_PASSWORD"
                "@postgres:5432/<db> -- direct to Postgres, NOT through pgbouncer (module "
                "docstring)."
            )


def _libpq_dsn(url: str) -> tuple[str, str]:
    """Split a SQLAlchemy URL into (libpq DSN without password, password).

    `postgresql+asyncpg://` is SQLAlchemy's spelling; libpq rejects the
    driver suffix outright. The password is stripped rather than re-encoded
    because every consumer here is a subprocess, and a DSN in `argv` is a
    password in `ps`.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.split("+", 1)[0]
    password = parts.password or ""
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    user = f"{parts.username}@" if parts.username else ""
    netloc = f"{user}{host}{port}"
    return urlunsplit((scheme, netloc, parts.path, "", "")), password


def _async_url(dsn: str, password: str) -> str:
    """The same connection as an asyncpg URL, for this tool's own SQL."""
    parts = urlsplit(dsn)
    user = parts.username or ""
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    credential = f"{user}:{password}@" if password else f"{user}@"
    return urlunsplit(("postgresql+asyncpg", f"{credential}{host}{port}", parts.path, "", ""))


def load_config() -> BackupConfig:
    """Assemble the tool's configuration, failing loudly on what it cannot
    invent."""
    settings = load_settings()
    url = os.environ.get("BACKUP_DATABASE_URL") or settings.database.url
    dsn, password = _libpq_dsn(url)
    return BackupConfig(
        dsn=dsn,
        password=password,
        bucket=os.environ.get("BACKUP_BUCKET", _DEFAULT_BUCKET),
        spool=Path(os.environ.get("BACKUP_SPOOL_DIR", _DEFAULT_SPOOL)),
        minio=settings.minio,
        qdrant_url=str(settings.qdrant.url).rstrip("/"),
    )


async def _minio_credentials() -> tuple[str, str]:
    """Vault first, environment second -- and the second is not laziness.

    The standing path is Vault (05 §3, the Composition Root's own read). But
    a backup tool whose credentials live behind a service that can be SEALED
    is a backup tool that stops working in exactly the incident it exists
    for (`ح-14`), so BACKUP_MINIO_ACCESS_KEY/SECRET_KEY override it. The
    override is what `deploy/backup/restore_drill.sh` uses: a restore drill
    that needed the platform's own secret manager to be healthy would prove
    much less than it claims.
    """
    access = os.environ.get("BACKUP_MINIO_ACCESS_KEY")
    secret = os.environ.get("BACKUP_MINIO_SECRET_KEY")
    if access and secret:
        return access, secret

    settings = load_settings()
    auth = load_vault_auth()
    client = create_vault_client(settings.vault, token=auth.token, secret_id=auth.secret_id)
    material = await VaultSecrets(client).get_secret("secret/data/minio")
    return material["access_key"], material["secret_key"]


def _client(config: BackupConfig, access_key: str, secret_key: str) -> Minio:
    return create_minio_client(config.minio, access_key=access_key, secret_key=secret_key)


# ------------------------------------------------------------- preflight --


@dataclass(frozen=True, slots=True)
class Preflight:
    """What the server says about the role that just connected to it."""

    role: str
    bypass_rls: bool
    replication: bool
    server_version: str
    system_identifier: int
    archive_mode: str
    archive_command: str
    wal_level: str
    host: str

    def require_dump(self) -> None:
        if not self.bypass_rls:
            raise BackupError(
                f"refusing to run pg_dump as {self.role}: the role does not have BYPASSRLS. "
                "Every tenant table is under FORCE ROW LEVEL SECURITY, so this dump would "
                "either fail on the first one or -- with --enable-row-security -- exit 0 "
                "having written zero tenant rows (module docstring, measured). "
                f"Fix: ALTER ROLE {self.role} BYPASSRLS;"
            )

    def require_base(self) -> None:
        if not self.replication:
            raise BackupError(
                f"refusing to run pg_basebackup as {self.role}: the role does not have "
                f"REPLICATION. Fix: ALTER ROLE {self.role} REPLICATION;"
            )

    def require_archiving(self) -> None:
        if self.archive_mode != "on":
            raise BackupError(
                "archive_mode is "
                f"{self.archive_mode!r}: this cluster is not archiving WAL, so a base backup "
                "taken now can only ever be restored to the instant it finished -- there is "
                "no point in time to choose. Enable it in docker-compose.yml (postgres "
                "`command:`) and restart the server; it is a START-time setting."
            )


def _engine(config: BackupConfig) -> AsyncEngine:
    """One short-lived, UNPOOLED engine per question.

    `NullPool` for the reason every other `app.ops.*` tool uses it: this
    process asks a handful of questions and exits, so a pool would be
    connections held open for nothing. It also keeps this tool's footprint in
    the connection budget (08 §2-ب) at ONE backend at a time, taken from the
    reserve that ledger already sets aside for administration -- and taken
    DIRECT, so it occupies no `MAX_CLIENT_CONN` seat in front of the pooler.
    """
    config.require_database()
    return create_engine(
        DatabaseSettings(url=_async_url(config.dsn, config.password)), poolclass=NullPool
    )


async def preflight(config: BackupConfig) -> Preflight:
    engine = _engine(config)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT current_user AS role, r.rolbypassrls, r.rolreplication, "
                        "current_setting('server_version') AS version, "
                        "(SELECT system_identifier FROM pg_control_system()) AS sysid, "
                        "current_setting('archive_mode') AS archive_mode, "
                        "current_setting('archive_command') AS archive_command, "
                        "current_setting('wal_level') AS wal_level, "
                        "coalesce(inet_server_addr()::text, 'local') AS host "
                        "FROM pg_roles r WHERE r.rolname = current_user"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    return Preflight(
        role=row.role,
        bypass_rls=bool(row.rolbypassrls),
        replication=bool(row.rolreplication),
        server_version=str(row.version),
        system_identifier=int(row.sysid),
        archive_mode=str(row.archive_mode),
        archive_command=str(row.archive_command),
        wal_level=str(row.wal_level),
        host=str(row.host),
    )


async def _canary_count(config: BackupConfig) -> int:
    engine = _engine(config)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(f"SELECT count(*) FROM {_CANARY_SCHEMA}.{_CANARY_TABLE}")
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


async def archiver_state(config: BackupConfig) -> dict[str, Any]:
    """What the server says it has archived -- and deliberately NOT
    ``failed_count``.

    MEASURED during this step: with a broken ``archive_command`` (exit 126,
    the script not executable) Postgres logged six consecutive
    ``FATAL: archive command failed`` and ``pg_stat_archiver`` still read
    ``archived_count=0, failed_count=0, last_failed_wal=NULL``. An exec
    failure never reaches the counter, so a dashboard built on
    ``failed_count`` shows a healthy archiver on a cluster that has archived
    nothing since it booted. ``archived_count`` moving, and the spool
    draining, are the two facts that cannot be faked that way.
    """
    engine = _engine(config)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT archived_count, last_archived_wal, last_archived_time, "
                        "failed_count, last_failed_wal, "
                        "pg_walfile_name(pg_current_wal_lsn()) AS current_wal "
                        "FROM pg_stat_archiver"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    return {
        "archived_count": int(row.archived_count),
        "last_archived_wal": row.last_archived_wal,
        "last_archived_at": (
            row.last_archived_time.isoformat() if row.last_archived_time else None
        ),
        "current_wal": row.current_wal,
        # Reported, but never the thing this tool judges on -- see the
        # docstring. A non-zero value is real; a zero one proves nothing.
        "failed_count_unreliable": int(row.failed_count),
        "last_failed_wal": row.last_failed_wal,
    }


async def _walfile_name(config: BackupConfig, lsn: str) -> str:
    engine = _engine(config)
    try:
        async with engine.connect() as conn:
            # The parameter is bound as TEXT and cast in SQL, not bound as
            # the `pg_lsn` the function signature implies. asyncpg infers the
            # argument type from the call site and its `pg_lsn` codec wants an
            # int, so both `pg_walfile_name(:lsn)` and
            # `pg_walfile_name(CAST(:lsn AS pg_lsn))` fail identically at run
            # time with "'str' object cannot be interpreted as an integer" --
            # measured, on a call that reads perfectly in both shapes.
            result = await conn.execute(
                text("SELECT pg_walfile_name(CAST(CAST(:lsn AS text) AS pg_lsn))"),
                {"lsn": lsn},
            )
            return str(result.scalar_one())
    finally:
        await engine.dispose()


# ------------------------------------------------------------ subprocess --


def _run(argv: Sequence[str], *, env: dict[str, str], what: str) -> str:
    """Run a libpq tool, and put its OWN diagnostics in the exception.

    `pg_dump`/`pg_basebackup` say precisely what went wrong on stderr and say
    nothing useful in the exit code; swallowing that in favour of "command
    failed with status 1" is how a backup outage becomes a debugging session.
    """
    started = time.monotonic()
    completed = subprocess.run(list(argv), env=env, capture_output=True, text=True, check=False)
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        raise BackupError(
            f"{what} failed after {elapsed:.1f}s (exit {completed.returncode}):\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stderr.strip()


def _pg_env(config: BackupConfig) -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = config.password
    # A backup that hangs forever on an unreachable server is indistinguishable
    # from one that is working; these bound the wait without bounding the copy.
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    return env


# --------------------------------------------------------------- objects --


def _timestamp() -> str:
    """UTC, sortable, filesystem- and S3-safe. Object keys sort
    lexicographically, so this is also the ordering `status` and `prune`
    rely on -- no listing is ever re-sorted by a parsed date."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parse_timestamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        raise BackupError(
            f"backup bucket {bucket!r} does not exist. It is created (and versioned) by "
            "deploy/minio/bootstrap.sh -- run `docker compose up minio-bootstrap`."
        )


def _put_file(client: Minio, bucket: str, key: str, path: Path) -> int:
    client.fput_object(bucket, key, str(path))
    stat = client.stat_object(bucket, key)
    size = int(stat.size or 0)
    if size != path.stat().st_size:
        raise BackupError(
            f"upload of {key} read back {size} bytes for a {path.stat().st_size}-byte file"
        )
    return size


def _put_json(client: Minio, bucket: str, key: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    client.put_object(
        bucket, key, io.BytesIO(body), length=len(body), content_type="application/json"
    )


def _get_json(client: Minio, bucket: str, key: str) -> dict[str, Any]:
    response = client.get_object(bucket, key)
    try:
        return dict(json.loads(response.read().decode("utf-8")))
    finally:
        response.close()
        response.release_conn()


def _set_names(client: Minio, bucket: str, prefix: str) -> list[str]:
    """The immediate children of a prefix -- one per backup set."""
    names: list[str] = []
    for obj in client.list_objects(bucket, prefix=prefix, recursive=False):
        name = (obj.object_name or "").removeprefix(prefix).rstrip("/")
        if name:
            names.append(name)
    return sorted(names)


# ------------------------------------------------------------------ base --


def _parse_backup_manifest(path: Path) -> dict[str, Any]:
    """Pull the two facts that make a base backup addressable from
    ``backup_manifest``: the WAL range it needs, and the cluster it belongs
    to.

    The system identifier is the fact that decides whether a shelf of WAL can
    be replayed onto this copy at all -- see the module docstring. MEASURED on
    PostgreSQL 16.14: the manifest does NOT carry `System-Identifier` (that
    field arrives in 17), so this reads `None` here and the identity recorded
    in our own manifest comes from `pg_control_system()` instead. Absent, not
    invented -- and the restore drill compares against the one that is real.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    ranges = manifest.get("WAL-Ranges") or []
    first = ranges[0] if ranges else {}
    return {
        "system_identifier": manifest.get("System-Identifier"),
        "timeline": first.get("Timeline"),
        "start_lsn": first.get("Start-LSN"),
        "end_lsn": first.get("End-LSN"),
        "files": len(manifest.get("Files") or []),
    }


async def action_base(config: BackupConfig, client: Minio, *, label: str | None) -> dict[str, Any]:
    checks = await preflight(config)
    checks.require_base()
    checks.require_archiving()
    _ensure_bucket(client, config.bucket)

    stamp = _timestamp()
    prefix = f"{BASE_PREFIX}{stamp}/"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aizzak-base-") as tmp:
        staging = Path(tmp)
        _run(
            [
                "pg_basebackup",
                "--dbname",
                config.dsn,
                "--pgdata",
                str(staging),
                "--format",
                "tar",
                "--gzip",
                # -X stream: the segments produced DURING the copy travel with
                # it, so the set is self-contained even if the archiver is
                # behind. Without it a base backup is unrestorable until the
                # archive catches up, and nothing says so at the time.
                "--wal-method",
                "stream",
                # An immediate checkpoint. The alternative (spread) waits for
                # up to `checkpoint_timeout` before the copy even starts,
                # which for a scheduled backup is dead time, not smoothing.
                "--checkpoint",
                "fast",
                "--label",
                label or f"aizzak-{stamp}",
                "--no-password",
            ],
            env=_pg_env(config),
            what="pg_basebackup",
        )
        copy_seconds = time.monotonic() - started

        produced = sorted(p.name for p in staging.iterdir())
        unexpected = [name for name in produced if name not in _BASE_ARTIFACTS]
        if unexpected:
            raise BackupError(
                f"pg_basebackup produced unexpected files {unexpected}. A tablespace map "
                "means this cluster has tablespaces outside PGDATA, which this tool has "
                "never seen and must not silently leave out of the set."
            )
        missing = [name for name in _BASE_ARTIFACTS if name not in produced]
        if missing:
            raise BackupError(f"pg_basebackup did not produce {missing}")

        details = _parse_backup_manifest(staging / "backup_manifest")
        start_wal = (
            await _walfile_name(config, details["start_lsn"]) if details.get("start_lsn") else None
        )

        sizes = {
            name: _put_file(client, config.bucket, f"{prefix}{name}", staging / name)
            for name in _BASE_ARTIFACTS
        }

    manifest = {
        "kind": "base",
        "created_at": datetime.now(UTC).isoformat(),
        "label": label or f"aizzak-{stamp}",
        "server_version": checks.server_version,
        "system_identifier": checks.system_identifier,
        "manifest_system_identifier": details.get("system_identifier"),
        "timeline": details.get("timeline"),
        "start_lsn": details.get("start_lsn"),
        "end_lsn": details.get("end_lsn"),
        "start_wal_file": start_wal,
        "data_files": details.get("files"),
        "bytes": sizes,
        "copy_seconds": round(copy_seconds, 3),
        "total_seconds": round(time.monotonic() - started, 3),
    }
    _put_json(client, config.bucket, f"{prefix}{MANIFEST_NAME}", manifest)
    return manifest


# ------------------------------------------------------------------ dump --


def _dump_row_count(dump: Path, env: dict[str, str]) -> int:
    """Rows the dump actually carries for the canary table.

    `pg_restore --data-only` writes the table's COPY block to stdout; the
    rows are the lines between the COPY header and the terminating `\\.`.
    This is the whole point of the check -- an empty block is a syntactically
    perfect dump of nothing (module docstring).
    """
    completed = subprocess.run(
        [
            "pg_restore",
            "--data-only",
            "--schema",
            _CANARY_SCHEMA,
            "--table",
            _CANARY_TABLE,
            # `-f -` for stdout, and it is REQUIRED: without one of
            # -d/--dbname or -f/--file, pg_restore refuses outright rather
            # than defaulting to stdout ("one of -d/--dbname and -f/--file
            # must be specified"). Measured, at the end of a two-minute dump.
            "--file",
            "-",
            str(dump),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BackupError(f"pg_restore could not read back the dump:\n{completed.stderr.strip()}")
    rows = 0
    inside = False
    for line in completed.stdout.splitlines():
        if not inside and line.startswith("COPY "):
            inside = True
            continue
        if inside:
            if line == "\\.":
                inside = False
                continue
            rows += 1
    return rows


async def action_dump(config: BackupConfig, client: Minio) -> dict[str, Any]:
    checks = await preflight(config)
    checks.require_dump()
    _ensure_bucket(client, config.bucket)

    live_rows = await _canary_count(config)
    stamp = _timestamp()
    prefix = f"{DUMP_PREFIX}{stamp}/"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aizzak-dump-") as tmp:
        dump = Path(tmp) / "aizzak.dump"
        _run(
            [
                "pg_dump",
                "--dbname",
                config.dsn,
                "--format",
                "custom",
                "--compress",
                "9",
                # NOT --enable-row-security. That flag is what turns the loud
                # failure into a silent empty dump (module docstring); the
                # role's BYPASSRLS attribute is what makes it unnecessary.
                "--file",
                str(dump),
                "--no-password",
            ],
            env=_pg_env(config),
            what="pg_dump",
        )
        dump_seconds = time.monotonic() - started
        dumped_rows = _dump_row_count(dump, _pg_env(config))
        if live_rows > 0 and dumped_rows == 0:
            raise BackupError(
                f"the dump carries ZERO rows for {_CANARY_SCHEMA}.{_CANARY_TABLE} while the "
                f"live table has {live_rows}. This is the row-level-security failure the "
                "module docstring describes, and the dump is being discarded rather than "
                "uploaded."
            )
        size = _put_file(client, config.bucket, f"{prefix}aizzak.dump", dump)

    manifest = {
        "kind": "dump",
        "created_at": datetime.now(UTC).isoformat(),
        "server_version": checks.server_version,
        "system_identifier": checks.system_identifier,
        "bytes": {"aizzak.dump": size},
        "canary": {
            "table": f"{_CANARY_SCHEMA}.{_CANARY_TABLE}",
            "live_rows": live_rows,
            "dumped_rows": dumped_rows,
        },
        "dump_seconds": round(dump_seconds, 3),
        "total_seconds": round(time.monotonic() - started, 3),
        # Stated in the artifact itself, so an operator reading a manifest in
        # an incident is not left to infer it: this file cannot consume the
        # `wal/` prefix beside it.
        "pitr": False,
        "note": "logical dump: restores to its own instant only. PITR uses base/ + wal/.",
    }
    _put_json(client, config.bucket, f"{prefix}{MANIFEST_NAME}", manifest)
    return manifest


# ------------------------------------------------------------------- wal --


def segment_sequence(name: str) -> int | None:
    """Position of a WAL segment within its timeline, as one integer.

    A segment name is timeline(8) + log id(8) + segment number(8) in hex.
    With the default 16MB segment size a log id holds 256 of them, and since
    PG 9.3 the last one (``...FF``) is used like any other -- so the sequence
    is simply ``log_id * 256 + segment_number`` and two consecutive segments
    differ by exactly one. `status` uses this to report GAPS, which is the
    only property of an archive that matters: recovery stops at the first
    missing segment and no amount of later ones helps.
    """
    if not _SEGMENT_RE.match(name):
        return None
    log_id = int(name[8:16], 16)
    segment = int(name[16:24], 16)
    if segment >= _SEGMENTS_PER_LOG_ID:
        return None
    return log_id * _SEGMENTS_PER_LOG_ID + segment


def _spooled(spool: Path) -> list[Path]:
    if not spool.is_dir():
        return []
    return sorted(p for p in spool.iterdir() if p.is_file() and not p.name.startswith("."))


def ship_spool(config: BackupConfig, client: Minio) -> dict[str, Any]:
    """Move every completed file out of the spool and into object storage.

    Compressed on the way: a 16MB segment from a mostly-idle cluster is
    mostly zeroes, and `archive_timeout` forces a switch whether or not there
    was traffic. Deleted from the spool only after `stat_object` reads the
    uploaded size back -- an upload this process believes in but the object
    store never saw is the one failure that would silently break the chain.
    """
    shipped: list[str] = []
    skipped: list[str] = []
    total_in = 0
    total_out = 0
    for path in _spooled(config.spool):
        key = f"{WAL_PREFIX}{path.name}.gz"
        raw = path.stat().st_size
        with tempfile.NamedTemporaryFile(prefix="aizzak-wal-", suffix=".gz", delete=False) as tmp:
            staged = Path(tmp.name)
        try:
            with path.open("rb") as source, gzip.open(staged, "wb", compresslevel=6) as sink:
                shutil.copyfileobj(source, sink)
            _put_file(client, config.bucket, key, staged)
        finally:
            staged.unlink(missing_ok=True)
        try:
            path.unlink()
        except OSError as exc:  # pragma: no cover - permission drift on the volume
            skipped.append(path.name)
            _logger.warning(
                "ops.backup.spool_unlink_failed",
                extra={"file": path.name, "error": str(exc)},
            )
            continue
        shipped.append(path.name)
        total_in += raw
        total_out += client.stat_object(config.bucket, key).size or 0
    return {
        "shipped": len(shipped),
        "segments": shipped,
        "undeleted": skipped,
        "bytes_in": total_in,
        "bytes_out": total_out,
        "spool_remaining": len(_spooled(config.spool)),
    }


def action_wal(
    config: BackupConfig,
    client: Minio,
    *,
    follow: bool,
    interval: float,
) -> dict[str, Any]:
    _ensure_bucket(client, config.bucket)
    if not follow:
        return ship_spool(config, client)

    # The standing form. `archive_command` cannot fail gracefully -- Postgres
    # retries forever and keeps every unarchived segment in pg_wal -- so this
    # loop logs and continues rather than exiting on a transient object-store
    # error, and lets the spool depth (reported by `status`) be the signal.
    #
    # It beats once per COMPLETED cycle, and the beat sits inside the `try` on
    # purpose: a cycle that raised shipped nothing, and a heartbeat that kept
    # ticking through a failing object store would report the exact
    # false-healthy the mechanism exists to prevent (ت-3). The container goes
    # unhealthy after `HEARTBEAT_MAX_AGE_S` instead.
    heartbeat = build_heartbeat(load_settings().health.heartbeat_dir, "wal-shipper")
    _logger.info("ops.backup.wal_follow_started", extra={"interval_s": interval})
    while True:
        try:
            result = ship_spool(config, client)
            if result["shipped"]:
                _logger.info("ops.backup.wal_shipped", extra=dict(result, segments=None))
            heartbeat.beat()
        except Exception as exc:
            _logger.error("ops.backup.wal_ship_failed", extra={"error": str(exc)})
        time.sleep(interval)


# ---------------------------------------------------------------- qdrant --


def _qdrant_collections(base_url: str, timeout: float) -> list[str]:
    response = httpx.get(f"{base_url}/collections", timeout=timeout)
    response.raise_for_status()
    return sorted(c["name"] for c in response.json()["result"]["collections"])


def action_qdrant(
    config: BackupConfig,
    client: Minio,
    *,
    collections: Sequence[str] | None,
    limit: int | None,
    timeout: float,
) -> dict[str, Any]:
    """Per-collection snapshots, not one whole-storage snapshot.

    A collection here is a tenant (`kn-<workspace>`, 07 §5), so a
    per-collection snapshot is the only shape that can restore ONE tenant --
    which is the restore an operator actually performs after a bad
    re-index or a workspace-level mistake. A whole-storage snapshot can only
    be restored by replacing every tenant's vectors at once.
    """
    _ensure_bucket(client, config.bucket)
    names = list(collections) if collections else _qdrant_collections(config.qdrant_url, timeout)
    if limit is not None:
        names = names[:limit]

    stamp = _timestamp()
    prefix = f"{QDRANT_PREFIX}{stamp}/"
    started = time.monotonic()
    taken: dict[str, int] = {}
    with httpx.Client(base_url=config.qdrant_url, timeout=timeout) as http:
        for name in names:
            created = http.post(f"/collections/{name}/snapshots", params={"wait": "true"})
            created.raise_for_status()
            snapshot = created.json()["result"]["name"]
            try:
                with tempfile.NamedTemporaryFile(
                    prefix="aizzak-qdrant-", suffix=".snapshot", delete=False
                ) as tmp:
                    staged = Path(tmp.name)
                try:
                    with (
                        http.stream("GET", f"/collections/{name}/snapshots/{snapshot}") as body,
                        staged.open("wb") as sink,
                    ):
                        body.raise_for_status()
                        for chunk in body.iter_bytes(chunk_size=1 << 20):
                            sink.write(chunk)
                    taken[name] = _put_file(
                        client, config.bucket, f"{prefix}{name}.snapshot", staged
                    )
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                # The snapshot lives in Qdrant's own storage until deleted;
                # 202 of them is a second copy of the whole vector store on
                # the same disk the first copy is on.
                http.delete(f"/collections/{name}/snapshots/{snapshot}")

    manifest = {
        "kind": "qdrant",
        "created_at": datetime.now(UTC).isoformat(),
        "collections": len(taken),
        "bytes": sum(taken.values()),
        "total_seconds": round(time.monotonic() - started, 3),
        "partial": limit is not None or collections is not None,
    }
    _put_json(client, config.bucket, f"{prefix}{MANIFEST_NAME}", manifest)
    return manifest


# ---------------------------------------------------------------- status --


@dataclass(frozen=True, slots=True)
class WalInventory:
    segments: list[str]
    gaps: list[tuple[str, str]]
    others: list[str]

    @property
    def oldest(self) -> str | None:
        return self.segments[0] if self.segments else None

    @property
    def newest(self) -> str | None:
        return self.segments[-1] if self.segments else None


def wal_inventory(names: Iterable[str]) -> WalInventory:
    """Sort the archive and name every hole in it.

    Gaps are reported as the pair that straddles them rather than as a count,
    because the pair is what an operator needs: recovery from a base backup
    older than the first name in a pair can never pass it.
    """
    segments: list[str] = []
    others: list[str] = []
    for name in names:
        (segments if _SEGMENT_RE.match(name) else others).append(name)
    segments.sort()
    gaps: list[tuple[str, str]] = []
    for previous, current in itertools.pairwise(segments):
        left, right = segment_sequence(previous), segment_sequence(current)
        if left is None or right is None:
            continue
        if right != left + 1:
            gaps.append((previous, current))
    return WalInventory(segments=segments, gaps=gaps, others=sorted(others))


def _archived_names(client: Minio, bucket: str) -> list[str]:
    return [
        (obj.object_name or "").removeprefix(WAL_PREFIX).removesuffix(".gz")
        for obj in client.list_objects(bucket, prefix=WAL_PREFIX, recursive=True)
    ]


def _read_manifests(client: Minio, bucket: str, prefix: str) -> list[dict[str, Any]]:
    sets: list[dict[str, Any]] = []
    for name in _set_names(client, bucket, prefix):
        try:
            manifest = _get_json(client, bucket, f"{prefix}{name}/{MANIFEST_NAME}")
        except Exception:
            manifest = {"kind": prefix.rstrip("/"), "incomplete": True}
        manifest["set"] = name
        sets.append(manifest)
    return sets


async def action_status(config: BackupConfig, client: Minio) -> dict[str, Any]:
    _ensure_bucket(client, config.bucket)
    bases = _read_manifests(client, config.bucket, BASE_PREFIX)
    dumps = _read_manifests(client, config.bucket, DUMP_PREFIX)
    vectors = _read_manifests(client, config.bucket, QDRANT_PREFIX)
    archive = wal_inventory(_archived_names(client, config.bucket))
    spooled = _spooled(config.spool)
    now = datetime.now(UTC)

    def _age_hours(sets: list[dict[str, Any]]) -> float | None:
        if not sets:
            return None
        stamp = _parse_timestamp(str(sets[-1]["set"]))
        return None if stamp is None else round((now - stamp).total_seconds() / 3600, 2)

    oldest_base = bases[0] if bases else None
    return {
        "bucket": config.bucket,
        "archiver": await archiver_state(config),
        "base": {
            "sets": len(bases),
            "newest_age_hours": _age_hours(bases),
            "oldest_set": oldest_base["set"] if oldest_base else None,
            "oldest_needs_wal_from": (oldest_base or {}).get("start_wal_file"),
        },
        "dump": {"sets": len(dumps), "newest_age_hours": _age_hours(dumps)},
        "qdrant": {"sets": len(vectors), "newest_age_hours": _age_hours(vectors)},
        "wal": {
            "archived": len(archive.segments),
            "oldest": archive.oldest,
            "newest": archive.newest,
            "gaps": [list(pair) for pair in archive.gaps],
            "other_files": archive.others,
        },
        "spool": {
            "path": str(config.spool),
            "files": len(spooled),
            "oldest_age_seconds": (
                round(time.time() - min(p.stat().st_mtime for p in spooled)) if spooled else None
            ),
        },
        # The one line an operator reads first. A base backup with a gap in
        # the WAL after it cannot reach `now`, and neither can one whose
        # starting segment was pruned away.
        "pitr_possible": bool(bases) and not archive.gaps,
    }


# ----------------------------------------------------------------- prune --


@dataclass(frozen=True, slots=True)
class PrunePlan:
    base_sets: list[str]
    dump_sets: list[str]
    qdrant_sets: list[str]
    wal_segments: list[str]
    wal_floor: str | None


def _expired(sets: list[dict[str, Any]], retention: timedelta, now: datetime) -> list[str]:
    """Sets older than `retention`, never dipping below `MIN_KEPT_SETS`.

    The floor is applied to the SURVIVORS, not to the candidates: with one
    ancient set on the shelf and nothing newer, the answer is "keep it", not
    "the policy says delete it".
    """
    ordered = sorted(sets, key=lambda item: str(item["set"]))
    keepable = len(ordered)
    doomed: list[str] = []
    for item in ordered:
        if keepable <= MIN_KEPT_SETS:
            break
        stamp = _parse_timestamp(str(item["set"]))
        if stamp is None or now - stamp <= retention:
            continue
        doomed.append(str(item["set"]))
        keepable -= 1
    return doomed


def plan_prune(
    *,
    bases: list[dict[str, Any]],
    dumps: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    archived: list[str],
    now: datetime,
) -> PrunePlan:
    """Decide what may go, and tie WAL to the base backups rather than to the
    clock (module docstring)."""
    doomed_bases = _expired(bases, BASE_RETENTION, now)
    survivors = [item for item in bases if str(item["set"]) not in doomed_bases]
    floors = [
        str(item["start_wal_file"])
        for item in survivors
        if item.get("start_wal_file") and _SEGMENT_RE.match(str(item["start_wal_file"]))
    ]
    floor = min(floors) if floors else None
    # No surviving base backup names a starting segment -> nothing about the
    # archive is safe to delete. Refusing to prune WAL is a bounded cost;
    # pruning the segment a restore needs is not.
    doomed_wal = (
        [name for name in sorted(archived) if _SEGMENT_RE.match(name) and name < floor]
        if floor
        else []
    )
    return PrunePlan(
        base_sets=doomed_bases,
        dump_sets=_expired(dumps, DUMP_RETENTION, now),
        qdrant_sets=_expired(vectors, QDRANT_RETENTION, now),
        wal_segments=doomed_wal,
        wal_floor=floor,
    )


def _delete_prefix(client: Minio, bucket: str, prefix: str) -> int:
    keys = [
        DeleteObject(obj.object_name)
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True)
        if obj.object_name
    ]
    if not keys:
        return 0
    errors = list(client.remove_objects(bucket, keys))
    if errors:
        raise BackupError(f"could not delete {prefix}: {[str(e) for e in errors]}")
    return len(keys)


def action_prune(config: BackupConfig, client: Minio, *, dry_run: bool) -> dict[str, Any]:
    _ensure_bucket(client, config.bucket)
    bases = _read_manifests(client, config.bucket, BASE_PREFIX)
    plan = plan_prune(
        bases=bases,
        dumps=_read_manifests(client, config.bucket, DUMP_PREFIX),
        vectors=_read_manifests(client, config.bucket, QDRANT_PREFIX),
        archived=_archived_names(client, config.bucket),
        now=datetime.now(UTC),
    )
    deleted = 0
    if not dry_run:
        for name in plan.base_sets:
            deleted += _delete_prefix(client, config.bucket, f"{BASE_PREFIX}{name}/")
        for name in plan.dump_sets:
            deleted += _delete_prefix(client, config.bucket, f"{DUMP_PREFIX}{name}/")
        for name in plan.qdrant_sets:
            deleted += _delete_prefix(client, config.bucket, f"{QDRANT_PREFIX}{name}/")
        if plan.wal_segments:
            errors = list(
                client.remove_objects(
                    config.bucket,
                    [DeleteObject(f"{WAL_PREFIX}{name}.gz") for name in plan.wal_segments],
                )
            )
            if errors:
                raise BackupError(f"could not delete WAL: {[str(e) for e in errors]}")
            deleted += len(plan.wal_segments)
    return {
        "dry_run": dry_run,
        "base_sets": plan.base_sets,
        "dump_sets": plan.dump_sets,
        "qdrant_sets": plan.qdrant_sets,
        "wal_floor": plan.wal_floor,
        "wal_segments": len(plan.wal_segments),
        "objects_deleted": deleted,
    }


# ------------------------------------------------------------------- cli --


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


async def _act(args: argparse.Namespace) -> int:
    config = load_config()
    if args.action == "preflight":
        checks = await preflight(config)
        _print(
            {
                "role": checks.role,
                "bypass_rls": checks.bypass_rls,
                "replication": checks.replication,
                "server_version": checks.server_version,
                "system_identifier": checks.system_identifier,
                "archive_mode": checks.archive_mode,
                "archive_command": checks.archive_command,
                "wal_level": checks.wal_level,
                "spool": str(config.spool),
            }
        )
        return 0

    access_key, secret_key = await _minio_credentials()
    client = _client(config, access_key, secret_key)

    if args.action == "base":
        _print(await action_base(config, client, label=args.label))
    elif args.action == "dump":
        _print(await action_dump(config, client))
    elif args.action == "wal":
        result = action_wal(config, client, follow=args.follow, interval=args.interval)
        _print(result)
    elif args.action == "qdrant":
        _print(
            action_qdrant(
                config,
                client,
                collections=args.collection or None,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
    elif args.action == "full":
        # Order is load-bearing: the base backup fixes the point every later
        # WAL segment builds on, so shipping the spool AFTER it is what makes
        # the set immediately restorable to "now".
        base = await action_base(config, client, label=args.label)
        dump = await action_dump(config, client)
        vectors = (
            None
            if args.skip_qdrant
            else action_qdrant(
                config, client, collections=None, limit=args.limit, timeout=args.timeout
            )
        )
        shipped = ship_spool(config, client)
        _print({"base": base, "dump": dump, "qdrant": vectors, "wal": shipped})
    elif args.action == "status":
        _print(await action_status(config, client))
    elif args.action == "prune":
        _print(action_prune(config, client, dry_run=args.dry_run))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.backup",
        description="Backup and point-in-time restore for Postgres, its WAL, and Qdrant "
        "(capacity step 2.5 -- module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser(
        "preflight",
        help="what the server says about this role and its archiving -- writes nothing",
    )

    base_parser = sub.add_parser("base", help="physical base backup (the PITR anchor)")
    base_parser.add_argument("--label", default=None, help="backup label (default: aizzak-<stamp>)")

    sub.add_parser("dump", help="logical pg_dump, verified by reading one table back out of it")

    wal_parser = sub.add_parser("wal", help="ship the archive spool to object storage")
    wal_parser.add_argument(
        "--follow",
        action="store_true",
        help="keep shipping every --interval seconds (the standing service's form)",
    )
    wal_parser.add_argument("--interval", type=float, default=_DEFAULT_FOLLOW_INTERVAL_S)

    qdrant_parser = sub.add_parser("qdrant", help="per-collection Qdrant snapshots")
    qdrant_parser.add_argument(
        "--collection", action="append", default=[], help="snapshot ONE collection (repeatable)"
    )
    qdrant_parser.add_argument(
        "--limit", type=int, default=None, help="snapshot at most N collections"
    )
    qdrant_parser.add_argument("--timeout", type=float, default=300.0)

    full_parser = sub.add_parser("full", help="base + dump + qdrant + ship the spool")
    full_parser.add_argument("--label", default=None)
    full_parser.add_argument("--limit", type=int, default=None)
    full_parser.add_argument("--timeout", type=float, default=300.0)
    full_parser.add_argument(
        "--skip-qdrant", action="store_true", help="Postgres only (the vector store is derived)"
    )

    sub.add_parser("status", help="inventory, WAL gaps, spool depth, and whether PITR is possible")

    prune_parser = sub.add_parser("prune", help="apply the retention windows")
    prune_parser.add_argument(
        "--dry-run", action="store_true", help="print what WOULD be deleted and delete nothing"
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_act(args)))
    except BackupError as exc:
        print(f"backup: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
