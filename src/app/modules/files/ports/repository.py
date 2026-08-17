"""Files persistence port (02-port-contracts §2).

Outbound repository contract for the ``File`` aggregate. Every method takes
``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter (DD-04).
``save`` uses an optimistic lock on ``version`` — a stale write surfaces as a
conflict at the adapter. ``count`` backs the per-workspace file cap
(07-nfr-slo §4) and counts only active (non-deleted) files.

``add`` persists the aggregate's ``space_id`` and ``save`` deliberately does
not: a file does not move between spaces (spaces plan, decision 3), and
leaving the column out of the UPDATE is what makes that true of the database
and not only of the type — the ``content_type``/``storage_key`` argument.
"""

from __future__ import annotations

from collections.abc import Sequence
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
        self, ctx: ExecutionContext, *, space_id: Uuid | None, limit: int, cursor: str | None
    ) -> Page[File]:
        """Active files, newest first, keyset-paginated on ``id``.

        ``space_id`` narrows the page to one space's files; ``None`` returns
        the workspace's, which is what every caller asked for before the
        spaces plan and what the router still asks for until ``?space_id=``
        becomes mandatory on the wire (§3.7, step 12). It is a REQUIRED
        keyword with no default so that "all spaces" is a decision written at
        the call site, never one a caller falls into by omission.
        """
        ...

    async def count(self, ctx: ExecutionContext) -> int: ...

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """Total ``size_bytes`` of the ACTIVE files owned by one space — the
        left-hand side of the 1 GiB quota (``docs/spaces-backend-plan.md``
        §3.3, step 5). ``0`` for an unknown or empty space.

        ``space_id`` is an OPAQUE identifier here: this module does not know
        what a space is, only that its rows carry one and can be totalled by
        it. The decision to spend the room belongs to the coordination service
        that holds the space's row lock (``framework/di/space_quota.py``) —
        this port answers "how much is stored", never "may I store more".

        Soft-deleted files are excluded, so deleting a file frees its bytes
        immediately, before the purge sweep ever runs. That is the intended
        meaning of the quota (a user who deletes must get their space back)
        and it is why this cannot reuse ``count``'s shape and be an argument
        away from it.
        """
        ...

    async def storage_keys_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> Sequence[str]:
        """Every stored object key this space's files name — step 5 of the
        cascade (``docs/spaces-backend-plan.md`` §3.6, step 11).

        **Soft-deleted files are INCLUDED**, unlike ``bytes_in_space`` right
        above, and the difference is the whole reason this is a second method
        rather than an argument on the first. A deleted file has already given
        its bytes back to the quota, but its OBJECT is still in MinIO — that
        is what "soft" means — so a purge that skipped it would leave the
        space's storage behind with no row left to name it, unreachable by
        anything but ``app.ops.purge`` and only when the whole workspace dies.

        Read BEFORE ``purge_space``, and it must be: afterwards there is no row
        left to say which objects were the space's.
        """
        ...

    async def purge_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """HARD-delete every file row of one space; returns how many went
        (step 6 of the cascade, §3.6).

        The module's only hard delete, and it is deliberate: a space that was
        deleted must stop counting against nothing, stop appearing in every
        listing, and above all stop holding rows that point at objects this
        cascade has already destroyed. A soft delete would leave exactly that —
        a ``ready`` file whose bytes are gone, which is worse than either
        state on its own.

        Soft-deleted rows go too, for the reason ``storage_keys_in_space``
        keeps them: their objects have just been deleted, so leaving the rows
        would be leaving a promise the storage can no longer keep.

        Deleting nothing is a no-op, not an error — a re-run of a cascade that
        died half-way is the case this exists to survive (§3.6).
        """
        ...
