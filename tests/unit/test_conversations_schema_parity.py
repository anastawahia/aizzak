"""The ``conversations.conversations`` column set, spelled the same in all
three places that spell it (ب-9 of
``docs/rag-agent-scenarios-implementation-plan.md`` §7).

A column exists three times in this repository and nothing compared the three:

* the **migration chain** under ``migrations/versions/conversations/``, which
  is what the database actually gets;
* the adapter's **SQLAlchemy table**, which is what every statement is compiled
  against;
* ``docs/design/01-data-model.md`` §2.4, which `06 §4` and the module's own
  docstrings cite as binding.

The failure this guards is quiet and expensive. A ``Table`` naming a column no
migration adds compiles fine and fails at runtime, in the tenant's database,
on the first write — and ``save`` deliberately writes EVERY mutable column, so
one missing DDL statement breaks rename, model-pin, soft-delete and the
pending clarification at once. A migration adding a column the adapter never
names is the opposite and worse: it fails nothing, so the field is simply
never persisted and the feature is a no-op nobody notices.

``test_port_contract_doc.py`` is this test's precedent and states the general
form of the argument: the wire shape had a guard and the internal contract did
not, so the internal one drifted. The same was true of the schema.

Text comparison, not a database. This is a unit test: it reads the migration
files as source and the ``Table`` as a Python object, and asserts they name
the same columns. Whether Postgres accepts the DDL is an integration
question and is answered by the live suites.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.modules.conversations.adapters.sql_repository import conversations, messages

_ROOT = Path(__file__).resolve().parents[2]
_CHAIN = _ROOT / "migrations" / "versions" / "conversations"
_DATA_MODEL = _ROOT / "docs" / "design" / "01-data-model.md"

# Columns the ADAPTER hydrates but the table does not hold. `message_count` is
# `COALESCE(MAX(seq), 0)` over the thread's messages (the entity says so at
# length); it is a computed field on the aggregate, so its absence from the
# DDL is correct rather than a gap.
_NOT_A_COLUMN = frozenset({"message_count"})


def _chain_sql() -> str:
    """Every migration in the conversations chain, concatenated.

    The whole chain and not the head revision: a column is added by whichever
    revision added it, and this module has four of them plus the one ب-9 adds.
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(_CHAIN.glob("0*.py")))


def _adapter_columns() -> set[str]:
    return {column.name for column in conversations.columns}


def test_every_column_the_adapter_writes_is_created_by_the_chain() -> None:
    """⚠️ The expensive direction. ``save`` writes every mutable column in one
    statement, so a single missing ``ADD COLUMN`` takes down four unrelated
    operations together — and only against a real database, which is to say
    not in this suite and not in review."""
    sql = _chain_sql()

    missing = sorted(
        name for name in _adapter_columns() if not re.search(rf"\b{re.escape(name)}\b", sql)
    )

    assert missing == [], f"columns the adapter names but no migration creates: {missing}"


def test_every_column_the_chain_creates_is_named_by_the_adapter() -> None:
    """The silent direction. A column nothing reads or writes fails no test
    and breaks no request — the feature behind it is simply never persisted,
    which is precisely how a stored preference the platform ignores comes to
    exist."""
    added = set(
        re.findall(
            r"ALTER TABLE conversations\.conversations ADD COLUMN (\w+)",
            _chain_sql(),
        )
    )

    assert added - _adapter_columns() - _NOT_A_COLUMN == set()


def test_the_pending_clarification_column_is_spelled_the_same_everywhere() -> None:
    """ب-9's own column, named explicitly rather than left to the sweeps above:
    it is the one this wave added, and a typo in any one of the three places
    would produce a different failure in each."""
    assert "pending_clarification" in _adapter_columns()
    assert "ADD COLUMN pending_clarification jsonb" in _chain_sql()
    assert "pending_clarification" in _DATA_MODEL.read_text(encoding="utf-8")


def test_the_column_is_nullable_because_nothing_pending_has_one_spelling() -> None:
    """NULL is "no question outstanding", and it is the ONLY spelling of it:
    the adapter writes NULL rather than ``[]`` for an empty list, so no future
    predicate has to remember to test for both."""
    column = conversations.columns["pending_clarification"]

    assert column.nullable
    assert column.server_default is None


