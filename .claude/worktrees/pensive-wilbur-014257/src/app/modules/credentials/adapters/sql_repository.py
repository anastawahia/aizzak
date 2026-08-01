"""SQL adapter for ``CredentialRepository`` (02-port-contracts §2; 01-data-model
§2.3; ``migrations/versions/credentials/0001_credentials.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly below. ``credentials.credentials`` additionally carries the R1-
style ``platform_credentials_read`` SELECT-only policy (01 §3.1) for
``scope='platform'`` rows (``workspace_id IS NULL``) — ``find_active``
branches on ``scope`` to reach those rows; ``get`` stays strict tenant-only
(R7) even though the RLS policy would otherwise let a platform row through,
because the extra application-level ``WHERE workspace_id = :ws`` filter
rules it out on purpose (DD-04 defence in depth). No RLS-subject path
inserts platform credentials in v1 (R3) — this adapter's ``add``/``save``
only ever run under a tenant's own RLS context.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.types import Uuid as UuidStr
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

credentials = Table(
    "credentials",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=True),
    Column("provider", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("label", Text, nullable=True),
    Column("ciphertext_ref", Text, nullable=False),
    Column("key_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="credentials",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlCredentialRepository:
    """SQL ``CredentialRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — there is no cross-call unit of work yet.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def add(self, ctx: ExecutionContext, credential: Credential) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched credential.workspace_id is then rejected by the
        # RLS WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent). In practice v1 only ever adds user-scope
        # credentials through this adapter (R3): no RLS-subject path inserts
        # a platform-scope row.
        stmt = insert(credentials).values(
            id=credential.id,
            workspace_id=credential.workspace_id,
            provider=credential.provider.value,
            scope=credential.scope.value,
            label=credential.label,
            ciphertext_ref=credential.ciphertext_ref.ciphertext,
            key_id=credential.ciphertext_ref.key_name,
            status=credential.status.value,
            created_by=credential.created_by,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
            version=credential.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def get(self, ctx: ExecutionContext, credential_id: UuidStr) -> Credential | None:
        # R7: strict tenant-only. A platform credential (`workspace_id IS
        # NULL`) can never match `workspace_id == ctx.workspace_id` (a real
        # tenant id), so it is never reachable here even though
        # `platform_credentials_read` would otherwise let it through at the
        # RLS layer -- only `find_active` resolves platform-scoped rows.
        stmt = select(credentials).where(
            credentials.c.id == credential_id, credentials.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def find_active(
        self, ctx: ExecutionContext, provider: ProviderRef, scope: CredentialScope
    ) -> Credential | None:
        if scope is CredentialScope.USER:
            stmt = (
                select(credentials)
                .where(
                    credentials.c.provider == provider.value,
                    credentials.c.scope == CredentialScope.USER.value,
                    credentials.c.status == CredentialStatus.ACTIVE.value,
                    credentials.c.workspace_id == ctx.workspace_id,
                )
                .limit(1)
            )
        else:
            # Platform rows (`workspace_id IS NULL`) are reached via the
            # additional, SELECT-only `platform_credentials_read` policy (01
            # §3.1) -- RLS policies OR together, so this is visible
            # regardless of what `tenant_isolation` alone would say.
            stmt = (
                select(credentials)
                .where(
                    credentials.c.provider == provider.value,
                    credentials.c.scope == CredentialScope.PLATFORM.value,
                    credentials.c.status == CredentialStatus.ACTIVE.value,
                    credentials.c.workspace_id.is_(None),
                )
                .limit(1)
            )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def list(self, ctx: ExecutionContext) -> list[Credential]:
        # Strict tenant-only (the `get` reasoning): `workspace_id = :ws` never
        # matches a platform row's NULL, so the SELECT-only
        # `platform_credentials_read` policy cannot widen this listing.
        # Ordered by `id` rather than `created_at` because UUIDv7 is
        # time-ordered — the same sort, but total, so two rows minted in the
        # same clock tick still come back in a stable order.
        stmt = (
            select(credentials)
            .where(credentials.c.workspace_id == ctx.workspace_id)
            .order_by(credentials.c.id.desc())
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate(row) for row in rows]

    async def save(self, ctx: ExecutionContext, credential: Credential) -> None:
        # Optimistic lock: only a row still at `credential.version` is
        # updated; v1's only in-place mutation is `revoke` (status only).
        stmt = (
            update(credentials)
            .where(
                credentials.c.id == credential.id,
                credentials.c.workspace_id == ctx.workspace_id,
                credentials.c.version == credential.version,
            )
            .values(
                status=credential.status.value,
                version=credentials.c.version + 1,
            )
            .returning(credentials.c.version, credentials.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(
                f"credential {credential.id} was modified concurrently (stale version)"
            )
        # `version`/`updated_at` are repository-owned (02-port-contracts §2)
        # -- write the fresh values back onto the aggregate, media precedent.
        credential.version, credential.updated_at = row


def _hydrate(row: RowMapping) -> Credential:
    return Credential(
        id=row["id"],
        workspace_id=row["workspace_id"],
        provider=ProviderRef(row["provider"]),
        scope=CredentialScope(row["scope"]),
        label=row["label"] or "",  # R8: column nullable, entity is non-optional str
        ciphertext_ref=CipherRef(ciphertext=row["ciphertext_ref"], key_name=row["key_id"]),
        status=CredentialStatus(row["status"]),
        created_by=row["created_by"] or "",  # R8: column nullable, entity is non-optional str
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: either a
    duplicate ``id`` or the R4 hardening index ``uq_cred_active_user``
    (a second concurrent active USER credential for the same
    ``(workspace_id, provider)``, closing the ``AddUserCredential``
    TOCTOU race) -- ``ConflictError`` (409, ``common.conflict``), which
    ``AddUserCredential`` already treats as its own invariant.
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``credential.workspace_id``
    on ``add``) -- an internal/500-class error (``common.internal``): a
    well-behaved caller can never trigger this, so it is not a normal 4xx.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("credential write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("credential write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting a credential", code="common.internal"
    )
