"""Workspace use-cases (06-domain-models §1).

Thin application services that coordinate the pure domain over injected
repository ports. They own identity/time (framework ``new_uuid7`` / ``utc_now``)
and translate domain-rule violations into the shared framework error hierarchy
at this boundary — the domain itself stays framework-free. Domain events are
returned to the caller for later dispatch (event-bus / outbox wiring lands in
Phase 5); nothing here performs I/O beyond the injected repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.types import Uuid
from app.modules.workspace.domain.entities import User, Workspace
from app.modules.workspace.domain.errors import WorkspaceArchivedError, WorkspaceError
from app.modules.workspace.domain.events import (
    UserProvisioned,
    WorkspaceArchived,
    WorkspaceCreated,
    WorkspaceEvent,
)
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)
from app.modules.workspace.ports.inbound import ProvisionedUser
from app.modules.workspace.ports.presence import UserPresenceStore
from app.modules.workspace.ports.repository import UserRepository, WorkspaceRepository

_MAX_NAME_LEN = 80


@dataclass(frozen=True, slots=True)
class ProvisionOutcome:
    """Result of a JIT provisioning attempt."""

    user: User
    created: bool
    events: tuple[WorkspaceEvent, ...]


class ProvisionUserOnFirstLogin:
    """Mirror a Firebase identity into a fresh owner workspace on first login.

    Idempotent: a repeat login for a known UID is a no-op that returns the
    existing user with ``created=False`` and no events (INV-W1: one owner
    workspace per user).
    """

    def __init__(self, workspaces: WorkspaceRepository, users: UserRepository) -> None:
        self._workspaces = workspaces
        self._users = users

    async def execute(
        self,
        *,
        firebase_uid: str,
        email: str,
        correlation_id: Uuid,
        display_name: str | None = None,
    ) -> ProvisionOutcome:
        existing = await self._users.get_by_firebase_uid(firebase_uid)
        if existing is not None:
            return ProvisionOutcome(user=existing, created=False, events=())

        try:
            address = Email(email)
            name = WorkspaceName(_default_workspace_name(display_name, address.value))
        except WorkspaceError as exc:
            raise ValidationError(str(exc)) from exc

        # The platform id is a minted UUIDv7 (DD-02); the Firebase UID is kept
        # as a separate unique lookup key (01 §2.1) — it is not a UUID.
        now = utc_now()
        workspace_id = new_uuid7()
        user_id = new_uuid7()
        ctx = ExecutionContext(
            workspace_id=workspace_id,
            user_id=user_id,
            correlation_id=correlation_id,
            roles=frozenset({"owner"}),
        )
        workspace = Workspace(
            id=workspace_id,
            owner_user_id=user_id,
            name=name,
            status=WorkspaceStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=1,
        )
        user = User(
            id=user_id,
            workspace_id=workspace_id,
            firebase_uid=firebase_uid,
            email=address,
            display_name=(display_name or "").strip() or address.value,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=1,
        )
        await self._workspaces.add(ctx, workspace)
        try:
            await self._users.add(ctx, user)
        except ConflictError:
            # R5 (status-doc §5-7 / compliance N3): JIT provisioning is two
            # separate transactions (no cross-repository unit of work yet),
            # so a concurrent first-login for the SAME firebase_uid can
            # commit its own `users.add` between this call's existence-check
            # above and this `add` -- the loser only discovers the race here,
            # as a uniqueness conflict on `firebase_uid`. Re-reading by the
            # natural key recovers the winner's row, and this degrades to an
            # ordinary idempotent re-login instead of surfacing a 409. The
            # `workspace` row just added above is an ACCEPTED orphan for v1
            # (no owner user ever references it, INV-W1 is not violated);
            # full atomicity arrives with a request-scoped unit of work.
            existing_after_race = await self._users.get_by_firebase_uid(firebase_uid)
            if existing_after_race is not None:
                return ProvisionOutcome(user=existing_after_race, created=False, events=())
            raise
        return ProvisionOutcome(
            user=user,
            created=True,
            events=(
                WorkspaceCreated(workspace_id, user_id, now),
                UserProvisioned(user_id, workspace_id, address.value, now),
            ),
        )

    async def provision_on_login(
        self,
        *,
        firebase_uid: str,
        email: str,
        display_name: str | None,
        correlation_id: Uuid,
    ) -> ProvisionedUser:
        """The ``UserProvisioning`` inbound port's implementation (6.4).

        A narrowing of ``execute`` for the one caller outside this module — the
        API layer's authenticator — which needs the tenant scope, the platform
        id, whether the account may act, and whether this login was the first
        one, and has no business with the rest of the ``User`` aggregate.

        The domain events are dropped here, exactly as ``execute``'s existing
        callers drop them: ``workspace`` publishes nothing to a stream in v1
        (04 §4 lists eight global events; none is this module's), so the tuple
        has no consumer to reach.
        """
        outcome = await self.execute(
            firebase_uid=firebase_uid,
            email=email,
            correlation_id=correlation_id,
            display_name=display_name,
        )
        return ProvisionedUser(
            workspace_id=outcome.user.workspace_id,
            user_id=outcome.user.id,
            active=outcome.user.status is UserStatus.ACTIVE,
            created=outcome.created,
        )


class GetWorkspace:
    """Read one workspace back.

    In practice the only workspace a caller can name is its own: ``ctx``
    scopes the read (RLS, DD-04) and ``GET /api/v1/workspace`` has no id in
    its path, so the router passes ``ctx.workspace_id``. The id is still an
    explicit argument rather than being pulled off ``ctx`` here, mirroring
    ``RenameWorkspace``/``ArchiveWorkspace`` — a use-case in this module
    names the aggregate it acts on, and a foreign id is a 404 by the same
    tenant scoping either way.
    """

    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self._workspaces = workspaces

    async def execute(self, ctx: ExecutionContext, workspace_id: Uuid) -> Workspace:
        workspace = await self._workspaces.get(ctx, workspace_id)
        if workspace is None:
            raise NotFoundError("workspace not found")
        return workspace


class RenameWorkspace:
    """Rename a workspace (an owner/admin action; authorization is the caller's)."""

    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self._workspaces = workspaces

    async def execute(self, ctx: ExecutionContext, workspace_id: Uuid, new_name: str) -> Workspace:
        try:
            name = WorkspaceName(new_name)
        except WorkspaceError as exc:
            raise ValidationError(str(exc)) from exc
        workspace = await self._workspaces.get(ctx, workspace_id)
        if workspace is None:
            raise NotFoundError("workspace not found")
        try:
            workspace.rename(name, utc_now())
        except WorkspaceArchivedError as exc:
            raise ConflictError(str(exc)) from exc
        await self._workspaces.save(ctx, workspace)
        return workspace


class ArchiveWorkspace:
    """Archive a workspace. Idempotent — re-archiving emits no new event."""

    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self._workspaces = workspaces

    async def execute(
        self, ctx: ExecutionContext, workspace_id: Uuid
    ) -> tuple[Workspace, tuple[WorkspaceEvent, ...]]:
        workspace = await self._workspaces.get(ctx, workspace_id)
        if workspace is None:
            raise NotFoundError("workspace not found")
        already_archived = workspace.status is WorkspaceStatus.ARCHIVED
        workspace.archive(utc_now())
        await self._workspaces.save(ctx, workspace)
        events: tuple[WorkspaceEvent, ...] = (
            () if already_archived else (WorkspaceArchived(workspace_id, workspace.updated_at),)
        )
        return workspace, events


class RecordUserPresence:
    """Store a server-timestamped heartbeat for the authenticated caller."""

    def __init__(self, presence: UserPresenceStore) -> None:
        self._presence = presence

    async def execute(self, ctx: ExecutionContext) -> datetime:
        if ctx.user_id is None:
            raise AppError("presence requires an authenticated user", code="common.internal")
        return await self._presence.record_heartbeat(ctx, user_id=ctx.user_id, seen_at=utc_now())


@dataclass(frozen=True, slots=True)
class WorkspaceUseCases:
    """The module's API-facing bundle — what ``/api/v1/workspace`` delegates
    to (the ``FileUseCases``/``MediaUseCases`` precedent, §3.60).

    Exactly the two use-cases 03 §1 gives the resource routes (GET · PATCH).
    ``ProvisionUserOnFirstLogin`` and ``ArchiveWorkspace`` are deliberately
    absent: provisioning is the AUTHENTICATION path's (6.4's Firebase seam
    calls it before any route runs, never a client), and archiving has no
    route in v1 — bundling either would advertise a reachable face that does
    not exist.
    """

    get: GetWorkspace
    rename: RenameWorkspace


def _default_workspace_name(display_name: str | None, email: str) -> str:
    """Derive a sensible default workspace name for a freshly provisioned owner."""
    base = (display_name or "").strip() or email.split("@", 1)[0]
    return base[:_MAX_NAME_LEN]
