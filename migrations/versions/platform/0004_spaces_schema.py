"""platform: the ``spaces`` schema (docs/spaces-backend-plan.md §3.2, step 2).

**Why the eleventh module schema is created HERE and not in
``0001_baseline_platform.py``'s ``MODULE_SCHEMAS``.** That revision is already
applied on every database this project has; editing an applied revision
changes nothing on any of them. A schema that only exists on databases
migrated from scratch is exactly the drift 08-local-runbook's rebuild step
would hide from us and the first real deploy would find.

**Why not inside ``spaces/0001_spaces.py`` itself, which is where the rest of
the module's DDL lives.** That chain records its applied revisions in its OWN
``version_table_schema`` (DAT-03, ``-x vts=spaces``), and Alembic creates
``spaces.alembic_version`` BEFORE it executes the first ``upgrade()``
(``MigrationContext.run_migrations`` calls ``_ensure_version_table`` ahead of
the migration steps). A chain therefore cannot create the schema its own
bookkeeping table lives in -- it would fail on ``CREATE TABLE
spaces.alembic_version`` with ``schema "spaces" does not exist`` before a line
of its own SQL ran. The baseline chain runs first
(``app.ops.provision.MIGRATION_CHAINS``), so this is the same ordering
guarantee the other ten schemas already stand on, just one revision later.

Revision ID: 0004_spaces_schema
Revises: 0003_retention_sweep
"""

from __future__ import annotations

from alembic import op

revision = "0004_spaces_schema"
down_revision = "0003_retention_sweep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS` for the same reason the baseline uses it: a database
    # rebuilt by dropping schemas (tests/integration/conftest.py) and one
    # migrated forward from v1 must both end in the same place.
    op.execute('CREATE SCHEMA IF NOT EXISTS "spaces"')


def downgrade() -> None:
    # CASCADE, and deliberately: this takes `spaces.alembic_version` with it,
    # which is correct -- the spaces chain's recorded state is meaningless
    # once the schema holding it is gone. The baseline's own `downgrade()`
    # drops the other ten module schemas exactly this way.
    op.execute('DROP SCHEMA IF EXISTS "spaces" CASCADE')
