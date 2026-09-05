"""Hermetic guards for capacity step 2.8 (``ح-16``) -- the per-table autovacuum
settings, the fourth retention target, and the redelivery floor.

``ح-16``'s mechanism is arithmetic, so most of it can be guarded without a
database: the trigger is ``threshold + scale_factor * n_live``, and the whole
defect is that ``scale_factor`` is non-zero on a table with millions of rows.
A test that reads the migration's own settings and asserts the proportional
term is gone is therefore not a tautology -- it is the one thing that makes
re-introducing the defect a failing test instead of a silent regression.

The live half -- that PostgreSQL actually stored the reloptions, that
``retention_sweeper`` can reach ``usage.usage_records`` across tenants and
CANNOT reach ``usage.usage_rollups`` -- is in
``tests/integration/test_ledger_autovacuum_live.py``. What a stub structurally
cannot prove is left there.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.framework.settings.settings import EventSettings
from app.ops import retention as retention_module
from app.ops.provision import RETENTION_GRANTS
from app.ops.retention import (
    PROCESSED_EVENTS_RETENTION,
    QUALIFIED,
    USAGE_RECORDS_RETENTION,
    check_retention_floor,
    processed_events_floor,
    sweep_usage_records,
)
from app.ops.table_growth import WATCHED, TableGrowth, warnings

_ROOT = Path(__file__).resolve().parents[2]
_PLATFORM_MIGRATION = _ROOT / "migrations/versions/platform/0005_ledger_autovacuum.py"
_USAGE_MIGRATION = _ROOT / "migrations/versions/usage/0004_usage_autovacuum.py"
_COMPOSE = _ROOT / "docker-compose.yml"


def _settings_of(migration: Path) -> dict[str, str]:
    """The reloptions a migration writes, parsed out of its own source. Read
    from the file rather than imported so the assertion is about what the
    migration APPLIES, not about a constant that could drift from it."""
    source = migration.read_text(encoding="utf-8")
    return dict(re.findall(r"(autovacuum_\w+|fillfactor) = \{?(\w+)\}?", source))


def _resolved(migration: Path) -> dict[str, str]:
    """Same, with the f-string placeholders resolved to the module's own
    constants, so a threshold written as ``{VACUUM_THRESHOLD}`` is compared as
    the number it becomes."""
    source = migration.read_text(encoding="utf-8")
    pairs = re.findall(r"^(\w+) = (\d[\d_]*)$", source, flags=re.MULTILINE)
    names = {name: value.replace("_", "") for name, value in pairs}
    return {k: names.get(v, v) for k, v in _settings_of(migration).items()}


# ----------------------------------------------------- the trigger itself --


@pytest.mark.parametrize("migration", [_PLATFORM_MIGRATION, _USAGE_MIGRATION])
def test_the_proportional_term_is_zero_on_every_ledger(migration: Path) -> None:
    """``ح-16`` in one assertion. With a non-zero ``scale_factor`` the trigger
    RISES with the table -- two million rows must be one-fifth garbage before
    anything starts, and ten million rows must be one-fifth of ten million.
    Zeroing it is what makes the trigger a flat count instead of a tax that
    grows with the backlog."""
    settings = _resolved(migration)

    assert settings["autovacuum_vacuum_scale_factor"] == "0"
    assert settings["autovacuum_analyze_scale_factor"] == "0"
    assert settings["autovacuum_vacuum_insert_scale_factor"] == "0"


@pytest.mark.parametrize("migration", [_PLATFORM_MIGRATION, _USAGE_MIGRATION])
def test_the_absolute_threshold_is_one_naptime_of_peak_production(migration: Path) -> None:
    """The number is derived, not chosen, and this is the derivation as a
    check: the relay cannot move more than ``outbox_relay_batch_size`` per
    ``outbox_poll_interval_ms``, each row dies twice (the publish UPDATE's old
    version, then the retention DELETE), and ``autovacuum_naptime`` is 60 s.
    A threshold far above one naptime's production re-introduces the defect in
    a different unit; far below it cannot fire more often than the naptime
    anyway and only arrives with more work each time."""
    events = EventSettings()
    rows_per_second = events.outbox_relay_batch_size / (events.outbox_poll_interval_ms / 1000)
    dead_per_naptime = rows_per_second * 2 * 60

    threshold = int(_resolved(migration)["autovacuum_vacuum_threshold"])

    assert 0.5 * dead_per_naptime <= threshold <= 2 * dead_per_naptime, (
        f"{threshold:,} is not within one autovacuum naptime of peak dead-tuple production "
        f"({dead_per_naptime:,.0f}); either the number or the derivation in the migration's "
        "docstring has moved without the other."
    )


@pytest.mark.parametrize("migration", [_PLATFORM_MIGRATION, _USAGE_MIGRATION])
def test_analyze_is_triggered_before_vacuum_not_after(migration: Path) -> None:
    """Stale statistics are the OTHER half of ``ح-16``'s damage ("plans degrade
    gradually, with no single event to warn"), and ANALYZE is far cheaper than
    VACUUM. A ledger whose plans are refreshed only when its garbage is
    collected has the cheap fix gated behind the expensive one."""
    settings = _resolved(migration)

    assert int(settings["autovacuum_analyze_threshold"]) < int(
        settings["autovacuum_vacuum_threshold"]
    )


@pytest.mark.parametrize("migration", [_PLATFORM_MIGRATION, _USAGE_MIGRATION])
def test_the_cost_throttle_is_lifted_on_these_tables(migration: Path) -> None:
    """A vacuum that triggers on time and does not FINISH before the next
    naptime never catches up, and the measured cost of not finishing is file
    growth that is never given back. The throttle is removed per table, so
    every other relation in the cluster keeps the server-wide value."""
    assert _resolved(migration)["autovacuum_vacuum_cost_delay"] == "0"


def test_only_the_rollups_get_a_fillfactor_and_the_ledgers_do_not() -> None:
    """HOT updates need an unindexed column to change. ``usage_rollups``'s
    upsert touches only the sum columns, so free space converts index writes
    into in-page rewrites. ``platform.outbox``'s relay UPDATE touches
    ``published_at``, which BOTH partial indexes cover, so HOT is impossible
    there by construction (measured: 3 HOT updates out of 500,000) and free
    space would cost heap for nothing."""
    assert "fillfactor" in _resolved(_USAGE_MIGRATION)
    assert "fillfactor" not in _resolved(_PLATFORM_MIGRATION)

    usage_source = _USAGE_MIGRATION.read_text(encoding="utf-8")
    assert "usage.usage_rollups SET (fillfactor" in usage_source
    assert "usage_records SET (fillfactor" not in usage_source


def test_every_watched_table_is_covered_by_one_of_the_two_migrations() -> None:
    """The report and the migrations cannot disagree about which tables are
    managed: a table added to ``WATCHED`` without settings would be reported
    every run as ``own = NO`` and never fixed."""
    managed = set(
        re.findall(r'"((?:platform|usage)\.\w+)"', _PLATFORM_MIGRATION.read_text(encoding="utf-8"))
    ) | set(
        re.findall(r'"((?:platform|usage)\.\w+)"', _USAGE_MIGRATION.read_text(encoding="utf-8"))
    )

    assert set(WATCHED) <= managed, f"unmanaged: {set(WATCHED) - managed}"


# --------------------------------------------------- the redelivery floor --


def test_the_floor_is_derived_from_the_redelivery_mechanics() -> None:
    """Step 2.8's warning ("no row is deleted before the retry window has fully
    elapsed") as arithmetic: the stale-claim window plus the automatic retry
    ladder, both read off ``EventSettings``."""
    events = EventSettings()
    expected = timedelta(
        seconds=events.consumer_stale_idle_s
        + (events.consumer_block_ms / 1000) * events.max_retries_before_dlq
    )

    assert processed_events_floor(events) == expected


def test_the_floor_moves_when_the_retry_settings_move() -> None:
    """Not a constant with a comment: doubling the retry ladder doubles the
    window in which deleting a dedup row revives a duplicate effect."""
    slow = EventSettings(consumer_stale_idle_s=1800.0, max_retries_before_dlq=10)

    assert processed_events_floor(slow) > processed_events_floor(EventSettings())


def test_sweeping_processed_events_inside_the_window_is_refused() -> None:
    """``--older-than-days 0`` is one keystroke from ``--older-than-days 30``
    and deletes the entire dedup ledger. Before 2.8 that was a paragraph."""
    with pytest.raises(ValueError, match="below processed_events's safe floor"):
        check_retention_floor("processed_events", timedelta(seconds=0))


def test_a_window_above_the_floor_is_allowed() -> None:
    """The floor is the PROVABLE half only. Above it, whether a replay can
    still arrive depends on the DLQ's own depth, which this process cannot
    see -- so it is the operator's call and not a refusal."""
    check_retention_floor("processed_events", processed_events_floor() + timedelta(seconds=1))


