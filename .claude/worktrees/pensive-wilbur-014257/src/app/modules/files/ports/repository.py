"""Files persistence port (02-port-contracts §2).

Outbound repository contract for the ``File`` aggregate. Every method takes
``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter (DD-04).
``save`` uses an optimistic lock on ``version`` — a stale write surfaces as a
conflict at the adapter. ``count`` backs the per-workspace file cap
(07-nfr-slo §4) and counts only active (non-deleted) files.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.files.domain.entities import File


class FileRepository(Protocol):
    """Tenant-scoped persistence for the ``File`` aggregate."""

    async def get(self, ctx: ExecutionContext, file_id: Uuid) -> File | None: ...

    async def add(self, ctx: ExecutionContext, file: File) -> None: ...

    async def save(self, ctx: ExecutionContext, file: File) -> None: ...

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
    ) -> Page[File]: ...

    async def count(self, ctx: ExecutionContext) -> int: ...
