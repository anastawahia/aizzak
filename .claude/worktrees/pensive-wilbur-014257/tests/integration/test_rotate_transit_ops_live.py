"""Live-Postgres proof for the Transit rotation sweep's RLS/grant reach
(``app.ops.rotate_transit``, P1-9, ``docs/p1-hardening-plan.md`` §3 step 12)
against real ``aizzak_test``.

The exit criterion this file exists for: the "RLS wall" `app.ops.retention`
hit is real here too, and the fix must be proven the identical way --
`transit_rotator` reaches EVERY tenant's row in one pass (no `app.
workspace_id` ever set), while remaining unable to forge or alter anything
but the one ciphertext column it is granted. A hermetic stub cannot prove
either half against real RLS/GRANT enforcement, which is why this lives here
rather than in ``tests/unit/test_ops_rotate_transit.py``.

Vault itself is stubbed (``_FakeSecrets``, the hermetic-file precedent): this
file is about the DATABASE half of the sweep -- persistence + tenant reach +
least privilege. The honest "rewrap is load-bearing" proof against a REAL,
throwaway Vault Transit key lives in
``tests/integration/test_vault_secrets.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.ops.rotate_transit import _TABLE_SPECS, rewrap_all, rewrap_table
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
_KEY = "tenant-secrets"


class _FakeSecrets:
    """Bumps ``:v1:`` to ``:v2:`` in the ciphertext string -- a Vault-shaped
    stand-in, never a real Vault call. What is real here is Postgres/RLS."""

    async def rewrap(self, key_name: str, ciphertext: str) -> str:
        return ciphertext.replace(":v1:", ":v2:")


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )


async def _seed_credential(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, *, ciphertext: str
) -> str:
    credential_id = new_uuid7()
    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                """
                INSERT INTO credentials.credentials
                    (id, workspace_id, provider, scope, label, ciphertext_ref, key_id,
                     status, created_at, updated_at, version)
                VALUES
                    (:id, :ws, 'openai', 'user', 'k', :ciphertext, :key_id,
                     'active', :now, :now, 1)
                """
            ),
            {
                "id": credential_id,
                "ws": ctx.workspace_id,
                "ciphertext": ciphertext,
                "key_id": _KEY,
                "now": _NOW,
            },
        )
    return credential_id


async def _seed_connection(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, *, ciphertext: str
) -> str:
    connection_id = new_uuid7()
    async with tenant_session(ctx) as session:
        await session.execute(
            text(
                """
                INSERT INTO integrations.connections
                    (id, workspace_id, connector_key, status, scopes, token_ref, key_id,
                     created_at, updated_at, version)
                VALUES
                    (:id, :ws, 'gmail', 'connected', '{}', :ciphertext, :key_id,
                     :now, :now, 1)
                """
            ),
            {
                "id": connection_id,
                "ws": ctx.workspace_id,
                "ciphertext": ciphertext,
                "key_id": _KEY,
                "now": _NOW,
            },
        )
    return connection_id


@pytest.mark.anyio
async def test_sweep_persists_the_rewrapped_ciphertext_across_every_tenant_in_one_pass(
    tenant_session: TenantSessionFactory, transit_rotator_engine: AsyncEngine, live_db: LiveDbDsns
) -> None:
    """Cross-tenant reach is the whole point of the role-scoped RLS carve-out
    (0002_transit_rotator.py, both migrations): two DIFFERENT workspaces, one
    credential each -- the sweep must reach and persist BOTH in one call,
    because it never sets any `app.workspace_id` at all (the
    `test_retention_ops_live.py` precedent)."""
    ws_one, ws_two = _ctx(new_uuid7()), _ctx(new_uuid7())
    cred_one = await _seed_credential(tenant_session, ws_one, ciphertext="vault:v1:aaa")
    cred_two = await _seed_credential(tenant_session, ws_two, ciphertext="vault:v1:bbb")

    result = await rewrap_table(transit_rotator_engine, _FakeSecrets(), _TABLE_SPECS[0])

    assert result.table == "credentials.credentials"
    assert result.scanned == 2
    assert result.rewrapped == 2

    async with tenant_session(ws_one) as session:
        row_one = (
            await session.execute(
                text("SELECT ciphertext_ref FROM credentials.credentials WHERE id = :id"),
                {"id": cred_one},
            )
        ).scalar_one()
    async with tenant_session(ws_two) as session:
        row_two = (
            await session.execute(
                text("SELECT ciphertext_ref FROM credentials.credentials WHERE id = :id"),
                {"id": cred_two},
            )
        ).scalar_one()
    assert row_one == "vault:v2:aaa"
    assert row_two == "vault:v2:bbb"


@pytest.mark.anyio
async def test_sweep_is_safe_to_rerun_and_skips_already_current_rows(
    tenant_session: TenantSessionFactory, transit_rotator_engine: AsyncEngine
) -> None:
    """Re-running the sweep against rows it already rewrapped must find
    nothing left to change -- the module docstring's re-run-safety claim,
    proven against real Postgres rather than the hermetic fake."""
    ws = _ctx(new_uuid7())
    await _seed_credential(tenant_session, ws, ciphertext="vault:v1:ccc")

    first = await rewrap_table(transit_rotator_engine, _FakeSecrets(), _TABLE_SPECS[0])
    second = await rewrap_table(transit_rotator_engine, _FakeSecrets(), _TABLE_SPECS[0])

    assert first.rewrapped == 1
    assert second.scanned == first.scanned
    assert second.rewrapped == 0, "a row already at the current version must not be rewritten"


@pytest.mark.anyio
async def test_sweep_reaches_a_nullable_integrations_column_and_leaves_unset_rows_alone(
    tenant_session: TenantSessionFactory, transit_rotator_engine: AsyncEngine
) -> None:
    """``integrations.connections.token_ref`` is NULLABLE (a `pending`
    connection has none yet) -- the sweep's `IS NOT NULL` filter must skip
    those rows rather than erroring on them."""
    ws = _ctx(new_uuid7())
    await _seed_connection(tenant_session, ws, ciphertext="vault:v1:ddd")
    # A pending connection with no token yet -- must not be scanned.
    async with tenant_session(ws) as session:
        await session.execute(
            text(
                "INSERT INTO integrations.connections "
                "(id, workspace_id, connector_key, status, scopes, "
                "created_at, updated_at, version) "
                "VALUES (:id, :ws, 'slack', 'pending', '{}', :now, :now, 1)"
            ),
            {"id": new_uuid7(), "ws": ws.workspace_id, "now": _NOW},
        )

    result = await rewrap_table(transit_rotator_engine, _FakeSecrets(), _TABLE_SPECS[1])

    assert result.scanned == 1  # only the connected row with a token_ref
    assert result.rewrapped == 1


@pytest.mark.anyio
async def test_rewrap_all_sweeps_all_three_tables_against_the_real_schema(
    transit_rotator_engine: AsyncEngine,
) -> None:
    """No seeded data required -- proves every table name/column this module
    hardcodes actually exists and is reachable by this role, not just that
    the SQL text looks right (the hermetic file's own limit)."""
    results = await rewrap_all(transit_rotator_engine, _FakeSecrets())

    assert [r.table for r in results] == [
        "credentials.credentials",
        "integrations.connections",
        "integrations.mcp_servers",
    ]
    assert all(r.rewrapped == 0 for r in results)  # nothing seeded in this test


@pytest.mark.anyio
async def test_transit_rotator_cannot_insert_delete_or_touch_any_other_column(
    transit_rotator_engine: AsyncEngine,
) -> None:
    """Least privilege, the denying direction (the ``retention_sweeper``/
    ``outbox_relay`` proof precedent): this role can overwrite ONE column on
    each of the three tables and nothing more -- never INSERT a row, never
    DELETE one, and never UPDATE a column other than the ciphertext it was
    granted."""
    statements = (
        "INSERT INTO credentials.credentials "
        "(id, workspace_id, provider, scope, label, ciphertext_ref, key_id, status) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'x', 'user', 'x', 'x', 'x', 'active')",
        "DELETE FROM credentials.credentials WHERE false",
        "UPDATE credentials.credentials SET status = 'revoked' WHERE false",
        "UPDATE integrations.connections SET status = 'revoked' WHERE false",
        "UPDATE integrations.mcp_servers SET status = 'disabled' WHERE false",
    )
    for statement in statements:
        with pytest.raises(DBAPIError) as exc_info:
            async with transit_rotator_engine.begin() as conn:
                await conn.execute(text(statement))
        assert getattr(exc_info.value.orig, "sqlstate", None) == "42501", statement