def test_the_shipped_default_is_itself_above_the_floor() -> None:
    """``check_retention_floor`` guards the ``--older-than-days`` override, and
    ``sweep_all`` never passes one -- so the default is the one window nothing
    checks at call time. Lowering ``PROCESSED_EVENTS_RETENTION`` under the
    redelivery window would make every unattended sweep unsafe silently."""
    assert processed_events_floor() <= PROCESSED_EVENTS_RETENTION


def test_tables_without_a_provable_floor_are_not_given_an_invented_one() -> None:
    """``outbox``/``idempotency_keys``/``usage_records`` windows are policy,
    and a policy this module cannot check is not one it pretends to."""
    for table in ("outbox", "idempotency_keys", "usage_records"):
        check_retention_floor(table, timedelta(seconds=0))


# ------------------------------------------------ the fourth sweep target --


def test_usage_records_is_swept_and_the_rollups_are_not() -> None:
    """The ledger is the detail and can expire; the rollup is the BALANCE the
    quota check reads (2.7's ``reserve``), so deleting one would hand a
    workspace back headroom it already spent."""
    assert "usage_records" in QUALIFIED
    assert QUALIFIED["usage_records"] == "usage.usage_records"
    assert not any(name.endswith("usage_rollups") for name in QUALIFIED.values())


