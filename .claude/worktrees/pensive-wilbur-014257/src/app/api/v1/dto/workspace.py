"""Workspace DTOs — the wire shapes of ``/api/v1/workspace`` (03-api-spec §2,
Phase 6.1-و-1).

Verbatim from the spec's Pydantic sketch. ``WorkspacePatchIn`` carries the
spec's own bounds (``min_length=1, max_length=80``) even though the domain's
``WorkspaceName`` enforces its own: the DTO's job is to refuse a nonsense
payload at the edge with ``common.validation_error``/422, and the domain's is
to hold the invariant no matter who calls it. Two guards, one number — the
spec's — and neither trusts the other.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WorkspaceOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime


class WorkspacePatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
