"""conversations: an assistant message remembers the sources it was answered from.

Adds ``conversations.messages.citations`` (01-data-model §2.4), the column
behind gap ث-1 of ``docs/delivery/data-search-android/``.

**What it is for.** The RAG agent already emits its sources on the SSE ``final``
frame, but nothing stored them: reloading a thread returned the answer text
with no sources, so a client could show citations on a live reply and lose them
on the next open. This column keeps them with the message they belong to.

**Why a column and not a key inside ``content``.** Android was promised that
``content`` is closed -- text plus attachment ids -- and a new key would change
a shape clients already decode (ق-2). A sibling column leaves ``content``
untouched. It also retires the premise recorded in ``0005`` ("a message has no
structured half"): a message now has exactly one, ``citations``.

``jsonb`` because an ORDERED list of objects has to survive the round trip.

``NULL`` means "not recorded", which is what every row that predates this
column means, so there is no backfill and no default. ``'[]'`` is a different
statement: the agent answered and said it had no sources. The adapter keeps the
two apart on purpose.

No index: the column is a projection read alongside its row, never a predicate.

No CHECK constraint: the rules (UUID-shaped ids, ``rank >= 1``, the cap on the
list) live in ``domain.Citation``. A CHECK on ``jsonb`` would restate half of
them in a second language and drift from the first.

Copied data: ``file_name`` is a snapshot of what the chunk carried at answer
time (ق-3), never refreshed from the ``files`` module, so a later rename or
delete does not rewrite history.

Row-level security is unchanged: ``tenant_isolation`` and FORCE RLS apply to
the whole row, so they cover the new column, and the module roles hold
table-level grants only.

Rollback: ``downgrade`` drops the column and therefore DISCARDS every stored
citation. It is lossless only for rows that never had one.

Revision ID: 0007_message_citations
Revises: 0006_space_count_covering
"""

from __future__ import annotations

from alembic import op

revision = "0007_message_citations"
down_revision = "0006_space_count_covering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations.messages ADD COLUMN citations jsonb NULL;")


def downgrade() -> None:
    # Data loss by design: every stored citation is discarded.
    op.execute("ALTER TABLE conversations.messages DROP COLUMN IF EXISTS citations;")