def test_the_sweep_targets_the_ledger_table_and_the_ninety_day_window() -> None:
    assert timedelta(days=90) == USAGE_RECORDS_RETENTION
    assert retention_module._DEFAULT_RETENTION["usage_records"] == USAGE_RECORDS_RETENTION
    assert retention_module._SWEEPERS["usage_records"] is sweep_usage_records


def test_the_retention_role_is_granted_the_ledger_and_never_the_rollups() -> None:
    """Enforced by the database, not only by this module's choice of
    statement: a future sweep that named ``usage_rollups`` would be denied."""
    granted = " ".join(RETENTION_GRANTS)

    assert "SELECT, DELETE ON usage.usage_records" in granted
    assert "usage_rollups" not in granted
    assert "INSERT" not in granted and "UPDATE" not in granted


# ------------------------------------------------------------ the report --


def _row(**overrides: object) -> TableGrowth:
    base: dict[str, object] = {
        "table": "platform.outbox",
        "live_tuples": 2_000_000,
        "dead_tuples": 0,
        "vacuum_trigger_at": 50_000,
        "heap_bytes": 1302 * 1048576,
        "index_bytes": 151 * 1048576,
        "autovacuum_count": 4,
        "last_autovacuum": None,
        "last_autoanalyze": None,
        "has_own_settings": True,
    }
    return TableGrowth(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_ratio_says_whether_a_table_is_behind_not_merely_dirty() -> None:
    """300,000 dead rows is an emergency on a small table and idle on a large
    one, because the right-hand side of the inequality moves with the table.
    The ratio is what makes the two distinguishable in one column."""
    assert _row(dead_tuples=25_000).trigger_ratio == pytest.approx(0.5)
    assert _row(dead_tuples=100_000).trigger_ratio == pytest.approx(2.0)


def test_bytes_per_row_is_the_column_that_moves_when_a_table_bloats() -> None:
    """At a constant row count every byte of growth is bloat. ``n_dead_tup``
    reads near zero right after a vacuum that reclaimed nothing to the
    filesystem; this does not."""
    packed = _row(heap_bytes=1302 * 1048576)
    bloated = _row(heap_bytes=1668 * 1048576)

    assert bloated.bytes_per_row > packed.bytes_per_row


def test_a_table_still_on_the_server_wide_trigger_is_called_out() -> None:
    notes = warnings([_row(has_own_settings=False)], maintenance_bytes=0, workers=0)

    assert any("SERVER-WIDE" in note and "platform.outbox" in note for note in notes)


def test_a_table_past_its_trigger_is_called_out_with_both_numbers() -> None:
    notes = warnings([_row(dead_tuples=492_335)], maintenance_bytes=0, workers=0)

    assert any("492,335 dead vs 50,000" in note for note in notes)


def test_a_table_that_has_never_been_autovacuumed_is_called_out() -> None:
    notes = warnings([_row(autovacuum_count=0)], maintenance_bytes=0, workers=0)

    assert any("never been autovacuumed" in note for note in notes)


def test_an_empty_table_that_has_never_been_vacuumed_is_not_called_out() -> None:
    """A table with no rows has nothing to clean; warning about it is how a
    report trains its reader to skip warnings."""
    notes = warnings([_row(live_tuples=0, autovacuum_count=0)], maintenance_bytes=0, workers=0)

    assert not any("never been autovacuumed" in note for note in notes)


# ------------------------------------------------------------- /dev/shm --


def test_compose_gives_postgres_more_shm_than_maintenance_work_mem_asks_for() -> None:
    """Docker's default ``/dev/shm`` is 64M and parallel VACUUM asks for
    ``maintenance_work_mem`` (default 64M) plus a header, so the request cannot
    fit the ceiling it is drawn from -- measured: ``could not resize shared
    memory segment ... to 67128960 bytes`` on a 2,000,000-row table, and the
    identical VACUUM finishing in 2.36 s with parallelism off. Step 2.1
    declares ``maintenance_work_mem=1GB``, so the ceiling must clear THAT, not
    only today's default."""
    compose = _COMPOSE.read_text(encoding="utf-8")

    match = re.search(r"^\s*shm_size:\s*(\d+)(g|m)b\s*$", compose, flags=re.MULTILINE | re.I)
    assert match is not None, "docker-compose.yml's postgres service sets no shm_size"

    size_mb = int(match.group(1)) * (1024 if match.group(2).lower() == "g" else 1)
    assert size_mb >= 1024, (
        f"shm_size is {size_mb}M; capacity step 2.1 sets maintenance_work_mem=1GB and parallel "
        "VACUUM sizes its segment to that value, so anything smaller re-introduces the failure "
        "2.8 measured -- as a regression nobody would attribute to a memory knob."
    )


def test_the_shm_note_is_only_printed_when_parallel_maintenance_is_enabled() -> None:
    """With no parallel maintenance workers there is no DSM segment to fail on,
    so the note would be noise."""
    with_workers = warnings([_row()], maintenance_bytes=64 * 1048576, workers=2)
    without = warnings([_row()], maintenance_bytes=64 * 1048576, workers=0)

    assert any("/dev/shm" in note for note in with_workers)
    assert not any("/dev/shm" in note for note in without)
