"""conversations: remember the file names a thread just asked the user about.

Adds ``conversations.conversations.pending_clarification`` (01-data-model
§2.4), the column behind ب-9 / gap ف-1أ of
``docs/rag-agent-scenarios-implementation-plan.md`` §7.

**What it is for.** The RAG agent already asks «أيّ ملفّ تقصد؟» and lists the
candidates its resolver refused to choose between — and then never hears the
answer: the next turn is classified from scratch, all three natural replies
(the full name, «الثاني», «2025») come out as ``content``, and the summary the
user asked for is never built. The other end of that conversation does not
exist because nothing remembers that a question was asked. This column is that
memory.

**Why a column and not the previous message.** The cheaper-looking option is
to read the clarification back out of the thread — but ``MessageContent``
(01 §2.4, 06 §4) has no structured half: a message is text plus attachment
ids. Recovering "these three files were offered, in this order" would mean
parsing free Arabic prose that the agent is free to reword, in the one place
where a mis-parse picks the wrong document. A column states the fact instead
of re-deriving it.

**Why the NAMES and not the document ids.** What the user was shown is names,
and the answer is about what was shown — including «الثاني», which can only be
read against the list that was actually displayed. Storing ids would make the
ordinal answerable only by re-deriving the display order from somewhere else,
which is the same guess the paragraph above refuses. The module translates a
chosen name back to a document through its own candidate walk, so an id never
has to cross a turn boundary.

``jsonb`` for ``messages.content``'s reason: it is this schema's shape for a
structured value, and an ORDERED array is exactly what has to survive the
round trip.

``NULL`` means "nothing pending", which is what every row that predates this
column means, so there is no backfill and no default. The adapter writes
``NULL`` rather than ``[]`` for an empty list deliberately: two spellings of
one state is how a reader learns to check for both and a writer learns to
forget one.

No index: the column is never a predicate, only a projection — read by id
alongside the row it lives on, exactly like ``model_route``.

No CHECK constraint. What makes a stored name meaningful is that the corpus
still holds a file called that, which is a fact in another schema and one that
changes without a migration; the read path is written to tolerate a name whose
file has since gone (it simply resolves nothing and the turn is answered as an
ordinary question).

Revision ID: 0005_conversation_clarification
Revises: 0004_conversation_space
"""

from __future__ import annotations

from alembic import op

revision = "0005_conversation_clarification"
down_revision = "0004_conversation_space"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversations.conversations ADD COLUMN pending_clarification jsonb NULL;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE conversations.conversations DROP COLUMN IF EXISTS pending_clarification;"
    )
