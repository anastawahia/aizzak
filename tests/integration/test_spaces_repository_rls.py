"""Live-Postgres tests for ``SqlSpaceRepository`` + the ``spaces`` migration
(09-testing-strategy §3; ``docs/spaces-backend-plan.md`` step 2).

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. Builders mirror ``tests/unit/test_spaces_use_cases.py``, except
every id is a real ``new_uuid7()`` string -- the underlying columns are
actually typed ``uuid``.

This file carries BOTH halves of the step's proof, because they are the same
proof: the adapter written in step 1 (§3.139) has never once touched a real
table, and the DDL written in step 2 has never once been asked to refuse
anything. Four properties live ONLY in the migration and are therefore
asserted directly rather than through the port -- the partial, case-folded
unique index; the ``char_length`` CHECK; the ``trg_touch`` trigger; and
``FORCE ROW LEVEL SECURITY``, which is what makes even the table's owner
policy-subject.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str, *, user_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(
    *,
    workspace_id: str,
    space_id: str | None = None,
    name: str = "Research",
    created_by: str | None = None,
    now: datetime | None = None,
    deleted_at: datetime | None = None,
    version: int = 1,
) -> Space:
    at = now or utc_now()
    return Space(
        id=space_id or new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=created_by or new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=deleted_at,
        version=version,
    )


async def _fetch_row_as_owner(
    owner_dsn: str, workspace_id: str | None, space_id: str
) -> RowMapping | None:
    """Bypass the repository entirely: read the raw row as the table owner,
    for assertions that must be independent of ``repo.get``'s own bookkeeping.

    Still goes through RLS -- ``0001_spaces.py`` uses ``FORCE ROW LEVEL
    SECURITY``, so even the owner is policy-subject -- hence setting the GUC
    first, on the same connection/transaction, before reading. Passing
    ``workspace_id=None`` deliberately skips that step, which is how
    ``test_force_row_level_security_binds_even_the_table_owner`` below shows
    what ``FORCE`` is actually doing.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            if workspace_id is not None:
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
                )
            result = await conn.execute(
                text("SELECT * FROM spaces.spaces WHERE id = :id"), {"id": space_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


async def _insert_as_owner(owner_dsn: str, workspace_id: str, name: str) -> None:
    """Insert one row bypassing the DOMAIN (never the database): the only way
    to ask the ``char_length`` CHECK a question, since ``SpaceName`` refuses
    the same values one layer earlier and the port would never carry them
    this far."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            await conn.execute(
                text(
                    "INSERT INTO spaces.spaces (id, workspace_id, name, created_at, updated_at) "
                    "VALUES (:id, :ws, :name, now(), now())"
                ),
                {"id": new_uuid7(), "ws": workspace_id, "name": name},
            )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1) round-trip add/get, value objects exact                                 #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_the_value_objects_exactly(
    repo_spaces: SqlSpaceRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(workspace_id=ws)

    await repo_spaces.add(ctx, space)
    fetched = await repo_spaces.get(ctx, space.id)

    assert fetched is not None
    assert fetched.id == space.id
    assert fetched.workspace_id == ws
    assert fetched.name == space.name
    assert fetched.created_by == space.created_by
    assert fetched.created_at == space.created_at
    assert fetched.updated_at == space.updated_at
    assert fetched.deleted_at is None
    assert fetched.version == 1
    assert fetched.is_active


# --------------------------------------------------------------------------- #
# (2) missing -> None                                                         #
# --------------------------------------------------------------------------- #
async def test_get_missing_space_returns_none(repo_spaces: SqlSpaceRepository) -> None:
    assert await repo_spaces.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3) rename: version advances, name persists, the TRIGGER moves updated_at   #
# --------------------------------------------------------------------------- #
async def test_rename_advances_the_version_and_the_trigger_moves_updated_at(
    repo_spaces: SqlSpaceRepository, live_db: LiveDbDsns
) -> None:
    """``save`` deliberately does NOT write ``updated_at`` (the adapter's
    UPDATE names ``name``/``deleted_at``/``version`` only), so a moved
    ``updated_at`` can have come from exactly one place: ``trg_touch``.

    The comparison is BEFORE-vs-AFTER the update, not ``updated_at`` against
    ``created_at``: the latter passes for a trigger wired to the wrong event
    (``BEFORE INSERT`` also leaves ``updated_at`` ahead of ``created_at``, and
    then never fires again), which is precisely the mutation that first got
    through here."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(workspace_id=ws, name="Research")
    await repo_spaces.add(ctx, space)
    created_at = space.created_at
    before = await _fetch_row_as_owner(live_db.owner, ws, space.id)
    assert before is not None

    space.rename(SpaceName("Research 2026"), utc_now())
    await repo_spaces.save(ctx, space)

    assert space.version == 2
    assert space.updated_at > created_at

    row = await _fetch_row_as_owner(live_db.owner, ws, space.id)
    assert row is not None
    assert row["name"] == "Research 2026"
    assert row["version"] == 2
    assert row["updated_at"] > before["updated_at"]

    reloaded = await repo_spaces.get(ctx, space.id)
    assert reloaded is not None
    assert reloaded.name.value == "Research 2026"
    assert reloaded.version == 2


# --------------------------------------------------------------------------- #
# (4) optimistic conflict                                                     #
# --------------------------------------------------------------------------- #
async def test_save_with_stale_version_raises_conflict_and_leaves_the_row_unchanged(
    repo_spaces: SqlSpaceRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(workspace_id=ws)
    await repo_spaces.add(ctx, space)

    reader_a = await repo_spaces.get(ctx, space.id)
    reader_b = await repo_spaces.get(ctx, space.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.rename(SpaceName("Winner"), utc_now())
    await repo_spaces.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.rename(SpaceName("Loser"), utc_now())  # still stuck at version==1
    with pytest.raises(ConflictError):
        await repo_spaces.save(ctx, reader_b)

    row = await _fetch_row_as_owner(live_db.owner, ws, space.id)
    assert row is not None
    assert row["version"] == 2
    assert row["name"] == "Winner"


# --------------------------------------------------------------------------- #
# (5) soft delete: `list` hides it, `get` still answers                       #
# --------------------------------------------------------------------------- #
async def test_a_soft_deleted_space_leaves_the_collection_but_stays_gettable(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The asymmetry is deliberate (adapter docstring): ``list`` filters
    ``deleted_at IS NULL``, ``get`` does not -- which is what makes
    ``DeleteSpace`` idempotent for a caller still holding the id."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(workspace_id=ws)
    await repo_spaces.add(ctx, space)

    space.soft_delete(utc_now())
    await repo_spaces.save(ctx, space)

    page = await repo_spaces.list(ctx, limit=10, cursor=None)
    assert page.data == []

    fetched = await repo_spaces.get(ctx, space.id)
    assert fetched is not None
    assert fetched.deleted_at is not None
    assert not fetched.is_active


# --------------------------------------------------------------------------- #
# (6) list returns only this workspace's active spaces                        #
# --------------------------------------------------------------------------- #
async def test_list_returns_only_this_workspaces_active_spaces(
    repo_spaces: SqlSpaceRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    keep = _space(workspace_id=ws_a, name="Keep")
    await repo_spaces.add(ctx_a, keep)
    deleted = _space(workspace_id=ws_a, name="Gone")
    await repo_spaces.add(ctx_a, deleted)
    deleted.soft_delete(utc_now())
    await repo_spaces.save(ctx_a, deleted)
    await repo_spaces.add(_ctx(ws_b), _space(workspace_id=ws_b, name="Other tenant"))

    page = await repo_spaces.list(ctx_a, limit=10, cursor=None)

    assert [s.id for s in page.data] == [keep.id]
    assert page.next_cursor is None


# --------------------------------------------------------------------------- #
# (7) cursor pagination: pages do not overlap, order stays newest-first       #
# --------------------------------------------------------------------------- #
async def test_list_cursor_pagination_pages_do_not_overlap_and_stay_ordered(
    repo_spaces: SqlSpaceRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    created = []
    for index in range(5):
        space = _space(workspace_id=ws, name=f"Space {index}")
        await repo_spaces.add(ctx, space)
        created.append(space)
    # UUIDv7 ids are monotonic, so insertion order IS id order -- and the
    # collection reads NEWEST FIRST (6.3-ب), i.e. reversed.
    expected_order = [s.id for s in reversed(created)]

    page1 = await repo_spaces.list(ctx, limit=2, cursor=None)
    assert [s.id for s in page1.data] == expected_order[:2]
    assert page1.next_cursor is not None

    page2 = await repo_spaces.list(ctx, limit=2, cursor=page1.next_cursor)
    assert [s.id for s in page2.data] == expected_order[2:4]
    assert page2.next_cursor is not None

    page3 = await repo_spaces.list(ctx, limit=2, cursor=page2.next_cursor)
    assert [s.id for s in page3.data] == expected_order[4:5]
    assert page3.next_cursor is None

    seen = {s.id for s in page1.data} | {s.id for s in page2.data} | {s.id for s in page3.data}
    assert seen == set(expected_order)


# --------------------------------------------------------------------------- #
# (8) the unique index -- the module's ONLY duplicate-name defence            #
# --------------------------------------------------------------------------- #
async def test_a_duplicate_name_in_one_workspace_is_refused_by_name(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """`CreateSpace` never pre-reads (a read-then-insert pair is wrong exactly
    when two requests race), so ``ux_spaces_ws_name`` IS the rule -- and
    §3.139's one deviation from the ``files`` translator, the named
    ``spaces.duplicate_name`` code, is only real if a real ``23505`` arrives
    here."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    await repo_spaces.add(ctx, _space(workspace_id=ws, name="Research"))

    with pytest.raises(ConflictError) as exc_info:
        await repo_spaces.add(ctx, _space(workspace_id=ws, name="Research"))

    assert exc_info.value.code == "spaces.duplicate_name"
    assert exc_info.value.status == 409


async def test_the_duplicate_check_folds_case(repo_spaces: SqlSpaceRepository) -> None:
    """``lower(name)`` in the index, not ``name``: "Research" and "research"
    are one name to the human choosing it."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    await repo_spaces.add(ctx, _space(workspace_id=ws, name="Research"))

    with pytest.raises(ConflictError):
        await repo_spaces.add(ctx, _space(workspace_id=ws, name="rEsEaRcH"))


async def test_a_deleted_space_does_not_hold_its_name_hostage(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """``WHERE deleted_at IS NULL`` on the index: a soft-deleted row keeps its
    name in the table forever, and must not keep it out of circulation."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    first = _space(workspace_id=ws, name="Research")
    await repo_spaces.add(ctx, first)
    first.soft_delete(utc_now())
    await repo_spaces.save(ctx, first)

    reborn = _space(workspace_id=ws, name="Research")
    await repo_spaces.add(ctx, reborn)  # must not raise

    fetched = await repo_spaces.get(ctx, reborn.id)
    assert fetched is not None
    assert fetched.name.value == "Research"


async def test_the_same_name_in_two_workspaces_is_not_a_duplicate(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The index is on ``(workspace_id, lower(name))`` -- uniqueness is a
    tenant-local promise, never a global one."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    await repo_spaces.add(_ctx(ws_a), _space(workspace_id=ws_a, name="Research"))
    await repo_spaces.add(_ctx(ws_b), _space(workspace_id=ws_b, name="Research"))


# --------------------------------------------------------------------------- #
# (9) the char_length CHECK -- the database's own copy of the domain rule     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["", "x" * 121])
async def test_the_check_constraint_refuses_what_spacename_refuses(
    live_db: LiveDbDsns, name: str
) -> None:
    """``SpaceName`` already rejects both of these, so nothing reaching the
    adapter can carry them -- which is precisely why the constraint needs its
    own proof: it exists for the paths that do NOT go through the domain (a
    future migration, a backfill, an operator's psql session)."""
    with pytest.raises(DBAPIError) as exc_info:
        await _insert_as_owner(live_db.owner, new_uuid7(), name)

    assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"


async def test_the_check_constraint_admits_the_boundary(live_db: LiveDbDsns) -> None:
    """``BETWEEN 1 AND 120`` is inclusive at both ends, matching
    ``_MAX_NAME_LEN`` exactly -- an off-by-one here would reject names the
    domain considers valid, and only in production."""
    await _insert_as_owner(live_db.owner, new_uuid7(), "x" * 120)


# --------------------------------------------------------------------------- #
# (10) RLS: no context at all -> zero rows, no exception                      #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_returns_zero_rows(
    repo_spaces: SqlSpaceRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo_spaces.add(_ctx(ws), _space(workspace_id=ws))

    async with sessionmaker_app() as session:
        count = (await session.execute(text("SELECT count(*) FROM spaces.spaces"))).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (11) empty-string GUC discriminator -- what the NULLIF form buys            #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo_spaces: SqlSpaceRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    """Without ``NULLIF``, an empty ``app.workspace_id`` raises ``invalid
    input syntax for type uuid`` -- a 500 where a clean zero-row answer
    belongs (rls-empty-string-hardening)."""
    ws = new_uuid7()
    await repo_spaces.add(_ctx(ws), _space(workspace_id=ws))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (await session.execute(text("SELECT count(*) FROM spaces.spaces"))).scalar_one()
        assert count == 0

        await session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
        count = (await session.execute(text("SELECT count(*) FROM spaces.spaces"))).scalar_one()
        assert count == 1


# --------------------------------------------------------------------------- #
# (12) RLS isolation between two tenants                                      #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_spaces: SqlSpaceRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    space_a = _space(workspace_id=ws_a)
    await repo_spaces.add(_ctx(ws_a), space_a)

    assert await repo_spaces.get(_ctx(ws_b), space_a.id) is None
    assert await repo_spaces.get(_ctx(ws_a), space_a.id) is not None

    space_b = _space(workspace_id=ws_b)
    await repo_spaces.add(_ctx(ws_b), space_b)

    assert await repo_spaces.get(_ctx(ws_a), space_b.id) is None
    assert await repo_spaces.get(_ctx(ws_b), space_b.id) is not None


# --------------------------------------------------------------------------- #
# (13) forged cross-tenant write -> the RLS WITH CHECK clause rejects it      #
# --------------------------------------------------------------------------- #
async def test_add_with_a_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """``add`` writes the AGGREGATE's ``workspace_id``, not ``ctx``'s, exactly
    so that a mismatch is refused by the database instead of being silently
    filed under the caller's own tenant. ``USING`` alone would not catch this
    -- an INSERT has no existing row to read."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged = _space(workspace_id=ws_b)

    with pytest.raises(AppError) as exc_info:
        await repo_spaces.add(_ctx(ws_a), forged)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_spaces.get(_ctx(ws_b), forged.id) is None


async def test_a_rename_cannot_reach_another_tenants_space(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """Two-layer isolation on the write path (DD-04): RLS hides the row and
    the explicit ``WHERE workspace_id`` would too, so the caller sees the
    stale-version conflict and the row is untouched."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    space = _space(workspace_id=ws_a, name="Research")
    await repo_spaces.add(_ctx(ws_a), space)

    space.rename(SpaceName("Stolen"), utc_now())
    with pytest.raises(ConflictError):
        await repo_spaces.save(_ctx(ws_b), space)

    reloaded = await repo_spaces.get(_ctx(ws_a), space.id)
    assert reloaded is not None
    assert reloaded.name.value == "Research"


# --------------------------------------------------------------------------- #
# (14) FORCE ROW LEVEL SECURITY -- the owner is policy-subject too            #
# --------------------------------------------------------------------------- #
async def test_force_row_level_security_binds_even_the_table_owner(
    repo_spaces: SqlSpaceRepository, live_db: LiveDbDsns
) -> None:
    """``ENABLE`` alone exempts the owner, and ``aizzak_owner`` owns every
    table here -- so without ``FORCE``, anything running as the migrator (the
    provisioning job, a repair script, this very harness) would quietly see
    every tenant at once. The proof is the pair: same connection, same query,
    the GUC the only difference."""
    ws = new_uuid7()
    space = _space(workspace_id=ws)
    await repo_spaces.add(_ctx(ws), space)

    assert await _fetch_row_as_owner(live_db.owner, None, space.id) is None
    assert await _fetch_row_as_owner(live_db.owner, ws, space.id) is not None
