"""Live-Postgres proof for ``conversations/0009_message_reply_to`` (gap ث-3).

The unit twin (``tests/unit/test_message_reply_to_migration.py``) reads the
migration as text and runs it against a fake ``op``; only a database can say
whether the DDL is accepted, whether ``downgrade`` really undoes it, and whether
a message written BEFORE the column existed survives the round trip with
``NULL`` in it.

The round trip is ``0009 -> 0008 -> 0009`` on the ``conversations`` chain, with
an old-style row (inserted while the column is absent) carried across.

Every SQL constant here is a FIXED string with bound parameters. The message
content is a bound ``text`` parameter cast to ``jsonb``, so no JSON braces ever
sit in the SQL text and nothing is rendered with ``str.format`` (the retro of
ث-2: a format string with JSON braces broke only in CI).

Alembic is driven SYNCHRONOUSLY (a plain ``def`` test, ``asyncio.run`` for the
SQL either side): ``migrations/env.py`` drives its own async engine with
``asyncio.run``, so calling ``command.upgrade`` from inside a running loop
raises. The chain is upgraded back to ``head`` in a ``finally``: a test that
died holding the schema one revision back would take the rest of the live suite
with it. Runs only in CI job ``integration`` (``live_db``).
"""

from __future__ import annotations

import asyncio
import json
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
_BEFORE = "0008_message_status"
_AFTER = "0009_message_reply_to"

_COLUMN = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'conversations' AND table_name = 'messages' "
    "AND column_name = 'reply_to'"
)
_CONVERSATION = (
    "INSERT INTO conversations.conversations (id, workspace_id, agent_key) "
    "VALUES (:id, :ws, 'rag_agent')"
)
# No JSON literal in the text: the content is a bound parameter.
_INSERT_MESSAGE = (
    "INSERT INTO conversations.messages "
    "(id, conversation_id, workspace_id, role, content, seq) "
    "VALUES (:id, :cid, :ws, 'assistant', CAST(:content AS jsonb), :seq)"
)
_INSERT_MESSAGE_REPLY_TO = (
    "INSERT INTO conversations.messages "
    "(id, conversation_id, workspace_id, role, content, seq, reply_to) "
    "VALUES (:id, :cid, :ws, 'assistant', CAST(:content AS jsonb), :seq, :reply_to)"
)
_SELECT_REPLY_TO = (
    "SELECT reply_to, reply_to IS NULL AS is_null "
    "FROM conversations.messages WHERE conversation_id = :cid ORDER BY seq"
)
_DELETE_MESSAGES = "DELETE FROM conversations.messages WHERE conversation_id = :c"
_DELETE_CONVERSATION = "DELETE FROM conversations.conversations WHERE id = :c"
_CONTENT = json.dumps({"text": "t", "attachments": []})


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


def _insert_message(owner_dsn: str, ws: str, cid: str, seq: int, **extra: Any) -> str:
    """Insert one assistant message; ``reply_to`` (if given) selects the second
    fixed statement. Returns the new message id."""
    message_id = new_uuid7()
    sql = _INSERT_MESSAGE_REPLY_TO if "reply_to" in extra else _INSERT_MESSAGE
    params = {"id": message_id, "cid": cid, "ws": ws, "seq": seq, "content": _CONTENT, **extra}
    asyncio.run(_run(owner_dsn, [(sql, params)], workspace_id=ws))
    return message_id


def test_downgrade_then_upgrade_round_trips_and_an_old_row_survives(live_db: LiveDbDsns) -> None:
    owner = live_db.owner
    ws = new_uuid7()
    cid = new_uuid7()
    asyncio.run(_run(owner, [(_CONVERSATION, {"id": cid, "ws": ws})], workspace_id=ws))

    try:
        # --- down: the column is gone ---------------------------------------
        _move(owner, _BEFORE, down=True)
        assert _query(owner, _COLUMN) == []

        # An old-style row: written while the column does not exist.
        old_id = _insert_message(owner, ws, cid, 1)

        # --- up again: the column is back, uuid, nullable --------------------
        _move(owner, _AFTER, down=False)
        assert _query(owner, _COLUMN) == [
            {"column_name": "reply_to", "data_type": "uuid", "is_nullable": "YES"}
        ]

        # The old row crossed the round trip and means "not recorded".
        survivors = asyncio.run(
            _run(owner, [(_SELECT_REPLY_TO, {"cid": cid})], workspace_id=ws, fetch=True)
        )
        assert [row["is_null"] for row in survivors] == [True]

        # The column accepts a uuid on a NEW row (no FK, no CHECK: nothing else
        # is asserted here -- integrity lives in the adapter's guarded insert).
        _insert_message(owner, ws, cid, 2, reply_to=old_id)
        rows = asyncio.run(
            _run(owner, [(_SELECT_REPLY_TO, {"cid": cid})], workspace_id=ws, fetch=True)
        )
        assert [row["is_null"] for row in rows] == [True, False]
        assert str(rows[1]["reply_to"]) == old_id
    finally:
        # Restore head first; the row cleanup still runs if that step fails.
        try:
            _move(owner, "head", down=False)
        finally:
            asyncio.run(
                _run(
                    owner,
                    [(_DELETE_MESSAGES, {"c": cid}), (_DELETE_CONVERSATION, {"c": cid})],
                    workspace_id=ws,
                )
            )
