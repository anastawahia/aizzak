"""Live-Postgres proof for the three ``space_id`` migrations (plan step 4:
``files/0002_file_space.py`` · ``conversations/0004_conversation_space.py`` ·
``knowledge/0004_document_space.py``).

Two things are proven here, and only one of them could be read off the schema.

**The shape** -- column present, typed ``uuid``, still NULLable, and indexed
the way each table can actually be indexed.

**The backfill** -- which no assertion against the migrated database can
reach, because by the time any test runs the migration has already run, once,
against zero pre-existing rows. So this file DOWNGRADES the three chains one
revision, seeds rows that predate the column, upgrades again, and looks at
what the backfill made of them. That round trip is the only way the
per-workspace loop, the ``set_config`` inside it and the "one shared space"
rule get EXECUTED rather than merely read.

Alembic is driven SYNCHRONOUSLY here (a plain ``def`` test, ``asyncio.run``
for the SQL either side): ``migrations/env.py`` drives its own async engine
with ``asyncio.run``, so calling ``command.upgrade`` from inside a running
loop raises -- the same constraint ``app.ops.provision.provision`` documents.

Every chain is upgraded back in a ``finally``: a test that died holding the
schema one revision back would take the rest of the live suite with it.
"""

from __future__ import annotations

import asyncio
import os
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_REPO_ROOT = Path(__file__).resolve().parents[2]

# (chain, the revision this step sits on top of == the downgrade target).
_SPACE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("files", "0001_files"),
    ("conversations", "0003_conversation_files"),
    ("knowledge", "0003_summaries"),
)


def _move(owner_dsn: str, chain: str, target: str, *, down: bool) -> None:
    os.environ["DATABASE_URL"] = owner_dsn
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.cmd_opts = Namespace(x=[f"vts={chain}"])
    (command.downgrade if down else command.upgrade)(config, f"{chain}@{target}")


