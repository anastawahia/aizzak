"""PostgreSQL adapter for the platform's provider keys (BE-ADM-011).

Declares its own local Core ``Table`` objects against a module-local
``MetaData`` — the same R9 pattern ``sql_accounts.py`` uses to reach
``workspace.users``/``access.role_assignments`` without importing those
modules (import-linter contract 4 forbids it, and a shared table object would
be a shared schema dependency in all but name).

Every statement runs under ``app.platform_admin_write``, whose policies on
``credentials.credentials`` are pinned to ``workspace_id IS NULL AND scope =
'platform'``, so nothing here can reach a tenant's own key even by writing a
wrong ``WHERE``. The application-level filters below are still spelled out:
the same DD-04 defence in depth every other adapter applies.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Select,
    Table,
    Text,
    Uuid,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.clock import utc_now
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.types import Uuid as AppUuid
from app.modules.admin.ports.providers import (
    KeyPresence,
    PlatformCredentialStore,
    PlatformKeyChange,
    PlatformProviderKey,
    StoredCipher,
)

_metadata = MetaData()
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
admin_audit_log = Table(
    "admin_audit_log",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("actor_user_id", _uuid_col, nullable=False),
    Column("target_user_id", _uuid_col, nullable=True),
    Column("workspace_id", _uuid_col, nullable=True),
    Column("action", Text, nullable=False),
    Column("previous_status", Text, nullable=True),
    Column("new_status", Text, nullable=True),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="platform",
)

_PLATFORM_SCOPE = "platform"
_ACTIVE = "active"
_REVOKED = "revoked"

# The credential's own `status` vocabulary above and the "was there a key
# before this call" vocabulary below share the string 'active' and nothing
# else; typing the second one keeps a row status from being reported as a
# presence by accident.
_KEY_ABSENT: KeyPresence = "absent"
_KEY_ACTIVE: KeyPresence = "active"

PlatformAdminWriteSessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SqlPlatformCredentialStore:
    """Rotate and revoke platform provider keys, each with its audit row."""

    def __init__(self, platform_admin_write_session: PlatformAdminWriteSessionProvider) -> None:
        self._platform_admin_write_session = platform_admin_write_session

    async def active_keys(self) -> tuple[PlatformProviderKey, ...]:
        """Every provider's current platform key, one row per provider.

        Revoked rows are NOT listed, which is the opposite of the tenant-facing
        ``ListCredentials`` and deliberate: this listing answers "what does the
        platform supply right now" for a set of providers the caller already
        knows, and a rotated-away key on the same provider would read as a
        second, competing entry. The history stays in the table and in the
        audit ledger, where a reader is asking a different question.
        """
        stmt = (
            select(credentials)
            .where(
                credentials.c.scope == _PLATFORM_SCOPE,
                credentials.c.workspace_id.is_(None),
                credentials.c.status == _ACTIVE,
            )
            .order_by(credentials.c.provider)
        )
        try:
            async with self._platform_admin_write_session() as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return tuple(_hydrate(row) for row in rows)

    async def active_cipher(self, provider: str) -> StoredCipher | None:
        """The Vault reference for one provider's live key, or ``None``."""
        stmt = select(credentials.c.ciphertext_ref, credentials.c.key_id).where(
            credentials.c.provider == provider,
            credentials.c.scope == _PLATFORM_SCOPE,
            credentials.c.workspace_id.is_(None),
            credentials.c.status == _ACTIVE,
        )
        try:
            async with self._platform_admin_write_session() as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            return None
        return StoredCipher(ciphertext=row["ciphertext_ref"], key_name=row["key_id"])

    async def store(
        self,
        *,
        provider: str,
        ciphertext: str,
        key_name: str,
        label: str,
        actor_user_id: AppUuid,
        reason: str,
    ) -> PlatformKeyChange:
        """Revoke whatever is live and insert the replacement, atomically.

        The existing row is locked before it is read, for the reason
        ``uq_cred_active_platform`` exists at all: two administrators rotating
        one provider at the same moment would otherwise both read "one active
        key", both revoke it and both insert, and the index would then reject
        the loser's INSERT as a ``23505`` — a 409 the caller can retry, which
        is the honest outcome, but the lock is what makes it a rare one rather
        than the normal result of two people working at once.
        """
        try:
            async with self._platform_admin_write_session() as session:
                previous = (await session.execute(_lock_active(provider))).mappings().one_or_none()
                now = utc_now()
                if previous is not None:
                    await session.execute(
                        update(credentials)
                        .where(credentials.c.id == previous["id"])
                        .values(status=_REVOKED, version=credentials.c.version + 1)
                    )
                credential_id = new_uuid7()
                await session.execute(
                    insert(credentials).values(
                        id=credential_id,
                        workspace_id=None,
                        provider=provider,
                        scope=_PLATFORM_SCOPE,
                        label=label,
                        ciphertext_ref=ciphertext,
                        key_id=key_name,
                        status=_ACTIVE,
                        created_by=actor_user_id,
                        created_at=now,
                        updated_at=now,
                        version=1,
                    )
                )
                previous_status = _KEY_ACTIVE if previous is not None else _KEY_ABSENT
                audit_id = await _append_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action="provider.key_stored",
                    previous_status=previous_status,
                    new_status=_ACTIVE,
                    reason=reason,
                    details={"provider": provider, "credential_id": credential_id},
                    created_at=now,
                )
                stored = (
                    (
                        await session.execute(
                            select(credentials).where(credentials.c.id == credential_id)
                        )
                    )
                    .mappings()
                    .one()
                )
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return PlatformKeyChange(
            provider=provider,
            key=_hydrate(stored),
            previous_status=previous_status,
            changed=True,
            audit_id=audit_id,
        )

    async def revoke(
        self, *, provider: str, actor_user_id: AppUuid, reason: str
    ) -> PlatformKeyChange:
        """Retire this provider's platform key; a provider with none is a no-op.

        Idempotent by the same rule as every other admin transition here: a
        second revocation writes no second audit row, because the ledger dates
        actions and the action is over.
        """
        try:
            async with self._platform_admin_write_session() as session:
                current = (await session.execute(_lock_active(provider))).mappings().one_or_none()
                if current is None:
                    return PlatformKeyChange(
                        provider=provider,
                        key=None,
                        previous_status=_KEY_ABSENT,
                        changed=False,
                        audit_id=None,
                    )
                now = utc_now()
                await session.execute(
                    update(credentials)
                    .where(credentials.c.id == current["id"])
                    .values(status=_REVOKED, version=credentials.c.version + 1)
                )
                audit_id = await _append_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action="provider.key_revoked",
                    previous_status=_KEY_ACTIVE,
                    new_status=_REVOKED,
                    reason=reason,
                    details={"provider": provider, "credential_id": current["id"]},
                    created_at=now,
                )
                revoked = (
                    (
                        await session.execute(
                            select(credentials).where(credentials.c.id == current["id"])
                        )
                    )
                    .mappings()
                    .one()
                )
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return PlatformKeyChange(
            provider=provider,
            key=_hydrate(revoked),
            previous_status=_KEY_ACTIVE,
            changed=True,
            audit_id=audit_id,
        )


