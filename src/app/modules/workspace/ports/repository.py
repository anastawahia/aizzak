"""Workspace persistence ports (02-port-contracts §2).

Outbound repository contracts for the two aggregates. Every tenant-scoped method
takes ``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter (DD-04).
``save`` uses an optimistic lock on ``version`` — a stale write surfaces as a
conflict at the adapter.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.workspace.domain.entities import User, Workspace


class WorkspaceRepository(Protocol):
    """Tenant-root persistence. Reads are implicitly workspace-scoped via ``ctx``."""

    async def get(self, ctx: ExecutionContext, workspace_id: Uuid) -> Workspace | None: ...

    async def add(self, ctx: ExecutionContext, workspace: Workspace) -> None: ...

    async def save(self, ctx: ExecutionContext, workspace: Workspace) -> None: ...


class UserRepository(Protocol):
    """User identity persistence.

    ``add`` is tenant-scoped via ``ctx``. ``get_by_firebase_uid`` is the single
    *unscoped* read: it resolves a user by the external Firebase UID (a unique
    natural key, not the platform ``id``) before any workspace/RLS scope exists —
    used only by JIT provisioning / auth to map an identity to its workspace and
    platform UUIDv7. The adapter backs it with a platform-level policy, never
    with a tenant filter.
    """

    async def get_by_firebase_uid(self, firebase_uid: str) -> User | None: ...

    async def add(self, ctx: ExecutionContext, user: User) -> None: ...
