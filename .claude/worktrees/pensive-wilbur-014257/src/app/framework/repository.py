"""Generic repository contract (02-port-contracts §2).

The driven persistence port. Every method takes ``ExecutionContext`` first so
the adapter can apply the RLS guard (``SET LOCAL app.workspace_id``) and the
belt-and-braces ``WHERE workspace_id = :ws`` filter (DD-04). Modules define
their own concrete repository ports; this ``Protocol`` documents the shared
shape and is not an inheritance requirement.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid

TAggregate = TypeVar("TAggregate")


class Repository(Protocol[TAggregate]):
    """Structural contract for an aggregate-scoped, tenant-isolated repository."""

    async def get(self, ctx: ExecutionContext, id: Uuid) -> TAggregate | None: ...

    async def add(self, ctx: ExecutionContext, entity: TAggregate) -> None: ...

    async def save(self, ctx: ExecutionContext, entity: TAggregate) -> None: ...

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
    ) -> Page[TAggregate]: ...