# --------------------------------------------------------------------------- #
# conversations.messages (ث-1)                                                 #
# --------------------------------------------------------------------------- #
_MESSAGES_DDL = re.compile(r"CREATE TABLE conversations\.messages \((.*?)\n\s*\)", re.DOTALL)
_MESSAGES_ADDED = re.compile(r"ALTER TABLE conversations\.messages ADD COLUMN (\w+)")
_CONSTRAINT_WORDS = frozenset({"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "REFERENCES"})


def _messages_chain_columns() -> set[str]:
    """Columns of ``conversations.messages`` as the chain builds it: the ones in
    the ``CREATE TABLE`` of ``0001`` plus every later ``ADD COLUMN``."""
    sql = _chain_sql()
    match = _MESSAGES_DDL.search(sql)
    assert match is not None, "0001 no longer creates conversations.messages in the expected form"
    created = set()
    for line in match.group(1).splitlines():
        words = line.strip().split()
        if words and words[0].upper() not in _CONSTRAINT_WORDS and not words[0].startswith("--"):
            created.add(words[0])
    return created | set(_MESSAGES_ADDED.findall(sql))


def test_every_messages_column_the_adapter_names_is_created_by_the_chain() -> None:
    """Same expensive direction as for ``conversations``: ``append_message``
    inserts every column, so one missing ``ADD COLUMN`` breaks every append."""
    adapter = {column.name for column in messages.columns}

    assert adapter - _messages_chain_columns() == set()


def test_every_messages_column_the_chain_creates_is_named_by_the_adapter() -> None:
    """The silent direction: a column nothing reads or writes is never stored."""
    adapter = {column.name for column in messages.columns}

    assert _messages_chain_columns() - adapter == set()


def test_the_citations_column_is_spelled_the_same_everywhere() -> None:
    assert "citations" in {column.name for column in messages.columns}
    assert "ALTER TABLE conversations.messages ADD COLUMN citations jsonb NULL" in _chain_sql()
    assert "citations" in _DATA_MODEL.read_text(encoding="utf-8")


def test_citations_is_nullable_without_a_default_and_writes_sql_null_for_none() -> None:
    """NULL is "not recorded" and ``'[]'`` is "no sources": two different
    statements. ``none_as_null`` is what stops Python ``None`` from being
    stored as the JSON literal ``null`` -- a third spelling of the first."""
    column = messages.columns["citations"]

    assert column.nullable
    assert column.server_default is None
    assert column.type.none_as_null is True  # type: ignore[attr-defined]


def test_the_citations_migration_downgrade_drops_the_column() -> None:
    source = (_CHAIN / "0007_message_citations.py").read_text(encoding="utf-8")
    downgrade = source.split("def downgrade()", 1)[1]

    assert "DROP COLUMN IF EXISTS citations" in downgrade


# --------------------------------------------------------------------------- #
# conversations.messages status / fallback (ث-2)                               #
# --------------------------------------------------------------------------- #
def _message_status_migration() -> str:
    return (_CHAIN / "0008_message_status.py").read_text(encoding="utf-8")


def test_the_status_and_fallback_columns_are_spelled_the_same_everywhere() -> None:
    names = {column.name for column in messages.columns}
    doc = _DATA_MODEL.read_text(encoding="utf-8")

    assert {"status", "fallback"} <= names
    assert "ALTER TABLE conversations.messages ADD COLUMN status text NULL" in _chain_sql()
    assert "ALTER TABLE conversations.messages ADD COLUMN fallback jsonb NULL" in _chain_sql()
    assert "status          text NULL" in doc
    assert "fallback        jsonb NULL" in doc
    assert "ck_messages_status" in doc


def test_status_and_fallback_are_nullable_without_a_default() -> None:
    """NULL is "not recorded" -- the meaning of every row that predates 0008 --
    so there is no DEFAULT that could call an old message complete."""
    for name in ("status", "fallback"):
        column = messages.columns[name]

        assert column.nullable
        assert column.server_default is None


def test_fallback_writes_sql_null_for_none() -> None:
    """``none_as_null`` keeps Python ``None`` from being stored as the JSON
    literal ``null``, a second spelling of "not recorded"."""
    assert messages.columns["fallback"].type.none_as_null is True  # type: ignore[attr-defined]


def test_the_status_check_is_added_not_valid_and_never_validated() -> None:
    """Deliberate: every existing row is NULL by construction, so a VALIDATE
    would be a full scan of the largest table under ACCESS EXCLUSIVE that
    proves nothing (see the migration docstring)."""
    source = _message_status_migration()
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]

    assert "ck_messages_status" in upgrade
    assert "CHECK (status IN ('complete', 'partial')) NOT VALID" in upgrade
    code = "\n".join(line for line in upgrade.splitlines() if not line.strip().startswith("#"))
    assert "VALIDATE" not in code
    # one ALTER per column: the sweeps above read "ADD COLUMN <name>" per statement
    assert code.count("ADD COLUMN") == 2


def test_the_status_migration_downgrade_drops_the_constraint_and_both_columns() -> None:
    downgrade = _message_status_migration().split("def downgrade()", 1)[1]

    assert "DROP CONSTRAINT IF EXISTS ck_messages_status" in downgrade
    assert "DROP COLUMN IF EXISTS status" in downgrade
    assert "DROP COLUMN IF EXISTS fallback" in downgrade
    # the constraint goes before the column it constrains
    assert downgrade.index("ck_messages_status") < downgrade.index("DROP COLUMN")


def test_the_status_migration_continues_the_chain() -> None:
    source = _message_status_migration()

    assert 'revision = "0008_message_status"' in source
    assert 'down_revision = "0007_message_citations"' in source


# --------------------------------------------------------------------------- #
# conversations.messages reply_to (ث-3)                                        #
# --------------------------------------------------------------------------- #
def _message_reply_to_migration() -> str:
    return (_CHAIN / "0009_message_reply_to.py").read_text(encoding="utf-8")


def test_the_reply_to_column_is_spelled_the_same_everywhere() -> None:
    doc = _DATA_MODEL.read_text(encoding="utf-8")

    assert "reply_to" in {column.name for column in messages.columns}
    assert "ALTER TABLE conversations.messages ADD COLUMN reply_to uuid NULL" in _chain_sql()
    assert "reply_to        uuid NULL" in doc


def test_reply_to_is_nullable_without_a_default() -> None:
    """NULL is "not recorded" -- the meaning of every row that predates 0009 --
    so there is no DEFAULT that could invent a question for an old answer."""
    column = messages.columns["reply_to"]

    assert column.nullable
    assert column.server_default is None


def test_the_reply_to_migration_downgrade_drops_the_column() -> None:
    downgrade = _message_reply_to_migration().split("def downgrade()", 1)[1]

    assert "DROP COLUMN IF EXISTS reply_to" in downgrade


def test_the_reply_to_migration_continues_the_chain() -> None:
    source = _message_reply_to_migration()

    assert 'revision = "0009_message_reply_to"' in source
    assert 'down_revision = "0008_message_status"' in source
