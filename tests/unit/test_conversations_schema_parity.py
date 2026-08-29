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

from app.modules.conversations.adapters.sql_repository import conversations

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
