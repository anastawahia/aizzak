"""Spaces use-cases (06-domain-models §6; ``docs/spaces-backend-plan.md`` §3.1).

Thin application services over an injected ``SpaceRepository``, the ``files``
shape at a fifth of the size. They own identity and time (framework
``new_uuid7`` / ``utc_now``) and translate the domain's two error kinds into
the shared framework hierarchy at this boundary.

**No unit-of-work wrappers here, and none are owed.** ``CompleteUploadService``
and ``SoftDeleteFileService`` exist to append events to the Outbox inside one
transaction; spaces publishes nothing to the Outbox (``domain/events.py`` says
why), and every write below is a single statement. Wrapping them would ship
the ceremony of atomicity around something with nothing to be atomic with —
the argument ``RenameFile`` already made for itself.

``DeleteSpace`` marks the row and stops. The seven-step cascade over
``knowledge``/Qdrant/``files``/MinIO/``conversations`` (§3.6) is a
composition-root service, because it crosses five modules and no module may
know that the other four exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.errors import SpaceError, SpaceStateError
from app.modules.spaces.domain.events import SpaceDeleted, SpaceEvent
from app.modules.spaces.domain.value_objects import SpaceName
from app.modules.spaces.ports.inbound import SpaceView
from app.modules.spaces.ports.repository import SpaceRepository


class CreateSpace:
    """Mint a space in the caller's workspace.

    Uniqueness is NOT checked here. ``ux_spaces_ws_name`` (§3.2) refuses a
    duplicate in the same statement that would have written it, and the
    adapter turns that into ``spaces.duplicate_name`` — a read-then-insert
    would give the same answer one round trip earlier and the wrong answer
    whenever two requests race, which is the only time the check matters.
    """

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def execute(self, ctx: ExecutionContext, *, name: str) -> Space:
        space_name = _parse_name(name)
        now = utc_now()
        space = Space(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            name=space_name,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            version=1,
        )
        await self._spaces.add(ctx, space)
        return space


class GetSpace:
    """Read one space. A soft-deleted space answers 404 exactly like a missing
    one — the §3.55 read precedent: a read's only truthful answer for a deleted
    resource is "gone", and the 409-shaped answer belongs to writes."""

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> Space:
        space = await self._spaces.get(ctx, space_id)
        if space is None or not space.is_active:
            raise NotFoundError("space not found")
        return space


class ListSpaces:
    """List active spaces in the workspace via keyset pagination on ``id``.

    The counts and ``bytes_used`` that §3.7 puts on the wire are NOT here:
    they are sums over ``files`` and ``conversations``, and a module that
    could compute them would be a module that can read another's tables. The
    router composes them from the other modules' own faces.
    """

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def execute(
        self, ctx: ExecutionContext, *, limit: int = 50, cursor: str | None = None
    ) -> Page[Space]:
        return await self._spaces.list(ctx, limit=limit, cursor=cursor)


class RenameSpace:
    """Rename a space. The no-op rule is the aggregate's (``Space.rename``);
    this service owns the boundary — the raw string becomes a ``SpaceName``
    here, and a write is skipped when nothing changed, because ``save`` bumps
    ``version``/``updated_at`` unconditionally (``RenameFile``'s reasoning,
    verbatim in force).

    The caller still gets the space back when the name was already that: "the
    name is already that" is a successful outcome of "make the name that".
    """

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def execute(self, ctx: ExecutionContext, space_id: Uuid, *, name: str) -> Space:
        space = await self._spaces.get(ctx, space_id)
        if space is None:
            raise NotFoundError("space not found")
        new_name = _parse_name(name)
        before = space.name.value
        try:
            space.rename(new_name, utc_now())
        except SpaceStateError as exc:
            raise ConflictError(str(exc)) from exc
        if space.name.value != before:
            await self._spaces.save(ctx, space)
        return space


class DeleteSpace:
    """Soft-delete a space — step 1 of the cascade, and nothing more.

    Idempotent: re-deleting emits no new event, the ``SoftDeleteFile`` shape.
    The event is returned rather than published (``domain/events.py``): its
    consumer is the composition-root ``DeleteSpaceService`` that runs the
    remaining six steps, not the Outbox.

    ``save`` is called even when the space was already deleted, and that is
    the ``files`` precedent rather than an oversight: the write is a provable
    no-op on the columns that matter, and branching around it would trade a
    round trip for a second code path through the optimistic lock.
    """

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def execute(
        self, ctx: ExecutionContext, space_id: Uuid
    ) -> tuple[Space, tuple[SpaceEvent, ...]]:
        space = await self._spaces.get(ctx, space_id)
        if space is None:
            raise NotFoundError("space not found")
        already_deleted = space.deleted_at is not None
        space.soft_delete(utc_now())
        await self._spaces.save(ctx, space)
        events: tuple[SpaceEvent, ...] = (
            () if already_deleted else (SpaceDeleted(space.id, ctx.workspace_id, space.updated_at),)
        )
        return space, events


class SpacesQueryService:
    """Implements the ``SpacesQuery`` inbound port (02 §2) over the repository.

    The one face other modules see. It answers ``None`` for a space that is
    missing OR deleted, which is what makes it safe to expose: neither answer
    reveals anything about a space the caller may not file into.
    """

    def __init__(self, spaces: SpaceRepository) -> None:
        self._spaces = spaces

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> SpaceView | None:
        space = await self._spaces.get(ctx, space_id)
        if space is None or not space.is_active:
            return None
        return SpaceView(space_id=space.id, name=space.name.value)


@dataclass(frozen=True, slots=True)
class SpaceUseCases:
    """The bundle the API layer consumes — the ``FileUseCases`` precedent, so
    ``ApiServices`` grows ONE field when the router lands (step 12)."""

    create: CreateSpace
    get: GetSpace
    list: ListSpaces
    rename: RenameSpace
    delete: DeleteSpace


def _parse_name(name: str) -> SpaceName:
    """The one boundary translation this module has: a raw request string
    becomes a validated ``SpaceName``, or a 422 with the domain's reason."""
    try:
        return SpaceName(name)
    except SpaceError as exc:
        raise ValidationError(str(exc)) from exc
