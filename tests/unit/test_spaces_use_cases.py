"""Unit tests for spaces use-cases over an in-memory fake repository.
Pure: the port is faked, so no infrastructure is exercised."""

from __future__ import annotations

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.modules.spaces.application.use_cases import (
    CreateSpace,
    DeleteSpace,
    GetSpace,
    ListSpaces,
    RenameSpace,
    SpacesQueryService,
)
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.events import SpaceDeleted
from app.modules.spaces.domain.value_objects import SpaceName


class _FakeSpaces:
    """In-memory ``SpaceRepository``.

    ``get`` returns soft-deleted rows (the port says so — that is what makes
    deletion idempotent) while ``list`` excludes them, and both scope by
    ``ctx.workspace_id``. ``saved`` records every write, because a dict-backed
    fake cannot otherwise tell "wrote the same row again" from "did not
    write" — and the no-op rename turns on exactly that difference.
    """

    def __init__(self) -> None:
        self.rows: dict[str, Space] = {}
        self.saved: list[str] = []

    async def get(self, ctx: ExecutionContext, space_id: str) -> Space | None:
        row = self.rows.get(space_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, space: Space) -> None:
        self.rows[space.id] = space

    async def save(self, ctx: ExecutionContext, space: Space) -> None:
        self.rows[space.id] = space
        self.saved.append(space.id)

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Space]:
        items = [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.deleted_at is None
        ]
        return Page(data=items[:limit], next_cursor=None, limit=limit)


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _seed(spaces: _FakeSpaces, ctx: ExecutionContext, *, name: str = "Research") -> Space:
    """Seed a space directly into the fake repo (bypassing the use-case)."""
    now = utc_now()
    space = Space(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        name=SpaceName(name),
        created_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )
    spaces.rows[space.id] = space
    return space


# --------------------------------------------------------------------------- #
# CreateSpace                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_mints_an_active_space_owned_by_the_caller() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    space = await CreateSpace(repo).execute(ctx, name="  Research  ")
    assert space.name.value == "Research"
    assert space.workspace_id == "w1"
    assert space.created_by == "u1"
    assert space.deleted_at is None
    assert space.version == 1
    assert repo.rows[space.id] is space


@pytest.mark.asyncio
async def test_create_writes_the_callers_workspace_not_a_forged_one() -> None:
    # The adapter writes the AGGREGATE's workspace_id and lets RLS `WITH CHECK`
    # judge it; the use-case is what puts the honest value there.
    repo = _FakeSpaces()
    space = await CreateSpace(repo).execute(_ctx("w2"), name="Research")
    assert space.workspace_id == "w2"


@pytest.mark.asyncio
async def test_create_rejects_an_invalid_name_as_422() -> None:
    with pytest.raises(ValidationError):
        await CreateSpace(_FakeSpaces()).execute(_ctx(), name="   ")


@pytest.mark.asyncio
async def test_create_does_not_check_uniqueness_itself() -> None:
    # Deliberate (`CreateSpace` docstring): `ux_spaces_ws_name` refuses the
    # duplicate in the same statement that would have written it. A guard here
    # would answer the same question one round trip earlier and answer it
    # wrongly whenever two requests race — the only time it matters.
    repo = _FakeSpaces()
    ctx = _ctx()
    create = CreateSpace(repo)
    first = await create.execute(ctx, name="Research")
    second = await create.execute(ctx, name="Research")
    assert first.id != second.id


# --------------------------------------------------------------------------- #
# GetSpace                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_returns_the_space() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    assert (await GetSpace(repo).execute(ctx, seeded.id)).id == seeded.id


@pytest.mark.asyncio
async def test_get_answers_404_for_an_unknown_space() -> None:
    with pytest.raises(NotFoundError):
        await GetSpace(_FakeSpaces()).execute(_ctx(), new_uuid7())


@pytest.mark.asyncio
async def test_get_answers_404_for_a_deleted_space() -> None:
    # A read's only truthful answer for a deleted resource is "gone" (§3.55).
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    seeded.soft_delete(utc_now())
    with pytest.raises(NotFoundError):
        await GetSpace(repo).execute(ctx, seeded.id)


