"""Live-Postgres proof for the workspace content-purge sweep (``app.ops.purge``,
BE-ADM-014) against real ``aizzak_test`` -- the ``test_retention_ops_live.py``
precedent: a hermetic stub cannot prove "purges what is eligible AND leaves
everything else untouched" against real RLS/FK/CHECK semantics.

Three workspaces, seeded with rows in every table §3.2 names:
  * ``eligible``      -- tombstoned user, ``deleted_at`` PAST the retention
                          cutoff. Must end up FULLY emptied + archived.
  * ``inside_window``  -- tombstoned user, ``deleted_at`` INSIDE the window
                          (too recent). Must be left completely untouched.
  * ``still_active``   -- an ACTIVE user. Must be left completely untouched.

All timestamps are explicit (never ``now()``), the ``test_retention_ops_live.py``
convention: "past cutoff" and "not past cutoff" must be exact, not
timing-dependent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.ops import purge as purge_module
from app.ops.purge import purge_all
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_RETENTION = timedelta(days=30)


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )


async def _owner_exec(owner_dsn: str, sql: str, params: dict[str, object]) -> None:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _seed_workspace(owner_dsn: str, workspace_id: str, owner_user_id: str, name: str) -> None:
    """``workspace.workspaces`` carries no RLS at all (01 §2.1) -- a plain
    owner-run INSERT."""
    await _owner_exec(
        owner_dsn,
        "INSERT INTO workspace.workspaces (id, owner_user_id, name) VALUES (:id, :owner, :name)",
        {"id": workspace_id, "owner": owner_user_id, "name": name},
    )


async def _seed_user(
    tenant_session: TenantSessionFactory,
    ctx: ExecutionContext,
    user_id: str,
    *,
    status: str,
    deleted_at: datetime | None,
) -> None:
    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                "INSERT INTO workspace.users "
                "(id, workspace_id, firebase_uid, email, status, deleted_at) "
                "VALUES (:id, :ws, :fb, :email, :status, :deleted_at)"
            ),
            {
                "id": user_id,
                "ws": ctx.workspace_id,
                "fb": f"fb-{user_id}",
                "email": f"user+{user_id}@example.test",
                "status": status,
                "deleted_at": deleted_at,
            },
        )


async def _seed_account_deleted_audit(
    owner_dsn: str,
    *,
    actor_user_id: str,
    target_user_id: str,
    workspace_id: str,
    created_at: datetime,
) -> None:
    await _owner_exec(
        owner_dsn,
        """
        INSERT INTO platform.admin_audit_log
            (id, actor_user_id, target_user_id, workspace_id, action,
             previous_status, new_status, reason, details, created_at)
        VALUES
            (:id, :actor, :target, :ws, 'account.deleted', 'active', 'deleted',
             'test seed', :details, :created_at)
        """,
        {
            "id": new_uuid7(),
            "actor": actor_user_id,
            "target": target_user_id,
            "ws": workspace_id,
            "details": '{"redacted": true, "roles_revoked": []}',
            "created_at": created_at,
        },
    )


async def _seed_content(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, user_id: str
) -> dict[str, str]:
    """One row per table §3.2 names, all under ONE workspace (``ctx``).
    ``user_id`` MUST already exist in ``workspace.users`` -- both
    ``workspace.user_presence.user_id`` and (informally)
    ``access.role_assignments.user_id`` name it. Returns the generated ids a
    caller might need (``file_id``, etc.)."""
    ws = ctx.workspace_id
    file_id = new_uuid7()
    document_id = new_uuid7()
    reindex_job_id = new_uuid7()
    conversation_id = new_uuid7()
    credential_id = new_uuid7()

    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                "INSERT INTO knowledge.documents (id, workspace_id, file_id) "
                "VALUES (:id, :ws, :file_id)"
            ),
            {"id": document_id, "ws": ws, "file_id": file_id},
        )
        await session.execute(
            text(
                "INSERT INTO knowledge.chunks (id, document_id, workspace_id, seq, text) "
                "VALUES (:id, :doc, :ws, 0, 'chunk text')"
            ),
            {"id": new_uuid7(), "doc": document_id, "ws": ws},
        )
        await session.execute(
            text("INSERT INTO knowledge.reindex_jobs (id, workspace_id) VALUES (:id, :ws)"),
            {"id": reindex_job_id, "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO knowledge.reindex_job_items "
                "(job_id, document_id, workspace_id, file_id, source_document_id) "
                "VALUES (:job, :doc, :ws, :file_id, :source_doc)"
            ),
            {
                "job": reindex_job_id,
                "doc": document_id,
                "ws": ws,
                "file_id": file_id,
                "source_doc": new_uuid7(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO knowledge.summaries "
                "(id, workspace_id, document_id, kind, lang, text, model, source_chunks, built_at) "
                "VALUES (:id, :ws, :doc, 'overview', 'en', 'summary', 'test-model', 1, :built_at)"
            ),
            {"id": new_uuid7(), "ws": ws, "doc": document_id, "built_at": _NOW},
        )
        await session.execute(
            text(
                "INSERT INTO knowledge.summary_jobs (id, workspace_id, document_id, kind, lang) "
                "VALUES (:id, :ws, :doc, 'overview', 'en')"
            ),
            {"id": new_uuid7(), "ws": ws, "doc": document_id},
        )
        await session.execute(
            text(
                "INSERT INTO conversations.conversations (id, workspace_id, agent_key) "
                "VALUES (:id, :ws, 'rag_agent')"
            ),
            {"id": conversation_id, "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO conversations.messages "
                "(id, conversation_id, workspace_id, role, content, seq) "
                "VALUES (:id, :conv, :ws, 'user', CAST(:content AS jsonb), 0)"
            ),
            {"id": new_uuid7(), "conv": conversation_id, "ws": ws, "content": '{"text": "hi"}'},
        )
        await session.execute(
            text(
                "INSERT INTO conversations.conversation_files "
                "(conversation_id, file_id, workspace_id) "
                "VALUES (:conv, :file_id, :ws)"
            ),
            {"conv": conversation_id, "file_id": file_id, "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO media.media_jobs (id, workspace_id, agent_key, kind, prompt) "
                "VALUES (:id, :ws, 'image_agent', 'image', 'a cat')"
            ),
            {"id": new_uuid7(), "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO memory.memory_items (id, workspace_id, agent_key, kind, content) "
                "VALUES (:id, :ws, 'rag_agent', 'semantic', 'remembered')"
            ),
            {"id": new_uuid7(), "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO files.files "
                "(id, workspace_id, name, content_type, size_bytes, storage_key) "
                "VALUES (:id, :ws, 'a.txt', 'text/plain', 3, :key)"
            ),
            {"id": file_id, "ws": ws, "key": f"{ws}/{file_id}"},
        )
        await session.execute(
            text(
                "INSERT INTO integrations.connections (id, workspace_id, connector_key) "
                "VALUES (:id, :ws, 'slack')"
            ),
            {"id": new_uuid7(), "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO integrations.mcp_servers (id, workspace_id, name, endpoint_url) "
                "VALUES (:id, :ws, 'srv', 'http://example.test')"
            ),
            {"id": new_uuid7(), "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO credentials.credentials "
                "(id, workspace_id, provider, scope, ciphertext_ref, key_id) "
                "VALUES (:id, :ws, 'openai', 'user', 'vault:v1:ciphertext', 'tenant-secrets')"
            ),
            {"id": credential_id, "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO usage.usage_records "
                "(id, workspace_id, agent_key, provider, operation_id) "
                "VALUES (:id, :ws, 'rag_agent', 'openai', :op)"
            ),
            {"id": new_uuid7(), "ws": ws, "op": new_uuid7()},
        )
        await session.execute(
            text(
                "INSERT INTO usage.usage_rollups (workspace_id, period, period_start) "
                "VALUES (:ws, 'day', :period_start)"
            ),
            {"ws": ws, "period_start": _NOW.date()},
        )
        await session.execute(
            text(
                "INSERT INTO usage.limits (id, workspace_id, scope, metric, period, limit_value) "
                "VALUES (:id, :ws, 'workspace', 'tokens', 'day', 1000)"
            ),
            {"id": new_uuid7(), "ws": ws},
        )
        await session.execute(
            text(
                "INSERT INTO access.role_assignments (id, workspace_id, user_id, role) "
                "VALUES (:id, :ws, :user_id, 'owner')"
            ),
            {"id": new_uuid7(), "ws": ws, "user_id": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO workspace.user_presence (user_id, workspace_id, last_seen_at) "
                "VALUES (:user_id, :ws, :seen)"
            ),
            {"user_id": user_id, "ws": ws, "seen": _NOW},
        )

    return {"file_id": file_id, "document_id": document_id, "credential_id": credential_id}


async def _table_count(purger_engine: AsyncEngine, table: str, workspace_id: str) -> int:
    async with purger_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
        )
        result = await conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE workspace_id = :ws"), {"ws": workspace_id}
        )
        return int(result.scalar_one())


async def _assert_all_tables_count(
    purger_engine: AsyncEngine, workspace_id: str, *, expected: int
) -> None:
    for spec in purge_module._SCHEMA_ORDER:
        for table in spec.tables:
            count = await _table_count(purger_engine, table, workspace_id)
            assert count == expected, (
                f"{table} for {workspace_id}: expected {expected}, got {count}"
            )


@pytest.fixture
async def three_workspaces(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> dict[str, dict[str, str]]:
    """Seeds ``eligible`` (tombstoned, past cutoff), ``inside_window``
    (tombstoned, too recent) and ``still_active`` (never tombstoned), each
    with a full complement of content rows, plus the actor's OWN separate
    workspace (never touched)."""
    actor_ws, actor_user = new_uuid7(), new_uuid7()
    await _seed_workspace(live_db.owner, actor_ws, actor_user, "admin workspace")
    await _seed_user(tenant_session, _ctx(actor_ws), actor_user, status="active", deleted_at=None)

    out: dict[str, dict[str, str]] = {"actor": {"workspace_id": actor_ws, "user_id": actor_user}}

    for label, status, deleted_at in (
        ("eligible", "deleted", _NOW - timedelta(days=40)),
        ("inside_window", "deleted", _NOW - timedelta(days=10)),
        ("still_active", "active", None),
    ):
        ws_id, user_id = new_uuid7(), new_uuid7()
        await _seed_workspace(live_db.owner, ws_id, user_id, f"{label} workspace")
        await _seed_user(tenant_session, _ctx(ws_id), user_id, status=status, deleted_at=deleted_at)
        content_ids = await _seed_content(tenant_session, _ctx(ws_id), user_id)
        if status == "deleted":
            await _seed_account_deleted_audit(
                live_db.owner,
                actor_user_id=actor_user,
                target_user_id=user_id,
                workspace_id=ws_id,
                created_at=deleted_at or _NOW,
            )
        out[label] = {"workspace_id": ws_id, "user_id": user_id, **content_ids}

    return out


async def test_purge_all_empties_only_the_eligible_workspace(
    live_db: LiveDbDsns,
    purger_engine: AsyncEngine,
    three_workspaces: dict[str, dict[str, str]],
) -> None:
    eligible_ws = three_workspaces["eligible"]["workspace_id"]
    inside_window_ws = three_workspaces["inside_window"]["workspace_id"]
    still_active_ws = three_workspaces["still_active"]["workspace_id"]

    class _NullQdrant:
        async def collection_exists(self, name: str) -> bool:
            return False

        async def delete_collection(self, name: str) -> bool:
            return False

    class _NullMinio:
        def list_objects(
            self, bucket: str, prefix: str | None = None, recursive: bool = False
        ) -> list[object]:
            return []

    engine = create_engine(DatabaseSettings(url=live_db.purger), poolclass=NullPool)
    try:
        results = await purge_all(
            engine,
            _NullQdrant(),  # type: ignore[arg-type]
            _NullMinio(),  # type: ignore[arg-type]
            "unused-bucket",
            retention=_RETENTION,
            now=_NOW,
            workspace_id=eligible_ws,
        )
    finally:
        await engine.dispose()

    assert [r.workspace_id for r in results] == [eligible_ws]
    result = results[0]
    assert not result.dry_run
    assert result.purged_at is not None
    assert result.candidate.target_user_id == three_workspaces["eligible"]["user_id"]
    assert result.candidate.actor_user_id == three_workspaces["actor"]["user_id"]

    # The eligible workspace is fully emptied across every table §3.2 names.
    await _assert_all_tables_count(purger_engine, eligible_ws, expected=0)

    # Its own status/purged_at are set.
    async with purger_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status, purged_at FROM workspace.workspaces WHERE id = :ws"),
                    {"ws": eligible_ws},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "archived"
    assert row["purged_at"] is not None

    # The audit row satisfies the new CHECK.
    async with purger_engine.begin() as conn:
        audit_row = (
            (
                await conn.execute(
                    text(
                        "SELECT previous_status, new_status, details FROM platform.admin_audit_log "
                        "WHERE workspace_id = :ws AND action = 'workspace.purged'"
                    ),
                    {"ws": eligible_ws},
                )
            )
            .mappings()
            .one()
        )
    assert audit_row["previous_status"] == "deleted"
    assert audit_row["new_status"] == "purged"
    assert "stores" in audit_row["details"]

    # The other two workspaces are completely untouched.
    await _assert_all_tables_count(purger_engine, inside_window_ws, expected=1)
    await _assert_all_tables_count(purger_engine, still_active_ws, expected=1)
    async with purger_engine.begin() as conn:
        for ws in (inside_window_ws, still_active_ws):
            still = (
                (
                    await conn.execute(
                        text("SELECT status, purged_at FROM workspace.workspaces WHERE id = :ws"),
                        {"ws": ws},
                    )
                )
                .mappings()
                .one()
            )
            assert still["purged_at"] is None


async def test_purge_all_rerun_against_the_same_workspace_is_a_clean_no_op(
    live_db: LiveDbDsns,
    purger_engine: AsyncEngine,
    three_workspaces: dict[str, dict[str, str]],
) -> None:
    eligible_ws = three_workspaces["eligible"]["workspace_id"]

    class _NullQdrant:
        async def collection_exists(self, name: str) -> bool:
            return False

        async def delete_collection(self, name: str) -> bool:
            return False

    class _NullMinio:
        def list_objects(
            self, bucket: str, prefix: str | None = None, recursive: bool = False
        ) -> list[object]:
            return []

    engine = create_engine(DatabaseSettings(url=live_db.purger), poolclass=NullPool)
    try:
        first = await purge_all(
            engine,
            _NullQdrant(),  # type: ignore[arg-type]
            _NullMinio(),  # type: ignore[arg-type]
            "unused-bucket",
            retention=_RETENTION,
            now=_NOW,
            workspace_id=eligible_ws,
        )
        assert len(first) == 1

        second = await purge_all(
            engine,
            _NullQdrant(),  # type: ignore[arg-type]
            _NullMinio(),  # type: ignore[arg-type]
            "unused-bucket",
            retention=_RETENTION,
            now=_NOW,
            workspace_id=eligible_ws,
        )
    finally:
        await engine.dispose()

    assert second == []  # already-purged workspace no longer eligible


async def test_purger_role_delete_without_set_config_fails_safe_to_zero_rows(
    purger_engine: AsyncEngine,
    three_workspaces: dict[str, dict[str, str]],
) -> None:
    """The RLS fail-safe proof: content genuinely exists (seeded by
    ``three_workspaces``), but a DELETE issued with NO ``app.workspace_id``
    set affects zero rows -- Layer 1 alone, with no explicit predicate,
    already refuses."""
    ws = three_workspaces["still_active"]["workspace_id"]
    async with purger_engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM knowledge.documents WHERE workspace_id = :ws"), {"ws": ws}
        )
    assert result.rowcount == 0


async def test_purger_role_column_grant_blocks_email_but_allows_status(
    purger_engine: AsyncEngine,
) -> None:
    async with purger_engine.begin() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            await conn.execute(text("SELECT email FROM workspace.users LIMIT 1"))
    assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"

    async with purger_engine.begin() as conn:
        await conn.execute(text("SELECT status FROM workspace.users LIMIT 1"))  # must not raise


async def test_platform_scope_credential_survives_purging_a_workspace_with_user_scope_credentials(
    live_db: LiveDbDsns,
    purger_engine: AsyncEngine,
    three_workspaces: dict[str, dict[str, str]],
) -> None:
    eligible_ws = three_workspaces["eligible"]["workspace_id"]
    platform_credential_id = new_uuid7()

    # `credentials.credentials` is FORCE ROW LEVEL SECURITY -- even the owner
    # is policy-subject, so a platform-scope (workspace_id IS NULL) row needs
    # the same transient NO FORCE toggle `seed_platform_credential` uses.
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE credentials.credentials NO FORCE ROW LEVEL SECURITY")
            )
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO credentials.credentials "
                        "(id, workspace_id, provider, scope, ciphertext_ref, key_id) "
                        "VALUES (:id, NULL, 'openai', 'platform', "
                        "'vault:v1:ciphertext', 'tenant-secrets')"
                    ),
                    {"id": platform_credential_id},
                )
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text("ALTER TABLE credentials.credentials FORCE ROW LEVEL SECURITY")
                )
    finally:
        await engine.dispose()

    class _NullQdrant:
        async def collection_exists(self, name: str) -> bool:
            return False

        async def delete_collection(self, name: str) -> bool:
            return False

    class _NullMinio:
        def list_objects(
            self, bucket: str, prefix: str | None = None, recursive: bool = False
        ) -> list[object]:
            return []

    purge_engine = create_engine(DatabaseSettings(url=live_db.purger), poolclass=NullPool)
    try:
        await purge_all(
            purge_engine,
            _NullQdrant(),  # type: ignore[arg-type]
            _NullMinio(),  # type: ignore[arg-type]
            "unused-bucket",
            retention=_RETENTION,
            now=_NOW,
            workspace_id=eligible_ws,
        )
    finally:
        await purge_engine.dispose()

    # The user-scope credential row is gone.
    assert await _table_count(purger_engine, "credentials.credentials", eligible_ws) == 0

    # The platform-scope row (workspace_id IS NULL) survives, reachable via
    # `platform_credentials_read` (PUBLIC, no GUC needed).
    async with purger_engine.begin() as conn:
        still_there = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM credentials.credentials "
                    "WHERE workspace_id IS NULL AND id = :id"
                ),
                {"id": platform_credential_id},
            )
        ).scalar_one()
    assert still_there == 1
