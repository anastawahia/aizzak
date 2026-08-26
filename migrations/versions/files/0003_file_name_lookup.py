"""files: the (space, name) key a replacement is found by (س-29 rule 1).

Adds one GENERATED STORED column and one partial index over it. No constraint.

**Why a stored column and not an expression index — this is the interesting
part, and it was MEASURED rather than assumed.** The obvious shape is a plain
expression index::

    CREATE INDEX … ON files.files(workspace_id, space_id, lower(normalize(name, NFC)))

It was written that way first, and PostgreSQL 16 refuses to use the expression
as a search key on THIS table. ``EXPLAIN`` puts ``workspace_id``/``space_id``
in ``Index Cond`` and drops the name into ``Filter``:

    Index Scan using ix_files_space_name on files
      Index Cond: ((workspace_id = …) AND (space_id = …))
      Filter: (lower(NORMALIZE(name, NFC)) = 'report.pdf'::text)

The cause is ``FORCE ROW LEVEL SECURITY``. ``lower`` and ``normalize`` are
``IMMUTABLE`` but **not** ``LEAKPROOF`` (``pg_proc.proleakproof = false``, both
of them), and the planner may not evaluate a non-leakproof function before a
row-security qual — a leak there would disclose rows the policy hides. So the
expression can only be applied AFTER the barrier, which is to say as a filter,
never as an index condition. The same expression index on an identical table
WITHOUT RLS matches all three keys; adding the policy is what breaks it
(reproduced both ways). Every expression index on the RLS tables in this schema
inherits that rule.

A stored column sidesteps it because the comparison becomes ``texteq``, which
IS ``LEAKPROOF``, so it may run before the barrier. Measured on the same
fixture: ``Index Cond: (… AND (name_key = 'report.pdf'::text))``.

It matters because ``max_files_per_workspace`` is ``10_000`` (07-nfr-slo §4)
and this predicate runs on **every upload completion and every rename** — the
module's hottest write path, the one ``ix_files_space``'s note already says is
the reason that index exists at all. A filter over one space's rows is fine at
three files and is not a cost worth paying ten thousand times a day when a
column removes it.

**The ADD rewrites the table, and that is accepted here.** A ``GENERATED ...
STORED`` column has to be materialised for every existing row, so this takes an
``ACCESS EXCLUSIVE`` lock for the length of one rewrite of ``files.files``. At
this deployment's size that is milliseconds; on a table that had grown to the
``max_files_per_workspace`` ceiling across many workspaces it would be a brief
outage on uploads, and the online alternative (add nullable, backfill in
batches, then attach a trigger or swap in the generated column) costs three
migrations and a window where the column lies. The cheap path is taken
knowingly rather than by default.

**The application never writes ``name_key``.** ``GENERATED ALWAYS … STORED``
means PostgreSQL derives it on every INSERT and UPDATE of ``name``, so it
cannot drift from the name it summarises, and ``SqlFileRepository`` neither
lists it in its INSERT nor in ``save``'s UPDATE. The ADD backfills every
existing row in the same statement.

**Why the expression is ``lower(normalize(name, NFC))``.** Two halves, each for
a different alphabet:

* ``lower`` is ``ux_spaces_ws_name``'s rule, taken deliberately rather than by
  imitation: "Report.pdf" and "report.pdf" are one name to the person who
  uploaded them, and a replacement rule that missed that would leave exactly
  the double-indexed corpus س-29 exists to prevent.
* ``normalize(.., NFC)`` is what makes the rule work for ARABIC, which is the
  primary case for this deployment and the one ``lower`` does nothing for. The
  same Arabic filename typed on two keyboards, or round-tripped through a macOS
  client, differs by combining marks alone. It has to be a letter that
  DECOMPOSES for that to happen -- ALEF WITH HAMZA ABOVE (U+0623) splits into
  U+0627 + U+0654, and a name built only from plain letters is byte-identical
  in both forms, which is a trap the tests for this fell into before it was
  measured. Without this half the two spellings are different files, both
  indexed, and the audit's cause #7 becomes reachable through a keyboard.

The application asks its question through the identical expression applied to
its ARGUMENT (``SqlFileRepository.live_namesakes`` compares ``name_key``
against ``lower(normalize(:name, NFC))``, which the planner constant-folds), so
neither side is normalised in Python and there is no Python/SQL agreement to
drift.

**Why an index and NOT a unique constraint.** ``spaces`` defends its own names
with a partial UNIQUE index and turns ``23505`` into ``spaces.duplicate_name``.
That cannot be copied here, and the reason is the DECISION rather than the
schema: for a space a duplicate name is an ERROR, for a file it is a
REPLACEMENT (س-29 rule 1, owner decision 2026-08-25 —
``docs/rag-fidelity-audit.md`` §4-هـ-2), ordered "upload first, then delete". So
between the moment the new row is inserted and the moment the old one is marked
deleted BOTH are live, and a unique index would reject the very insert the
decision mandates. Uniqueness is a state the application CONVERGES to, not an
invariant the database asserts.

**Partial on ``deleted_at IS NULL``**, matching ``ix_files_ws`` and
``ix_files_space``: a soft-deleted file has already given up its name, and the
sweep must never resurrect one to be replaced a second time.

``space_id`` is in the key and ``workspace_id`` before it. The space is where
the rule's scope comes from — spaces are isolated completely (س-32) so the same
name in two spaces names two unrelated files and neither replaces the other —
and the workspace stays leftmost because every read on this table carries it
(DD-04, layer 2) and the RLS predicate filters on it first.

Revision ID: 0003_file_name_lookup
Revises: 0002_file_space
"""

from __future__ import annotations

from alembic import op

revision = "0003_file_name_lookup"
down_revision = "0002_file_space"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE files.files
          ADD COLUMN name_key text
            GENERATED ALWAYS AS (lower(normalize(name, NFC))) STORED;
        """
    )
    op.execute(
        """
        CREATE INDEX ix_files_space_name
          ON files.files(workspace_id, space_id, name_key)
          WHERE deleted_at IS NULL;
        """
    )


def downgrade() -> None:
    # The index goes with the column it is on; dropping the column would take
    # it anyway, and naming both keeps the reversal readable.
    op.execute("DROP INDEX IF EXISTS files.ix_files_space_name")
    op.execute("ALTER TABLE files.files DROP COLUMN IF EXISTS name_key")
