"""Live-Postgres tests for ``SqlMemoryRepository`` + RLS (09-testing-strategy §3).

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. Builders mirror ``tests/unit/test_memory_use_cases.py``, except
every id is a *real* ``new_uuid7()`` string rather than an arbitrary label --
the underlying Postgres columns are actually typed ``uuid``.

``memory.memory_items`` carries no ``version`` column (01-data-model §2.5):
unlike every other repository test in this wave, there is no optimistic-lock
test here -- ``save`` is a plain update-by-id and repeated calls never
conflict (documented in test 4 below).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.modules.memory.adapters.sql_repository import SqlMemoryRepository
from app.modules.memory.domain.entities import MemoryItem
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef

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


def _item(
    *,
    workspace_id: str,
    item_id: str | None = None,
    agent_key: str = "rag-agent",
    kind: MemoryKind = MemoryKind.SEMANTIC,
    content: str = "the user likes tea",
    vector_ref: VectorRef | None = None,
    salience: float = 0.0,
    now: datetime | None = None,
    deleted_at: datetime | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id or new_uuid7(),
        workspace_id=workspace_id,
        agent_key=AgentKey(agent_key),
        kind=kind,
        content=content,
        vector_ref=vector_ref,
        salience=salience,
        created_at=now or utc_now(),
        deleted_at=deleted_at,
    )


# --------------------------------------------------------------------------- #
# (1) round-trip add/get, vector_ref None                                    #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_with_vector_ref_none(
    repo_memory: SqlMemoryRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    item = _item(workspace_id=ws, salience=0.5)

    await repo_memory.add(ctx, item)
    fetched = await repo_memory.get(ctx, item.id)

    assert fetched is not None
    assert fetched.id == item.id
    assert fetched.workspace_id == ws
    assert fetched.agent_key == item.agent_key
    assert fetched.kind is MemoryKind.SEMANTIC
    assert fetched.content == item.content
    assert fetched.vector_ref is None
    assert fetched.salience == 0.5
    assert fetched.created_at == item.created_at
    assert fetched.deleted_at is None


# --------------------------------------------------------------------------- #
# (2) get missing -> None                                                    #
# --------------------------------------------------------------------------- #
async def test_get_missing_memory_item_returns_none(repo_memory: SqlMemoryRepository) -> None:
    assert await repo_memory.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3a) save() attach_vector round-trips (populated vector_ref)               #
# --------------------------------------------------------------------------- #
async def test_save_attach_vector_round_trips(repo_memory: SqlMemoryRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    item = _item(workspace_id=ws)
    await repo_memory.add(ctx, item)

    ref = VectorRef(collection=f"mem-{ws}", point_id=new_uuid7())
    item.attach_vector(ref)
    await repo_memory.save(ctx, item)

    fetched = await repo_memory.get(ctx, item.id)
    assert fetched is not None
    assert fetched.vector_ref == ref
    assert fetched.deleted_at is None


# --------------------------------------------------------------------------- #
# (3b) save() forget round-trips                                             #
# --------------------------------------------------------------------------- #
async def test_save_forget_round_trips(repo_memory: SqlMemoryRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    item = _item(workspace_id=ws)
    await repo_memory.add(ctx, item)

    item.forget(utc_now())
    await repo_memory.save(ctx, item)

    fetched = await repo_memory.get(ctx, item.id)
    assert fetched is not None
    assert fetched.deleted_at is not None


# --------------------------------------------------------------------------- #
# (4) no version column -- repeated saves never conflict                     #
# --------------------------------------------------------------------------- #
async def test_repeated_saves_never_conflict_no_version_column(
    repo_memory: SqlMemoryRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    item = _item(workspace_id=ws)
    await repo_memory.add(ctx, item)

    item.attach_vector(VectorRef(collection="c1", point_id=new_uuid7()))
    await repo_memory.save(ctx, item)  # first save: no error
    await repo_memory.save(ctx, item)  # second save, same object: still no error (no version)

    fetched = await repo_memory.get(ctx, item.id)
    assert fetched is not None
    assert fetched.vector_ref == item.vector_ref


# --------------------------------------------------------------------------- #
# (5) list_by_agent: tenant + agent scoped, excludes soft-deleted           #
# --------------------------------------------------------------------------- #
async def test_list_by_agent_scopes_by_tenant_and_agent_and_excludes_deleted(
    repo_memory: SqlMemoryRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    live = _item(workspace_id=ws_a, agent_key="rag-agent")
    await repo_memory.add(ctx_a, live)
    forgotten = _item(workspace_id=ws_a, agent_key="rag-agent")
    await repo_memory.add(ctx_a, forgotten)
    forgotten.forget(utc_now())
    await repo_memory.save(ctx_a, forgotten)
    other_agent = _item(workspace_id=ws_a, agent_key="other-agent")
    await repo_memory.add(ctx_a, other_agent)
    cross_tenant = _item(workspace_id=ws_b, agent_key="rag-agent")
    await repo_memory.add(_ctx(ws_b), cross_tenant)

    page = await repo_memory.list_by_agent(ctx_a, "rag-agent", limit=10, cursor=None)

    assert [i.id for i in page.data] == [live.id]
    assert page.next_cursor is None


# --------------------------------------------------------------------------- #
# (6) RLS: no context set at all -> zero rows, no exception                 #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_returns_zero_rows(
    repo_memory: SqlMemoryRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo_memory.add(_ctx(ws), _item(workspace_id=ws))

    async with sessionmaker_app() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM memory.memory_items"))
        ).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (7) empty-string GUC discriminator -- what the NULLIF fix buys            #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo_memory: SqlMemoryRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws_a = new_uuid7()
    await repo_memory.add(_ctx(ws_a), _item(workspace_id=ws_a))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (
            await session.execute(text("SELECT count(*) FROM memory.memory_items"))
        ).scalar_one()
        assert count == 0

        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a}
        )
        count = (
            await session.execute(text("SELECT count(*) FROM memory.memory_items"))
        ).scalar_one()
        assert count == 1


# --------------------------------------------------------------------------- #
# (8) RLS isolation between two tenants                                      #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_memory: SqlMemoryRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    item_a = _item(workspace_id=ws_a)
    await repo_memory.add(_ctx(ws_a), item_a)

    assert await repo_memory.get(_ctx(ws_b), item_a.id) is None
    assert await repo_memory.get(_ctx(ws_a), item_a.id) is not None

    item_b = _item(workspace_id=ws_b)
    await repo_memory.add(_ctx(ws_b), item_b)

    assert await repo_memory.get(_ctx(ws_a), item_b.id) is None
    assert await repo_memory.get(_ctx(ws_b), item_b.id) is not None


# --------------------------------------------------------------------------- #
# (9) forged cross-tenant write -> RLS WITH CHECK rejects it                #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_memory: SqlMemoryRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged_item = _item(workspace_id=ws_b)

    with pytest.raises(AppError) as exc_info:
        await repo_memory.add(_ctx(ws_a), forged_item)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_memory.get(_ctx(ws_b), forged_item.id) is None
