"""Live-Postgres proof for ``conversations/0008_message_status`` (gap ث-2).

The unit suite reads the migration as text; only a database can say whether the
DDL is accepted, whether ``downgrade`` really undoes it, and whether a message
written BEFORE the columns existed survives the round trip with ``NULL`` in
both. The retro of ث-1 recorded that its ``downgrade`` was never executed; this
file executes this one.

The round trip is ``0008 -> 0007 -> 0008`` on the ``conversations`` chain, with
an old-style row (inserted while the columns are absent) carried across.

``ck_messages_status`` is added ``NOT VALID`` and never validated (see the
migration docstring), so ``pg_constraint.convalidated`` must stay ``false``
while the constraint still refuses a bad value on a NEW row (SQLSTATE 23514).

Alembic is driven SYNCHRONOUSLY (a plain ``def`` test, ``asyncio.run`` for the
SQL either side): ``migrations/env.py`` drives its own async engine with
``asyncio.run``, so calling ``command.upgrade`` from inside a running loop
raises. The chain is upgraded back to ``head`` in a ``finally``: a test that
died holding the schema one revision back would take the rest of the live suite
with it. Runs only in CI job ``integration`` (``live_db``).
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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BEFORE = "0007_message_citations"
_AFTER = "0008_message_status"

_COLUMNS = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'conversations' AND table_name = 'messages' "
    "AND column_name IN ('status', 'fallback') ORDER BY column_name"
)
_CONSTRAINT = (
    "SELECT convalidated FROM pg_constraint "
    "WHERE conname = 'ck_messages_status' "
    "AND conrelid = 'conversations.messages'::regclass"
)
_INSERT_MESSAGE = (
    "INSERT INTO conversations.messages "
    "(id, conversation_id, workspace_id, role, content, seq{extra_cols}) "
    "VALUES (:id, :cid, :ws, 'assistant', "
    # Rendered with ``str.format``: the JSON literal's braces MUST be doubled.
    'CAST(\'{{"text": "t", "attachments": []}}\' AS jsonb), :seq{extra_vals})'
)


def _move(owner_dsn: str, target: str, *, down: bool) -> None:
    os.environ["DATABASE_URL"] = owner_dsn
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.cmd_opts = Namespace(x=["vts=conversations"])
    (command.downgrade if down else command.upgrade)(config, f"conversations@{target}")


async def _run(
    owner_dsn: str,
    statements: list[tuple[str, dict[str, Any]]],
    *,
    workspace_id: str | None = None,
    fetch: bool = False,
) -> list[dict[str, Any]]:
    """Run statements as the OWNER in one transaction, optionally inside a tenant
    GUC (``conversations.*`` is FORCE ROW LEVEL SECURITY: without it an INSERT is
    refused and a SELECT quietly returns nothing). Returns the LAST result."""
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


def _query(owner_dsn: str, sql: str, **params: Any) -> list[dict[str, Any]]:
    return asyncio.run(_run(owner_dsn, [(sql, params)], fetch=True))


def _insert_message(owner_dsn: str, ws: str, cid: str, seq: int, **columns: Any) -> None:
    """Insert one assistant message; ``columns`` names the extra columns."""
    extra_cols = "".join(f", {name}" for name in columns)
    extra_vals = "".join(f", :{name}" for name in columns)
    sql = _INSERT_MESSAGE.format(extra_cols=extra_cols, extra_vals=extra_vals)
    asyncio.run(
        _run(
            owner_dsn,
            [(sql, {"id": new_uuid7(), "cid": cid, "ws": ws, "seq": seq, **columns})],
            workspace_id=ws,
        )
    )


def test_downgrade_then_upgrade_round_trips_and_an_old_row_survives(live_db: LiveDbDsns) -> None:
    owner = live_db.owner
    ws = new_uuid7()
    cid = new_uuid7()
    asyncio.run(
        _run(
            owner,
            [
                (
                    "INSERT INTO conversations.conversations (id, workspace_id, agent_key) "
                    "VALUES (:id, :ws, 'rag_agent')",
                    {"id": cid, "ws": ws},
                )
            ],
            workspace_id=ws,
        )
    )

    try:
        # --- down: the columns and the constraint are gone ------------------
        _move(owner, _BEFORE, down=True)
        assert _query(owner, _COLUMNS) == []
        assert _query(owner, _CONSTRAINT) == []

        # An old-style row: written while the columns do not exist.
        _insert_message(owner, ws, cid, 1)

        # --- up again: columns back, nullable, constraint NOT VALID ---------
        _move(owner, _AFTER, down=False)
        columns = {row["column_name"]: row for row in _query(owner, _COLUMNS)}
        assert set(columns) == {"status", "fallback"}
        assert columns["status"]["data_type"] == "text"
        assert columns["fallback"]["data_type"] == "jsonb"
        assert columns["status"]["is_nullable"] == "YES"
        assert columns["fallback"]["is_nullable"] == "YES"
        assert _query(owner, _CONSTRAINT) == [{"convalidated": False}]

        # The old row crossed the round trip and means "not recorded".
        survivors = asyncio.run(
            _run(
                owner,
                [
                    (
                        "SELECT status IS NULL AS s, fallback IS NULL AS f "
                        "FROM conversations.messages WHERE conversation_id = :cid",
                        {"cid": cid},
                    )
                ],
                workspace_id=ws,
                fetch=True,
            )
        )
        assert survivors == [{"s": True, "f": True}]

        # --- the constraint is enforced on NEW rows although never validated -
        with pytest.raises(DBAPIError) as refused:
            _insert_message(owner, ws, cid, 2, status="error")
        assert "ck_messages_status" in str(refused.value)
        assert "23514" in str(getattr(refused.value.orig, "sqlstate", "")) or (
            "CheckViolation" in str(refused.value)
        )
        _insert_message(owner, ws, cid, 2, status="partial")
        _insert_message(owner, ws, cid, 3, status="complete")
    finally:
        # Restore head first; the row cleanup still runs if that step fails.
        try:
            _move(owner, "head", down=False)
        finally:
            asyncio.run(
                _run(
                    owner,
                    [
                        (
                            "DELETE FROM conversations.messages WHERE conversation_id = :c",
                            {"c": cid},
                        ),
                        ("DELETE FROM conversations.conversations WHERE id = :c", {"c": cid}),
                    ],
                    workspace_id=ws,
                )
            )
