"""Live-Postgres tests for ``SqlFileRepository`` + RLS (09-testing-strategy §3).

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. Builders mirror ``tests/unit/test_files_use_cases.py``, except
every id is a *real* ``new_uuid7()`` string rather than an arbitrary label --
the underlying Postgres columns are actually typed ``uuid``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import (
    ContentType,
    FileName,
    FileStatus,
    Sha256,
    StorageKey,
)
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


def _file(
    *,
    workspace_id: str,
    space_id: str | None = None,
    file_id: str | None = None,
    name: str = "report.pdf",
    content_type: str = "application/pdf",
    size_bytes: int = 2048,
    storage_key: str | None = None,
    checksum: str | None = None,
    status: FileStatus = FileStatus.UPLOADED,
    uploaded_by: str | None = None,
    now: datetime | None = None,
    deleted_at: datetime | None = None,
    version: int = 1,
) -> File:
    fid = file_id or new_uuid7()
    at = now or utc_now()
    return File(
        id=fid,
        workspace_id=workspace_id,
        space_id=space_id,
        name=FileName(name),
        content_type=ContentType(content_type),
        size_bytes=size_bytes,
        storage_key=StorageKey(storage_key or f"{workspace_id}/{fid}"),
        checksum=Sha256(checksum) if checksum else None,
        status=status,
        uploaded_by=uploaded_by or new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=deleted_at,
        version=version,
    )


async def _fetch_row_as_owner(owner_dsn: str, workspace_id: str, file_id: str) -> RowMapping | None:
    """Bypass the repository entirely: read the raw row as the table owner,
    for assertions that must be independent of ``repo.get``'s own
    bookkeeping. Still goes through RLS -- ``0001_files.py`` uses ``FORCE ROW
    LEVEL SECURITY``, so even the owner is policy-subject -- hence setting
    the GUC first, on the same connection/transaction, before reading.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT * FROM files.files WHERE id = :id"), {"id": file_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1) round-trip add/get, VOs exact                                          #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_the_value_objects_exactly(
    repo_files: SqlFileRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws)

    await repo_files.add(ctx, file)
    fetched = await repo_files.get(ctx, file.id)

    assert fetched is not None
    assert fetched.id == file.id
    assert fetched.workspace_id == ws
    assert fetched.name == file.name
    assert fetched.content_type == file.content_type
    assert fetched.size_bytes == file.size_bytes
    assert fetched.storage_key == file.storage_key
    assert fetched.checksum is None
    assert fetched.status is FileStatus.UPLOADED
    assert fetched.uploaded_by == file.uploaded_by
    assert fetched.created_at == file.created_at
    assert fetched.updated_at == file.updated_at
    assert fetched.deleted_at is None
    assert fetched.version == 1


# --------------------------------------------------------------------------- #
# (2) missing -> None                                                        #
# --------------------------------------------------------------------------- #
async def test_get_missing_file_returns_none(repo_files: SqlFileRepository) -> None:
    assert await repo_files.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3) save() status transitions: version/updated_at write-back, owner-verified#
# --------------------------------------------------------------------------- #
async def test_save_status_transitions_advance_version_and_write_back(
    repo_files: SqlFileRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws)
    await repo_files.add(ctx, file)
    created_at = file.created_at

    file.mark_scanning(utc_now())
    await repo_files.save(ctx, file)
    assert file.version == 2
    assert file.status is FileStatus.SCANNING

    sha = Sha256("a" * 64)
    file.complete(sha, utc_now())
    await repo_files.save(ctx, file)

    assert file.version == 3
    assert file.status is FileStatus.READY
    assert file.checksum == sha
    assert file.updated_at >= created_at

    row = await _fetch_row_as_owner(live_db.owner, ws, file.id)
    assert row is not None
    assert row["status"] == "ready"
    assert row["checksum"] == sha.value
    assert row["version"] == 3

    fetched = await repo_files.get(ctx, file.id)
    assert fetched is not None
    assert fetched.status is FileStatus.READY
    assert fetched.checksum == sha
    assert fetched.version == 3


