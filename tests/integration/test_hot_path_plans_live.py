"""Capacity step 2.4's schema change, asserted against a real planner.

Three things only a live PostgreSQL can say, and the third is the one that
makes the other two worth writing.

1. **The indexes exist, and the ones they SUPERSEDE do not.**
   ``migrations/versions/{knowledge/0009,files/0004,conversations/0006}`` are
   the step's whole schema change; a chain that did not run leaves a green unit
   suite and a platform that sequentially scans a million-row table on every
   retrieval. Two of the three revisions REPLACE a single-column space index,
   so a survivor is a different defect: an index every insert maintains and no
   predicate can use as a leading key.
2. **The planner USES each one for the predicate its adapter issues.** An
   index that exists and is never chosen costs writes and buys nothing -- and
   on this schema that is not hypothetical: ``files/0003_file_name_lookup``
   records an expression index PostgreSQL refused to use as a search key under
   ``FORCE ROW LEVEL SECURITY``, found only by looking at a plan.
3. **The assertion is proven able to FAIL.** Each one is repeated with the
   index dropped inside a transaction that is then rolled back. A guard nobody
   has seen fail is a guard whose polarity nobody knows.

**What this file asserts, and what it deliberately does NOT.** It asserts the
INDEX NAME in the plan, never ``app.ops.explain_hot_paths``'s verdict. Those
thresholds are calibrated against the Wave-0 corpus (a million chunks across
200 tenants); a fixture of a few thousand freshly written rows produces plans
that are *correct* and score differently -- a sequential scan of 800 parent
rows is genuinely the cheaper plan, and an index-only scan over rows
``autovacuum`` has not reached yet still visits the heap. Asserting the verdict
here would assert the fixture. The verdict belongs to a run against the seed
(``python -m app.ops.explain_hot_paths run``); the index-in-the-plan question
is the one a test of the SCHEMA can answer, and it is the question these three
migrations were written to answer.

**Volume is still the point.** A sequential scan of twelve rows is CHEAPER than
descending a btree, and PostgreSQL will choose it however many indexes exist --
so on a small table every assertion below passes whether the migration ran or
not. ``_ROWS`` is sized so the planner's own cost model prefers each index, and
``VACUUM ANALYZE`` follows the insert because a planner costing against an
empty ``pg_class``, over a visibility map nothing has set, does not choose the
plan production gets. This is 0.1's condition (3) -- "a query on an empty table
measures the index, not the platform" -- applied to a test.

Runs against the real local Compose PostgreSQL (see
``tests/integration/conftest.py``); auto-skips via ``live_db``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.ops.explain_hot_paths import CATALOGUE, HotPath, summarise
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]

#: Enough rows that the planner's cost model prefers each of the three indexes.
#: Measured, not guessed: at 2,000 rows PostgreSQL reads all three tables
#: sequentially whatever is indexed, at 4,000 it takes ``ix_doc_ws_file`` and
#: ``ix_files_space_bytes``, and ``ix_chunks_ws_point`` needs 8,000 before
#: reaching fifty rows by index beats scanning for them. Every row is rewritten
#: for every test in this file (``conftest.truncate_tables`` is autouse), so
#: the number is the smallest that makes all three assertions mean something.
_ROWS = 8000

#: Files and documents are spread over this many spaces. ONE space would make
#: the quota sum read the whole table, where a sequential scan is correct and
#: the covering index would prove nothing -- the same reason ``load_seed``
#: skews its tenants instead of dividing evenly.
_SPACES = 10

#: The three entries this file proves, each with the index its migration added
#: and the plan node it must appear as. ``Index Only Scan`` for the quota sum
#: is not decoration: the whole point of ``INCLUDE (size_bytes)`` is that the
#: aggregate never reaches the heap, and a plain ``Index Scan`` on the same
#: index would mean the payload column went unused.
_EXPECTED: dict[str, tuple[str, str, str]] = {
    "knowledge.chunks.parent_texts_for_chunk_ids": (
        "knowledge",
        "ix_chunks_ws_point",
        "Index Scan",
    ),
    "knowledge.documents.ids_for_files": ("knowledge", "ix_doc_ws_file", "Index Scan"),
    # The SAME index the namesake seek uses, and that is the point of
    # `files/0004`: one index answers both, so the planner has no cheaper
    # near-duplicate to take for the seek and drop `name_key` into a filter.
    "files.bytes_in_space": ("files", "ix_files_space_name", "Index Only Scan"),
}

#: What the two replacing migrations took away.
_SUPERSEDED = (("files", "ix_files_space"), ("conversations", "ix_conv_space"))

_INDEXES = sorted({(schema, index) for schema, index, _ in _EXPECTED.values()})


def _entry(name: str) -> HotPath:
    return next(entry for entry in CATALOGUE if entry.name == name)


# --------------------------------------------------------------------------- #
# Fixture: one tenant, at a volume that makes a plan mean something            #
# --------------------------------------------------------------------------- #


@pytest.fixture
def seeded(live_db: LiveDbDsns) -> Iterator[dict[str, Any]]:
    """Write the rows for ONE test, and hand back the ids its plans bind.

    Function-scoped rather than session-scoped, and not by preference:
    ``conftest.truncate_tables`` is autouse and empties every table after each
    test, so a session fixture would seed once and every test after the first
    would plan against nothing -- and pass, because a query that reads no rows
    reads no rows fast. Affordable because the insert is cheap (measured: 0.7s
    for 24,800 rows).
    """
    volume: dict[str, Any] = {
        "workspace_id": new_uuid7(),
        "document_id": new_uuid7(),
        "spaces": [new_uuid7() for _ in range(_SPACES)],
        "parents": [new_uuid7() for _ in range(_ROWS // 10)],
        "file_ids": [new_uuid7() for _ in range(20)],
        "point_ids": [new_uuid7() for _ in range(50)],
        "file_name": "report.pdf",
    }
    volume["space_id"] = volume["spaces"][0]
    asyncio.run(_write(live_db, volume))
    yield volume


async def _write(live_db: LiveDbDsns, volume: dict[str, Any]) -> None:
    app_engine = create_engine(DatabaseSettings(url=live_db.app), poolclass=NullPool)
    try:
        # As `app_rw`, under the tenant's own GUC -- `load_seed`'s rule for its
        # reason: rows written around the policies could be rows no tenant can
        # read back, and a plan over them would be a plan for a database the
        # application cannot produce.
        async with app_engine.connect() as conn, conn.begin():
            await _set_tenant(conn, volume["workspace_id"])
            await _insert_documents(conn, volume)
            await _insert_parent_chunks(conn, volume)
            await _insert_chunks(conn, volume)
            await _insert_files(conn, volume)
    finally:
        await app_engine.dispose()

    owner_engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with owner_engine.connect() as conn:
            # AUTOCOMMIT because VACUUM cannot run inside a transaction block.
            # ANALYZE alone is NOT enough: without the VACUUM half nothing
            # marks a page all-visible, so an index-only scan still fetches
            # every row from the heap and the covering index looks like it did
            # nothing. Measured -- `bytes_in_space` scored `uncovered` on
            # freshly inserted rows with up-to-date statistics.
            vacuuming = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await vacuuming.execute(
                text(
                    "VACUUM ANALYZE knowledge.chunks, knowledge.parent_chunks, "
                    "knowledge.documents, files.files"
                )
            )
    finally:
        await owner_engine.dispose()


async def _set_tenant(conn: AsyncConnection, workspace_id: str) -> None:
    await conn.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
    )


async def _insert_documents(conn: AsyncConnection, volume: dict[str, Any]) -> None:
    """``_ROWS`` documents, the first twenty carrying the file ids the plan
    binds. The rest exist so that reading the tenant's whole set is visibly
    different from reading twenty rows -- which is the entire question
    ``ix_doc_ws_file`` answers."""
    await conn.execute(
        text(
            """
            INSERT INTO knowledge.documents
                (id, workspace_id, file_id, status, chunk_count, space_id)
            VALUES (:id, :ws, :file_id, 'pending', 0, :space_id)
            """
        ),
        [
            {
                "id": volume["document_id"] if index == 0 else new_uuid7(),
                "ws": volume["workspace_id"],
                "file_id": (
                    volume["file_ids"][index] if index < len(volume["file_ids"]) else new_uuid7()
                ),
                "space_id": volume["spaces"][index % _SPACES],
            }
            for index in range(_ROWS)
        ],
    )


async def _insert_parent_chunks(conn: AsyncConnection, volume: dict[str, Any]) -> None:
    """Ten chunks per parent, the ratio ``P-34``'s widening produces.

    The Wave-0 seed leaves this table EMPTY by its own declaration, which is
    why the statement that joins it cannot be planned at scale there
    (``docs/capacity-status.md``). Here it is populated, because an inner join
    to an empty table never reaches the other side at all -- the plan would
    stop before touching ``knowledge.chunks``, and the assertion would be about
    nothing.
    """
    await conn.execute(
        text(
            """
            INSERT INTO knowledge.parent_chunks
                (id, document_id, workspace_id, seq, text, is_complete)
            VALUES (:id, :doc, :ws, :seq, :text, true)
            """
        ),
        [
            {
                "id": parent_id,
                "doc": volume["document_id"],
                "ws": volume["workspace_id"],
                "seq": seq,
                "text": f"parent {seq}",
            }
            for seq, parent_id in enumerate(volume["parents"])
        ],
    )


async def _insert_chunks(conn: AsyncConnection, volume: dict[str, Any]) -> None:
    """``_ROWS`` chunks on ONE document, the first fifty carrying the point ids
    a retrieval binds (``k`` at its ceiling). One document rather than many
    because ``uq_chunk_seq(document_id, seq)`` is the index this table's OTHER
    statements use, and concentrating the rows keeps this fixture from
    accidentally making that one look good too."""
    await conn.execute(
        text(
            """
            INSERT INTO knowledge.chunks
                (id, document_id, workspace_id, seq, text, token_count, collection,
                 point_id, parent_id)
            VALUES (:id, :doc, :ws, :seq, :text, 2, :collection, :point, :parent)
            """
        ),
        [
            {
                "id": new_uuid7(),
                "doc": volume["document_id"],
                "ws": volume["workspace_id"],
                "seq": index,
                "text": f"chunk {index}",
                "collection": f"kn-{volume['workspace_id']}",
                "point": (
                    volume["point_ids"][index] if index < len(volume["point_ids"]) else new_uuid7()
                ),
                "parent": volume["parents"][index % len(volume["parents"])],
            }
            for index in range(_ROWS)
        ],
    )


async def _insert_files(conn: AsyncConnection, volume: dict[str, Any]) -> None:
    """``_ROWS`` files spread over ``_SPACES`` spaces, so the quota sum reads a
    tenth of the table rather than all of it."""
    await conn.execute(
        text(
            """
            INSERT INTO files.files
                (id, workspace_id, name, content_type, size_bytes, storage_key, status,
                 uploaded_by, space_id)
            VALUES (:id, :ws, :name, 'application/pdf', 1024, :key, 'ready', :ws, :space_id)
            """
        ),
        [
            {
                "id": new_uuid7(),
                "ws": volume["workspace_id"],
                "name": volume["file_name"] if index == 0 else f"file-{index}.pdf",
                "key": f"{volume['workspace_id']}/{index}",
                "space_id": volume["spaces"][index % _SPACES],
            }
            for index in range(_ROWS)
        ],
    )


# --------------------------------------------------------------------------- #
# Connections and plans                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def app_conn(live_db: LiveDbDsns) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = create_engine(DatabaseSettings(url=live_db.app), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest.fixture
async def owner_conn(live_db: LiveDbDsns) -> AsyncIterator[AsyncConnection]:
    """The connection the DROP-and-roll-back assertions need, and the only
    reason they do not run as ``app_rw``.

    ``DROP INDEX`` requires ownership and ``app_rw`` owns nothing -- that least
    privilege is a property this repository defends everywhere else, so the
    test bends rather than the role. The substitution is sound HERE and not in
    general: ``aizzak_owner`` is ``NOSUPERUSER`` and not ``BYPASSRLS`` and
    every table below is ``FORCE ROW LEVEL SECURITY``, so the owner is a
    subject of ``tenant_isolation`` exactly as the application is
    (``files/0002_file_space.py`` records the same fact from the migration
    side). That it gets the SAME plan is asserted rather than assumed --
    ``test_the_owner_gets_the_same_plan_as_app_rw`` is what licenses reading
    anything into the drop results.
    """
    engine: AsyncEngine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


async def _explain(
    conn: AsyncConnection, name: str, volume: Mapping[str, Any], *, drop: tuple[str, str] | None
) -> dict[str, Any]:
    """One catalogue entry's plan, in a transaction that is ALWAYS rolled back.

    ``DROP INDEX`` is transactional in PostgreSQL, so ``drop`` removes the
    index for the length of this one statement and the rollback puts it back --
    no teardown to forget, and no state for the next test to inherit.
    """
    entry = _entry(name)
    bindings: dict[str, object] = {"page": 21}
    for need in entry.needs:
        value = volume[need]
        bindings[need] = list(value) if isinstance(value, list) else value
    async with conn.begin() as tx:
        await _set_tenant(conn, str(volume["workspace_id"]))
        if drop is not None:
            schema, index = drop
            await conn.execute(text(f"DROP INDEX {schema}.{index}"))
        raw = await conn.scalar(
            text(f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) {entry.sql}"), bindings
        )
        await tx.rollback()
    document = json.loads(raw) if isinstance(raw, str) else raw
    return dict(document[0])


def _index_scans(explained: Mapping[str, Any]) -> set[tuple[str, str]]:
    """``(node type, index name)`` for every node in the plan that used one."""
    found: set[tuple[str, str]] = set()

    def walk(node: Mapping[str, Any]) -> None:
        if "Index Name" in node:
            found.add((str(node["Node Type"]), str(node["Index Name"])))
        for child in node.get("Plans", ()) or ():
            walk(child)

    walk(explained["Plan"])
    return found


# --------------------------------------------------------------------------- #
# 1. the schema change landed                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("schema", "index"), _INDEXES)
async def test_the_step_s_index_is_applied(
    app_conn: AsyncConnection, schema: str, index: str
) -> None:
    found = await app_conn.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :i"),
        {"s": schema, "i": index},
    )
    assert found is not None, (
        f"{schema}.{index} is missing: capacity step 2.4's migration for this chain has not run"
    )


@pytest.mark.parametrize(("schema", "index"), _SUPERSEDED)
async def test_the_superseded_space_index_is_gone(
    app_conn: AsyncConnection, schema: str, index: str
) -> None:
    """``files/0004`` and ``conversations/0006`` REPLACE these; a survivor is
    an index every insert maintains that no predicate can lead with."""
    found = await app_conn.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :i"),
        {"s": schema, "i": index},
    )
    assert found is None, f"{schema}.{index} survived the migration that supersedes it"


# --------------------------------------------------------------------------- #
# 2. the planner uses it, and 3. the assertion has polarity                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(_EXPECTED))
async def test_the_planner_reaches_the_hot_path_through_the_new_index(
    app_conn: AsyncConnection, seeded: dict[str, Any], name: str
) -> None:
    _schema, index, node_type = _EXPECTED[name]
    scans = _index_scans(await _explain(app_conn, name, seeded, drop=None))
    assert (node_type, index) in scans, (
        f"{name}: expected a `{node_type}` on {index}; the plan used {sorted(scans) or 'no index'}"
    )


@pytest.mark.parametrize("name", sorted(_EXPECTED))
async def test_the_owner_gets_the_same_plan_as_app_rw(
    app_conn: AsyncConnection,
    owner_conn: AsyncConnection,
    seeded: dict[str, Any],
    name: str,
) -> None:
    """What licenses the drop assertion below to run as the owner."""
    as_app = _index_scans(await _explain(app_conn, name, seeded, drop=None))
    as_owner = _index_scans(await _explain(owner_conn, name, seeded, drop=None))
    assert as_app == as_owner


@pytest.mark.parametrize("name", sorted(_EXPECTED))
async def test_without_its_index_the_plan_costs_strictly_more(
    owner_conn: AsyncConnection, seeded: dict[str, Any], name: str
) -> None:
    """The assertion proven able to fail.

    Two things are checked, because either alone can be satisfied by accident:
    the index is no longer in the plan (so the planner really lost it), and the
    plan costs strictly more without it (so it was doing work, not merely being
    chosen).

    **Cost is BUFFERS and not rows, and the covering index is why.** Dropping
    ``ix_files_space_bytes`` does not change how many rows ``sum(size_bytes)``
    adds up -- it is the same space, the same 800 files either way. What it
    changes is where they are read from: an ``Index Only Scan`` touching 8
    blocks becomes a ``Bitmap Heap Scan`` touching 241, because the payload
    column is no longer in the index and every row costs a heap page. A rows
    comparison reports "no change" on the one entry whose whole point is that
    the rows stay the same, which is the same trap ``judge`` avoids by asking
    an aggregate about heap access instead of about ratios.
    """
    schema, index, node_type = _EXPECTED[name]
    with_index = summarise(await _explain(owner_conn, name, seeded, drop=None))
    without = summarise(await _explain(owner_conn, name, seeded, drop=(schema, index)))

    assert (node_type, index) not in _index_scans(
        await _explain(owner_conn, name, seeded, drop=(schema, index))
    ), f"{name}: the index survived a DROP inside the transaction that took this plan"
    assert without.shared_blocks > with_index.shared_blocks, (
        f"{name}: dropping {index} did not change what the plan costs "
        f"({with_index.shared_blocks} blocks either way), so the assertion above proves "
        "nothing about it"
    )


@pytest.mark.parametrize(("schema", "index"), _INDEXES)
async def test_a_dropped_index_comes_back_on_rollback(
    app_conn: AsyncConnection, schema: str, index: str
) -> None:
    """Asserted rather than assumed: every test after the drop ones depends on
    it, and a leaked ``DROP INDEX`` would surface as an unrelated failure three
    files later."""
    found = await app_conn.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :i"),
        {"s": schema, "i": index},
    )
    assert found is not None, f"{schema}.{index} did not come back after a rollback"


@pytest.mark.parametrize("name", sorted(_EXPECTED))
async def test_the_plan_is_taken_under_the_row_security_qual(
    app_conn: AsyncConnection, seeded: dict[str, Any], name: str
) -> None:
    """Every plan here is taken under ``SET LOCAL app.workspace_id``, and the
    proof is in the plan itself: the policy folds to a ``One-Time Filter``
    comparing the GUC against the bound tenant. A plan without it was taken by
    a role the policy does not apply to -- the exact mistake
    ``explain_hot_paths.refuse_privileged_role`` exists to prevent, asserted
    here on the plans this file actually took."""
    rendered = json.dumps(await _explain(app_conn, name, seeded, drop=None))
    assert "current_setting" in rendered, (
        f"{name}: no row-security qual in the plan -- this was not measured under RLS"
    )
