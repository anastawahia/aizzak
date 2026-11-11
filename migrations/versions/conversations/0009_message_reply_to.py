"""conversations: an assistant message remembers which question it answers.

Adds ``conversations.messages.reply_to`` (01-data-model §2.4), the column behind
gap ث-3 of ``docs/delivery/data-search-android/`` (send reconciliation). ``seq``
order is not enough to pair a question with its answer once turns overlap (a
WebSocket with two turns in flight, two devices on one thread); the turn that
writes both knows the pairing, so the answer keeps the id of its question.

``uuid`` and not ``text``: the same type as ``messages.id``.

``NULL`` means "not recorded". That is what every user message holds, every row
that predates this column, every workflow step output, every summary delivered
by a worker, and every ``load_seed`` row. There is therefore no backfill and no
``DEFAULT``.

**Why no foreign key.** A foreign-key check bypasses row-level security, so it
could not prove that the question belongs to the same workspace, nor the same
conversation, nor that it is a ``user`` message. It would also constrain the
delete order in ``purge.py``. Integrity is enforced by the adapter's guarded
write instead: ``INSERT ... SELECT ... WHERE EXISTS (a user message of this
conversation)`` in the same statement, under the same RLS.

**No index.** The column is a projection read with its row, never a predicate
(no path filters by it), and the ``EXISTS`` in the guarded insert goes through
the primary key on ``id``.

Row-level security and grants are unchanged: ``tenant_isolation`` and FORCE RLS
apply to the whole row, and the module roles hold table-level grants only.

Lock and duration: one ``ALTER TABLE ... ADD COLUMN ... NULL`` without a
default is a catalogue-only change on PostgreSQL 11+ (no table rewrite, no
scan). It takes ``ACCESS EXCLUSIVE`` for an instant, under the ``lock_timeout``
that ``migrations/env.py`` sets, so it is safe on the largest table.

Compatibility: code that predates this revision inserts without the column (it
becomes NULL) and reads with its older ``Table``, which ignores it. The new code
names the column, so ``migrate`` must run before the new image, the order
already in ``docker-compose.yml`` and ``deploy/rolling-deploy.sh``.

Rollback: ``downgrade`` drops the column and therefore DISCARDS every stored
question-answer link recorded since the upgrade. The messages themselves are
untouched. Prefer leaving the column in place on a rollback; it is inert for the
older code.

Revision ID: 0009_message_reply_to
Revises: 0008_message_status
"""

from __future__ import annotations

from alembic import op

revision = "0009_message_reply_to"
down_revision = "0008_message_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One ALTER per column: test_conversations_schema_parity.py reads
    # "ALTER TABLE conversations.messages ADD COLUMN <name>".
    op.execute("ALTER TABLE conversations.messages ADD COLUMN reply_to uuid NULL;")


def downgrade() -> None:
    # DATA LOSS by design: every stored question-answer link is discarded.
    op.execute("ALTER TABLE conversations.messages DROP COLUMN IF EXISTS reply_to;")