async def _run(
    owner_dsn: str,
    statements: list[tuple[str, dict[str, Any]]],
    *,
    workspace_id: str | None = None,
    fetch: bool = False,
) -> list[dict[str, Any]]:
    """Run statements as the OWNER, optionally inside a tenant GUC.

    ``workspace_id`` is not optional decoration: the owner is ``NOSUPERUSER``
    and not ``BYPASSRLS``, and every table below except
    ``workspace.workspaces`` is ``FORCE ROW LEVEL SECURITY`` -- without the
    GUC an INSERT is refused outright and a SELECT quietly returns nothing.
    It is the same wall the migration's backfill loop climbs the same way.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    rows: list[dict[str, Any]] = []
    try:
        async with engine.begin() as conn:
            if workspace_id is not None:
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
                )
            for sql, params in statements:
                result = await conn.execute(text(sql), params)
                if fetch:
                    rows = [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()
    return rows


async def _column(owner_dsn: str, schema: str, table: str, column: str) -> dict[str, Any] | None:
    rows = await _run(
        owner_dsn,
        [
            (
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c",
                {"s": schema, "t": table, "c": column},
            )
        ],
        fetch=True,
    )
    return rows[0] if rows else None


async def _indexdef(owner_dsn: str, schema: str, name: str) -> str | None:
    rows = await _run(
        owner_dsn,
        [
            (
                "SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :n",
                {"s": schema, "n": name},
            )
        ],
        fetch=True,
    )
    return str(rows[0]["indexdef"]) if rows else None


async def test_all_three_tables_carry_a_uuid_space_id(live_db: LiveDbDsns) -> None:
    for schema, table in (
        ("files", "files"),
        ("conversations", "conversations"),
        ("knowledge", "documents"),
    ):
        column = await _column(live_db.owner, schema, table, "space_id")
        assert column is not None, f"{schema}.{table}.space_id is missing"
        assert column["data_type"] == "uuid"
        # NULLable ON PURPOSE, and only until plan §4 row 8-b: nothing writes
        # the column until steps 6-8, so a NOT NULL here would refuse every
        # INSERT on three tables for four steps running. When 8-b lands this
        # assertion is the one to FLIP -- not the one to delete.
        assert column["is_nullable"] == "YES"


async def test_the_two_soft_deleted_tables_index_live_rows_only(live_db: LiveDbDsns) -> None:
    for schema, name in (("files", "ix_files_space"), ("conversations", "ix_conv_space")):
        indexdef = await _indexdef(live_db.owner, schema, name)
        assert indexdef is not None, f"{name} is missing"
        assert "space_id" in indexdef
        assert "WHERE (deleted_at IS NULL)" in indexdef


async def test_the_documents_index_is_total_because_the_table_has_no_soft_delete(
    live_db: LiveDbDsns,
) -> None:
    """The plan's §3.2 table calls all three indexes partial; this one cannot
    be, and the reason is a column that does not exist rather than a taste.
    Asserting the ABSENCE of ``deleted_at`` next to the total index is what
    stops a later reader from "restoring" the predicate into a syntax error --
    and would equally catch someone giving ``knowledge.documents`` soft delete
    without coming back to this index."""
    assert await _column(live_db.owner, "knowledge", "documents", "deleted_at") is None

    indexdef = await _indexdef(live_db.owner, "knowledge", "ix_kndoc_space")
    assert indexdef is not None, "ix_kndoc_space is missing"
    assert "space_id" in indexdef
    assert "WHERE" not in indexdef


def test_rows_that_predate_the_column_are_migrated_into_one_shared_space(
    live_db: LiveDbDsns,
) -> None:
    """A file, a conversation and a document that existed BEFORE the column
    all come out of the migration in the SAME space.

    That is decision 1 ("a space's conversations see all of that space's
    files") expressed as data: three backfills each minting their own default
    space would migrate a workspace into one where its conversations can see
    none of its files -- a state nothing reports and no later step repairs.
    """
    owner = live_db.owner
    workspace_id = new_uuid7()
    file_id = new_uuid7()
    conversation_id = new_uuid7()
    document_id = new_uuid7()

    # `workspace.workspaces` is the one table here with no RLS at all
    # (`workspace/0001_workspace.py` R2) -- which is also why the backfill can
    # enumerate workspaces from it before any GUC is set.
    asyncio.run(
        _run(
            owner,
            [
                (
                    "INSERT INTO workspace.workspaces (id, owner_user_id, name) "
                    "VALUES (:id, :owner, 'space-backfill probe')",
                    {"id": workspace_id, "owner": new_uuid7()},
                )
            ],
        )
    )

    try:
        for chain, previous in _SPACE_MIGRATIONS:
            _move(owner, chain, previous, down=True)

        asyncio.run(
            _run(
                owner,
                [
                    (
                        "INSERT INTO files.files "
                        "(id, workspace_id, name, content_type, size_bytes, storage_key) "
                        "VALUES (:id, :ws, 'old.txt', 'text/plain', 3, :key)",
                        {"id": file_id, "ws": workspace_id, "key": f"{workspace_id}/{file_id}"},
                    ),
                    (
                        "INSERT INTO conversations.conversations (id, workspace_id, agent_key) "
                        "VALUES (:id, :ws, 'rag_agent')",
                        {"id": conversation_id, "ws": workspace_id},
                    ),
                    (
                        "INSERT INTO knowledge.documents (id, workspace_id, file_id) "
                        "VALUES (:id, :ws, :file_id)",
                        {"id": document_id, "ws": workspace_id, "file_id": file_id},
                    ),
                ],
                workspace_id=workspace_id,
            )
        )

        for chain, _ in _SPACE_MIGRATIONS:
            _move(owner, chain, "head", down=False)

        migrated = asyncio.run(
            _run(
                owner,
                [
                    (
                        """
                        SELECT s.id AS space_id, s.name,
                          (SELECT space_id FROM files.files WHERE id = :file_id)        AS f,
                          (SELECT space_id FROM conversations.conversations
                            WHERE id = :conversation_id)                                AS c,
                          (SELECT space_id FROM knowledge.documents WHERE id = :doc_id) AS d
                        FROM spaces.spaces s WHERE s.workspace_id = :ws
                        """,
                        {
                            "ws": workspace_id,
                            "file_id": file_id,
                            "conversation_id": conversation_id,
                            "doc_id": document_id,
                        },
                    )
                ],
                workspace_id=workspace_id,
                fetch=True,
            )
        )

        # ONE row: one space for the whole workspace, not one per chain.
        assert len(migrated) == 1, f"expected exactly one migrated space, got {migrated}"
        row = migrated[0]
        assert row["name"] == "General"
        assert row["f"] is not None, "the pre-existing file was left without a space"
        assert row["f"] == row["space_id"]
        assert row["c"] == row["space_id"]
        assert row["d"] == row["space_id"]
    finally:
        for chain, _ in _SPACE_MIGRATIONS:
            _move(owner, chain, "head", down=False)
        asyncio.run(
            _run(
                owner,
                [
                    ("DELETE FROM knowledge.documents WHERE id = :id", {"id": document_id}),
                    (
                        "DELETE FROM conversations.conversations WHERE id = :id",
                        {"id": conversation_id},
                    ),
                    ("DELETE FROM files.files WHERE id = :id", {"id": file_id}),
                    ("DELETE FROM spaces.spaces WHERE workspace_id = :ws", {"ws": workspace_id}),
                ],
                workspace_id=workspace_id,
            )
        )
        asyncio.run(
            _run(
                owner,
                [("DELETE FROM workspace.workspaces WHERE id = :id", {"id": workspace_id})],
            )
        )
