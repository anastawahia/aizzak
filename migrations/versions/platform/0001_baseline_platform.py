"""baseline: module schemas + platform event infrastructure.

Creates the ten v1 module schemas + the structural ``platform`` schema, the
shared ``touch_updated_at`` trigger function, and the event-infra tables
(Outbox, processed_events, stream_offsets) per 01-data-model §4 / 04 §1.

Reserved schemas (scheduling/sandbox/runs) are intentionally NOT created in v1
(DD-01). Module tables, RLS policies, and the ``app_rw`` role are added by the
per-module migration chains in Phase 3.

Revision ID: 0001_baseline_platform
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_baseline_platform"
down_revision = None
# Named so it stays selectable once a second chain (e.g. versions/knowledge)
# gives this multi-root revision graph more than one head -- an unqualified
# `alembic upgrade head` becomes ambiguous, so commands target a branch
# explicitly: `alembic upgrade platform@head` /
# `alembic -x vts=knowledge upgrade knowledge@head` (12-module-authoring-
# guide §4 binding point 3).
branch_labels = ("platform",)
depends_on = None

# Ten v1 module schemas (DD-01) + the structural platform schema.
MODULE_SCHEMAS = (
    "workspace",
    "access",
    "credentials",
    "conversations",
    "memory",
    "files",
    "knowledge",
    "media",
    "integrations",
    "usage",
)
ALL_SCHEMAS = (*MODULE_SCHEMAS, "platform")


def upgrade() -> None:
    for schema in ALL_SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    # Shared updated_at trigger function (01-data-model §0).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.touch_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Transactional Outbox (01 §4.1, D-18). id == event_id (UUIDv7).
    op.execute(
        """
        CREATE TABLE platform.outbox (
            id             uuid PRIMARY KEY,
            workspace_id   uuid,
            aggregate_type text NOT NULL,
            aggregate_id   uuid NOT NULL,
            event_type     text NOT NULL,
            stream         text NOT NULL,
            payload        jsonb NOT NULL,
            correlation_id uuid,
            causation_id   uuid,
            created_at     timestamptz NOT NULL DEFAULT now(),
            published_at   timestamptz NULL,
            attempts       integer NOT NULL DEFAULT 0
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_outbox_unpublished ON platform.outbox(created_at)
            WHERE published_at IS NULL;
        """
    )

    # Idempotency ledger (01 §4.2, DD-09).
    op.execute(
        """
        CREATE TABLE platform.processed_events (
            consumer_group text NOT NULL,
            event_id       uuid NOT NULL,
            processed_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (consumer_group, event_id)
        );
        """
    )

    # Stream offsets — optional monitoring aid (01 §4.3).
    op.execute(
        """
        CREATE TABLE platform.stream_offsets (
            stream         text NOT NULL,
            consumer_group text NOT NULL,
            last_id        text NOT NULL,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (stream, consumer_group)
        );
        """
    )


def downgrade() -> None:
    # Module schemas FIRST: their touch triggers reference
    # platform.touch_updated_at(), so dropping the function (or the platform
    # schema) before them fails with DependentObjectsStillExistError once any
    # module chain has been applied (hit live during the media proof slice).
    for schema in reversed(MODULE_SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    op.execute("DROP TABLE IF EXISTS platform.stream_offsets")
    op.execute("DROP TABLE IF EXISTS platform.processed_events")
    op.execute("DROP INDEX IF EXISTS platform.ix_outbox_unpublished")
    op.execute("DROP TABLE IF EXISTS platform.outbox")
    op.execute("DROP FUNCTION IF EXISTS platform.touch_updated_at()")
    op.execute('DROP SCHEMA IF EXISTS "platform" CASCADE')
