"""Live-Postgres tests for ``SqlCredentialRepository`` + RLS (09-testing-strategy
§3), plus the R4 hardening index and the 01 §3.1 platform-credentials-read
policy.

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. A credential builder mirrors
``tests/unit/test_credentials_use_cases.py``, except every id is a *real*
``new_uuid7()`` string rather than an arbitrary label.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.framework.types import Json
from app.infrastructure.persistence.database import create_engine
from app.modules.credentials.adapters.sql_repository import SqlCredentialRepository
from app.modules.credentials.application.use_cases import AddUserCredential
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
class _FakeSecrets:
    """In-memory Transit stand-in (mirrors ``tests/unit/test_credentials_use_cases.py``):
    a base64 round-trip standing in for Vault encrypt/decrypt."""

    async def get_secret(self, path: str) -> Json:
        return {}

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        return f"vault:v1:{base64.b64encode(plaintext).decode('ascii')}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return base64.b64decode(ciphertext.split(":", 2)[2])


def _ctx(workspace_id: str, *, user_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _credential(
    *,
    workspace_id: str | None,
    scope: CredentialScope = CredentialScope.USER,
    credential_id: str | None = None,
    provider: str = "openai",
    label: str = "****abcd",
    ciphertext: str = "vault:v1:xx",
    key_name: str = "tenant-secrets",
    status: CredentialStatus = CredentialStatus.ACTIVE,
    created_by: str | None = None,
    now: datetime | None = None,
) -> Credential:
    at = now or utc_now()
    return Credential(
        id=credential_id or new_uuid7(),
        workspace_id=workspace_id,
        provider=ProviderRef(provider),
        scope=scope,
        label=label,
        ciphertext_ref=CipherRef(ciphertext=ciphertext, key_name=key_name),
        status=status,
        created_by=created_by or new_uuid7(),
        created_at=at,
        updated_at=at,
        version=1,
    )


async def _fetch_row_as_owner(
    owner_dsn: str, workspace_id: str, credential_id: str
) -> RowMapping | None:
    """Bypass the repository entirely: read the raw row as the table owner.
    ``credentials.credentials`` IS ``FORCE ROW LEVEL SECURITY`` (01 §3), so
    even the owner needs the tenant GUC set first, on the same
    connection/transaction, before reading."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT * FROM credentials.credentials WHERE id = :id"), {"id": credential_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1) round-trip add/get                                                      #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips(repo_credentials: SqlCredentialRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    cred = _credential(workspace_id=ws)

    await repo_credentials.add(ctx, cred)
    fetched = await repo_credentials.get(ctx, cred.id)

    assert fetched is not None
    assert fetched.id == cred.id
    assert fetched.workspace_id == ws
    assert fetched.provider == cred.provider
    assert fetched.scope is CredentialScope.USER
    assert fetched.label == cred.label
    assert fetched.ciphertext_ref == cred.ciphertext_ref
    assert fetched.status is CredentialStatus.ACTIVE
    assert fetched.created_by == cred.created_by
    assert fetched.created_at == cred.created_at
    assert fetched.updated_at == cred.updated_at
    assert fetched.version == 1


# --------------------------------------------------------------------------- #
# (2) missing -> None                                                        #
# --------------------------------------------------------------------------- #
async def test_get_missing_returns_none(repo_credentials: SqlCredentialRepository) -> None:
    assert await repo_credentials.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3) find_active(USER) hits the active row                                  #
