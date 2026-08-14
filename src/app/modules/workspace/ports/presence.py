"""Narrow port for recording a caller's server-observed presence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class UserPresenceStore(Protocol):
    """Persist a heartbeat only for the principal in its tenant context."""

    async def record_heartbeat(
        self, ctx: ExecutionContext, *, user_id: Uuid, seen_at: datetime
    ) -> datetime: ...
