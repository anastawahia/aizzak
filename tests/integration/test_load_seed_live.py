"""The one claim ``app.ops.load_seed`` makes that only a live database can
check: the corpus is written THROUGH row-level security, not around it.

Capacity step 0.1's condition (3) does not merely ask for a million rows, it
asks for «أداةٍ تحترم RLS». The hermetic module
(``tests/unit/test_load_seed.py``) proves the generator's arithmetic and the
shape of every row; it cannot prove the property that matters here, because a
seeder that respects the policies and one that happens not to trip them are
identical until PostgreSQL actually evaluates a ``WITH CHECK``. Three things
need a real server:

* that ``app_rw`` can write these rows AT ALL under ``FORCE ROW LEVEL
  SECURITY`` -- a single column the policy disagrees with turns the whole
  batch into ``new row violates row-level security policy``;
* that a row written for tenant A is invisible to tenant B, which is the
  ONLY reason the previous point is worth anything;
* that ``purge`` removes exactly the seed's own workspaces -- a corpus that
  cannot be taken back out is a corpus an operator will not put in.

Everything here runs at a deliberately tiny scale (two workspaces, tens of
rows) against the live ``aizzak_test`` database, and cleans up after itself.
The MILLION-row corpus is an operator action with a manifest, not a test:
``deploy/load/README.md`` §3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.knowledge.domain.sparse import Bm25Params
from app.ops import load_seed
from app.ops.load_seed import (
    CorpusSize,
    SeedPlan,
    TextPool,
    build_plan,
    seed_postgres,
    tenant_transaction,
)

pytestmark = [pytest.mark.live_db, pytest.mark.anyio]

_ANCHOR = datetime(2026, 9, 3, tzinfo=UTC)
_BM25 = Bm25Params(k1=1.5, b=0.75, avg_len=32.0)

# Small enough to write in well under a second, large enough that every table
# the seeder touches gets more than one row and the thread/message split is
# exercised.
_TARGET = CorpusSize(workspaces=2, messages=45, files=6, vectors=18)


async def _drop(engine: AsyncEngine, plan: SeedPlan) -> None:
    """Remove this plan's tenants, in FK order. Deliberately NOT
    ``load_seed.purge`` -- the fixture must not depend on the function two of
    these tests are checking."""
    async with engine.connect() as conn:
        for workspace in plan.workspaces:
            async with tenant_transaction(conn, workspace.workspace_id):
                for table in (
                    "knowledge.chunks",
                    "knowledge.documents",
                    "files.files",
                    "conversations.messages",
                    "conversations.conversations",
                    "spaces.spaces",
                    "workspace.users",
                ):
                    await conn.execute(
                        text(f"DELETE FROM {table} WHERE workspace_id = CAST(:ws AS uuid)"),
                        {"ws": workspace.workspace_id},
                    )
            async with conn.begin():
                await conn.execute(
                    text("DELETE FROM workspace.workspaces WHERE id = CAST(:ws AS uuid)"),
                    {"ws": workspace.workspace_id},
                )


@pytest.fixture
async def seeded(app_engine: AsyncEngine) -> AsyncIterator[SeedPlan]:
    """A tiny corpus written by the real ``seed_postgres``, torn down after.

    The seed id carries the test's name so a crashed run leaves rows that are
    obviously test debris and cannot collide with an operator's own corpus.
    """
    plan = build_plan(seed_id="pytest-load-seed-live", anchor=_ANCHOR, target=_TARGET, skew=1.0)
    await _drop(app_engine, plan)  # a previous crashed run leaves the same ids
    await seed_postgres(app_engine, plan, TextPool("pytest", size=8, bm25=_BM25), progress=False)
    try:
        yield plan
    finally:
        await _drop(app_engine, plan)


async def _count(engine: AsyncEngine, workspace_id: str, table: str) -> int:
    async with engine.connect() as conn, tenant_transaction(conn, workspace_id):
        return int(await conn.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


async def test_the_corpus_is_written_through_the_policies(
    app_engine: AsyncEngine, seeded: SeedPlan
) -> None:
    """Every table lands, as ``app_rw``, under ``FORCE ROW LEVEL SECURITY``.

    This is the whole of condition (3)'s "respects RLS" in one assertion: had
    a single ``workspace_id`` disagreed with the GUC the seeder set, the
    ``WITH CHECK`` would have rejected the batch and there would be nothing
    here to count.
    """
    for workspace in seeded.workspaces:
        assert (
            await _count(app_engine, workspace.workspace_id, "conversations.messages")
            == workspace.messages
        )
        assert await _count(app_engine, workspace.workspace_id, "files.files") == workspace.files
        assert (
            await _count(app_engine, workspace.workspace_id, "knowledge.chunks")
            == workspace.vectors
        )
        assert (
            await _count(app_engine, workspace.workspace_id, "knowledge.documents")
            == workspace.documents
        )
        assert await _count(app_engine, workspace.workspace_id, "workspace.users") == 1


async def test_one_tenants_corpus_is_invisible_to_another(
    app_engine: AsyncEngine, seeded: SeedPlan
) -> None:
    """The point of writing through the policies rather than around them.

    A seeder running as a ``BYPASSRLS`` role produces rows that pass every
    count above and still leak across tenants -- which the load run would
    then measure as if it were the platform's own behaviour.
    """
    first, second = seeded.workspaces[0], seeded.workspaces[1]
    async with app_engine.connect() as conn, tenant_transaction(conn, second.workspace_id):
        leaked = await conn.scalar(
            text(
                "SELECT count(*) FROM conversations.messages WHERE workspace_id = CAST(:ws AS uuid)"
            ),
            {"ws": first.workspace_id},
        )
    assert leaked == 0


async def test_no_tenant_context_reads_nothing(app_engine: AsyncEngine, seeded: SeedPlan) -> None:
    """The fail-safe the empty-string GUC hardening exists for: a transaction
    that never set ``app.workspace_id`` sees zero rows, not all of them."""
    async with app_engine.connect() as conn, conn.begin():
        assert await conn.scalar(text("SELECT count(*) FROM conversations.messages")) == 0


async def test_re_running_the_same_seed_writes_nothing_new(
    app_engine: AsyncEngine, seeded: SeedPlan
) -> None:
    """``ON CONFLICT DO NOTHING`` over deterministic ids, which is what makes
    an interrupted million-row run resumable by simply running it again."""
    before = await _count(app_engine, seeded.workspaces[0].workspace_id, "conversations.messages")
    await seed_postgres(app_engine, seeded, TextPool("pytest", size=8, bm25=_BM25), progress=False)
    after = await _count(app_engine, seeded.workspaces[0].workspace_id, "conversations.messages")
    assert after == before


async def test_purge_removes_the_seeds_own_tenants(
    app_engine: AsyncEngine, seeded: SeedPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``purge`` is the reason an operator is willing to seed at all.

    Postgres only: the ``live_db`` marker promises a database, not a vector
    store, so the per-tenant collection drop is intercepted and asserted on
    rather than performed. That it drops the RIGHT collection name is the
    part worth holding here; that Qdrant honours a delete is Qdrant's
    contract, already covered where the store adapter is tested.
    """
    dropped: list[str] = []

    async def _record(_client: object, name: str) -> bool:
        dropped.append(name)
        return True

    monkeypatch.setattr(load_seed, "drop_collection", _record)
    removed = await load_seed.purge(app_engine, None, seeded, progress=False)

    assert removed["conversations.messages"] == seeded.actual.messages
    assert removed["knowledge.chunks"] == seeded.actual.vectors
    assert removed["workspace.workspaces"] == len(seeded.workspaces)
    assert dropped == [
        knowledge_collection(workspace.workspace_id) for workspace in seeded.workspaces
    ]
    for workspace in seeded.workspaces:
        assert await _count(app_engine, workspace.workspace_id, "conversations.messages") == 0


async def test_a_mistyped_included_workspace_is_refused(app_engine: AsyncEngine) -> None:
    """``--include-workspace`` with an id that names nothing must stop before
    the first row.

    Nothing downstream would catch it: the RLS policies compare the row's
    ``workspace_id`` to the GUC and never ask whether that tenant exists, and
    the account rows an included tenant would have are exactly the ones this
    path skips. The result would be a complete, valid-looking corpus no token
    can reach -- discovered as an empty load run, hours later.
    """
    plan = build_plan(
        seed_id="pytest-load-seed-missing",
        anchor=_ANCHOR,
        target=_TARGET,
        skew=1.0,
        include=("00000000-0000-7000-8000-0000000dead0",),
    )
    with pytest.raises(SystemExit, match="do not exist"):
        await seed_postgres(
            app_engine, plan, TextPool("pytest", size=8, bm25=_BM25), progress=False
        )
