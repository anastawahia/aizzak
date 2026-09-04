"""Capacity step 2.5's arithmetic and its refusals, without a server.

Three families, and each exists because getting it wrong fails SILENTLY:

1. **WAL continuity.** Recovery stops at the first missing segment; the
   segments after it help nobody. A hole in the archive is therefore not a
   degradation, it is the end of the recoverable window -- and it is
   invisible in a bucket listing, which shows the newest object either way.
2. **Prune safety.** "Delete WAL older than N days" is how a point-in-time
   restore dies without anybody noticing: the base backup is still on the
   shelf and the segment it starts from is gone. The floor is computed from
   the surviving base backups, never from the clock.
3. **The dump's refusals.** A `pg_dump` taken by a role without `BYPASSRLS`
   is either a loud failure or -- with the flag the error message invites --
   a successful backup containing no tenant rows at all (module docstring:
   measured, 202 workspaces and zero users). The refusal is the feature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ops.backup import (
    BASE_RETENTION,
    DUMP_RETENTION,
    MIN_KEPT_SETS,
    BackupError,
    Preflight,
    _async_url,
    _expired,
    _libpq_dsn,
    _parse_timestamp,
    plan_prune,
    segment_sequence,
    wal_inventory,
)

_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _stamp(offset: timedelta) -> str:
    return (_NOW - offset).strftime("%Y%m%dT%H%M%SZ")


def _base(
    offset: timedelta, start_wal: str | None = "000000010000000000000010"
) -> dict[str, object]:
    return {"set": _stamp(offset), "start_wal_file": start_wal}


# ------------------------------------------------------ segment sequence --


def test_consecutive_segments_differ_by_exactly_one() -> None:
    """A segment name is timeline(8) + log id(8) + segment(8) in hex, and a
    log id holds 256 segments at the default 16MB size."""
    assert segment_sequence("0000000100000000000000FE") is not None
    assert (
        segment_sequence("0000000100000000000000FF")
        == segment_sequence("0000000100000000000000FE") + 1
    )


def test_the_log_id_rolls_over_after_ff_and_not_after_fe() -> None:
    """Since PostgreSQL 9.3 the ``...FF`` segment IS used. A tool that still
    skips it (the pre-9.3 arithmetic, and the shape most hand-written
    checkers copy) reports a gap at every rollover on a healthy archive --
    and an operator who has learned to ignore those reports will ignore the
    real one too."""
    last_of_log = segment_sequence("0000000100000000000000FF")
    first_of_next = segment_sequence("000000010000000100000000")
    assert first_of_next == last_of_log + 1


def test_a_name_that_is_not_a_segment_has_no_sequence() -> None:
    assert segment_sequence("00000001.history") is None
    assert segment_sequence("000000010000000000000010.00000028.backup") is None
    assert segment_sequence("aizzak.dump") is None


# ------------------------------------------------------------- inventory --


def test_a_complete_archive_reports_no_gaps() -> None:
    names = [f"00000001000000000000{n:04X}" for n in range(0x10, 0x20)]
    inventory = wal_inventory(names)

    assert inventory.gaps == []
    assert inventory.oldest == "000000010000000000000010"
    assert inventory.newest == "00000001000000000000001F"


def test_a_missing_segment_is_reported_as_the_pair_that_straddles_it() -> None:
    """The PAIR, not a count: recovery from a base backup older than the left
    name can never pass it, which is the fact an operator has to act on."""
    names = [
        "000000010000000000000010",
        "000000010000000000000011",
        # 12 was deleted by a date-based rule somebody added
        "000000010000000000000013",
    ]
    assert wal_inventory(names).gaps == [("000000010000000000000011", "000000010000000000000013")]


def test_history_and_backup_files_are_kept_but_take_no_part_in_continuity() -> None:
    inventory = wal_inventory(
        [
            "000000010000000000000010",
            "000000010000000000000011",
            "00000002.history",
        ]
    )

    assert inventory.gaps == []
    assert inventory.others == ["00000002.history"]


# ----------------------------------------------------------------- prune --


def test_wal_is_pruned_against_the_oldest_SURVIVING_base_and_never_the_clock() -> None:
    """The whole point of the module's retention paragraph, as one case: an
    ancient segment stays because a base backup that survives the sweep still
    starts from it."""
    plan = plan_prune(
        bases=[
            _base(timedelta(days=6), start_wal="000000010000000000000010"),
            _base(timedelta(days=1), start_wal="000000010000000000000030"),
        ],
        dumps=[],
        vectors=[],
        archived=[f"00000001000000000000{n:04X}" for n in range(0x08, 0x40)],
        now=_NOW,
    )

    assert plan.base_sets == []  # both inside BASE_RETENTION
    assert plan.wal_floor == "000000010000000000000010"
    # Everything below the floor, and nothing at or above it.
    assert plan.wal_segments == [f"00000001000000000000{n:04X}" for n in range(0x08, 0x10)]


def test_the_floor_moves_up_only_when_the_base_that_needed_it_is_swept() -> None:
    plan = plan_prune(
        bases=[
            _base(BASE_RETENTION + timedelta(days=1), start_wal="000000010000000000000010"),
            _base(timedelta(days=1), start_wal="000000010000000000000030"),
        ],
        dumps=[],
        vectors=[],
        archived=[f"00000001000000000000{n:04X}" for n in range(0x08, 0x40)],
        now=_NOW,
    )

    assert plan.base_sets == [_stamp(BASE_RETENTION + timedelta(days=1))]
    assert plan.wal_floor == "000000010000000000000030"


def test_no_base_backup_names_a_starting_segment_and_nothing_is_pruned() -> None:
    """Refusing to prune costs storage. Pruning the segment a restore needs
    costs the restore, and the cost is discovered during the incident."""
    plan = plan_prune(
        bases=[_base(timedelta(days=1), start_wal=None)],
        dumps=[],
        vectors=[],
        archived=[f"00000001000000000000{n:04X}" for n in range(0x08, 0x40)],
        now=_NOW,
    )

    assert plan.wal_floor is None
    assert plan.wal_segments == []


def test_an_empty_shelf_is_never_a_legal_outcome() -> None:
    """Every set is far past its window; exactly ``MIN_KEPT_SETS`` survive.
    A retention policy that can leave zero backups is not a policy."""
    ancient = [_base(timedelta(days=400) + timedelta(days=n)) for n in range(4)]
    plan = plan_prune(bases=ancient, dumps=[], vectors=[], archived=[], now=_NOW)

    assert len(plan.base_sets) == len(ancient) - MIN_KEPT_SETS


def test_the_survivor_is_the_newest_and_not_whichever_was_listed_first() -> None:
    ancient = [_base(timedelta(days=400)), _base(timedelta(days=500))]
    plan = plan_prune(bases=ancient, dumps=[], vectors=[], archived=[], now=_NOW)

    survivors = {item["set"] for item in ancient} - set(plan.base_sets)
    assert survivors == {_stamp(timedelta(days=400))}


def test_each_artifact_class_ages_on_its_own_window() -> None:
    """Same age, different verdicts -- which is the only reason the three
    constants are three constants."""
    age = (BASE_RETENTION + DUMP_RETENTION) / 2
    sets = [{"set": _stamp(age)}, {"set": _stamp(timedelta(0))}]

    assert _expired(sets, BASE_RETENTION, _NOW) == [_stamp(age)]
    assert _expired(sets, DUMP_RETENTION, _NOW) == []


def test_a_set_name_that_is_not_a_timestamp_is_never_swept() -> None:
    """An object nobody can date is an object nobody may delete."""
    sets = [{"set": "handmade-copy"}, {"set": _stamp(timedelta(0))}]

    assert _expired(sets, timedelta(seconds=1), _NOW) == []
    assert _parse_timestamp("handmade-copy") is None


# ----------------------------------------------------------------- dsn --


def test_the_libpq_dsn_drops_the_driver_and_the_password() -> None:
    """`postgresql+asyncpg://` is SQLAlchemy's spelling and libpq rejects it;
    the password is removed because every consumer is a subprocess and
    `/proc/<pid>/cmdline` is world-readable."""
    dsn, password = _libpq_dsn("postgresql+asyncpg://backup_operator:s3cret@postgres:5432/aizzak")

    assert dsn == "postgresql://backup_operator@postgres:5432/aizzak"
    assert password == "s3cret"
    assert "s3cret" not in dsn


def test_the_async_url_round_trips_back_to_the_same_connection() -> None:
    dsn, password = _libpq_dsn("postgresql://backup_operator:s3cret@postgres:5432/aizzak")

    assert _async_url(dsn, password) == (
        "postgresql+asyncpg://backup_operator:s3cret@postgres:5432/aizzak"
    )


# ------------------------------------------------------------ preflight --


def _checks(**overrides: object) -> Preflight:
    fields: dict[str, object] = {
        "role": "backup_operator",
        "bypass_rls": True,
        "replication": True,
        "server_version": "16.14",
        "system_identifier": 1,
        "archive_mode": "on",
        "archive_command": "/bin/sh /usr/local/bin/archive_wal.sh %p %f",
        "wal_level": "replica",
        "host": "172.20.0.2",
    }
    fields.update(overrides)
    return Preflight(**fields)  # type: ignore[arg-type]


def test_a_role_without_bypassrls_may_not_take_a_dump_at_all() -> None:
    """The measured failure this whole step turns on: without BYPASSRLS the
    dump either errors on the first tenant table or, with
    `--enable-row-security`, exits 0 carrying none of them."""
    with pytest.raises(BackupError, match="BYPASSRLS"):
        _checks(bypass_rls=False).require_dump()


def test_a_role_without_replication_may_not_take_a_base_backup() -> None:
    with pytest.raises(BackupError, match="REPLICATION"):
        _checks(replication=False).require_base()


def test_a_base_backup_is_refused_on_a_cluster_that_does_not_archive() -> None:
    """It would succeed, and it would be restorable to exactly one instant --
    a "point-in-time" backup with no point to choose. Refused at the point
    where the operator can still fix it, not discovered during the restore."""
    with pytest.raises(BackupError, match="archive_mode"):
        _checks(archive_mode="off").require_archiving()


def test_a_correctly_privileged_role_passes_all_three() -> None:
    checks = _checks()

    checks.require_dump()
    checks.require_base()
    checks.require_archiving()
