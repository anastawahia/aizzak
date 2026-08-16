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

    async def lock(self, ctx: ExecutionContext, space_id: Uuid) -> bool:
        """Take a row-level write lock on ONE ACTIVE space; ``True`` if there
        was one to lock (``docs/spaces-backend-plan.md`` §3.3, step 5).

        This is the quota's serialisation anchor and nothing else: it returns
        no aggregate, because the caller that needs it — a cross-module
        coordination service — must not be handed a ``Space`` it has no
        business reading. ``get`` above answers "what is this space"; this
        answers "hold it still while I decide".

        **It is only a lock inside a unit of work.** A row lock lives until
        its transaction ends, and every method on this port opens its own
        transaction unless an enclosing ``UnitOfWork.begin(ctx)`` is active —
        so calling this outside one takes a lock and releases it one statement
        later, silently. The single caller (``framework/di/space_quota.py``)
        opens the unit of work first; there is no way for this port to check.

        Unlike ``get``, a soft-deleted space is ``False``, not a row: locking
        one would serialise writes into a space that must not receive any.
        """
        ...

    async def add(self, ctx: ExecutionContext, space: Space) -> None: ...

    async def save(self, ctx: ExecutionContext, space: Space) -> None: ...

    async def list(self, ctx: ExecutionContext, *, limit: int, cursor: str | None) -> Page[Space]:
        """Active spaces only, newest first, keyset-paginated on ``id``
        (framework ``pagination``, 6.3-ب)."""
        ...
