"""Unit tests for the spaces domain — the name value object and the aggregate.
Pure: no infrastructure, no ports."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.framework.clock import utc_now
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.errors import InvalidSpaceInput, SpaceStateError
from app.modules.spaces.domain.value_objects import SpaceName


def _space(*, deleted: bool = False, name: str = "Research") -> Space:
    now = utc_now()
    return Space(
        id="s1",
        workspace_id="w1",
        name=SpaceName(name),
        created_by="u1",
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
        version=1,
    )


# --------------------------------------------------------------------------- #
# SpaceName                                                                    #
# --------------------------------------------------------------------------- #
def test_space_name_trims_whitespace() -> None:
    assert SpaceName("  Research  ").value == "Research"


def test_space_name_accepts_boundary_length() -> None:
    assert len(SpaceName("x" * 120).value) == 120


def test_space_name_rejects_one_over_the_column_check() -> None:
    # `CHECK (char_length(name) BETWEEN 1 AND 120)` — the value object exists
    # so this is a 422 with a reason and not a 500 from a constraint.
    with pytest.raises(InvalidSpaceInput):
        SpaceName("x" * 121)


def test_space_name_measures_length_after_trimming() -> None:
    # What the column stores is the TRIMMED value, so that is what the limit
    # applies to: padding a 120-character name must not make it invalid.
    assert len(SpaceName("  " + "x" * 120 + "  ").value) == 120


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_space_name_rejects_blank(value: str) -> None:
    with pytest.raises(InvalidSpaceInput):
        SpaceName(value)


def test_space_name_rejects_control_characters() -> None:
    with pytest.raises(InvalidSpaceInput):
        SpaceName("bad\x00name")


def test_space_name_keeps_slashes() -> None:
    # NOT a path (the one deliberate divergence from `FileName`): a space name
    # is a label a person typed, and cutting it at the slash would silently
    # rename "R&D / 2026" to "2026".
    assert SpaceName("R&D / 2026").value == "R&D / 2026"


# --------------------------------------------------------------------------- #
# Space.rename                                                                 #
# --------------------------------------------------------------------------- #
def test_rename_changes_the_name_and_stamps_the_time() -> None:
    space = _space()
    later = space.updated_at + timedelta(minutes=1)
    space.rename(SpaceName("Drafts"), later)
    assert space.name.value == "Drafts"
    assert space.updated_at == later


def test_rename_to_the_same_name_is_a_no_op() -> None:
    space = _space()
    before = space.updated_at
    space.rename(SpaceName("Research"), before + timedelta(minutes=1))
    assert space.updated_at == before


def test_rename_to_the_same_name_after_trimming_is_a_no_op() -> None:
    # The comparison happens on the value object's NORMALIZED value, so
    # "  Research  " is the name it already has.
    space = _space()
    before = space.updated_at
    space.rename(SpaceName("  Research  "), before + timedelta(minutes=1))
    assert space.updated_at == before


def test_rename_that_only_changes_case_is_a_real_change() -> None:
    # Only the UNIQUENESS rule folds case (`lower(name)`); the stored name is
    # what a person reads, so "Research" -> "research" is applied.
    space = _space()
    later = space.updated_at + timedelta(minutes=1)
    space.rename(SpaceName("research"), later)
    assert space.name.value == "research"
    assert space.updated_at == later


def test_rename_refuses_a_deleted_space() -> None:
    space = _space(deleted=True)
    with pytest.raises(SpaceStateError):
        space.rename(SpaceName("Drafts"), utc_now())


# --------------------------------------------------------------------------- #
# Space.soft_delete / is_active                                                #
# --------------------------------------------------------------------------- #
def test_soft_delete_marks_and_stamps() -> None:
    space = _space()
    now = space.updated_at + timedelta(minutes=1)
    space.soft_delete(now)
    assert space.deleted_at == now
    assert space.updated_at == now


def test_soft_delete_is_idempotent() -> None:
    space = _space()
    first = space.updated_at + timedelta(minutes=1)
    space.soft_delete(first)
    space.soft_delete(first + timedelta(minutes=1))
    assert space.deleted_at == first
    assert space.updated_at == first


def test_is_active_tracks_deletion() -> None:
    space = _space()
    assert space.is_active is True
    space.soft_delete(utc_now())
    assert space.is_active is False
