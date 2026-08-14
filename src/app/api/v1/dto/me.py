"""Authenticated caller context DTOs.

The browser needs a server-authoritative answer to one narrow question before
it can expose administrative navigation: who is the caller in this tenant and
which permissions were resolved for this request.  The response deliberately
contains identifiers and RBAC facts only; it does not turn ``/me`` into a user
profile or an administrative user lookup.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MeUserOut(BaseModel):
    id: str


class MeWorkspaceOut(BaseModel):
    id: str


class MeContextOut(BaseModel):
    user: MeUserOut
    workspace: MeWorkspaceOut
    roles: list[str]
    permissions: list[str]


class MeHeartbeatOut(BaseModel):
    last_seen_at: datetime
