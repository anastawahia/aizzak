"""conversations: pin one configured model route per thread (BE-RAG-003).

Adds ``conversations.conversations.model_route`` (01-data-model §2.4).

**What the column holds, and what it deliberately does not.** The value is a
ROUTING KEY from the D-16 table (``Settings.provider_routing.llm``) — the same
key ``GET /api/v1/models`` publishes as ``ModelOut.capability`` — and never a
raw model name. FR-73 puts the provider/model choice in configuration: a
column holding ``"gpt-4o"`` would let a thread name a model the operator never
routed, and the failure would surface as a provider error at the next turn
rather than as a refused write. Holding the key means the pin can be validated
against the live table at write time and re-resolved at read time, so a route
the operator retires degrades to the agent's own routing instead of pinning a
thread to something that no longer exists.

``NULL`` means "not pinned" — resolve by agent key, which is the behaviour
every existing thread already has. That is why there is no backfill and no
default: the column is born with the semantics of the rows that predate it.

No CHECK constraint on the value. Valid keys are whatever the operator has
configured TODAY, which the database cannot know and which changes without a
migration; enforcing it in SQL would either be a lie (a stale allow-list) or a
deployment coupling (every routing change becomes a migration). The check
belongs where the table is parsed — ``PinConversationModel`` against
``ModelCatalog`` — and the read path is written to tolerate a key that has
since disappeared.

No index: the column is never a predicate, only a projection.

Revision ID: 0002_conversation_model_route
Revises: 0001_conversations
"""

from __future__ import annotations

from alembic import op

revision = "0002_conversation_model_route"
down_revision = "0001_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations.conversations ADD COLUMN model_route text NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations.conversations DROP COLUMN IF EXISTS model_route;")
