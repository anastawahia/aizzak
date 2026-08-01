"""usage module: mark a ledger row whose token count was ESTIMATED, not
measured (Phase 4.7-c-1, the second half of 4.7-a's user-gated billing
decision).

4.7-a amended ``LlmChunk`` so a streamed turn can finally report the
provider's real ``prompt_tokens``/``completion_tokens``; ``None`` there means
"the provider reported nothing, the caller must estimate". This column is what
keeps that distinction ALIVE in the ledger: without it a measured 1,300 tokens
and a guessed 1,300 tokens are indistinguishable rows, and no operator
auditing a bill could ever tell which is which. The user's decision was
explicitly "exact-when-available **+ a marker**" — this is the marker.

``DEFAULT false`` + ``NOT NULL`` is safe on this append-only table: every row
written before this migration came from a path that had no estimation
mechanism at all, so ``false`` is the truthful backfill value rather than a
convenient one. No RLS work is needed — ``tenant_isolation`` on
``usage.usage_records`` is a row-level policy on ``workspace_id``, unaffected
by adding a column, and it was already born NULLIF-hardened in ``0001_usage``.

Applied with the per-module chain (DAT-03)::

    alembic -x vts=usage upgrade usage@head

Revision ID: 0002_usage_estimated
Revises: 0001_usage
"""

from __future__ import annotations

from alembic import op

revision = "0002_usage_estimated"
down_revision = "0001_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE usage.usage_records
            ADD COLUMN estimated boolean NOT NULL DEFAULT false;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE usage.usage_records DROP COLUMN IF EXISTS estimated")
