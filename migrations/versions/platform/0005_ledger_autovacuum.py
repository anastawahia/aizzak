"""platform: per-table autovacuum settings for the three write-heavy ledgers
(capacity step 2.8, ``ح-16`` — ``docs/capacity-plan.md`` §5, Wave 2).

**What ``ح-16`` actually is, measured on this stack rather than asserted.**
The bottleneck row says these tables have "no partitioning, no archival and no
autovacuum settings of their own", and that the default settings mean a large
table "is practically never cleaned". The mechanism is the *proportional* half
of the trigger. Autovacuum starts on a table when::

    dead tuples > autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * n_live

with the shipped defaults ``50`` and ``0.2``. On an empty table that is 50 dead
rows; on ``platform.outbox`` holding a 90-day backlog of two million rows it is
**400,050** — the table must be one-fifth garbage before anything begins, and
the threshold *rises as the table grows*, so the bigger the ledger the longer
it is allowed to rot. That is the whole of ``ح-16``: not a missing job, an
inverted one.

Measured on the live stack (``aizzak_test``, 2,000,000-row backlog, equal rates
of append/publish/sweep from real client transactions, row count constant by
construction so every byte of growth is bloat and not data)::

    defaults:  heap 1302 MB -> 1673 MB (+28.5%), 2 autovacuums, peak 549,818 dead
    this file: heap 1302 MB -> 1406 MB  (+8.0%), 7 autovacuums, peak 103,084 dead
                                        and flat for ten consecutive samples

The defaults do not grow without bound -- they settle at a high-water mark set
by how much garbage accumulates before the trigger is crossed. But that mark is
a PROPORTION of the table, so the tax grows with the backlog: a fifth of two
million today, a fifth of ten million after a quarter at peak. That is why the
fix removes the proportional term rather than shrinking it.

**Why the plan's own parenthetical is corrected here rather than followed.**
Step 2.8 asks for "عتباتٌ **نسبيّةٌ** لا مطلقة" — relative thresholds, not
absolute — and gives the right reason ("the default threshold on a table with
millions of rows means it is practically never cleaned") attached to the wrong
lever. The default IS the relative one; ``autovacuum_vacuum_scale_factor``
*is* the proportion, and it is what makes a big table wait. The fix that
produces the behaviour the sentence asks for is the opposite of its wording:
zero the proportional term and set a flat, absolute dead-tuple count, so the
trigger stops scaling with the backlog. The intent is honoured, the lever is
inverted. Recorded in ``docs/capacity-status.md`` §5 as a plan-text correction.

**Where the absolute numbers come from — derived, not chosen.**
``EventSettings`` declares the relay's own ceiling: ``outbox_relay_batch_size``
(256) per ``outbox_poll_interval_ms`` (500 ms) = **512 rows/s**, which is the
most this table can sustainably receive because it is the most the relay can
drain. Each row becomes dead *twice* — once when the relay stamps
``published_at`` (the old version) and once when ``app.ops.retention`` deletes
it — so peak dead-tuple production is **1,024/s**, or ~61,400 per
``autovacuum_naptime`` (60 s, the launcher's own revisit interval).
``VACUUM_THRESHOLD = 50_000`` therefore fires on the naptime at peak and only
on real garbage below it. A threshold *below* one naptime's production cannot
make autovacuum run more often than the naptime and only makes it arrive with
more work each time; a threshold far above it reintroduces the defect.

``ANALYZE_THRESHOLD`` is half of it. Stale statistics are the *other* half of
``ح-16``'s damage ("query plans degrade gradually, with no single event to
warn") and ANALYZE is orders of magnitude cheaper than VACUUM, so the plans are
refreshed before the cleanup rather than after it.

**``autovacuum_vacuum_cost_delay = 0`` — the throttle is removed on these
tables and only these.** The delay exists to keep a background vacuum of a
cold table from competing with the request path for I/O. These three are the
smallest and hottest tables on the server, and the measured cost of a vacuum
that does not *finish* is unbounded file growth; a vacuum that is still running
when the next naptime arrives never catches up. This is a per-table override,
so every other table in the cluster keeps the server-wide throttle.

**The insert-driven trigger is set for the same reason as the dead-tuple one.**
``platform.processed_events`` is append-then-sweep: between sweeps it takes
almost no updates, so the dead-tuple trigger barely moves and PostgreSQL 13+'s
``autovacuum_vacuum_insert_*`` pair is what actually fires. Its defaults are
proportional in exactly the same way (``1000 + 0.2 * n_live``), so it gets the
same treatment — otherwise the freeze/visibility-map maintenance a growing
append-only ledger needs is deferred by its own size.

**What this migration deliberately does NOT do.**

* No ``fillfactor`` on ``platform.outbox``. It looks like the obvious companion
  fix — the relay UPDATEs one column on every row — but HOT updates require
  that *no indexed column* change, and ``published_at`` is indexed by BOTH
  partial indexes (``ix_outbox_published``/``ix_outbox_unpublished``). The
  relay's update can never be HOT, by construction. Measured, before assuming:
  ``n_tup_hot_upd = 3`` out of 500,000 updates. Leaving free space in every
  page would cost heap size and buy nothing.
* No partitioning. Step 2.8 makes it conditional ("if measurement proves the
  need"), and range-partitioning by ``created_at`` would turn the retention
  sweep into a ``DROP TABLE`` that produces no dead tuples at all. It is the
  right end state and it is not this migration: it rewrites the table under a
  lock this repository cannot yet take safely (step 2.9's ``lock_timeout`` and
  expand/contract are not built), and the measurement below decides whether it
  is needed at all. ``docs/capacity-status.md`` records the number that would
  reopen it.
* No grants. Grants never live in a migration here (01 §6); they are
  ``app.ops.provision``'s.

Applied with the platform chain::

    alembic upgrade platform@head

Revision ID: 0005_ledger_autovacuum
Revises: 0004_spaces_schema
"""

