"""conversations: an assistant message remembers how its turn ended.

Adds ``conversations.messages.status`` and ``conversations.messages.fallback``
(01-data-model §2.4), the columns behind gap ث-2 of
``docs/delivery/data-search-android/``. It overturns decision 1 of ب-10 (the
cut-off marker is a sentence, not a field) by owner decision, 2026-11-10; both
sentences stay in the stored text for every reader that does not know the
columns.

**What they are for.** A reloaded thread returned the answer text only, so a
client could not tell a whole answer from one cut short, nor one given by the
local fallback model (capacity-plan 6.4). The code path that writes the message
knows both facts; these columns keep them with the message.

**Why columns and not keys inside ``content``.** ``content`` is closed -- text
plus attachment ids (ق-2 of ث-1). Sibling columns leave it untouched.

**Why two columns and not one.** The status and the switch are orthogonal: a
turn can be ``partial`` and still have been answered by the local model. One
jsonb column holding both would also make the closed status impossible to
express as a plain CHECK.

``status`` is ``text``, not an enum type: the closed set is ``ck_messages_status``
plus ``domain.MessageStatus``, and an ``ALTER TYPE`` is a heavier migration to
run later. ``fallback`` is ``jsonb`` shaped like ``final.fallback``
(``{from: {provider, model}, to: {provider, model}}``) and carries no CHECK:
its rules live in ``domain.MessageFallback``, as ``citations``' do.

``NULL`` means "not recorded", which is what every row that predates these
columns means (and every user message, and every ``load_seed`` row). There is
therefore no backfill and no ``DEFAULT``: Android asked explicitly that old
messages are not called complete.

**The CHECK is ``NOT VALID`` and is deliberately never validated.** ``NOT
VALID`` makes ``ADD CONSTRAINT`` a catalogue-only change, and PostgreSQL still
ENFORCES the constraint on every INSERT and UPDATE after it. Every existing row
holds NULL in ``status`` by construction -- the column is born in this very
migration -- and NULL passes a CHECK. ``VALIDATE CONSTRAINT`` would therefore be
a full scan of ``conversations.messages``, the largest table on the platform
(about a million rows on the capacity seed), run under the same ``ACCESS
EXCLUSIVE`` lock because Alembic runs the whole migration in one transaction
(``migrations/env.py``), and it would prove nothing. This differs on purpose
from ``files/0005`` and ``files/0006``, whose table is bounded by
``max_files_per_workspace``. The visible side effect: ``\\d`` shows the
constraint as ``NOT VALID``, and ``pg_dump`` keeps it that way.

No index: both columns are projections read alongside their row, never
predicates.

Row-level security and grants are unchanged: ``tenant_isolation`` and FORCE RLS
apply to the whole row, and the module roles hold table-level grants only.

Compatibility: code that predates this revision inserts without the columns
(they become NULL, which the CHECK accepts) and reads with its older ``Table``,
which ignores them. The new code names both columns, so ``migrate`` must run
before the new image, the order already in ``docker-compose.yml`` and
``deploy/rolling-deploy.sh``.

Rollback: ``downgrade`` drops the constraint and both columns and therefore
DISCARDS every stored status and fallback recorded since the upgrade. The text,
with its two sentences, is untouched. Prefer leaving the columns in place on a
rollback; they are inert for the older code.

Revision ID: 0008_message_status
Revises: 0007_message_citations
"""

from __future__ import annotations

from alembic import op

revision = "0008_message_status"
down_revision = "0007_message_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One ALTER per column: test_conversations_schema_parity.py reads
    # "ALTER TABLE conversations.messages ADD COLUMN <name>".
    op.execute("ALTER TABLE conversations.messages ADD COLUMN status text NULL;")
    op.execute("ALTER TABLE conversations.messages ADD COLUMN fallback jsonb NULL;")
    op.execute(
        """
        ALTER TABLE conversations.messages ADD CONSTRAINT ck_messages_status
          CHECK (status IN ('complete', 'partial')) NOT VALID;
        """
    )
    # Deliberately NO `VALIDATE CONSTRAINT` (module docstring).


def downgrade() -> None:
    # DATA LOSS by design: every stored status and fallback is discarded.
    op.execute("ALTER TABLE conversations.messages DROP CONSTRAINT IF EXISTS ck_messages_status;")
    op.execute(
        "ALTER TABLE conversations.messages DROP COLUMN IF EXISTS fallback, "
        "DROP COLUMN IF EXISTS status;"
    )