@pytest.mark.asyncio
async def test_get_answers_404_across_workspaces() -> None:
    repo = _FakeSpaces()
    seeded = _seed(repo, _ctx("w1"))
    with pytest.raises(NotFoundError):
        await GetSpace(repo).execute(_ctx("w2"), seeded.id)


# --------------------------------------------------------------------------- #
# ListSpaces                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_returns_active_spaces_of_this_workspace_only() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    kept = _seed(repo, ctx, name="Research")
    gone = _seed(repo, ctx, name="Drafts")
    gone.soft_delete(utc_now())
    _seed(repo, _ctx("w2"), name="Other")
    page = await ListSpaces(repo).execute(ctx)
    assert [row.id for row in page.data] == [kept.id]


# --------------------------------------------------------------------------- #
# RenameSpace                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rename_persists_the_new_name() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    renamed = await RenameSpace(repo).execute(ctx, seeded.id, name="Drafts")
    assert renamed.name.value == "Drafts"
    assert repo.saved == [seeded.id]


@pytest.mark.asyncio
async def test_rename_to_the_same_name_writes_nothing_and_still_succeeds() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx, name="Research")
    result = await RenameSpace(repo).execute(ctx, seeded.id, name="Research")
    assert result.name.value == "Research"
    assert repo.saved == []


@pytest.mark.asyncio
async def test_rename_rejects_an_invalid_name_as_422() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    with pytest.raises(ValidationError):
        await RenameSpace(repo).execute(ctx, seeded.id, name="x" * 121)
    assert repo.saved == []


@pytest.mark.asyncio
async def test_rename_refuses_a_deleted_space_as_409() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    seeded.soft_delete(utc_now())
    with pytest.raises(ConflictError):
        await RenameSpace(repo).execute(ctx, seeded.id, name="Drafts")


@pytest.mark.asyncio
async def test_rename_answers_404_for_an_unknown_space() -> None:
    with pytest.raises(NotFoundError):
        await RenameSpace(_FakeSpaces()).execute(_ctx(), new_uuid7(), name="Drafts")


# --------------------------------------------------------------------------- #
# DeleteSpace                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delete_marks_the_row_and_returns_the_cascade_event() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    space, events = await DeleteSpace(repo).execute(ctx, seeded.id)
    assert space.deleted_at is not None
    assert repo.saved == [seeded.id]
    assert events == (SpaceDeleted(seeded.id, "w1", space.updated_at),)


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_emits_no_second_event() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    delete = DeleteSpace(repo)
    _, first = await delete.execute(ctx, seeded.id)
    space, second = await delete.execute(ctx, seeded.id)
    assert len(first) == 1
    assert second == ()
    # The deletion time is the FIRST one: a repeat must not move it.
    assert space.deleted_at == first[0].occurred_at


@pytest.mark.asyncio
async def test_delete_answers_404_for_an_unknown_space() -> None:
    with pytest.raises(NotFoundError):
        await DeleteSpace(_FakeSpaces()).execute(_ctx(), new_uuid7())


# --------------------------------------------------------------------------- #
# SpacesQueryService — the inbound port other modules bind to                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_query_service_projects_an_active_space() -> None:
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    view = await SpacesQueryService(repo).get_active(ctx, seeded.id)
    assert view is not None
    assert (view.space_id, view.name) == (seeded.id, "Research")


@pytest.mark.asyncio
async def test_query_service_hides_a_deleted_space() -> None:
    # `None` for missing AND deleted alike: both mean "nothing may be filed
    # here", and distinguishing them would disclose that the space exists.
    repo = _FakeSpaces()
    ctx = _ctx()
    seeded = _seed(repo, ctx)
    seeded.soft_delete(utc_now())
    assert await SpacesQueryService(repo).get_active(ctx, seeded.id) is None


@pytest.mark.asyncio
async def test_query_service_hides_another_workspaces_space() -> None:
    repo = _FakeSpaces()
    seeded = _seed(repo, _ctx("w1"))
    assert await SpacesQueryService(repo).get_active(_ctx("w2"), seeded.id) is None


@pytest.mark.asyncio
async def test_query_service_returns_none_for_an_unknown_space() -> None:
    assert await SpacesQueryService(_FakeSpaces()).get_active(_ctx(), new_uuid7()) is None