from __future__ import annotations

from alembic import op

revision = "0005_ledger_autovacuum"
down_revision = "0004_spaces_schema"
branch_labels = None
depends_on = None

# One autovacuum naptime (60 s) of peak dead-tuple production -- see the
# module docstring's derivation. Imported by `tests/unit/test_ledger_
# autovacuum.py` and by `app.ops.table_growth`, never re-typed as a literal.
VACUUM_THRESHOLD = 50_000
ANALYZE_THRESHOLD = 25_000

#: Every ledger gets the same shape: kill the proportional term, set a flat
#: count, and take the throttle off so a run finishes inside a naptime.
_LEDGER_SETTINGS: tuple[str, ...] = (
    "autovacuum_vacuum_scale_factor = 0",
    f"autovacuum_vacuum_threshold = {VACUUM_THRESHOLD}",
    "autovacuum_analyze_scale_factor = 0",
    f"autovacuum_analyze_threshold = {ANALYZE_THRESHOLD}",
    "autovacuum_vacuum_insert_scale_factor = 0",
    f"autovacuum_vacuum_insert_threshold = {VACUUM_THRESHOLD}",
    "autovacuum_vacuum_cost_delay = 0",
)

#: `idempotency_keys` rides along for a reason, not for symmetry: it is the
#: third table `app.ops.retention` sweeps, it takes an UPDATE per request (the
#: response body is filled in after the operation) and a DELETE per failure,
#: and 01 §4.2-ب already names it as growing without a policy. Same shape,
#: same defect, same fix.
LEDGER_TABLES: tuple[str, ...] = (
    "platform.outbox",
    "platform.processed_events",
    "platform.idempotency_keys",
)

_DEFAULTS: tuple[str, ...] = tuple(setting.split(" = ")[0] for setting in _LEDGER_SETTINGS)


def upgrade() -> None:
    settings = ", ".join(_LEDGER_SETTINGS)
    for table in LEDGER_TABLES:
        op.execute(f"ALTER TABLE {table} SET ({settings})")


def downgrade() -> None:
    # RESET returns each table to the server-wide value; it does not write the
    # shipped default back, so a later change to the server config is still
    # inherited the way it would have been had this migration never run.
    resets = ", ".join(_DEFAULTS)
    for table in LEDGER_TABLES:
        op.execute(f"ALTER TABLE {table} RESET ({resets})")
