"""conversations module: conversations (threads) + messages (append-only child).

Creates ``conversations.conversations`` and ``conversations.messages``
(01-data-model §2.4), the ``touch_updated_at`` trigger on ``conversations``
only, and the ``tenant_isolation`` RLS policy (01 §3) on BOTH tables.

``conversations.messages`` gets NO touch trigger -- the table carries no
``updated_at`` column (access precedent, 01 §2.4): messages are append +
soft-delete only, so there is nothing for ``platform.touch_updated_at()`` to
touch. It DOES get its own ``tenant_isolation`` policy: it carries its own
``workspace_id`` column (denormalized off its parent conversation so every
statement can filter/isolate on it directly, DD-04), so it is a tenant table
in its own right, independently of ``conversations``.

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=conversations``), so a
single ``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=conversations upgrade conversations@head

Revision ID: 0001_conversations
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_conversations"
down_revision = None
branch_labels = ("conversations",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations.conversations (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          agent_key    text NOT NULL,
          kind         text NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','workflow')),
          title        text,
          created_by   uuid,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          deleted_at   timestamptz NULL,
          version      integer NOT NULL DEFAULT 1
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_conv_ws_agent ON conversations.conversations(workspace_id, agent_key) "
        "WHERE deleted_at IS NULL;"
    )

    op.execute(
        """
        CREATE TABLE conversations.messages (
          id              uuid PRIMARY KEY,
          conversation_id uuid NOT NULL,
          workspace_id    uuid NOT NULL,
          role            text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
          content         jsonb NOT NULL,
          token_count     integer,
          seq             integer NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          deleted_at      timestamptz NULL,
          CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id)
              REFERENCES conversations.conversations(id),
          CONSTRAINT uq_msg_seq UNIQUE (conversation_id, seq)
        );
        """
    )

    # touch trigger (01 §0, shared platform.touch_updated_at()) -- on
    # `conversations` only; see the module docstring for why `messages`
    # has none.
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON conversations.conversations
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening) on BOTH tables: each carries its own
    # `workspace_id` (01 §2.4).
    op.execute("ALTER TABLE conversations.conversations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE conversations.conversations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON conversations.conversations
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )
    op.execute("ALTER TABLE conversations.messages ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE conversations.messages FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON conversations.messages
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversations.messages")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversations.conversations")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON conversations.conversations")
    op.execute("DROP INDEX IF EXISTS conversations.ix_conv_ws_agent")
    # messages BEFORE conversations: fk_msg_conv references conversations.conversations(id).
    op.execute("DROP TABLE IF EXISTS conversations.messages")
    op.execute("DROP TABLE IF EXISTS conversations.conversations")
