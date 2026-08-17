"""The cascade that empties a deleted space — ``docs/spaces-backend-plan.md``
step 11, §3.6.

One rule: **deleting a space destroys everything it owned**, across five
modules and two external stores, and it survives being interrupted. Decision 2
of the plan is "حذف متسلسل", and this file is the only place in the platform
that knows the whole list.

**Why this file is here and not in a module.** The deletion crosses ``spaces``,
``knowledge``, ``files`` and ``conversations``, and no module may import
another (import-linter contract 4). ``app/ops/purge.py`` already does exactly
this shape for a whole workspace, and §3.6 puts this one in "خدمة تنسيق عند
جذر التركيب" for the same reason. What keeps it honest is the
``space_quota.py`` discipline, applied a second time: this module imports NO
module at all — the collaborators are structural ``Protocol``\\ s declared
right here and bound by the Composition Root, where mypy checks the fit at the
call. So the framework-kernel contract (7) needs no new exception, and it must
not gain one.

**There is no unit of work around the whole cascade, deliberately.** Two
external stores sit inside it — Qdrant between steps 2 and 4, MinIO between 5
and 6 — and network I/O may not run under an open database transaction (R2).
Each step is therefore its own transaction, which is exactly why every step
below is idempotent: a point already deleted is a no-op, a MinIO object
already gone answers 204, and a ``DELETE`` matching no rows succeeds. An
interrupted cascade leaves a space that is marked deleted and partly empty,
and the fix is to run it again.

**The marking comes first, and it is the only step that can refuse.** §3.6
orders it first so a user never sees a half-emptied space still listed as
live; and because it is the step that reads the space, an unknown or
already-purged id is a ``NotFoundError`` here rather than six silent no-ops
reported as a successful deletion. Re-deleting an already-deleted space is NOT
refused — that is the resume path, and ``DeleteSpace`` is idempotent by design.

**Nothing is published.** ``SpaceDeleted`` has no row in 04-event-catalog and
no ``event_mapping`` (``spaces/domain/events.py``): the marking step hands the
record to this service, and this service IS its consumer. Hence no
``EventOutbox`` here, and no unit of work to pair one with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class SpaceMarker(Protocol):
    """``DeleteSpace.execute`` — soft-delete the space row (step 1).

    The result is deliberately ``object``: it is ``(Space, events)``, and both
    halves are ``app.modules`` types this file may not name. Nor does it need
    them — the aggregate is the spaces module's, and the event's only consumer
    is this service, which learns everything it needs from the call returning
    at all. What matters here is the RAISE: an id that names no space never
    reaches step 2.
    """

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> object: ...


class SpaceContentPurge(Protocol):
    """One module's "destroy everything you own in this space", returning how
    many of its own top-level rows went.

    ONE protocol for all three consumers rather than three identical ones: the
    shape is the contract, and three names for it would be documentation
    pretending to be typing. The cost is that the three are interchangeable to
    mypy, which is why ``DeleteSpaceService`` takes them as KEYWORDS — a
    positional swap would type-check perfectly and delete the right rows in the
    wrong order.

    Each implementation owns the internals the cascade must not know: which
    tables, in which FK order, and which external store to empty first.
    """

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int: ...


@dataclass(frozen=True, slots=True)
class SpaceDeletion:
    """What one cascade destroyed — the ``PurgeResult`` shape (``ops/purge``),
    at the scale of one space.

    Counts, not booleans, and they are the observable behaviour of an operation
    whose whole job is to leave nothing behind: a cascade that silently deleted
    zero of a space's three thousand documents looks identical to a correct one
    from the outside. Each number is that module's own top-level row count —
    documents, files, threads — never the total of every child table it swept,
    which would be an unstable number nobody could predicate on.
    """

    space_id: Uuid
    documents: int
    files: int
    conversations: int


class DeleteSpaceService:
    """The seven steps of §3.6, in the order §3.6 fixes.

    The order is the design, not an implementation detail:

    1. **mark** — so the space stops being listed before anything is destroyed.
    2-4. **knowledge** — its points before its rows (that module's own rule).
    5-6. **files** — its objects before its rows.
    7. **conversations** — last, because its threads are what a user reads the
       rest through; a thread whose files and corpus are gone is a thread that
       answers "not found" to everything, and it is the least useful thing to
       be left holding if the cascade dies mid-way.
    """

    def __init__(
        self,
        mark: SpaceMarker,
        *,
        knowledge: SpaceContentPurge,
        files: SpaceContentPurge,
        conversations: SpaceContentPurge,
    ) -> None:
        self._mark = mark
        self._knowledge = knowledge
        self._files = files
        self._conversations = conversations

    async def delete(self, ctx: ExecutionContext, space_id: Uuid) -> SpaceDeletion:
        # Step 1. Raises `NotFoundError` for an id this workspace does not
        # own -- and that is the whole existence check for the six steps
        # below, none of which can tell an empty space from an absent one.
        await self._mark.execute(ctx, space_id)
        documents = await self._knowledge.execute(ctx, space_id)
        files = await self._files.execute(ctx, space_id)
        conversations = await self._conversations.execute(ctx, space_id)
        return SpaceDeletion(
            space_id=space_id,
            documents=documents,
            files=files,
            conversations=conversations,
        )
