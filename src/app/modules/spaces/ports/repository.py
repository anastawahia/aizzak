"""Spaces persistence port (02-port-contracts §2).

Outbound repository contract for the ``Space`` aggregate, the ``files`` shape
applied to a smaller aggregate. Every method takes ``ExecutionContext`` first
so the SQL adapter can apply the RLS guard (``SET LOCAL app.workspace_id``) and
the ``WHERE workspace_id`` filter (DD-04). ``save`` uses an optimistic lock on
``version`` — a stale write surfaces as a conflict at the adapter.

**No ``find_by_name``, and none is missing.** Name uniqueness is the partial
index ``ux_spaces_ws_name`` (§3.2), enforced in the same statement as the
write; a lookup-then-insert would answer the same question one round trip
earlier and be wrong under concurrency exactly when it matters. The adapter
translates the resulting ``23505`` into ``spaces.duplicate_name``.

**No ``count``.** There is no cap on the number of spaces — the quota this
plan introduces is 1 GiB of BYTES per space (§3.3), enforced against
``files``, not here.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.spaces.domain.entities import Space


class SpaceRepository(Protocol):
    """Tenant-scoped persistence for the ``Space`` aggregate."""

    async def get(self, ctx: ExecutionContext, space_id: Uuid) -> Space | None:
        """One space by id, INCLUDING a soft-deleted one.

        The ``files`` rule: a caller holding an id may still re-``get`` it
        after a soft-delete, which is what makes deletion idempotent. Callers
        that must not see a deleted space (reads, and the cross-module
        existence check) filter on ``deleted_at`` themselves.
        """
        ...

    async def add(self, ctx: ExecutionContext, space: Space) -> None: ...

    async def save(self, ctx: ExecutionContext, space: Space) -> None: ...

    async def list(self, ctx: ExecutionContext, *, limit: int, cursor: str | None) -> Page[Space]:
        """Active spaces only, newest first, keyset-paginated on ``id``
        (framework ``pagination``, 6.3-ب)."""
        ...
