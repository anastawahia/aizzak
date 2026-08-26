"""Live-Postgres proof of س-29 rule 1's predicate —
``SqlFileRepository.live_namesakes`` and the index it is written against
(09-testing-strategy §3).

The unit file next door (``tests/unit/test_file_replacement.py``) proves the
RULE: which files a completion replaces, in which direction, and that a failed
sweep cannot turn a successful upload into a failure. It proves all of it
against a dict, over a Python re-statement of "the same name" — so what it
cannot prove is the one thing that only a database has an opinion about:

* that ``normalize(name, NFC)`` and ``lower`` **exist and compose** as this
  adapter spells them. A wrong function name, a wrong argument order or an
  argument PostgreSQL will not take is a syntax error against a server and
  nothing at all against a fake;
* that the two spellings **agree** — the predicate in
  ``SqlFileRepository.live_namesakes`` and the expression in
  ``ix_files_space_name`` (``migrations/versions/files/0003_file_name_lookup.py``).
  An expression index only serves a predicate written the same way, so a
  divergence here does not fail: it silently sequentially scans;
* that the collation this deployment actually runs does to Arabic what the
  Python fake does. ``casefold`` and SQL's ``lower`` are different functions,
  and the point of the NFC half is that neither of them fixes the Arabic case
  on its own.

The ROWS are seeded through ``RegisterUpload`` rather than by hand, for the
cascade suite's reason: the rows the predicate reads have to be the rows the
platform would have written.
"""

from __future__ import annotations

import unicodedata

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import FindNamesakes, RegisterUpload
from app.modules.files.domain.entities import File
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

pytestmark = [pytest.mark.live_db]

# The same name in two Unicode forms — identical on screen, different bytes.
# This is the case `lower` alone does nothing for and the whole reason
# `name_key` normalises at all.
#
# ⚠️ **The letter has to be one that actually decomposes.** Plain Arabic
# letters do not: `تقرير.pdf` is byte-identical in NFC and NFD, so a test
# written on it asserts nothing at all and passes for the wrong reason (it
# did, until this line was measured). `أ` — ALEF WITH HAMZA ABOVE, U+0623 —
# has a canonical decomposition to U+0627 + U+0654, which is exactly the split
# a macOS client or a second keyboard layout produces. The assertion below is
# what stops this from silently going vacuous again.
_ARABIC = "تأكيد الفصل.pdf"
_ARABIC_NFC = unicodedata.normalize("NFC", _ARABIC)
_ARABIC_NFD = unicodedata.normalize("NFD", _ARABIC)
assert _ARABIC_NFC != _ARABIC_NFD, "the fixture no longer exercises normalisation"


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(workspace_id: str, name: str) -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


async def _upload(
    tenant_session: TenantSessionFactory,
    ctx: ExecutionContext,
    space_id: str | None,
    name: str,
    spaces: SqlSpaceRepository,
) -> File:
    """One registered file, through the module's own use-case."""
    return await RegisterUpload(
        SqlFileRepository(tenant_session), Limits(), SpacesQueryService(spaces)
    ).execute(ctx, space_id=space_id, name=name, content_type="application/pdf", size_bytes=1024)


async def _soft_delete(
    sessionmaker_app: async_sessionmaker[AsyncSession], workspace_id: str, file_id: str
) -> None:
    async with sessionmaker_app() as session:
        # `set_config(.., true)` and not `SET LOCAL`: the latter takes no bind
        # parameter (`syntax error at or near "$1"`).
        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
        )
        await session.execute(
            text("UPDATE files.files SET deleted_at = now() WHERE id = :id"), {"id": file_id}
        )
        await session.commit()


async def test_the_predicate_runs_at_all_against_postgres(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The floor: ``lower(normalize(name, NFC))`` and the row-value comparison
    are accepted by the server. Everything else in this file assumes it."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    only = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, only) == []


async def test_the_older_file_of_the_same_name_is_found(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    older = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)
    newer = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)

    found = await SqlFileRepository(tenant_session).live_namesakes(ctx, newer)

    assert found == [older.id]


async def test_the_older_file_never_finds_the_newer_one(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The direction of the rule, proven against the real ``(created_at, id)``
    row-value comparison rather than a Python tuple — two rows registered in
    the same millisecond still order totally, because the id breaks the tie."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    older = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)
    await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, older) == []


