"""Session actions for the authenticated caller."""

from __future__ import annotations

from math import ceil
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.v1.dependencies import Context, Principal, Services, current_principal
from app.api.v1.dto.me import MeContextOut, MeHeartbeatOut, MeUserOut, MeWorkspaceOut
from app.framework.auth.revocation import MAX_REVOCATION_TTL_S, SessionRevocationList
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission

router = APIRouter(prefix="/me", tags=["session"])


@router.get("/context")
async def get_context(
    principal: Annotated[Principal, Depends(current_principal)],
    services: Services,
) -> MeContextOut:
    """Return the authenticated caller's tenant and resolved RBAC context.

    Roles come from the same fresh authentication result every protected
    request uses. Permissions are derived through the authorization port, so
    this API never duplicates the static role catalog or asks a browser to
    infer access from a legacy ``is_admin`` flag.
    """
    permissions = sorted(
        permission.value
        for permission in Permission
        if services.authorization.is_allowed(principal.roles, permission.value)
    )
    return MeContextOut(
        user=MeUserOut(id=principal.user_id),
        workspace=MeWorkspaceOut(id=principal.workspace_id),
        roles=sorted(principal.roles),
        permissions=permissions,
    )


@router.post("/heartbeat")
async def heartbeat(services: Services, ctx: Context) -> MeHeartbeatOut:
    """Record a server-observed activity signal for the authenticated caller.

    The browser emits this once each minute while it has an active verified
    session.  The endpoint accepts no user or workspace id, so it cannot be
    used to change another account's presence.
    """
    if services.presence is None:
        raise AppError("user presence is not configured", code="common.internal")
    return MeHeartbeatOut(last_seen_at=await services.presence.execute(ctx))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> Response:
    """Invalidate the caller's Firebase subject until this token expires."""
    revocations: SessionRevocationList | None = getattr(request.app.state, "revocations", None)
    if revocations is None:
        # Production wiring always provides this. Refusing logout is safer than
        # presenting a successful response while a stolen bearer remains live.
        raise RuntimeError("session revocation is not configured")

    expires_at = principal.token_expires_at
    if expires_at is None:
        ttl_s = MAX_REVOCATION_TTL_S
    else:
        ttl_s = max(1, min(MAX_REVOCATION_TTL_S, ceil(expires_at - time()) + 60))
    await revocations.revoke(principal.firebase_uid, ttl_s=ttl_s)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
