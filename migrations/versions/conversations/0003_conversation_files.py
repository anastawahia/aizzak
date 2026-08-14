"""conversations: pin a thread's retrieval scope to specific files (BE-RAG-005).

Adds ``conversations.conversation_files`` (01-data-model §2.4).

**A reference, not ownership.** Files stay workspace-level and are indexed
once into the single per-workspace Qdrant collection; ``knowledge.documents``
carries a ``file_id`` and knows nothing about conversations (01 §2.7). This
table does not move a file into a thread — it records "answer this thread from
these documents only". With rows, ``rag_agent``'s retrieval filter gains the
``document_id``s derived from these files; with none, it searches the whole
workspace corpus.

**Empty means everything, which is why there is no backfill.** Every thread
that predates this table has zero rows here and keeps exactly the behaviour it
has today. A pin is opt-in, and un-pinning the last file returns the thread to
the corpus rather than to an empty knowledge base — the alternative (empty set
⇒ retrieve nothing) would have made the first pin a destructive act and the
last un-pin unrecoverable from the UI.

``file_id`` is a LOGICAL reference with no foreign key, the same as
``knowledge.documents.file_id`` (01 §2.7): ``files`` is another module with
its own schema and its own migration chain, and a cross-schema FK would couple
two chains that Alembic cannot order relative to each other (see
``0001_conversations.py`` on ``depends_on``). The consequence is deliberate
and handled in the read path: deleting a file leaves its pins behind, and
retrieval simply finds nothing for them — the same outcome as a file that was
never indexed. ``conversation_id`` DOES get its FK, because its parent lives
in this schema and this chain.

The composite PRIMARY KEY is the idempotency guarantee: pinning the same file
twice cannot make a second row, so ``pin_file`` can be a plain upsert instead
of a read-then-write race. It also serves the only query this table has —
"the pins of one conversation" — as its leading-column prefix, so there is no
secondary index.

No ``updated_at`` and no touch trigger: a pin is created and dropped, never
edited (the ``messages`` precedent in ``0001_conversations.py``). No
``deleted_at`` either — un-pinning destroys nothing that a soft delete would
be preserving, and a tombstoned pin would have to be excluded from every read
for no gain.

Revision ID: 0003_conversation_files
Revises: 0002_conversation_model_route
"""

from __future__ import annotations

from alembic import op

revision = "0003_conversation_files"
down_revision = "0002_conversation_model_route"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations.conversation_files (
          conversation_id uuid NOT NULL,
          file_id         uuid NOT NULL,
          workspace_id    uuid NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (conversation_id, file_id),
          CONSTRAINT fk_convfile_conv FOREIGN KEY (conversation_id)
              REFERENCES conversations.conversations(id)
        );
        """
    )

    # RLS (01 §3, hardened NULLIF form): the table carries its own
    # `workspace_id`, denormalized off its parent exactly like `messages`
    # (DD-04), so it is a tenant table in its own right.
    op.execute("ALTER TABLE conversations.conversation_files ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE conversations.conversation_files FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON conversations.conversation_files
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversations.conversation_files")
    op.execute("DROP TABLE IF EXISTS conversations.conversation_files")
