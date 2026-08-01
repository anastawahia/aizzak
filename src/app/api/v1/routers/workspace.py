"""The Workspace router — ``/api/v1/workspace`` (03-api-spec §1 · FR-100) —
Phase 6.1-و-1.

Two routes over ``WorkspaceUseCases``: read the caller's own workspace, and
rename it.

**No id in the path, and that is the point.** 03 §0: ``workspace_id`` is
derived from the identity, never passed by the client. Both routes therefore
act on ``ctx.workspace_id`` — the value the authenticated principal put
there, which is also what drives RLS (DD-04). There is no route shape here
that could ever name another tenant's workspace, so tenant isolation on this
resource is structural rather than a check that could be forgotten.

**A single resource, returned bare** (API-04: only collections wear the
envelope) — this resource has no collection face at all: a caller belongs to
exactly one workspace.

**Rename's failure faces are the use-case's:** a blank/over-long name is
refused twice (DTO bounds at the edge, ``WorkspaceName`` in the domain — 422
either way), and renaming an ARCHIVED workspace is a ``ConflictError``/409,
which is the one case where a well-formed request on one's own workspace
still fails. A missing row is a 404 that in practice means a principal
outlived its workspace.

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0); the owner/admin RBAC guard PATCH deserves is 6.4's, like every other
router's.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.workspace import WorkspaceOut, WorkspacePatchIn
from app.modules.access.domain.value_objects import Permission
from app.modules.workspace.domain.entities import Workspace

# Belt and braces, knowingly (the §3.56 precedent): both routes build `ctx`.
router = APIRouter(
    prefix="/workspace", tags=["workspace"], dependencies=[Depends(current_principal)]
)


def _to_workspace_out(workspace: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name.value,
        status=workspace.status.value,
        created_at=workspace.created_at,
    )


@router.get("", dependencies=[Depends(require(Permission.WORKSPACE_READ))])
async def get_workspace(services: Services, ctx: Context) -> WorkspaceOut:
    """The caller's own workspace."""
    workspace = await services.workspace.get.execute(ctx, ctx.workspace_id)
    return _to_workspace_out(workspace)


@router.patch("", dependencies=[Depends(require(Permission.WORKSPACE_MANAGE))])
async def patch_workspace(body: WorkspacePatchIn, services: Services, ctx: Context) -> WorkspaceOut:
    """Rename it. The response is the SAVED aggregate the use-case returns —
    ``updated_at``/``version`` have already moved — never an echo of the
    request body."""
    workspace = await services.workspace.rename.execute(ctx, ctx.workspace_id, body.name)
    return _to_workspace_out(workspace)