def _lock_active(provider: str) -> Select[tuple[str]]:
    """Select-for-update on the provider's live platform row, if any."""
    return (
        select(credentials.c.id)
        .where(
            credentials.c.provider == provider,
            credentials.c.scope == _PLATFORM_SCOPE,
            credentials.c.workspace_id.is_(None),
            credentials.c.status == _ACTIVE,
        )
        .with_for_update()
    )


async def _append_audit(
    session: AsyncSession,
    *,
    actor_user_id: AppUuid,
    action: str,
    previous_status: str,
    new_status: str,
    reason: str,
    details: dict[str, object],
    created_at: datetime,
) -> AppUuid:
    """Write one ledger row that names no target user, and say so explicitly.

    ``target_user_id``/``workspace_id`` are passed as ``None`` rather than
    omitted: a platform key belongs to the deployment, and the migration's
    paired CHECK is what turns "no subject" into a shape the ledger accepts
    instead of a column somebody forgot to fill.
    """
    audit_id = new_uuid7()
    await session.execute(
        insert(admin_audit_log).values(
            id=audit_id,
            actor_user_id=actor_user_id,
            target_user_id=None,
            workspace_id=None,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            details=details,
            created_at=created_at,
        )
    )
    return audit_id


def _hydrate(row: RowMapping) -> PlatformProviderKey:
    return PlatformProviderKey(
        id=row["id"],
        provider=row["provider"],
        label=row["label"] or "",
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Driver failures never escape this adapter (the R6 house rule).

    ``23505`` is ``uq_cred_active_platform``: a concurrent rotation of the
    same provider won the race, and 409 tells the loser to re-read and retry.
    ``42501`` means the RLS ``WITH CHECK`` refused the write, which a
    well-behaved caller cannot provoke — the sentinel's policies accept
    exactly the platform-scope shape this adapter writes — so it is a 500,
    not a 4xx that would blame the operator.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("this provider's platform key was rotated concurrently")
    if sqlstate == "42501":
        return AppError(
            "platform credential write rejected by row-level security", code="common.internal"
        )
    return AppError(
        "unexpected database error while persisting a platform credential", code="common.internal"
    )


def _conforms(store: SqlPlatformCredentialStore) -> PlatformCredentialStore:
    """Static structural proof for the driven port."""
    return store
