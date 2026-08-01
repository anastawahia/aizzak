"""Unit tests for workspace use-cases over in-memory fake repositories.
Pure: the ports are faked, so no infrastructure is exercised."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.modules.workspace.application.use_cases import (
    ArchiveWorkspace,
    ProvisionUserOnFirstLogin,
    RenameWorkspace,
)
from app.modules.workspace.domain.entities import User, Workspace
from app.modules.workspace.domain.events import UserProvisioned
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)


class _FakeWorkspaces:
    """In-memory ``WorkspaceRepository`` — ignores ``ctx`` (no RLS in unit tests)."""

    def __init__(self) -> None:
        self.rows: dict[str, Workspace] = {}

    async def get(self, ctx: ExecutionContext, workspace_id: str) -> Workspace | None:
        return self.rows.get(workspace_id)

    async def add(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        self.rows[workspace.id] = workspace

    async def save(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        self.rows[workspace.id] = workspace


class _FakeUsers:
    """In-memory ``UserRepository`` — keyed by the ``firebase_uid`` natural key,
    mirroring the unscoped identity read."""

    def __init__(self) -> None:
        self.rows: dict[str, User] = {}

    async def get_by_firebase_uid(self, firebase_uid: str) -> User | None:
        return self.rows.get(firebase_uid)

    async def add(self, ctx: ExecutionContext, user: User) -> None:
        self.rows[user.firebase_uid] = user


class _FakeUsersRaceOnce(_FakeUsers):
    """R5: simulates the JIT race -- a concurrent first-login for the SAME
    ``firebase_uid`` commits between this call's existence-check and its own
    ``add``. The first ``get_by_firebase_uid`` still sees nothing (the race
    hasn't happened yet from this call's point of view); ``add`` then loses
    the uniqueness race (mirroring the SQL adapter's 23505 ->
    ``ConflictError`` translation); the retried ``get_by_firebase_uid``
    finds the winner's already-committed row."""

    def __init__(self, winner: User) -> None:
        super().__init__()
        self._winner = winner
        self._get_calls = 0

    async def get_by_firebase_uid(self, firebase_uid: str) -> User | None:
        self._get_calls += 1
        if self._get_calls == 1:
            return None
        return self._winner

    async def add(self, ctx: ExecutionContext, user: User) -> None:
        raise ConflictError("firebase_uid already provisioned (simulated race)")


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"owner"}),
    )


async def _seed_workspace(
    workspaces: _FakeWorkspaces, *, status: WorkspaceStatus = WorkspaceStatus.ACTIVE
) -> Workspace:
    now = utc_now()
    ws = Workspace(
        id=new_uuid7(),
        owner_user_id="u1",
        name=WorkspaceName("Acme"),
        status=status,
        created_at=now,
        updated_at=now,
        version=1,
    )
    await workspaces.add(_ctx(ws.id), ws)
    return ws


# --------------------------------------------------------------------------- #
# ProvisionUserOnFirstLogin                                                    #
# --------------------------------------------------------------------------- #
async def test_provision_creates_workspace_and_user() -> None:
    workspaces, users = _FakeWorkspaces(), _FakeUsers()
    outcome = await ProvisionUserOnFirstLogin(workspaces, users).execute(
        firebase_uid="fb-1", email="Zaid@Example.com", correlation_id="c"
    )
    assert outcome.created is True
    # Platform id is a minted UUIDv7 (DD-02); the external uid is a separate key.
    assert outcome.user.firebase_uid == "fb-1"
    assert UUID(outcome.user.id).version == 7
    assert outcome.user.email.value == "zaid@example.com"
    assert outcome.user.workspace_id in workspaces.rows
    assert workspaces.rows[outcome.user.workspace_id].owner_user_id == outcome.user.id
    assert len(outcome.events) == 2
    provisioned = outcome.events[1]
    assert isinstance(provisioned, UserProvisioned)
    assert provisioned.user_id == outcome.user.id
    assert users.rows["fb-1"].workspace_id == outcome.user.workspace_id


