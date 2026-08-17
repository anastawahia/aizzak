"""In-memory spaces wiring shared by the API router tests
(``docs/spaces-backend-plan.md`` step 12).

Not a ``test_*`` module, so pytest never collects it — the
``support_files_media`` precedent.

**One store behind four faces.** ``InMemorySpaceRepository`` satisfies the
spaces module's own ``SpaceRepository``, the ``ActiveSpaces`` seam ``files``
and ``conversations`` each declare for themselves, and the ``SpaceLock`` the
quota service holds. That is not fake convenience: it is exactly the wiring
``_build_space_services`` builds in production (one ``SqlSpaceRepository``
behind ``SpacesQueryService``, ``RegisterUpload``'s proof and the quota's
lock), and it is what lets a router test create a space through ``POST
/spaces`` and immediately upload into it. Separate fakes would let those two
disagree, which is the one thing a suite about ownership must not permit.

The fake is faithful on the three behaviours the routes turn on: ``get``
returns a soft-deleted row (which is what makes deletion idempotent),
``lock``/``get_active`` do NOT (a deleted space may receive nothing), and
``add`` refuses a duplicate active name with the adapter's own
``spaces.duplicate_name`` — the partial unique index folded to lower case,
including the fact that a deleted space's name is free again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid
from app.modules.spaces.application.use_cases import (
    CreateSpace,
    DeleteSpace,
    GetSpace,
    ListSpaces,
    RenameSpace,
    SpacesQueryService,
    SpaceUseCases,
)
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.ports.inbound import SpaceView


@dataclass
class InMemorySpaceRepository:
    """A structural ``SpaceRepository`` over one dict, plus the two narrow
    faces other modules bind to it."""

    rows: dict[str, Space] = field(default_factory=dict)
    # Every id `save` was called with, in order — the `InMemoryFileRepository`
    # device: a dict cannot show "wrote the same row again" versus "did not
    # write", and the no-op rename turns on exactly that difference.
    saved: list[str] = field(default_factory=list)

    async def get(self, ctx: ExecutionContext, space_id: Uuid) -> Space | None:
        row = self.rows.get(space_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def lock(self, ctx: ExecutionContext, space_id: Uuid) -> bool:
        row = await self.get(ctx, space_id)
        return row is not None and row.is_active

    async def add(self, ctx: ExecutionContext, space: Space) -> None:
        if any(
            row.workspace_id == ctx.workspace_id
            and row.deleted_at is None
            and row.name.value.lower() == space.name.value.lower()
            for row in self.rows.values()
        ):
            # `ux_spaces_ws_name` as the adapter translates it: the index is
            # partial on `deleted_at IS NULL` and folds case, so a deleted
            # space frees its name.
            raise ConflictError(
                f"a space named {space.name.value!r} already exists",
                code="spaces.duplicate_name",
            )
        self.rows[space.id] = space

    async def save(self, ctx: ExecutionContext, space: Space) -> None:
        space.version += 1
        self.rows[space.id] = space
        self.saved.append(space.id)

    async def list(self, ctx: ExecutionContext, *, limit: int, cursor: str | None) -> Page[Space]:
        # Active only, newest first, through the REAL cursor codec — so a
        # cursor minted here is decoded by the rules the SQL adapter uses and
        # a malformed one is reachable from a unit test.
        matches = sorted(
            (
                row
                for row in self.rows.values()
                if row.workspace_id == ctx.workspace_id and row.deleted_at is None
            ),
            key=lambda row: row.id,
            reverse=True,
        )
        if cursor is not None:
            after = decode_id_cursor(cursor)
            matches = [row for row in matches if row.id < after]
        window = matches[: limit + 1]
        has_more = len(window) > limit
        page = window[:limit]
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)

    # --- the seams other modules declare for themselves ---------------------
    #
    # `get_active` is NOT re-implemented here: `SpacesQueryService` is the real
    # one and `build_spaces` binds it, so a test proves the production
    # translation (missing and deleted both answer `None`) rather than a
    # second copy of it.


@dataclass(frozen=True, slots=True)
class SpaceGateway:
    """The two questions ``files`` asks about a space, over one store.

    ``build_files_media`` wants a single object answering both because
    production has one — ``_build_space_services`` hands the SAME
    ``SqlSpaceRepository`` to ``RegisterUpload``'s proof (through
    ``SpacesQueryService``) and to the quota's lock. Neither method is
    re-implemented here: both delegate to the real face.
    """

    query: SpacesQueryService
    repository: InMemorySpaceRepository

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> SpaceView | None:
        return await self.query.get_active(ctx, space_id)

    async def lock(self, ctx: ExecutionContext, space_id: Uuid) -> bool:
        return await self.repository.lock(ctx, space_id)


@dataclass(frozen=True, slots=True)
class SpacesStack:
    """The bundle the router consumes, the query seam other modules bind, and
    the store both sit on."""

    use_cases: SpaceUseCases
    query: SpacesQueryService
    gateway: SpaceGateway
    repository: InMemorySpaceRepository


def build_spaces(repository: InMemorySpaceRepository | None = None) -> SpacesStack:
    """One repository behind every face — the ``_build_space_services``
    wiring, in memory."""
    repository = repository if repository is not None else InMemorySpaceRepository()
    query = SpacesQueryService(repository)
    return SpacesStack(
        use_cases=SpaceUseCases(
            create=CreateSpace(repository),
            get=GetSpace(repository),
            list=ListSpaces(repository),
            rename=RenameSpace(repository),
            delete=DeleteSpace(repository),
        ),
        query=query,
        gateway=SpaceGateway(query, repository),
        repository=repository,
    )
