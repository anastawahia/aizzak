"""The per-space storage quota — ``docs/spaces-backend-plan.md`` step 5, §3.3.

One rule: **a space never holds more than ``Limits.max_space_bytes``**, and it
holds under concurrency, which is the only interesting part. Two 600 MiB
uploads registered at the same instant read the same total, both find room,
and the space ends up at 1.2 GiB — so the check is not a read followed by a
write, it is a read *inside a transaction that already holds the space's row
lock* (plan risk 2).

**Why this file is here and not in a module.** The lock is taken on
``spaces.spaces`` and the sum is read from ``files.files``, and those two
modules do not know each other (import-linter contract 4). Plan §3.3 puts the
sequence in "خدمة تنسيق عند جذر التركيب" for that reason. What keeps it honest
is that it imports NO module at all: the three collaborators are structural
``Protocol``\\ s declared right here, bound to ``SqlSpaceRepository``,
``SqlFileRepository`` and ``FileTransferService`` by the Composition Root,
where mypy checks the binding at the call — the ``conversations/ports/
files.py`` pattern applied outward instead of sideways. So the framework-kernel
contract (7) needs no new exception for this module, and it must not gain one.

**Two ceilings, two locks, and the second one arrived late (capacity-plan
2.7).** The space's byte quota is held still by the space's own row; the
WORKSPACE's file cap (``Limits.max_files_per_workspace``, enforced inside
``RegisterUpload`` one call below) is not, and a row lock on ONE space cannot
serialise two registrations into two DIFFERENT spaces of the same workspace.
Measured on the live stack with one slot left under the cap: 100 concurrent
registrations into 100 distinct spaces produced **30** files against a cap of
10 -- and 30 was the size of the connection pool, not of anything anyone
configured (``pool_size=5`` produced 5; ``pool_size=30`` produced 29). So this
service now takes a workspace-scoped ``QuotaLock`` FIRST, in the same unit of
work, and the count one call below is finally a count nothing can invalidate
before it is acted on. The lock is taken here rather than inside
``RegisterUpload`` because the unit of work is here: a lock taken in a
transaction of its own is released before the count it protects (the port's
adapter refuses to take one).

**The ceiling is checked, not reserved.** Nothing is written to say "600 MiB
of this space is spoken for"; the serialisation IS the reservation, and it
lasts exactly as long as the unit of work. That is sound because the row this
transaction goes on to insert is itself the record of the spend — which it
became at plan step 6, when ``files.files`` started storing the space this
service hands the registrar. Until then the sum read a column every registered
file left NULL: a real ceiling against a structurally-zero total (§3.143 said
so rather than letting the gap be discovered later).

**The lock is held across ``registrar.register``**, which presigns a PUT. In
the only adapter that exists (MinIO) presigning is local HMAC work, but
``StorageProvider`` does not promise that, so this is a genuine — bounded —
widening of the window: two registrations into the SAME space serialise for
the length of one presign call. The alternative is to release the lock before
the insert, which is the exact race the lock exists to prevent.

⚠️ **And 2.7 widened WHO waits, not how long.** The window is unchanged — one
registrar call — but the contention set is now the WORKSPACE rather than the
space, because the file cap is a workspace ceiling. Two spaces of one tenant
serialise where they used to overlap; two tenants still never wait for each
other. ``test_space_quota_live.py`` asserted the old behaviour and now asserts
this one, deliberately and by name.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError
from app.framework.ports.quota_lock import QuotaLock
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.settings.settings import Limits
from app.framework.types import Uuid

# The ``QuotaLock`` name for ``Limits.max_files_per_workspace``. Declared HERE,
# beside the only transaction that can hold it, and not in
# ``files/application/use_cases.py`` where the ceiling itself lives: this file
# imports no ``app.modules`` at all (see the module docstring), and that rule
# is worth more than co-location. It is a constant rather than a literal
# because the string IS the lock's identity -- two spellings are two locks.
_FILE_COUNT_CEILING = "files.max_files_per_workspace"


class SpaceLock(Protocol):
    """``SpaceRepository.lock`` — hold an active space's row still."""

    async def lock(self, ctx: ExecutionContext, space_id: Uuid) -> bool: ...


class SpaceBytesUsed(Protocol):
    """``FileRepository.bytes_in_space`` — the space's stored volume."""

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> int: ...


class UploadRegistrar[R](Protocol):
    """``FileTransferService.register`` — validate, mint, presign.

    Generic in its RESULT so this file can stay free of ``app.modules``: the
    Composition Root binds ``FileTransferService`` and mypy infers
    ``R = RegisteredUpload`` at that call, so callers keep the concrete type
    without this module ever naming it.
    """

    async def register(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid,
        name: str,
        content_type: str,
        size_bytes: int,
    ) -> R: ...


class SpaceQuotaService[R]:
    """Registration, gated on the space's remaining room (§3.3).

    The whole body is ONE unit of work — ``UnitOfWork.begin`` is what makes
    ``lock`` a lock rather than a statement that takes and drops one, and what
    lets a rejection leave nothing behind.
    """

    def __init__(
        self,
        registrar: UploadRegistrar[R],
        spaces: SpaceLock,
        files: SpaceBytesUsed,
        uow: UnitOfWork,
        limits: Limits,
        lock: QuotaLock,
    ) -> None:
        self._registrar = registrar
        self._spaces = spaces
        self._files = files
        self._uow = uow
        self._limits = limits
        self._lock = lock

    async def register(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid,
        name: str,
        content_type: str,
        size_bytes: int,
    ) -> R:
        async with self._uow.begin(ctx):
            # 2.7 -- BEFORE the space's own row lock, and the order is the
            # ordinary deadlock discipline rather than a preference: every
            # contender for the workspace's file cap must take these two locks
            # in the same order, and this one is the coarser of the two. Taking
            # the space row first would let two registrations into two spaces
            # hold each other's next lock.
            await self._lock.hold(ctx, _FILE_COUNT_CEILING)
            if not await self._spaces.lock(ctx, space_id):
                # Missing, or belonging to another tenant, or soft-deleted —
                # one answer for all three. A caller that cannot write to a
                # space has no right to learn that it exists (the §3.55 read
                # precedent, applied to a write's precondition).
                raise NotFoundError("space not found")

            ceiling = self._limits.max_space_bytes
            used = await self._files.bytes_in_space(ctx, space_id)
            # A size the PER-FILE cap already rejects is not this service's to
            # judge. `RegisterUpload` owns `files.too_large` (413) and answers
            # with the number the client can act on; telling them "the space is
            # full" about an EMPTY space, because they asked to upload more
            # than the space could ever hold, is true and useless. The
            # condition defers rather than duplicating the cap: the registrar
            # below raises it one line later, from the one place that owns it.
            if size_bytes <= self._limits.max_upload_bytes and used + size_bytes > ceiling:
                raise ConflictError(
                    f"space has {max(ceiling - used, 0)} of {ceiling} bytes left, "
                    f"and this upload needs {size_bytes}",
                    code="spaces.quota_exceeded",
                )

            # The space is passed DOWN, not merely checked here: the row this
            # insert writes is the record of the spend, so a registration that
            # landed without its space would be measured by no later sum and
            # the ceiling above would keep comparing against zero. This is the
            # line that turned the quota from measured into binding (step 6).
            return await self._registrar.register(
                ctx,
                space_id=space_id,
                name=name,
                content_type=content_type,
                size_bytes=size_bytes,
            )