async def test_provision_is_idempotent_for_known_uid() -> None:
    workspaces, users = _FakeWorkspaces(), _FakeUsers()
    provision = ProvisionUserOnFirstLogin(workspaces, users)
    first = await provision.execute(firebase_uid="fb-1", email="a@b.com", correlation_id="c")
    second = await provision.execute(firebase_uid="fb-1", email="a@b.com", correlation_id="c")
    assert first.created is True
    assert second.created is False
    assert second.events == ()
    assert second.user is users.rows["fb-1"]
    assert len(workspaces.rows) == 1  # no second workspace minted


async def test_provision_rejects_invalid_email() -> None:
    provision = ProvisionUserOnFirstLogin(_FakeWorkspaces(), _FakeUsers())
    with pytest.raises(ValidationError):
        await provision.execute(firebase_uid="fb-1", email="not-an-email", correlation_id="c")


async def test_provision_race_on_concurrent_first_login_returns_existing_user() -> None:
    """R5: a losing concurrent first-login degrades to an idempotent re-login
    (``created=False``, no events) instead of surfacing the ``ConflictError``
    from ``users.add`` to the caller."""
    workspaces = _FakeWorkspaces()
    now = utc_now()
    winner = User(
        id=new_uuid7(),
        workspace_id=new_uuid7(),
        firebase_uid="fb-1",
        email=Email("a@b.com"),
        display_name="a@b.com",
        status=UserStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        version=1,
    )
    users = _FakeUsersRaceOnce(winner)

    outcome = await ProvisionUserOnFirstLogin(workspaces, users).execute(
        firebase_uid="fb-1", email="a@b.com", correlation_id="c"
    )

    assert outcome.created is False
    assert outcome.events == ()
    assert outcome.user is winner
    # The loser's own workspace write still landed -- an accepted orphan for
    # v1 (R5): no owner user ever references it, and INV-W1 is not violated
    # since it never becomes anyone's *provisioned* workspace.
    assert len(workspaces.rows) == 1


# --------------------------------------------------------------------------- #
# RenameWorkspace                                                              #
# --------------------------------------------------------------------------- #
async def test_rename_updates_persisted_workspace() -> None:
    workspaces = _FakeWorkspaces()
    ws = await _seed_workspace(workspaces)
    result = await RenameWorkspace(workspaces).execute(_ctx(ws.id), ws.id, "Renamed")
    assert result.name.value == "Renamed"
    assert workspaces.rows[ws.id].name.value == "Renamed"


async def test_rename_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await RenameWorkspace(_FakeWorkspaces()).execute(_ctx(), "missing", "X")


async def test_rename_archived_raises_conflict() -> None:
    workspaces = _FakeWorkspaces()
    ws = await _seed_workspace(workspaces, status=WorkspaceStatus.ARCHIVED)
    with pytest.raises(ConflictError):
        await RenameWorkspace(workspaces).execute(_ctx(ws.id), ws.id, "X")


async def test_rename_rejects_blank_name() -> None:
    workspaces = _FakeWorkspaces()
    ws = await _seed_workspace(workspaces)
    with pytest.raises(ValidationError):
        await RenameWorkspace(workspaces).execute(_ctx(ws.id), ws.id, "   ")


# --------------------------------------------------------------------------- #
# ArchiveWorkspace                                                             #
# --------------------------------------------------------------------------- #
async def test_archive_emits_event_and_persists() -> None:
    workspaces = _FakeWorkspaces()
    ws = await _seed_workspace(workspaces)
    _, events = await ArchiveWorkspace(workspaces).execute(_ctx(ws.id), ws.id)
    assert len(events) == 1
    assert workspaces.rows[ws.id].status is WorkspaceStatus.ARCHIVED


async def test_archive_is_idempotent_no_event() -> None:
    workspaces = _FakeWorkspaces()
    ws = await _seed_workspace(workspaces, status=WorkspaceStatus.ARCHIVED)
    _, events = await ArchiveWorkspace(workspaces).execute(_ctx(ws.id), ws.id)
    assert events == ()
