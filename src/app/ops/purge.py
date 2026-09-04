"""BE-ADM-014 -- workspace content purge for platform-admin-deleted users.

**The unit of purge is the WORKSPACE, and eligibility is "no surviving
non-deleted user", not "this user was deleted".** ``00-detailed-design-
decisions.md`` §I-1 (OQ-03): one personal workspace per user;
``access.role_assignments`` is a general ``user x workspace x role`` model
for a future multi-user extension with no schema change.
``migrations/versions/workspace/0001_workspace.py``'s ``uq_users_owner`` --
``UNIQUE(workspace_id) WHERE status='active'`` -- enforces at most one active
user per workspace, and ``06-domain-models.md`` INV-W1 states each user owns
exactly one workspace. So in v1, tombstoning the user always empties the
workspace, but this module keys on the general predicate (no user row with
``status <> 'deleted'`` in that workspace, and the newest ``deleted_at`` in
that workspace older than the retention window) -- correct today, and it
stays correct if multi-user ever lands.

**Nothing in ``workspace.users``/``workspace.workspaces`` is ever deleted.**
``platform.admin_audit_log.actor_user_id``/``target_user_id`` reference
``workspace.users(id)`` and ``workspace_id`` references
``workspace.workspaces(id)``, all with no ``ON DELETE``
(``migrations/versions/workspace/0004_platform_admin_accounts.py``). This
sweep stops at the tenant root: it empties the workspace's CONTENT and marks
it ``status='archived'``/``purged_at`` (0009_workspace_purge.py), never
removes the workspace or user row itself.

**Trigger: a periodic ops sweep, not an event-driven worker.**
``python -m app.ops.purge``, the ``app.ops.retention``/``app.ops.rotate_
transit`` footing (CLI, no HTTP surface, no scheduler built here -- cron/
systemd-timer is a runbook line, 08-local-runbook.md §4.6). No deletion event
exists in ``04-event-catalog.md`` (``SqlPlatformAccountManager.delete``
appends no outbox row at all), the work is deliberately delayed by a
retention window that a v1-scoped scheduler does not exist to honour
(``scheduling`` schema is reserved/unbuilt), and eligibility must be
re-evaluated at purge time anyway -- a poll does that for free.

CONFIRMED 2026-08-12 (human review, no DPA/privacy-notice erasure deadline on
file today): ``PURGE_RETENTION`` (30 days) stays as-is. It is not a grace
period for the tombstoned user -- the tombstone is already irreversible by
design -- it is a window for the OPERATOR: long enough to notice and restore
from backup, short enough that "deleted" still means something. If a
DPA/privacy notice ever names a different erasure deadline, THAT number wins
and this constant must be revisited.

CONFIRMED 2026-08-12 (human review): ``usage``
(``usage.usage_records``/``usage_rollups``/``limits``/``reservations``, the
last added by capacity-plan 2.7) is purged WITH the
workspace, not rolled into a workspace-less aggregate first. It is a
per-workspace meter reading, not a financial obligation (billing is out of
v1 scope) -- DAT-07's "append-only, no deleted_at" governs the module's own
writes, not an out-of-band admin erasure. If a billing/tax retention duty is
ever asserted, the fix is rolling usage into an aggregate BEFORE purge, not
skipping the purge; that is not implemented speculatively here.

**Store order: Qdrant -> MinIO -> Postgres.** Both external stores are
addressable from ``workspace_id`` alone (``kn-<workspace_id>``/
``mem-<workspace_id>`` Qdrant collections, the ``<workspace_id>/`` MinIO key
prefix, INV-F1). Destroy the stores that outlive their own bookkeeping
FIRST, so a mid-run failure leaves everything as it was and the workspace is
simply re-swept next run -- Postgres-first would leave vectors searchable and
objects downloadable with no row left accountable for them.

**Order inside Postgres is forced by FK** (no ``ON DELETE`` anywhere except
``workspace.user_presence``, which the app never relies on to cascade since
its parents -- ``workspace.users``/``workspace.workspaces`` -- are never
deleted by this sweep, so it needs an explicit delete of its own). One
transaction per (workspace, schema): ``knowledge`` (summaries, chunks,
reindex_job_items, documents, reindex_jobs, summary_jobs) -> ``conversations``
(messages, conversation_files, conversations) -> ``media`` -> ``memory`` ->
``files`` (after knowledge/conversations/media, so no dangling logical ref
from ``documents.file_id``/``conversation_files.file_id``/
``media_jobs.result_file_id``) -> ``integrations`` (mcp_servers, connections)
-> ``credentials`` (workspace-scoped rows only -- platform rows with
``workspace_id IS NULL`` are structurally unreachable via RLS) -> ``usage``
-> ``access`` -> ``workspace`` (user_presence) -> ``spaces`` (last of the
row deletions: `files`/`conversations`/`knowledge` grow a LOGICAL, un-FK'd
``space_id`` in docs/spaces-backend-plan.md step 4, so the space rows they
name must outlive them) -> finalize (own transaction, LAST:
``workspace.workspaces`` archived + ``platform.admin_audit_log`` row).

**Out of scope, deliberately:** ``platform.outbox``/``processed_events``/
``idempotency_keys`` (no tenant column, or already swept at up to 90/30/2
days by ``app.ops.retention`` -- nothing of these survives to 30 days).
Redis (every tenant-scoped key already carries a TTL). Firebase
(``firebase_uid`` stays -- BE-ADM-006 already decided this; touching it here
turns "delete" into "reset").

**RLS posture -- deliberately NOT ``retention_sweeper``'s blanket carve-out.**
This role (``workspace_purger``, ``app.ops.provision.PURGE_ROLE``) holds no
``USING (true)`` policy on any tenant table it deletes from. Instead:
``SELECT set_config('app.workspace_id', :ws, true)`` per workspace
(parameterized, never string-interpolated -- ``infrastructure/persistence/
rls.py``'s own convention), then every ordinary ``tenant_isolation`` policy
does the rest. A forgotten/failed ``set_config`` therefore fails SAFE to zero
affected rows, never to "every tenant's copy of this table". The ONE
carve-out this role does get -- ``workspace_purger_select`` on
``workspace.users`` (``0009_workspace_purge.py``) -- is SELECT-only, and
exists purely so ``find_candidates`` can scan for eligible workspaces across
every tenant before any one workspace is chosen to purge.

**Idempotency, partial failure, audit.** Every step is individually
idempotent: dropping a missing Qdrant collection is a no-op
(``drop_collection``), deleting an already-gone MinIO prefix is a no-op
(``delete_prefix``), and a ``DELETE`` matching zero Postgres rows is a
no-op. ``purged_at`` is written LAST, in the SAME transaction as the audit
row (``finalize``) -- its absence always means "not finished", so an
interrupted run is simply re-swept next time with nothing double-counted.
``--dry-run`` writes nothing anywhere -- no DELETE, no collection drop, no
object removal, no audit row -- and every count comes from the identical
read-only predicate (the ``app.ops.retention``/``app.ops.rotate_transit``
dry-run contract, applied here too).

**Skip-and-warn, never fabricate.** A candidate workspace with no matching
``account.deleted`` audit row (the actor lookup fails) is skipped with a
``_logger.warning`` and excluded from the run entirely -- never purged with a
fabricated ``actor_user_id``.

Usage::

    python -m app.ops.purge scan [--older-than-days N] [--limit N]
    python -m app.ops.purge run  [--older-than-days N] [--workspace-id UUID] \
        [--limit N] (--dry-run | --yes)

``scan`` is Postgres-only and mutates nothing. ``run`` requires ``--yes``
unless ``--dry-run`` is given -- refuses otherwise with a clear message (the
``app.ops.dlq purge --yes`` precedent). ``--workspace-id`` narrows to one
candidate, which must still satisfy the eligibility predicate.

``DATABASE_URL`` for THIS process must be the ``workspace_purger`` role's
OWN DSN -- the same per-process convention every other ``app.ops.*`` module
documents: one role per process, never one role wearing another's hat.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from minio import Minio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.di.storage_binding import read_minio_credentials
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings, load_vault_auth
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.secrets.vault_secrets import (
    VaultSecrets,
    create_approle_relogin,
    create_vault_client,
)
from app.infrastructure.storage.minio_storage import create_minio_client, delete_prefix
from app.infrastructure.vector.qdrant_store import create_qdrant_client, drop_collection
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.memory.domain.collections import memory_collection

_logger = logging.getLogger(__name__)

# See the module docstring's "CONFIRMED 2026-08-12" note immediately above
# this constant -- revisit only if a DPA/privacy notice names a different
# erasure deadline.
PURGE_RETENTION: timedelta = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class PurgeCandidate:
    """One workspace eligible for purge: no surviving non-deleted user, and
    the newest tombstone in it older than the retention window."""

    workspace_id: str
    target_user_id: str  # the tombstone whose deletion emptied the workspace
    actor_user_id: str  # from the matching account.deleted audit row
    deleted_at: datetime


@dataclass(frozen=True, slots=True)
class StoreResult:
    """One store's purge outcome (e.g. ``"qdrant:kn-<ws>"``,
    ``"minio:<ws>/"``, ``"knowledge.chunks"``). ``affected`` is a REAL count
    when ``dry_run`` is ``False``; the count a real purge WOULD affect
    otherwise -- never both."""

    store: str
    affected: int
    dry_run: bool


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """One workspace's whole purge outcome across every store.
    ``purged_at`` is ``None`` on a dry run (module docstring: nothing was
    finalized) and is otherwise the ``finalize`` timestamp."""

    workspace_id: str
    candidate: PurgeCandidate
    stores: tuple[StoreResult, ...]
    purged_at: datetime | None
    dry_run: bool


# Finds, per workspace, the newest tombstoned user (``rn = 1``) whose
# workspace now holds NO non-deleted user at all, older than the cutoff, and
# not already purged -- then LEFT JOINs the matching `account.deleted` audit
# row for its actor. The `workspace_id` filter is expressed as a single
# always-present, possibly-NULL bind (`CAST(:workspace_id AS uuid) IS NULL OR
# ...`) rather than string-built SQL, so this stays ONE static, reviewable
# query regardless of whether `--workspace-id` was passed. `CAST(...)`, never
# the terser `:workspace_id::uuid` -- SQLAlchemy's `text()` bind-parameter
# regex does not recognise a name immediately followed by `::` (measured
# live: it silently compiles with ZERO params and ships the literal `::uuid`
# text straight to asyncpg, which then rejects the bare `:name` it cannot
# itself parse).

_FIND_CANDIDATES_SQL = """
WITH last_deleted AS (
    SELECT
        workspace_id,
        id AS target_user_id,
        deleted_at,
        row_number() OVER (PARTITION BY workspace_id ORDER BY deleted_at DESC) AS rn
    FROM workspace.users
    WHERE status = 'deleted'
),
eligible AS (
    SELECT ld.workspace_id, ld.target_user_id, ld.deleted_at
    FROM last_deleted ld
    WHERE ld.rn = 1
      AND ld.deleted_at < :cutoff
      AND NOT EXISTS (
          SELECT 1 FROM workspace.users u
          WHERE u.workspace_id = ld.workspace_id AND u.status <> 'deleted'
      )
)
SELECT e.workspace_id, e.target_user_id, e.deleted_at, a.actor_user_id
FROM eligible e
JOIN workspace.workspaces w ON w.id = e.workspace_id AND w.purged_at IS NULL
LEFT JOIN LATERAL (
    SELECT actor_user_id
    FROM platform.admin_audit_log
    WHERE action = 'account.deleted' AND target_user_id = e.target_user_id
    ORDER BY created_at DESC
    LIMIT 1
) a ON true
WHERE (CAST(:workspace_id AS uuid) IS NULL OR e.workspace_id = CAST(:workspace_id AS uuid))
ORDER BY e.deleted_at ASC
"""


async def find_candidates(
    conn: AsyncConnection,
    *,
    retention: timedelta = PURGE_RETENTION,
    now: datetime | None = None,
    limit: int | None = None,
    workspace_id: str | None = None,
) -> list[PurgeCandidate]:
    """Postgres-only, read-only scan for eligible workspaces (module
    docstring's eligibility predicate). Skips (with a warning, module
    docstring's "Skip-and-warn") any candidate whose ``account.deleted``
    audit row cannot be found -- it is never returned with a fabricated
    ``actor_user_id``. ``limit`` bounds the number of candidates RETURNED
    (applied after the skip, so a run always processes exactly the number of
    workspaces it reports, never fewer because some were silently dropped).
    """
    cutoff = (now or datetime.now(UTC)) - retention
    rows = (
        (
            await conn.execute(
                text(_FIND_CANDIDATES_SQL),
                {"cutoff": cutoff, "workspace_id": workspace_id},
            )
        )
        .mappings()
        .all()
    )

    candidates: list[PurgeCandidate] = []
    for row in rows:
        if row["actor_user_id"] is None:
            _logger.warning(
                "ops.purge.candidate_skipped_no_actor",
                extra={
                    "workspace_id": str(row["workspace_id"]),
                    "target_user_id": str(row["target_user_id"]),
                },
            )
            continue
        candidates.append(
            PurgeCandidate(
                workspace_id=str(row["workspace_id"]),
                target_user_id=str(row["target_user_id"]),
                actor_user_id=str(row["actor_user_id"]),
                deleted_at=row["deleted_at"],
            )
        )
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


async def purge_vectors(
    client: AsyncQdrantClient, workspace_id: str, *, dry_run: bool = False
) -> tuple[StoreResult, ...]:
    """Drop ``kn-<workspace_id>`` then ``mem-<workspace_id>`` (module
    docstring's store order). ``dry_run`` checks existence without dropping
    (``collection_exists``, never ``drop_collection``) so a preview run
    truly mutates nothing."""
    results: list[StoreResult] = []
    for collection in (knowledge_collection(workspace_id), memory_collection(workspace_id)):
        existed = (
            await client.collection_exists(collection)
            if dry_run
            else await drop_collection(client, collection)
        )
        results.append(
            StoreResult(store=f"qdrant:{collection}", affected=1 if existed else 0, dry_run=dry_run)
        )
    return tuple(results)


async def purge_objects(
    client: Minio, bucket: str, workspace_id: str, *, dry_run: bool = False
) -> StoreResult:
    """Delete every object under ``<workspace_id>/`` (INV-F1) via
    ``delete_prefix``, which itself refuses anything that is not exactly a
    UUID-shaped prefix."""
    prefix = f"{workspace_id}/"
    affected = await delete_prefix(client, bucket, prefix, dry_run=dry_run)
    return StoreResult(store=f"minio:{prefix}", affected=affected, dry_run=dry_run)


@dataclass(frozen=True, slots=True)
class _SchemaSpec:
    """One schema's tables, in the delete order the module docstring's
    "Order inside Postgres" paragraph fixes -- FK-forced within a schema,
    and the eleven schemas themselves ordered so no later schema's row can
    dangle a logical reference into an earlier, already-emptied one."""

    schema: str
    tables: tuple[str, ...]


_SCHEMA_ORDER: tuple[_SchemaSpec, ...] = (
    _SchemaSpec(
        "knowledge",
        (
            "knowledge.summaries",
            "knowledge.chunks",
            # P-14 (rag-indexing-plan.md §3.2, step 6): explicitly swept
            # here rather than left to `fk_parent_chunk_doc ... ON DELETE
            # CASCADE` firing off the `knowledge.documents` delete below --
            # this module's own rule (module docstring) is that every
            # tenant table is emptied by an explicit statement the sweep
            # can count, never by a cascade nobody here issued. Ordered
            # after `knowledge.chunks`, whose `parent_id` FK carries no
            # cascade of its own, for the same reason `chunks` precedes
            # `documents`: nothing may still reference a row this sweep is
            # about to delete.
            "knowledge.parent_chunks",
            "knowledge.reindex_job_items",
            "knowledge.documents",
            "knowledge.reindex_jobs",
            "knowledge.summary_jobs",
        ),
    ),
    _SchemaSpec(
        "conversations",
        (
            "conversations.messages",
            "conversations.conversation_files",
            "conversations.conversations",
        ),
    ),
    _SchemaSpec("media", ("media.media_jobs",)),
    _SchemaSpec("memory", ("memory.memory_items",)),
    _SchemaSpec("files", ("files.files",)),
    _SchemaSpec(
        "integrations",
        (
            "integrations.mcp_servers",
            "integrations.connections",
        ),
    ),
    # Column-scoped RLS already confines this to the workspace's OWN rows;
    # `scope='platform'` rows (`workspace_id IS NULL`) never match the
    # `WHERE workspace_id = :ws` predicate below, so they are untouched --
    # the module docstring's own "structurally unreachable" claim, enforced
    # doubly here (RLS + the explicit predicate).
    _SchemaSpec("credentials", ("credentials.credentials",)),
    _SchemaSpec(
        "usage",
        (
            "usage.usage_records",
            "usage.usage_rollups",
            "usage.limits",
            # 2.7 -- an in-flight admission belongs to the workspace being
            # purged exactly as its ledger does, and it expires on its own
            # anyway; leaving it would keep a purged tenant's rows behind.
            "usage.reservations",
        ),
    ),
    _SchemaSpec("access", ("access.role_assignments",)),
    # `user_presence` CASCADEs off `workspace.users`/`workspace.workspaces`,
    # neither of which this sweep ever deletes (module docstring), so it
    # needs an explicit delete of its own -- the one schema this sweep
    # empties without a row deletion anywhere upstream causing it for free.
    _SchemaSpec("workspace", ("workspace.user_presence",)),
    # LAST, and the position is the point (docs/spaces-backend-plan.md step 3).
    # `files.files`, `conversations.conversations` and `knowledge.documents`
    # all grow a `space_id` in step 4, and the plan keeps cross-schema
    # references LOGICAL -- no FK, so Postgres will happily let a space row go
    # first and leave those columns pointing at nothing. Nothing but this
    # position enforces "children before parents" here, which is exactly why
    # it is written down instead of left to the reader to notice.
    _SchemaSpec("spaces", ("spaces.spaces",)),
)


async def purge_rows(
    engine: AsyncEngine, workspace_id: str, *, dry_run: bool = False
) -> tuple[StoreResult, ...]:
    """One transaction PER SCHEMA (module docstring: no cross-schema FKs per
    DD-01, so each schema's deletion is independently consistent, and
    holding locks across every table for one giant transaction buys
    nothing). Each transaction sets the RLS GUC exactly once
    (parameterized, ``rls.py``'s own convention) and relies on it for every
    statement inside -- Layer 1 -- while the explicit ``WHERE workspace_id``
    predicate on every statement is Layer 2, defense in depth."""
    results: list[StoreResult] = []
    for spec in _SCHEMA_ORDER:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": workspace_id},
            )
            for table in spec.tables:
                if dry_run:
                    result = await conn.execute(
                        text(f"SELECT count(*) FROM {table} WHERE workspace_id = :ws"),
                        {"ws": workspace_id},
                    )
                    affected = int(result.scalar_one())
                else:
                    result = await conn.execute(
                        text(f"DELETE FROM {table} WHERE workspace_id = :ws"),
                        {"ws": workspace_id},
                    )
                    affected = result.rowcount
                results.append(StoreResult(store=table, affected=affected, dry_run=dry_run))
    return tuple(results)


async def finalize(
    engine: AsyncEngine,
    candidate: PurgeCandidate,
    stores: tuple[StoreResult, ...],
    *,
    now: datetime | None = None,
) -> datetime:
    """Archive the workspace and append the ``workspace.purged`` audit row
    in ONE transaction, LAST (module docstring: ``purged_at``'s absence
    always means "not finished")."""
    purged_at = now or datetime.now(UTC)
    details = json.dumps(
        {
            "stores": {s.store: s.affected for s in stores},
            "retention_days": PURGE_RETENTION.days,
        }
    )
    reason = (
        f"app.ops.purge: retention window ({PURGE_RETENTION.days}d) elapsed since account.deleted"
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE workspace.workspaces SET status = 'archived', purged_at = :purged_at "
                "WHERE id = :ws"
            ),
            {"purged_at": purged_at, "ws": candidate.workspace_id},
        )
        await conn.execute(
            text(
                "INSERT INTO platform.admin_audit_log "
                "(id, actor_user_id, target_user_id, workspace_id, action, "
                " previous_status, new_status, reason, details, created_at) "
                "VALUES (:id, :actor, :target, :ws, 'workspace.purged', "
                " 'deleted', 'purged', :reason, CAST(:details AS jsonb), :created_at)"
            ),
            {
                "id": new_uuid7(),
                "actor": candidate.actor_user_id,
                "target": candidate.target_user_id,
                "ws": candidate.workspace_id,
                "reason": reason,
                "details": details,
                "created_at": purged_at,
            },
        )
    return purged_at


async def purge_workspace(
    engine: AsyncEngine,
    qdrant: AsyncQdrantClient,
    minio: Minio,
    bucket: str,
    candidate: PurgeCandidate,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PurgeResult:
    """Purge ONE workspace, in the module docstring's fixed order: Qdrant,
    then MinIO, then Postgres, then (unless ``dry_run``) ``finalize``."""
    vector_results = await purge_vectors(qdrant, candidate.workspace_id, dry_run=dry_run)
    object_result = await purge_objects(minio, bucket, candidate.workspace_id, dry_run=dry_run)
    row_results = await purge_rows(engine, candidate.workspace_id, dry_run=dry_run)
    stores = (*vector_results, object_result, *row_results)

    purged_at: datetime | None = None
    if not dry_run:
        purged_at = await finalize(engine, candidate, stores, now=now)

    return PurgeResult(
        workspace_id=candidate.workspace_id,
        candidate=candidate,
        stores=stores,
        purged_at=purged_at,
        dry_run=dry_run,
    )


async def purge_all(
    engine: AsyncEngine,
    qdrant: AsyncQdrantClient,
    minio: Minio,
    bucket: str,
    *,
    retention: timedelta = PURGE_RETENTION,
    dry_run: bool = False,
    limit: int | None = None,
    workspace_id: str | None = None,
    now: datetime | None = None,
) -> list[PurgeResult]:
    """Find every eligible candidate (or one, narrowed by ``workspace_id``,
    which must still satisfy the eligibility predicate) and purge each in
    turn. ``now`` defaults to the real wall clock (every OTHER function in
    this module's own convention) -- exposed as a keyword so a test can pin
    it, the same way ``find_candidates``/``purge_workspace``/``finalize``
    already do."""
    async with engine.connect() as conn:
        candidates = await find_candidates(
            conn, retention=retention, now=now, limit=limit, workspace_id=workspace_id
        )
    return [
        await purge_workspace(engine, qdrant, minio, bucket, candidate, dry_run=dry_run, now=now)
        for candidate in candidates
    ]


def _print_result(result: PurgeResult) -> None:
    payload = {
        "workspace_id": result.workspace_id,
        "target_user_id": result.candidate.target_user_id,
        "actor_user_id": result.candidate.actor_user_id,
        "deleted_at": result.candidate.deleted_at.isoformat(),
        "stores": [
            {"store": s.store, "affected": s.affected, "dry_run": s.dry_run} for s in result.stores
        ],
        "purged_at": result.purged_at.isoformat() if result.purged_at is not None else None,
        "dry_run": result.dry_run,
    }
    print(json.dumps(payload, ensure_ascii=False))
    _logger.info(
        "ops.purge.workspace_purged",
        extra={"workspace_id": result.workspace_id, "dry_run": result.dry_run},
    )


async def _run_cli(args: argparse.Namespace) -> int:
    settings = load_settings()
    retention = (
        timedelta(days=args.older_than_days)
        if args.older_than_days is not None
        else PURGE_RETENTION
    )
    engine = create_engine(DatabaseSettings(url=settings.database.url), poolclass=NullPool)
    try:
        if args.action == "scan":
            async with engine.connect() as conn:
                candidates = await find_candidates(conn, retention=retention, limit=args.limit)
            for candidate in candidates:
                print(
                    json.dumps(
                        {
                            "workspace_id": candidate.workspace_id,
                            "target_user_id": candidate.target_user_id,
                            "actor_user_id": candidate.actor_user_id,
                            "deleted_at": candidate.deleted_at.isoformat(),
                        },
                        ensure_ascii=False,
                    )
                )
            return 0

        # "run" -- needs Qdrant + MinIO too; `main` has already refused to
        # reach here without --yes or --dry-run.
        vault_auth = load_vault_auth()
        vault_client = create_vault_client(
            settings.vault, token=vault_auth.token, secret_id=vault_auth.secret_id
        )
        secrets = VaultSecrets(
            vault_client,
            relogin=(
                create_approle_relogin(settings.vault, vault_client, secret_id=vault_auth.secret_id)
                if not vault_auth.token
                else None
            ),
        )
        access_key, secret_key = await read_minio_credentials(secrets)
        minio_client = create_minio_client(
            settings.minio, access_key=access_key, secret_key=secret_key
        )
        qdrant_client = create_qdrant_client(settings.qdrant)
        try:
            results = await purge_all(
                engine,
                qdrant_client,
                minio_client,
                settings.minio.bucket,
                retention=retention,
                dry_run=args.dry_run,
                limit=args.limit,
                workspace_id=args.workspace_id,
            )
        finally:
            await qdrant_client.close()
        for result in results:
            _print_result(result)
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.purge",
        description="Purge a deleted-and-past-retention workspace's content across Qdrant, "
        "MinIO and Postgres (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    scan_parser = sub.add_parser(
        "scan", help="Postgres-only: list eligible workspaces, mutate nothing"
    )
    scan_parser.add_argument("--older-than-days", type=int, default=None)
    scan_parser.add_argument("--limit", type=int, default=None)

    run_parser = sub.add_parser(
        "run", help="purge every eligible workspace (or one, with --workspace-id)"
    )
    run_parser.add_argument("--older-than-days", type=int, default=None)
    run_parser.add_argument("--workspace-id", default=None)
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument(
        "--dry-run", action="store_true", help="count/preview what WOULD happen; mutate nothing"
    )
    run_parser.add_argument(
        "--yes", action="store_true", help="required explicit confirmation -- purge is irreversible"
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "run" and not args.dry_run and not args.yes:
        raise SystemExit(
            "run refused: pass --yes to confirm (or --dry-run to preview) -- this "
            "destructively deletes a workspace's content across Qdrant, MinIO and Postgres, "
            "and there is no undo."
        )
    raise SystemExit(asyncio.run(_run_cli(args)))


if __name__ == "__main__":
    main()
