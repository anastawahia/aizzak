"""platform: the ``electrical`` schema (WireTwin, electrical-drawings INC-1, LLD 4.2).

The same reason ``0004_spaces_schema.py`` gives, applied to the thirteenth chain: Alembic
creates ``electrical.alembic_version`` BEFORE it executes the first ``upgrade()`` of the
``electrical`` chain, so that chain cannot create the schema its own bookkeeping table lives
in. The baseline chain runs first (``app.ops.provision.MIGRATION_CHAINS``), so this is the same
ordering guarantee the other schemas stand on. ``0001_baseline_platform.py`` is already applied
everywhere and is not edited.

Backward compatible (expand only): an empty schema nobody reads before the new code ships.

``downgrade`` DROPS THE SCHEMA WITH ``CASCADE``, which takes every ``electrical.*`` table and
the chain's ``alembic_version`` with it. That is data loss if ``electrical`` holds rows
(projects, documents, jobs): downgrade the ``electrical`` chain first and decide about the data
before, not after (LLD 4.4 prefers leaving the schema and switching ``ELECTRICAL_ENABLED`` off).

Revision ID: 0006_electrical_schema
Revises: 0005_ledger_autovacuum
"""

from __future__ import annotations

from alembic import op

revision = "0006_electrical_schema"
down_revision = "0005_ledger_autovacuum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS`: a database rebuilt by dropping schemas and one migrated forward
    # must both end in the same place (0004_spaces_schema.py).
    op.execute('CREATE SCHEMA IF NOT EXISTS "electrical"')


def downgrade() -> None:
    op.execute('DROP SCHEMA IF EXISTS "electrical" CASCADE')