# --------------------------------------------------------------------------- #
# (4) optimistic conflict                                                    #
# --------------------------------------------------------------------------- #
async def test_save_with_stale_version_raises_conflict_and_leaves_row_unchanged(
    repo_files: SqlFileRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws)
    await repo_files.add(ctx, file)

    reader_a = await repo_files.get(ctx, file.id)
    reader_b = await repo_files.get(ctx, file.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.mark_scanning(utc_now())
    await repo_files.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.mark_scanning(utc_now())  # reader_b is still stuck at version==1
    with pytest.raises(ConflictError):
        await repo_files.save(ctx, reader_b)

    row = await _fetch_row_as_owner(live_db.owner, ws, file.id)
    assert row is not None
    assert row["version"] == 2
    assert row["status"] == "scanning"


# --------------------------------------------------------------------------- #
# (5) list returns only this workspace's active files                       #
# --------------------------------------------------------------------------- #
async def test_list_returns_only_this_workspaces_active_files(
    repo_files: SqlFileRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    keep = _file(workspace_id=ws_a)
    await repo_files.add(ctx_a, keep)
    deleted = _file(workspace_id=ws_a)
    await repo_files.add(ctx_a, deleted)
    deleted.soft_delete(utc_now())
    await repo_files.save(ctx_a, deleted)
    other_tenant = _file(workspace_id=ws_b)
    await repo_files.add(_ctx(ws_b), other_tenant)

    page = await repo_files.list(ctx_a, space_id=None, limit=10, cursor=None)

    assert [f.id for f in page.data] == [keep.id]
    assert page.next_cursor is None


# --------------------------------------------------------------------------- #
# (6) cursor pagination: page1/page2 no overlap, stable order               #
# --------------------------------------------------------------------------- #
async def test_list_cursor_pagination_pages_do_not_overlap_and_stay_ordered(
    repo_files: SqlFileRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    created = []
    for _ in range(5):
        f = _file(workspace_id=ws)
        await repo_files.add(ctx, f)
        created.append(f)
    # UUIDv7 ids are monotonic, so insertion order IS id order — and the
    # collection reads NEWEST FIRST (6.3-ب), i.e. reversed.
    expected_order = [f.id for f in reversed(created)]

    page1 = await repo_files.list(ctx, space_id=None, limit=2, cursor=None)
    assert [f.id for f in page1.data] == expected_order[:2]
    assert page1.next_cursor is not None

    page2 = await repo_files.list(ctx, space_id=None, limit=2, cursor=page1.next_cursor)
    assert [f.id for f in page2.data] == expected_order[2:4]
    assert page2.next_cursor is not None

    page3 = await repo_files.list(ctx, space_id=None, limit=2, cursor=page2.next_cursor)
    assert [f.id for f in page3.data] == expected_order[4:5]
    assert page3.next_cursor is None

    seen = {f.id for f in page1.data} | {f.id for f in page2.data} | {f.id for f in page3.data}
    assert seen == set(expected_order)


# --------------------------------------------------------------------------- #
# (7) count correct per workspace, active only                              #
# --------------------------------------------------------------------------- #
async def test_count_counts_only_active_files_per_workspace(
    repo_files: SqlFileRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    await repo_files.add(ctx_a, _file(workspace_id=ws_a))
    second = _file(workspace_id=ws_a)
    await repo_files.add(ctx_a, second)
    await repo_files.add(_ctx(ws_b), _file(workspace_id=ws_b))

    assert await repo_files.count(ctx_a) == 2
    assert await repo_files.count(_ctx(ws_b)) == 1

    second.soft_delete(utc_now())
    await repo_files.save(ctx_a, second)

    assert await repo_files.count(ctx_a) == 1


# --------------------------------------------------------------------------- #
# (8) RLS: no context set at all -> zero rows, no exception                 #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_returns_zero_rows(
    repo_files: SqlFileRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo_files.add(_ctx(ws), _file(workspace_id=ws))

    async with sessionmaker_app() as session:
        count = (await session.execute(text("SELECT count(*) FROM files.files"))).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (9) empty-string GUC discriminator -- what the NULLIF fix buys            #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo_files: SqlFileRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws_a = new_uuid7()
    await repo_files.add(_ctx(ws_a), _file(workspace_id=ws_a))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (await session.execute(text("SELECT count(*) FROM files.files"))).scalar_one()
        assert count == 0

        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a}
        )
        count = (await session.execute(text("SELECT count(*) FROM files.files"))).scalar_one()
        assert count == 1


# --------------------------------------------------------------------------- #
# (10) RLS isolation between two tenants                                     #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_files: SqlFileRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    file_a = _file(workspace_id=ws_a)
    await repo_files.add(_ctx(ws_a), file_a)

    assert await repo_files.get(_ctx(ws_b), file_a.id) is None
    assert await repo_files.get(_ctx(ws_a), file_a.id) is not None

    file_b = _file(workspace_id=ws_b)
    await repo_files.add(_ctx(ws_b), file_b)

    assert await repo_files.get(_ctx(ws_a), file_b.id) is None
    assert await repo_files.get(_ctx(ws_b), file_b.id) is not None


# --------------------------------------------------------------------------- #
# (11) forged cross-tenant write -> RLS WITH CHECK rejects it               #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_files: SqlFileRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged_file = _file(workspace_id=ws_b)

    with pytest.raises(AppError) as exc_info:
        await repo_files.add(_ctx(ws_a), forged_file)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_files.get(_ctx(ws_b), forged_file.id) is None


# --------------------------------------------------------------------------- #
# (12) rename — `save` now writes `name` (BE-RAG-006)                        #
# --------------------------------------------------------------------------- #
async def test_save_persists_a_renamed_name(repo_files: SqlFileRepository) -> None:
    """`name` joined the UPDATE statement in BE-RAG-006. Nothing above the
    adapter can prove the column is actually written — the in-memory fake
    stores the same object it was handed — so it is proven here, on a real
    round trip through Postgres."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws)
    await repo_files.add(ctx, file)

    file.rename(FileName("Q1 summary.pdf"), utc_now())
    await repo_files.save(ctx, file)

    reloaded = await repo_files.get(ctx, file.id)
    assert reloaded is not None
    assert reloaded.name.value == "Q1 summary.pdf"
    assert reloaded.version == 2


async def test_saving_a_rename_leaves_the_byte_facing_columns_alone(
    repo_files: SqlFileRepository,
) -> None:
    """The columns describing the OBJECT — `content_type`, `size_bytes`,
    `storage_key` — are absent from the UPDATE on purpose; this is the test
    that notices if one is ever added."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws)
    await repo_files.add(ctx, file)
    before = (file.content_type.value, file.size_bytes, file.storage_key.value)

    file.rename(FileName("renamed.pdf"), utc_now())
    await repo_files.save(ctx, file)

    reloaded = await repo_files.get(ctx, file.id)
    assert reloaded is not None
    assert (
        reloaded.content_type.value,
        reloaded.size_bytes,
        reloaded.storage_key.value,
    ) == before


async def test_a_rename_cannot_reach_another_tenants_file(repo_files: SqlFileRepository) -> None:
    """Two-layer isolation on the write path (DD-04): the stale-version
    conflict is what a caller sees, and the row is untouched."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    file = _file(workspace_id=ws_a)
    await repo_files.add(_ctx(ws_a), file)

    file.rename(FileName("stolen.pdf"), utc_now())
    with pytest.raises(ConflictError):
        await repo_files.save(_ctx(ws_b), file)

    reloaded = await repo_files.get(_ctx(ws_a), file.id)
    assert reloaded is not None
    assert reloaded.name.value == "report.pdf"


# --------------------------------------------------------------------------- #
# (13) the space column: written, filtered on, and immovable                  #
# --------------------------------------------------------------------------- #
async def test_the_space_a_file_was_added_under_round_trips(
    repo_files: SqlFileRepository, live_db: LiveDbDsns
) -> None:
    """``add`` names ``space_id`` in its INSERT and ``_hydrate`` reads it back
    (plan step 6). Asserted through the aggregate AND against the raw row,
    because "the repository remembers what I handed it" is also what a
    repository that never wrote the column would look like from the aggregate
    side alone."""
    ws, space = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws, space_id=space)

    await repo_files.add(ctx, file)

    fetched = await repo_files.get(ctx, file.id)
    assert fetched is not None
    assert fetched.space_id == space
    row = await _fetch_row_as_owner(live_db.owner, ws, file.id)
    assert row is not None
    assert str(row["space_id"]) == space


async def test_a_file_registered_without_a_space_stores_and_reads_back_null(
    repo_files: SqlFileRepository,
) -> None:
    """The transitional state the column is NULLable for (plan row 8-b): the
    media worker has no space to name until step 7, and "no space" must be
    storable rather than an insert that fails at 2 a.m. in a worker."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    file = _file(workspace_id=ws, space_id=None)

    await repo_files.add(ctx, file)

    fetched = await repo_files.get(ctx, file.id)
    assert fetched is not None
    assert fetched.space_id is None


async def test_listing_by_space_returns_that_spaces_active_files_and_nothing_else(
    repo_files: SqlFileRepository,
) -> None:
    """The narrowing is an ADDED condition, not a replacement: the workspace
    filter, the soft-delete filter and the space filter all have to survive
    together, and each row below fails the page for a different one of them.
    """
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    mine, sibling = new_uuid7(), new_uuid7()

    keep = _file(workspace_id=ws_a, space_id=mine)
    await repo_files.add(ctx_a, keep)
    deleted = _file(workspace_id=ws_a, space_id=mine)
    await repo_files.add(ctx_a, deleted)
    deleted.soft_delete(utc_now())
    await repo_files.save(ctx_a, deleted)
    await repo_files.add(ctx_a, _file(workspace_id=ws_a, space_id=sibling))
    await repo_files.add(ctx_a, _file(workspace_id=ws_a, space_id=None))
    # Same space id, other tenant: RLS and the WHERE both have to hold, and a
    # space id is not a secret -- another workspace could hold the same value.
    await repo_files.add(_ctx(ws_b), _file(workspace_id=ws_b, space_id=mine))

    page = await repo_files.list(ctx_a, space_id=mine, limit=10, cursor=None)

    assert [f.id for f in page.data] == [keep.id]


async def test_listing_without_a_space_still_spans_the_whole_workspace(
    repo_files: SqlFileRepository,
) -> None:
    """``space_id=None`` means "every space", not "the files that have none" —
    the distinction the router depends on until ``?space_id=`` lands (step 12),
    and the one an `IS NULL` reading of the same parameter would invert."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    in_space = _file(workspace_id=ws, space_id=new_uuid7())
    spaceless = _file(workspace_id=ws, space_id=None)
    await repo_files.add(ctx, in_space)
    await repo_files.add(ctx, spaceless)

    page = await repo_files.list(ctx, space_id=None, limit=10, cursor=None)

    assert {f.id for f in page.data} == {in_space.id, spaceless.id}


async def test_a_save_cannot_move_a_file_into_another_space(
    repo_files: SqlFileRepository,
) -> None:
    """Decision 3 — a file does not move between spaces — enforced by the
    column's absence from the UPDATE rather than by the aggregate having no
    mutator. The aggregate's field is assigned directly here precisely BECAUSE
    that is the one thing no supported code path can do: the test has to reach
    past the domain to prove the adapter would refuse it anyway.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    original, hijacked = new_uuid7(), new_uuid7()
    file = _file(workspace_id=ws, space_id=original)
    await repo_files.add(ctx, file)

    file.space_id = hijacked
    file.rename(FileName("renamed.pdf"), utc_now())
    await repo_files.save(ctx, file)

    reloaded = await repo_files.get(ctx, file.id)
    assert reloaded is not None
    assert reloaded.space_id == original
    assert reloaded.name.value == "renamed.pdf"
