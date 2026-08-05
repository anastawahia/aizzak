"""Session actions for the authenticated caller."""

from __future__ import annotations

from math import ceil
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.v1.dependencies import Principal, current_principal
from app.framework.auth.revocation import MAX_REVOCATION_TTL_S, SessionRevocationList

router = APIRouter(prefix="/me", tags=["session"])


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
