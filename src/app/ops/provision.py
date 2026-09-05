"""Database provisioning — migrations, then grants (7.1).

**The step 01-data-model §6 deliberately keeps out of the schema.** No
migration issues a ``GRANT``: "ترتيب البذر: schemas → جداول المنصّة → جداول
الوحدات → سياسات RLS → الأدوار/الصلاحيات (`app_rw`)" makes the last item a
runbook step, not DDL. Until 7.1 the ONLY place that step existed was
``tests/integration/conftest.py`` — so a container that ran
``alembic upgrade`` and nothing else came up with an ``app_rw`` role holding
no privilege on any table, and answered ``permission denied`` on the first
request that touched one. This module is that missing operational artifact,
and it is the SINGLE source of truth: the live test harness imports the same
constants rather than keeping its own copy.

**``alembic upgrade head`` is not a command this repository has.** The
platform runs TWELVE independent chains (DAT-03: one ``version_table_schema``
per module, 01 §6), so ``head`` is ambiguous and Alembic refuses it with "Multiple head
revisions are present". The real invocation is ``platform@head`` followed by
each module's ``-x vts=<module> upgrade <module>@head`` — the sequence
``MIGRATION_CHAINS`` below encodes, and the one 08-local-runbook §3 step 5
abbreviates.

**Roles are NOT created here.** ``CREATE ROLE`` is a cluster-wide privilege
that ``aizzak_owner`` deliberately does not hold (it owns every table, which
is all it needs to ``GRANT`` on them). Role creation belongs to cluster
init — ``deploy/postgres/initdb/`` in the Compose topology, the local
superuser in the test harness. This module verifies the roles exist and
fails with a message naming that step, rather than failing statement by
statement halfway through.

**Every replica runs this, and until capacity step 2.9 that was a race**
(``ح-18``). Measured on this stack, three simultaneous ``python -m
app.ops.provision`` against one database:

* against an EMPTY database, two of the three die immediately --
  ``UniqueViolationError`` on ``pg_type_typname_nsp_index``, key
  ``(alembic_version, 2200)``: all three try to create the version table.
* against an ALREADY-MIGRATED database -- the ordinary rolling-deploy case,
  where there is nothing at all to migrate -- one of the three still dies, and
  it dies in ``apply_grants``: ``tuple concurrently updated`` (``XX000``) on
  ``GRANT USAGE ON SCHEMA spaces TO app_rw``. Two concurrent ``GRANT`` statements on
  one catalog row is an internal error, not a serialisation failure, so no
  ordinary retry rule would even recognise it as retryable.

The second measurement is the one that matters, and it falsifies a sentence
that used to stand a few lines below this one: ``apply_grants`` called itself
"idempotent ... safe on every deploy". It is idempotent for ONE runner and
unsafe for two, and repeated grants were never the hazard -- concurrent ones
were. Since ``migrate`` is a ``service_completed_successfully`` dependency of
every long-running service, a replica whose provisioning step exits non-zero
never starts at all; that is why ``ح-18`` says this alone makes step 7.2
impossible.

The fix is one session-level advisory lock held across all three phases (see
``_hold_provision_lock``). Losers WAIT rather than skip: a replica that
skipped could start serving against a schema the winner has not finished
migrating, which is the failure the lock exists to prevent, wearing a
different hat.

Run as ``aizzak_owner``: ``DATABASE_URL`` for THIS process is the owner DSN,
the same per-process convention ``workers/bootstrap.py`` already documents
for the relay's own role.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from argparse import Namespace
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings, MigrationSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine

_logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]

APP_ROLE = "app_rw"
RELAY_ROLE = "outbox_relay"
# P1-5 (docs/p1-hardening-plan.md §3 step 8): the retention sweep's own role
# -- see `_app_rw_grants`'s docstring for why this could not just be a wider
# `app_rw` grant, and `RETENTION_GRANTS` below for what it actually holds.
RETENTION_ROLE = "retention_sweeper"
# P1-3 (docs/p1-hardening-plan.md §3 step 10): the `/metrics` endpoint's own
# read of `platform.outbox` -- `_app_rw_grants`'s docstring names why this
# cannot be a wider `app_rw` grant either. See `METRICS_GRANTS` below for
# what it actually holds.
METRICS_ROLE = "metrics_reader"
# P1-9 (docs/p1-hardening-plan.md §3 step 12, `app.ops.rotate_transit`): the
# Transit key-rotation sweep's own role. It needs cross-tenant SELECT/UPDATE
# reach on the three ciphertext-bearing columns -- the same "RLS wall"
# `retention_sweeper` hit (below), resolved the identical way: its own role
# and its own migration-added policies, never a widened `app_rw`. See
# `TRANSIT_ROTATOR_GRANTS` for what it actually holds.
TRANSIT_ROTATOR_ROLE = "transit_rotator"
# BE-ADM-014 (docs/design refs, `app.ops.purge`): the workspace content-purge
# sweep's own role. A workspace tombstoned by BE-ADM-006 and past its
# retention window is emptied across eleven schemas, and the SAME "RLS wall"
# `retention_sweeper`/`transit_rotator` hit applies here too -- widening
# `app_rw` to reach every tenant's rows would undo the isolation guarantee the
# whole platform stands on. Unlike those two, this role gets NO blanket
# `USING (true)` RLS carve-out on the tables it deletes from (see
# `PURGE_GRANTS`'s own docstring): it sets `app.workspace_id` per workspace and
# relies on each table's ordinary `tenant_isolation` policy, so a forgotten
# `set_config` fails safe to zero affected rows rather than reaching every
# tenant at once. The ONE carve-out it does get --
# `migrations/versions/workspace/0009_workspace_purge.py`'s
# `workspace_purger_select` policy on `workspace.users` -- is SELECT-only and
# exists purely so `app.ops.purge.find_candidates` can scan for eligible
# workspaces cross-tenant before any workspace is chosen to purge.
PURGE_ROLE = "workspace_purger"

# The platform baseline chain first (it creates every schema and the
# ``platform`` tables the module chains' triggers reference), then one entry
# per module chain. ``None`` marks the baseline: its own commands never pass
# ``-x vts=``, so ``version_table_schema`` stays Alembic's default.
MIGRATION_CHAINS: tuple[tuple[str, str | None], ...] = (
    ("platform@head", None),
    # `spaces` comes immediately after the baseline, ahead of every chain that
    # grew a `space_id` column (docs/spaces-backend-plan.md step 4): those
    # migrations backfill against real space rows, so the table they read must
    # already be there. No FK enforces this order (the plan keeps cross-schema
    # references logical, `conversation_files`'s precedent) -- which is exactly
    # why the order is stated here rather than left to Alembic to discover.
    ("spaces@head", "spaces"),
    ("media@head", "media"),
    # `workspace` is the OTHER chain those three backfills read, and for a
    # sharper reason than convenience: `workspace.workspaces` carries no RLS
    # (`workspace/0001_workspace.py` R2), so it is the only table a migrator
    # that is neither superuser nor BYPASSRLS can enumerate before it knows
    # which workspace to set `app.workspace_id` to. Empty here means every
    # backfill silently does nothing.
    ("workspace@head", "workspace"),
    ("credentials@head", "credentials"),
    ("access@head", "access"),
    ("conversations@head", "conversations"),
    ("files@head", "files"),
    ("memory@head", "memory"),
    ("knowledge@head", "knowledge"),
    ("integrations@head", "integrations"),
    ("usage@head", "usage"),
)

# Every tenant table of all eleven modules gets full CRUD: `app_rw` is
# NOINHERIT, without BYPASSRLS and not a table owner, so the RLS policy —
# not the grant — is what confines it to one workspace (01 §3).
_TENANT_TABLES: tuple[str, ...] = (
    "spaces.spaces",
    "workspace.workspaces",
    "workspace.users",
    # The heartbeat upsert runs as `app_rw` under tenant RLS, so it needs the
    # same CRUD as any other tenant table; the platform directory's read of it
    # goes through the separate `platform_admin_read` policy, not a grant.
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
    # P-14 (rag-indexing-plan.md §3.2, step 6,
    # `migrations/versions/knowledge/0005_parent_chunks.py`) -- same CRUD as
    # every other tenant table; RLS, not the grant, confines it to one
    # workspace.
    "knowledge.parent_chunks",
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
    # capacity-plan 2.7 -- the in-flight admission a quota check takes out
    # (`migrations/versions/usage/0003_usage_reservations.py`). Full CRUD
    # like every other tenant table: `app_rw` INSERTs its own reservation
    # and DELETEs it when the charge lands, and RLS -- not the grant --
    # confines both to one workspace.
    "usage.reservations",
)

_MODULE_SCHEMAS: tuple[str, ...] = (
    "spaces",
    "workspace",
    "access",
    "credentials",
    "conversations",
    "memory",
    "files",
    "knowledge",
    "media",
    "integrations",
    "usage",
)


def _app_rw_grants() -> tuple[str, ...]:
    """USAGE on every module schema + full CRUD on every tenant table, plus
    the two deliberate ``platform`` exceptions.

    ``platform.outbox`` is INSERT-only: the app is a PRODUCER (D-18), and a
    producer able to UPDATE ``published_at`` could make an event vanish
    unpublished — SELECT/UPDATE there belong to ``outbox_relay`` alone.
    ``platform.processed_events`` is INSERT-only for the same reason: the
    claim is a bare INSERT with ``23505`` consumed as "duplicate", so a role
    that can only INSERT can neither enumerate the ledger nor un-process an
    event to force a replay.

    ``platform.idempotency_keys`` (3.79) is the ONE ``platform`` table that
    gets full CRUD, and it is not an inconsistency: unlike the two above, its
    entire purpose is to be READ BACK (the stored first response), UPDATEd (the
    response is filled in after the operation) and DELETEd (an unfinished claim
    is released when the operation raised, so one 500 does not brick a key
    forever). What confines it is not the grant but RLS — it is the only
    ``platform`` table with a tenant policy, so ``app_rw`` sees one workspace's
    rows at a time exactly as it does for a module table.

    ``platform.stream_offsets`` is granted NOTHING, deliberately. It is the
    "اختياري للمراقبة" table of 01 §4.3 and no code in ``src/`` reads or
    writes it; a privilege nobody exercises is privilege for free.

    Neither ``outbox`` nor ``processed_events`` grow a DELETE grant here for
    the retention sweep either (P1-5, ``docs/p1-hardening-plan.md`` §3 step
    8) -- that would undo the exact guarantee this docstring's first
    paragraph names. The sweep gets its OWN role instead; see
    ``RETENTION_GRANTS``.
    """
    statements = [f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}" for schema in _MODULE_SCHEMAS]
    statements.append(f"GRANT USAGE ON SCHEMA platform TO {APP_ROLE}")
    statements.append(f"GRANT INSERT ON platform.outbox TO {APP_ROLE}")
    statements.append(f"GRANT INSERT ON platform.processed_events TO {APP_ROLE}")
    statements.append(f"GRANT INSERT ON platform.admin_audit_log TO {APP_ROLE}")
    statements.append(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON platform.idempotency_keys TO {APP_ROLE}"
    )
    statements += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}" for table in _TENANT_TABLES
    ]
    return tuple(statements)


APP_RW_GRANTS: tuple[str, ...] = _app_rw_grants()

# The mirror image of app_rw's INSERT-only outbox grant (5.1-ب).
OUTBOX_RELAY_GRANTS: tuple[str, ...] = (
    f"GRANT USAGE ON SCHEMA platform TO {RELAY_ROLE}",
    f"GRANT SELECT, UPDATE ON platform.outbox TO {RELAY_ROLE}",
)

# P1-5's retention role (docs/p1-hardening-plan.md §3 step 8, `app.ops.
# retention`): SELECT (dry-run counts) + DELETE (the actual sweep) on the
# three unbounded ledgers, and NOTHING else -- no INSERT/UPDATE anywhere, so
# a compromised sweeper can shrink these tables but never forge or alter a
# row. On `platform.idempotency_keys`, this DELETE/SELECT reach only becomes
# cross-tenant through the role-scoped RLS policies added by
# `migrations/versions/platform/0003_retention_sweep.py`
# (`retention_sweeper_select`/`retention_sweeper_delete`) -- the grant alone
# would still leave the role bound to `tenant_isolation` like any other.
#
# Capacity step 2.8 (`ح-16`) adds a FOURTH target, `usage.usage_records`: the
# append-only metering ledger, the one of 2.8's three named tables that had no
# retention policy of any kind. Its reach is cross-tenant only through the
# role-scoped policies `migrations/versions/usage/0004_usage_autovacuum.py`
# adds (the `0003_retention_sweep.py` mechanism, applied to a second schema) --
# every `usage` table is under FORCE ROW LEVEL SECURITY, so the grant alone
# would leave this role confined to `tenant_isolation` like any other.
#
# `usage.usage_rollups` is deliberately NOT granted. It is the aggregate the
# quota check reads (step 2.7's `reserve`), so deleting a row there does not
# age out history -- it hands a workspace back headroom it already spent. The
# ledger is the detail and can expire; the rollup is the balance and cannot.
RETENTION_GRANTS: tuple[str, ...] = (
    f"GRANT USAGE ON SCHEMA platform TO {RETENTION_ROLE}",
    f"GRANT SELECT, DELETE ON platform.outbox TO {RETENTION_ROLE}",
    f"GRANT SELECT, DELETE ON platform.processed_events TO {RETENTION_ROLE}",
    f"GRANT SELECT, DELETE ON platform.idempotency_keys TO {RETENTION_ROLE}",
    f"GRANT USAGE ON SCHEMA usage TO {RETENTION_ROLE}",
    f"GRANT SELECT, DELETE ON usage.usage_records TO {RETENTION_ROLE}",
)

# P1-3's `/metrics` role (docs/p1-hardening-plan.md §3 step 10,
# `app.infrastructure.monitoring.metrics_source.SqlRedisMetricsSource`):
# SELECT-only on `platform.outbox`, nothing else -- narrower than
# `outbox_relay` (which also needs UPDATE to stamp `published_at`) and
# narrower than `retention_sweeper` (no DELETE, no reach on the other two
# ledgers -- this role never needs to enumerate or sweep them). `platform
# .outbox` carries no RLS policy (unlike `idempotency_keys`), so this grant
# alone is the role's whole reach; no migration is needed the way
# `0003_retention_sweep.py` needed one for the RLS carve-out.
METRICS_GRANTS: tuple[str, ...] = (
    f"GRANT USAGE ON SCHEMA platform TO {METRICS_ROLE}",
    f"GRANT SELECT ON platform.outbox TO {METRICS_ROLE}",
)

# P1-9's Transit-rotation role (docs/p1-hardening-plan.md §3 step 12,
# `app.ops.rotate_transit`): SELECT (to read the stored ciphertext + key
# name back) plus UPDATE of ONE named column per table (the ciphertext
# column alone -- `credentials.credentials.ciphertext_ref`,
# `integrations.connections.token_ref`, `integrations.mcp_servers.
# auth_ref`) and nothing else. The column-scoped GRANT is narrower than
# `retention_sweeper`'s own table-wide reach: this role can overwrite the
# ciphertext of any row, but a forged/garbage value there only breaks
# decryption of that ONE secret -- it can never touch `status`, `label`,
# `scopes`, or any other column that could forge or misrepresent a row's
# identity/state. `USAGE` on both schemas because the two ciphertext-
# bearing tables live in `credentials`/`integrations`, never touched by
# `retention_sweeper`/`metrics_reader`.
TRANSIT_ROTATOR_GRANTS: tuple[str, ...] = (
    f"GRANT USAGE ON SCHEMA credentials TO {TRANSIT_ROTATOR_ROLE}",
    f"GRANT USAGE ON SCHEMA integrations TO {TRANSIT_ROTATOR_ROLE}",
    f"GRANT SELECT, UPDATE (ciphertext_ref) ON credentials.credentials TO {TRANSIT_ROTATOR_ROLE}",
    f"GRANT SELECT, UPDATE (token_ref) ON integrations.connections TO {TRANSIT_ROTATOR_ROLE}",
    f"GRANT SELECT, UPDATE (auth_ref) ON integrations.mcp_servers TO {TRANSIT_ROTATOR_ROLE}",
)

# BE-ADM-014's purge role: USAGE on every schema it reaches (the eleven module
# schemas whose rows it deletes, plus `platform` for `admin_audit_log`), a
# narrow COLUMN-scoped read/write pair on the two identity tables
# (`workspace.workspaces`/`workspace.users`), and plain SELECT+DELETE on
# every tenant table it purges. Deliberately NO grant at all on
# `platform.outbox`/`processed_events`/`idempotency_keys` -- this role has no
# business enumerating those, and `usage`'s own three tables are purged WITH
# the workspace (module docstring's usage-retention REVIEW POINT, resolved
# for v1: purge, don't roll up).
#
# `workspace.users` gets SELECT on `(id, workspace_id, status, deleted_at)`
# ONLY -- never `email`/`display_name`/`firebase_uid`: this role reads a
# tombstone's STATE to find eligible workspaces, never the identity it
# redacted. No UPDATE/DELETE on `users` at all -- the tombstone itself stays
# `SqlPlatformAccountManager.delete`'s alone; this role only ever empties what
# is AROUND it. `workspace.workspaces` gets SELECT on the four columns
# `find_candidates`/`finalize` actually touch, plus UPDATE of exactly the two
# columns `finalize` writes (`status`, `purged_at`) -- never a table-wide
# UPDATE, which could otherwise rename or resurrect a workspace.
PURGE_GRANTS: tuple[str, ...] = (
    # Grant order carries no semantics -- the order rows are actually deleted
    # in is `app.ops.purge._SCHEMA_ORDER`, where `spaces` comes LAST because
    # every table that will carry `space_id` (docs/spaces-backend-plan.md
    # step 4) must be emptied before the space rows they point at.
    f"GRANT USAGE ON SCHEMA spaces TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA workspace TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA access TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA credentials TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA conversations TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA memory TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA files TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA knowledge TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA media TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA integrations TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA usage TO {PURGE_ROLE}",
    f"GRANT USAGE ON SCHEMA platform TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON spaces.spaces TO {PURGE_ROLE}",
    f"GRANT SELECT (id, owner_user_id, status, purged_at), UPDATE (status, purged_at) "
    f"ON workspace.workspaces TO {PURGE_ROLE}",
    f"GRANT SELECT (id, workspace_id, status, deleted_at) ON workspace.users TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON workspace.user_presence TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON access.role_assignments TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON credentials.credentials TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON conversations.conversations TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON conversations.messages TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON conversations.conversation_files TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON memory.memory_items TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON files.files TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.documents TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.chunks TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.parent_chunks TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.reindex_jobs TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.reindex_job_items TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.summaries TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON knowledge.summary_jobs TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON media.media_jobs TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON integrations.connections TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON integrations.mcp_servers TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON usage.usage_records TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON usage.usage_rollups TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON usage.limits TO {PURGE_ROLE}",
    f"GRANT SELECT, DELETE ON usage.reservations TO {PURGE_ROLE}",
    f"GRANT SELECT, INSERT ON platform.admin_audit_log TO {PURGE_ROLE}",
)


def run_migrations(owner_url: str) -> None:
    """Apply all twelve chains in dependency order (``MIGRATION_CHAINS``).

    The root log level is saved and restored around the Alembic run, and that
    is not cosmetic: ``migrations/env.py`` calls ``fileConfig`` on
    ``alembic.ini``, whose ``[logger_root] level = WARNING`` REPLACES whatever
    ``main`` configured. Every ``INFO`` this module emits after the first
    chain was therefore swallowed -- including ``provision.complete``, the one
    line that tells an operator the step finished. That was tolerable while
    provisioning was a single process; with capacity 2.9's lock it is not,
    because a replica queued behind another one now has a legitimate reason to
    sit silent for a while, and "no output" must not mean both "waiting" and
    "finished".
    """
    os.environ["DATABASE_URL"] = owner_url
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))

    root_level = logging.getLogger().level
    try:
        for revision, vts in MIGRATION_CHAINS:
            config.cmd_opts = None if vts is None else Namespace(x=[f"vts={vts}"])
            _logger.info("provision.migrating", extra={"revision": revision, "version_schema": vts})
            command.upgrade(config, revision)
            logging.getLogger().setLevel(root_level)
    finally:
        logging.getLogger().setLevel(root_level)


async def _require_roles(owner_url: str, roles: tuple[str, ...]) -> None:
    """Fail once, clearly, instead of statement-by-statement halfway through
    the grants -- a missing role is a cluster-init omission, and the operator
    needs to be told which step to go run, not which GRANT died.

    This check runs BEFORE ``run_migrations`` (see ``provision()``), which
    since 0003_retention_sweep.py is no longer only a fail-fast courtesy for
    the GRANTs that come after: that migration's own
    ``CREATE POLICY ... TO retention_sweeper`` validates the role at DDL
    time, so a missing ``RETENTION_ROLE`` would otherwise fail migration
    application itself, deep inside Alembic, with a far less legible error.
    ``TRANSIT_ROTATOR_ROLE`` joins it for the identical reason:
    ``migrations/versions/credentials/0002_transit_rotator.py`` and
    ``migrations/versions/integrations/0002_transit_rotator.py`` both issue
    ``CREATE POLICY ... TO transit_rotator``. ``PURGE_ROLE`` (BE-ADM-014)
    joins for the same reason again:
    ``migrations/versions/workspace/0009_workspace_purge.py`` issues
    ``CREATE POLICY ... TO workspace_purger``.
    """
    engine = create_engine(DatabaseSettings(url=owner_url), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname = ANY(:names)"),
                {"names": list(roles)},
            )
            present = {row[0] for row in result}
    finally:
        await engine.dispose()

    missing = sorted(set(roles) - present)
    if missing:
        raise SystemExit(
            f"provisioning aborted: role(s) {', '.join(missing)} do not exist. "
            "Roles are created at cluster init (deploy/postgres/initdb/), not here -- "
            "aizzak_owner deliberately has no CREATEROLE."
        )


async def apply_grants(owner_url: str) -> None:
    """Run every grant. Re-granting a privilege a role already holds is a
    no-op in Postgres, so running this again on a later deploy is free.

    It is NOT safe to run twice AT ONCE, and the old wording here claimed it
    was. Two sessions issuing the same ``GRANT`` update the same catalog row,
    and PostgreSQL answers the loser with ``tuple concurrently updated``
    (``XX000``) -- measured on this stack in the module docstring, on a
    database with nothing left to migrate. Idempotent and concurrency-safe are
    different properties; only the first one was ever true here. What makes
    the deploy safe is that ``provision`` holds the advisory lock across this
    call, so there is never a second session in it."""
    engine = create_engine(DatabaseSettings(url=owner_url), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for statement in (
                *APP_RW_GRANTS,
                *OUTBOX_RELAY_GRANTS,
                *RETENTION_GRANTS,
                *METRICS_GRANTS,
                *TRANSIT_ROTATOR_GRANTS,
                *PURGE_GRANTS,
            ):
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


PROVISION_ROLES: tuple[str, ...] = (
    APP_ROLE,
    RELAY_ROLE,
    RETENTION_ROLE,
    METRICS_ROLE,
    TRANSIT_ROTATOR_ROLE,
    PURGE_ROLE,
)

# One `int8` key, hashed IN SQL. `AdvisoryQuotaLock` hashes in SQL for the
# same reason and says it at length: Python's `hash()` is randomised per
# process (`PYTHONHASHSEED`), so three replicas would compute three keys for
# this one string and serialise against nobody -- a lock that reads as a lock
# in the source and is not one.
_PROVISION_LOCK_SQL = text("SELECT pg_advisory_lock(hashtextextended(:key, 0))")

# `lock_timeout`'s own SQLSTATE. Postgres spells the advisory-lock wait the
# same way it spells a table-lock wait, which is the whole reason the wait
# here can be bounded at all -- see `_hold_provision_lock`.
_LOCK_NOT_AVAILABLE = "55P03"


async def _hold_provision_lock(conn: AsyncConnection, settings: MigrationSettings) -> None:
    """Take the whole-provisioning advisory lock, or die saying who holds it.

    **Session-level (``pg_advisory_lock``), not transaction-level.** The three
    phases it covers span many transactions and one whole Alembic run, so
    ``pg_advisory_xact_lock`` -- the form ``AdvisoryQuotaLock`` uses -- would
    release at the first COMMIT and guard nothing. The session form releases
    when the SESSION ends, which is also the recovery path: a ``migrate``
    container that is killed mid-migration drops its connection, and the
    backend exit frees the lock. There is no lease to expire and no stale row
    to clean up.

    **On its own connection, in AUTOCOMMIT, and that is load-bearing.** Held
    on a connection with an open transaction, this lock would pin the
    database's ``xmin`` horizon for the entire migration -- and a transaction
    that stays open across a long deploy is precisely what stops autovacuum
    from removing dead tuples, which is the defect step 2.8 just finished
    removing on these same tables. The lock holder must be idle, not
    idle-in-transaction.

    **The wait is bounded by ``lock_timeout``, and its number is NOT the
    migration's.** Measured: ``SET lock_timeout='2s'`` does cancel a waiting
    ``pg_advisory_lock`` (``55P03`` after 2.44 s), so despite the name it is
    not table-locks-only. That is what makes this bound expressible at all --
    and also why the two numbers must not be shared: this connection waits out
    another replica's ENTIRE migration run, while a migration connection may
    freeze a hot table for only ``MIGRATION_LOCK_TIMEOUT_MS``. See
    ``MigrationSettings``.

    **This connection must be a direct one.** A session-level lock taken
    through PgBouncer in transaction pooling would ride on a server connection
    handed to somebody else at the next COMMIT, so the lock would be held by,
    and released by, the wrong client. The ``migrate`` service points at
    ``postgres:5432`` and not at the pooler for exactly this reason;
    ``tests/unit/test_zero_downtime_migrations.py`` fails the build if that is
    ever changed.
    """
    await conn.execute(text(f"SET lock_timeout = {int(settings.provision_lock_wait_ms)}"))
    started = time.monotonic()
    try:
        await conn.execute(_PROVISION_LOCK_SQL, {"key": settings.provision_lock_key})
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        raise SystemExit(
            "provisioning aborted: another replica has held the provisioning lock "
            f"({settings.provision_lock_key!r}) for more than "
            f"{settings.provision_lock_wait_ms} ms. This is a stuck-holder ceiling, "
            "not a normal wait -- find the holder with: SELECT pid, query_start FROM "
            "pg_locks l JOIN pg_stat_activity a USING (pid) WHERE l.locktype = 'advisory';"
        ) from exc
    waited_ms = (time.monotonic() - started) * 1000.0
    _logger.info(
        "provision.lock_acquired",
        extra={"key": settings.provision_lock_key, "waited_ms": round(waited_ms, 1)},
    )


@asynccontextmanager
async def provision_lock(
    owner_url: str, settings: MigrationSettings
) -> AsyncIterator[AsyncConnection]:
    """Hold the provisioning lock for the body, on a connection of its own.

    Public because the live tests take it the same way ``provision`` does: a
    guard that asserts a property of a connection the tests built themselves
    proves nothing about the one the deploy uses.
    """
    engine = create_engine(DatabaseSettings(url=owner_url), poolclass=NullPool)
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    try:
        async with autocommit.connect() as lock_conn:
            await _hold_provision_lock(lock_conn, settings)
            yield lock_conn
    finally:
        await engine.dispose()


async def _provision_locked(owner_url: str, settings: MigrationSettings) -> None:
    """The three phases, under the lock, on one connection that outlives them.

    ``run_migrations`` goes through ``asyncio.to_thread`` rather than being
    awaited: ``migrations/env.py`` drives its own engine with ``asyncio.run``,
    which refuses to start inside a running loop. A worker thread has no loop,
    so the same synchronous Alembic driver this module has always used runs
    unchanged -- and the lock-holding connection stays alive in this loop
    while it does.
    """
    async with provision_lock(owner_url, settings):
        await _require_roles(owner_url, PROVISION_ROLES)
        await asyncio.to_thread(run_migrations, owner_url)
        await apply_grants(owner_url)


def provision(owner_url: str, settings: MigrationSettings | None = None) -> None:
    """Deliberately SYNCHRONOUS, and now with exactly ONE ``asyncio.run``.

    It used to be three, because ``run_migrations`` could not be awaited from
    inside a running loop. That is still true -- and it is now handled by
    ``asyncio.to_thread`` inside ``_provision_locked`` instead, because the
    advisory lock has to be held across all three phases and a lock taken in
    one ``asyncio.run`` is dropped by the connection that call closes.
    """
    asyncio.run(_provision_locked(owner_url, settings or MigrationSettings()))
    _logger.info(
        "provision.complete",
        extra={
            "chains": len(MIGRATION_CHAINS),
            "grants": (
                len(APP_RW_GRANTS)
                + len(OUTBOX_RELAY_GRANTS)
                + len(RETENTION_GRANTS)
                + len(METRICS_GRANTS)
                + len(TRANSIT_ROTATOR_GRANTS)
                + len(PURGE_GRANTS)
            ),
        },
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    settings = load_settings()
    provision(settings.database.url, settings.migrations)


if __name__ == "__main__":
    main()
