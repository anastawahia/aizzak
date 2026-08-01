"""Memory persistence port (02-port-contracts §2).

Outbound repository for the ``MemoryItem`` aggregate. Every method takes
``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter (DD-04).
The table has no ``version`` column (01-data-model §2.5): ``MemoryItem`` is
append + soft-delete only, so ``save`` is a plain update-by-id with no
optimistic lock.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.memory.domain.entities import MemoryItem


class MemoryRepository(Protocol):
    """Tenant-scoped persistence for memory items (append + soft-delete)."""

    async def add(self, ctx: ExecutionContext, item: MemoryItem) -> None: ...

    async def get(self, ctx: ExecutionContext, memory_id: Uuid) -> MemoryItem | None: ...

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[MemoryItem]: ...

    async def save(self, ctx: ExecutionContext, item: MemoryItem) -> None:
        """Update the row by id.

        The table has no ``version`` column (01-data-model §2.5), so this is a
        plain update-by-id — no optimistic lock. Used to persist the
        ``forget`` and ``attach_vector`` mutations.
        """
