"""What an account deletion writes, read off the emitted SQL.

The adapter runs against a session that answers each statement from a small
scripted state instead of a database: every claim below — the erased address,
the revoked assignments, the audit row's shape, the last-administrator refusal
— is decided in the adapter, before PostgreSQL is involved.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Delete, Insert, Select, Update

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ForbiddenError, ValidationError
from app.modules.admin.adapters.sql_accounts import SqlPlatformAccountManager
from app.modules.admin.application.users import DeletePlatformUser

_ACTOR = "018f0000-0000-7000-8000-00000000000a"
_TARGET = "018f0000-0000-7000-8000-000000000001"
_WORKSPACE = "018f0000-0000-7000-8000-0000000000f1"
_CORRELATION = "018f0000-0000-7000-8000-0000000000c1"


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0]


class _ScriptedSession:
    """Answer the adapter's reads in order and keep every statement."""

    def __init__(
        self,
        *,
        status: str = "active",
        deleted_at: datetime | None = None,
        platform_admins: list[str] | None = None,
        survivors: int = 1,
        revoked: list[str] | None = None,
    ) -> None:
        self.statements: list[Any] = []
        self._user = [
            {
                "id": _TARGET,
                "workspace_id": _WORKSPACE,
                "firebase_uid": "target-uid",
                "status": status,
                "deleted_at": deleted_at,
            }
        ]
        self._platform_admins = platform_admins or []
        self._survivors = survivors
        self._revoked = revoked or []
        self._reads = 0

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if isinstance(statement, Select):
            self._reads += 1
            return _Result([self._user, self._platform_admins, [self._survivors]][self._reads - 1])
        if isinstance(statement, Delete):
            return _Result(self._revoked)
        return _Result([])


def _manager(session: _ScriptedSession) -> SqlPlatformAccountManager:
    @asynccontextmanager
    async def provider() -> Any:
        yield session

    return SqlPlatformAccountManager(provider)


def _of(session: _ScriptedSession, kind: type) -> list[Any]:
    return [s for s in session.statements if isinstance(s, kind)]


async def test_a_deletion_erases_the_person_and_keeps_only_the_tombstone() -> None:
    session = _ScriptedSession(revoked=["member", "owner"])

    deletion = await _manager(session).delete(
        actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
    )

    (update,) = _of(session, Update)
    values = update.compile().params
    assert values["status"] == "deleted"
    assert values["display_name"] is None
    # The address is gone, not moved: `.invalid` can never be delivered to and
    # the id is all that is left to tell two tombstones apart.
    assert values["email"] == f"deleted+{_TARGET}@removed.invalid"
    assert deletion.deleted is True
    assert deletion.roles_revoked == ("member", "owner")
    assert deletion.deleted_at == values["deleted_at"]


async def test_the_audit_row_records_the_erasure_without_repeating_it() -> None:
    session = _ScriptedSession(revoked=["owner"])

    await _manager(session).delete(
        actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
    )

    (audit,) = _of(session, Insert)
    row = audit.compile().params
    assert row["action"] == "account.deleted"
    assert (row["previous_status"], row["new_status"]) == ("active", "deleted")
    assert row["reason"] == "Left the company"
    assert row["details"] == {"redacted": True, "roles_revoked": ["owner"]}
    # An audit row that quoted the erased address would not have erased it.
    assert "@" not in str(row["details"])


async def test_authority_does_not_outlive_the_account() -> None:
    session = _ScriptedSession(revoked=["member"])

    await _manager(session).delete(
        actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
    )

    (revoke,) = _of(session, Delete)
    assert _TARGET in revoke.compile().params.values()


async def test_repeating_a_deletion_keeps_the_original_date_and_writes_nothing() -> None:
    when = datetime(2026, 2, 1, tzinfo=UTC)
    session = _ScriptedSession(status="deleted", deleted_at=when)

    deletion = await _manager(session).delete(
        actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
    )

    assert (deletion.deleted, deletion.deleted_at, deletion.audit_id) == (False, when, None)
    assert deletion.roles_revoked == ()
    # Re-dating a finished event is what a second audit row would do.
    assert _of(session, Insert) == _of(session, Update) == []


async def test_the_last_platform_administrator_cannot_be_deleted() -> None:
    # Two administrators deleting each other at the same moment is the only way
    # to reach this: each would otherwise read the other as a survivor.
    session = _ScriptedSession(platform_admins=[_TARGET], survivors=0)

    with pytest.raises(ConflictError):
        await _manager(session).delete(
            actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
        )

    assert _of(session, Update) == _of(session, Delete) == []


async def test_deleting_one_of_several_administrators_proceeds() -> None:
    session = _ScriptedSession(platform_admins=[_TARGET, _ACTOR], survivors=1)

    deletion = await _manager(session).delete(
        actor_user_id=_ACTOR, target_user_id=_TARGET, reason="Left the company"
    )

    assert deletion.deleted is True


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WORKSPACE, user_id=_ACTOR, correlation_id=_CORRELATION, roles=frozenset()
    )


async def test_an_administrator_cannot_delete_their_own_account() -> None:
    session = _ScriptedSession()

    # Disabling yourself is undone by any other administrator; this is not
    # undone by anyone, so the refusal is not merely the status route's rule
    # repeated — it is the one place where locking yourself out is permanent.
    with pytest.raises(ForbiddenError):
        await DeletePlatformUser(_manager(session)).execute(
            _ctx(), target_user_id=_ACTOR, reason="Mistaken cleanup"
        )

    assert session.statements == []


async def test_an_unexplained_deletion_never_reaches_the_database() -> None:
    session = _ScriptedSession()

    with pytest.raises(ValidationError):
        await DeletePlatformUser(_manager(session)).execute(
            _ctx(), target_user_id=_TARGET, reason="  x  "
        )

    assert session.statements == []


async def test_the_reason_is_stored_trimmed() -> None:
    session = _ScriptedSession()

    await DeletePlatformUser(_manager(session)).execute(
        _ctx(), target_user_id=_TARGET, reason="  Left the company  "
    )

    (audit,) = _of(session, Insert)
    assert audit.compile().params["reason"] == "Left the company"
