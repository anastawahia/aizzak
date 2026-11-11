"""Local proof for ``conversations/0009_message_reply_to`` (gap ث-3).

No database: the migration file is loaded with ``importlib``, its ``op`` is
replaced by a recorder, and ``upgrade()`` / ``downgrade()`` run for real, so the
exact statements they would send are compared literally. Whether Postgres
accepts them is the job of ``tests/integration/test_message_reply_to_migration_live.py``
(CI ``integration`` only).

The second half is the retro lesson of ث-2: its live test rendered a SQL
template with ``str.format`` while the SQL held JSON braces, and that broke only
in CI. The live test of this wave uses fixed strings with bound parameters; this
file imports its SQL constants and checks none of them contains a brace.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests.integration import test_message_reply_to_migration_live as live

_FILE = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "conversations"
    / "0009_message_reply_to.py"
)


class _RecordingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_migration_0009_message_reply_to", _FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, _RecordingOp]:
    module = _load()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    return module, recorder


def test_upgrade_adds_the_nullable_uuid_column_and_nothing_else(
    migration: tuple[ModuleType, _RecordingOp],
) -> None:
    module, recorder = migration

    module.upgrade()

    assert recorder.statements == [
        "ALTER TABLE conversations.messages ADD COLUMN reply_to uuid NULL;"
    ]


def test_downgrade_drops_the_column_and_nothing_else(
    migration: tuple[ModuleType, _RecordingOp],
) -> None:
    module, recorder = migration

    module.downgrade()

    assert recorder.statements == [
        "ALTER TABLE conversations.messages DROP COLUMN IF EXISTS reply_to;"
    ]


def test_upgrade_is_catalog_only() -> None:
    """No DEFAULT (a rewrite on old servers), no constraint, no index, no
    backfill: the column is NULL = "not recorded" for every existing row."""
    module = _load()
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    sql = " ".join(recorder.statements).upper()

    for forbidden in (
        "DEFAULT",
        "CONSTRAINT",
        "REFERENCES",
        "CHECK",
        "INDEX",
        "UPDATE",
        "NOT NULL",
    ):
        assert forbidden not in sql


def test_the_migration_continues_the_chain() -> None:
    module = _load()

    assert module.revision == "0009_message_reply_to"
    assert module.down_revision == "0008_message_status"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_the_live_test_sql_constants_have_no_template_braces() -> None:
    """Fixed strings with bound parameters only: a brace would be a JSON literal
    or a ``str.format`` field, the exact mistake that broke ث-2 in CI."""
    constants = {
        name: value
        for name, value in vars(live).items()
        if name.startswith("_") and name.isupper() and isinstance(value, str)
    }
    sql_like = {
        name: value
        for name, value in constants.items()
        if value.lstrip().upper().startswith(("SELECT", "INSERT", "DELETE", "UPDATE"))
    }

    assert {
        "_COLUMN",
        "_CONVERSATION",
        "_INSERT_MESSAGE",
        "_INSERT_MESSAGE_REPLY_TO",
        "_SELECT_REPLY_TO",
        "_DELETE_MESSAGES",
        "_DELETE_CONVERSATION",
    } <= set(sql_like)
    for name, value in sql_like.items():
        assert "{" not in value and "}" not in value, name


def test_the_live_test_inserts_bind_their_content() -> None:
    """The JSON content travels as a bound parameter cast to jsonb."""
    for sql in (live._INSERT_MESSAGE, live._INSERT_MESSAGE_REPLY_TO):
        assert "CAST(:content AS jsonb)" in sql
    assert ":reply_to" in live._INSERT_MESSAGE_REPLY_TO
    assert ":reply_to" not in live._INSERT_MESSAGE