@pytest.mark.parametrize(
    ("stored", "arriving"),
    [
        ("Report.pdf", "report.pdf"),
        ("REPORT.PDF", "Report.pdf"),
        # The half that carries Arabic. `lower` does nothing here; without
        # `normalize(.., NFC)` these are two files, both indexed.
        (_ARABIC_NFC, _ARABIC_NFD),
        (_ARABIC_NFD, _ARABIC_NFC),
    ],
)
async def test_the_same_name_matches_up_to_case_and_normalisation(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    stored: str,
    arriving: str,
) -> None:
    """What only a database can answer: that PostgreSQL's ``lower`` and
    ``normalize`` under THIS deployment's collation reach the same verdict the
    Python fake reaches with ``casefold`` and ``unicodedata``."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    older = await _upload(tenant_session, ctx, space.id, stored, repo_spaces)
    newer = await _upload(tenant_session, ctx, space.id, arriving, repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == [older.id]


async def test_a_different_name_matches_nothing(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The negative that keeps the normalisation from becoming a fuzzy match:
    two names that merely look alike are two names."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)
    newer = await _upload(tenant_session, ctx, space.id, "report-final.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == []


async def test_another_space_is_a_different_file(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The scope, on the real column: spaces are isolated completely (س-32),
    so one space's ``report.pdf`` is not the other's."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    here = _space(ws, "Research")
    there = _space(ws, "Archive")
    await repo_spaces.add(ctx, here)
    await repo_spaces.add(ctx, there)
    await _upload(tenant_session, ctx, there.id, "report.pdf", repo_spaces)
    newer = await _upload(tenant_session, ctx, here.id, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == []


async def test_two_spaceless_files_still_replace_each_other(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """ "No space" is ONE bucket, not a wildcard, and this is the branch that
    proves it: written as ``= NULL`` the predicate would match nothing and a
    spaceless file would silently replace nothing at all. The state is real
    until plan row 8-b makes the column ``NOT NULL``."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    older = await _upload(tenant_session, ctx, None, "report.pdf", repo_spaces)
    newer = await _upload(tenant_session, ctx, None, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == [older.id]


async def test_a_soft_deleted_namesake_is_not_returned(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The partial index's ``WHERE deleted_at IS NULL``, as behaviour: a
    deleted file has already given up its name, and sweeping it again would
    make a second cascade's worth of Qdrant work out of nothing."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    gone = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)
    await _soft_delete(sessionmaker_app, ws, gone.id)
    newer = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == []


async def test_another_workspace_is_never_reached(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """DD-04 on the path where a leak would DESTROY rather than disclose: a
    namesake found across tenants would be deleted, with its index."""
    foreign_ws = new_uuid7()
    foreign_ctx = _ctx(foreign_ws)
    foreign_space = _space(foreign_ws, "Research")
    await repo_spaces.add(foreign_ctx, foreign_space)
    await _upload(tenant_session, foreign_ctx, foreign_space.id, "report.pdf", repo_spaces)

    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    newer = await _upload(tenant_session, ctx, space.id, "report.pdf", repo_spaces)

    assert await SqlFileRepository(tenant_session).live_namesakes(ctx, newer) == []


async def test_the_generated_key_is_what_the_column_stores(
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
    tenant_session: TenantSessionFactory,
) -> None:
    """``name_key`` is ``GENERATED ALWAYS ... STORED``, so PostgreSQL — not the
    application — derives it, and it cannot drift from the name it summarises.
    Pinned on an ARABIC name in the decomposed form, which is the case the
    whole `normalize` half exists for."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    stored = await _upload(tenant_session, ctx, space.id, _ARABIC_NFD, repo_spaces)

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
        key = (
            await session.execute(
                text("SELECT name_key FROM files.files WHERE id = :id"), {"id": stored.id}
            )
        ).scalar_one()

    assert key == unicodedata.normalize("NFC", _ARABIC_NFD).lower()
    assert key != _ARABIC_NFD  # the stored NAME is still exactly what was sent


async def test_the_name_key_is_an_index_condition_and_not_a_filter(
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """The reason ``name_key`` is a stored COLUMN and not an index expression,
    asserted rather than described — and it is the finding that produced the
    column.

    ``files.files`` is ``FORCE ROW LEVEL SECURITY``. ``lower`` and
    ``normalize`` are IMMUTABLE but NOT ``LEAKPROOF``, and PostgreSQL refuses
    to evaluate a non-leakproof function before a row-security qual, so an
    expression index over them can never be a search key on this table — the
    planner puts the name in ``Filter`` and narrows on ``(workspace_id,
    space_id)`` alone. That does not FAIL: it returns the same rows, so every
    other test in this file would stay green while the predicate scanned one
    space's ten thousand files on every upload completion.

    A stored column makes the comparison ``texteq``, which IS leakproof, so it
    may run before the barrier. ``enable_seqscan = off`` removes the "the
    table is tiny" answer and asks the planner the question this is about:
    CAN the key be used, not would it bother.
    """
    async with sessionmaker_app() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": new_uuid7()}
        )
        plan = "\n".join(
            row[0]
            for row in (
                await session.execute(
                    text(
                        """
                        EXPLAIN SELECT id FROM files.files
                        -- CAST(...) throughout, never the `pg` shorthand
                        -- cast, because that shorthand collides with
                        -- SQLAlchemy's own parameter syntax. And a cast is
                        -- needed at all because a bare parameter inside
                        -- `normalize(...)` gives asyncpg no type to infer.
                        -- (No colons in this comment on purpose: `text()`
                        -- scans comments too and would invent parameters.)
                        WHERE workspace_id = CAST(:ws AS uuid)
                          AND space_id = CAST(:sp AS uuid)
                          AND name_key = lower(normalize(CAST(:name AS text), NFC))
                          AND deleted_at IS NULL
                        """
                    ),
                    {"ws": new_uuid7(), "sp": new_uuid7(), "name": "Report.PDF"},
                )
            ).all()
        )

    assert "ix_files_space_name" in plan, plan
    index_cond = next(line for line in plan.splitlines() if "Index Cond" in line)
    assert "name_key" in index_cond, plan


async def test_the_use_case_refuses_an_id_it_cannot_read(
    tenant_session: TenantSessionFactory,
) -> None:
    """``FindNamesakes`` raises rather than answering "nothing to replace" —
    the caller is about to ACT on that answer, and silence would be
    indistinguishable from a file that simply has no namesakes."""
    ctx = _ctx(new_uuid7())
    with pytest.raises(NotFoundError):
        await FindNamesakes(SqlFileRepository(tenant_session)).execute(ctx, new_uuid7())