# --------------------------------------------------------------------------- #
async def test_find_active_user_hits_active_credential(
    repo_credentials: SqlCredentialRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    cred = _credential(workspace_id=ws, provider="openai")
    await repo_credentials.add(ctx, cred)

    found = await repo_credentials.find_active(ctx, ProviderRef("openai"), CredentialScope.USER)
    assert found is not None
    assert found.id == cred.id


# --------------------------------------------------------------------------- #
# (4) find_active(USER) skips a revoked row                                  #
# --------------------------------------------------------------------------- #
async def test_find_active_user_skips_revoked(repo_credentials: SqlCredentialRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    cred = _credential(workspace_id=ws, provider="openai", status=CredentialStatus.REVOKED)
    await repo_credentials.add(ctx, cred)

    found = await repo_credentials.find_active(ctx, ProviderRef("openai"), CredentialScope.USER)
    assert found is None


# --------------------------------------------------------------------------- #
# (5) save() revoke: version/updated_at write-back, owner-verified           #
# --------------------------------------------------------------------------- #
async def test_save_revoke_advances_version_and_writes_back(
    repo_credentials: SqlCredentialRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    cred = _credential(workspace_id=ws)
    await repo_credentials.add(ctx, cred)
    created_at = cred.created_at

    cred.revoke(utc_now())
    await repo_credentials.save(ctx, cred)

    assert cred.version == 2
    assert cred.status is CredentialStatus.REVOKED
    assert cred.updated_at >= created_at
    row = await _fetch_row_as_owner(live_db.owner, ws, cred.id)
    assert row is not None
    assert row["status"] == "revoked"
    assert row["version"] == 2

    fetched = await repo_credentials.get(ctx, cred.id)
    assert fetched is not None
    assert fetched.status is CredentialStatus.REVOKED
    assert fetched.version == 2


# --------------------------------------------------------------------------- #
# (6) optimistic conflict                                                     #
# --------------------------------------------------------------------------- #
async def test_save_with_stale_version_raises_conflict_and_leaves_row_unchanged(
    repo_credentials: SqlCredentialRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    cred = _credential(workspace_id=ws)
    await repo_credentials.add(ctx, cred)

    reader_a = await repo_credentials.get(ctx, cred.id)
    reader_b = await repo_credentials.get(ctx, cred.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.revoke(utc_now())
    await repo_credentials.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.revoke(utc_now())  # reader_b is still stuck at version==1
    with pytest.raises(ConflictError):
        await repo_credentials.save(ctx, reader_b)

    row = await _fetch_row_as_owner(live_db.owner, ws, cred.id)
    assert row is not None
    assert row["version"] == 2
    assert row["status"] == "revoked"


# --------------------------------------------------------------------------- #
# (7) forged cross-tenant write -> RLS WITH CHECK rejects it                  #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_credentials: SqlCredentialRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged = _credential(workspace_id=ws_b)  # claims tenant B

    with pytest.raises(AppError) as exc_info:
        await repo_credentials.add(_ctx(ws_a), forged)  # added under tenant A's context

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_credentials.get(_ctx(ws_b), forged.id) is None


# --------------------------------------------------------------------------- #
# (8) two-tenant isolation                                                    #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_credentials: SqlCredentialRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    cred_a = _credential(workspace_id=ws_a)
    await repo_credentials.add(_ctx(ws_a), cred_a)

    assert await repo_credentials.get(_ctx(ws_b), cred_a.id) is None
    assert await repo_credentials.get(_ctx(ws_a), cred_a.id) is not None

    cred_b = _credential(workspace_id=ws_b)
    await repo_credentials.add(_ctx(ws_b), cred_b)

    assert await repo_credentials.get(_ctx(ws_a), cred_b.id) is None
    assert await repo_credentials.get(_ctx(ws_b), cred_b.id) is not None


# --------------------------------------------------------------------------- #
# (9) PLATFORM scope: visible from BOTH tenants; user rows stay isolated      #
# --------------------------------------------------------------------------- #
async def test_platform_scope_visible_from_both_tenants_user_rows_stay_isolated(
    repo_credentials: SqlCredentialRepository,
    seed_platform_credential: Callable[[Credential], Awaitable[None]],
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    platform_cred = _credential(
        workspace_id=None, scope=CredentialScope.PLATFORM, provider="claude"
    )
    await seed_platform_credential(platform_cred)

    found_a = await repo_credentials.find_active(
        _ctx(ws_a), ProviderRef("claude"), CredentialScope.PLATFORM
    )
    found_b = await repo_credentials.find_active(
        _ctx(ws_b), ProviderRef("claude"), CredentialScope.PLATFORM
    )
    assert found_a is not None
    assert found_b is not None
    assert found_a.id == found_b.id == platform_cred.id
    assert found_a.workspace_id is None

    # An ordinary user-scope row for the SAME provider stays tenant-isolated
    # (the platform row's visibility does not leak tenant rows either way).
    user_cred_a = _credential(workspace_id=ws_a, provider="claude", scope=CredentialScope.USER)
    await repo_credentials.add(_ctx(ws_a), user_cred_a)

    assert await repo_credentials.get(_ctx(ws_b), user_cred_a.id) is None
    cross_tenant_find = await repo_credentials.find_active(
        _ctx(ws_b), ProviderRef("claude"), CredentialScope.USER
    )
    assert cross_tenant_find is None


# --------------------------------------------------------------------------- #
# (10) empty-string GUC hides tenant rows; platform rows unaffected (row-keyed)#
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_hides_tenant_rows_but_platform_rows_stay_visible(
    repo_credentials: SqlCredentialRepository,
    seed_platform_credential: Callable[[Credential], Awaitable[None]],
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """``tenant_isolation``'s ``NULLIF`` hardening fails safe to zero rows for
    ``scope='user'`` rows once the GUC resets to ``''``. But
    ``platform_credentials_read`` (01 §3.1) is a ROW-KEYED policy
    (``workspace_id IS NULL AND scope = 'platform'``) that never references
    the GUC at all -- it is unaffected by the tenant GUC's value, so a
    platform row legitimately stays visible even with no tenant context."""
    ws_a = new_uuid7()
    await repo_credentials.add(_ctx(ws_a), _credential(workspace_id=ws_a, provider="gemini"))
    platform_cred = _credential(
        workspace_id=None, scope=CredentialScope.PLATFORM, provider="gemini"
    )
    await seed_platform_credential(platform_cred)

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        tenant_count = (
            await session.execute(
                text("SELECT count(*) FROM credentials.credentials WHERE scope = 'user'")
            )
        ).scalar_one()
        platform_count = (
            await session.execute(
                text("SELECT count(*) FROM credentials.credentials WHERE scope = 'platform'")
            )
        ).scalar_one()

    assert tenant_count == 0
    assert platform_count == 1


# --------------------------------------------------------------------------- #
# (11) R4 hardening end-to-end: duplicate-active -> 409 via the real use case #
# --------------------------------------------------------------------------- #
async def test_add_user_credential_use_case_duplicate_active_conflicts_end_to_end(
    repo_credentials: SqlCredentialRepository,
) -> None:
    """Drives two full ``AddUserCredential.execute()`` calls over the REAL
    adapter (with a tiny fake ``SecretsProvider``) to prove the R4 hardening
    index (``uq_cred_active_user``) backs the use case's own duplicate check
    end-to-end, closing the TOCTOU window a purely in-process check would
    leave open under real concurrency."""
    ctx = _ctx(new_uuid7())
    add = AddUserCredential(repo_credentials, _FakeSecrets())

    await add.execute(ctx, provider="openai", raw_key="sk-first-key")

    with pytest.raises(ConflictError):
        await add.execute(ctx, provider="openai", raw_key="sk-second-key")
