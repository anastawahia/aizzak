"""FR-ID-03, FR-ID-04, FR-ID-07, FR-ID-08 / INC1-AC-4: terminals, bridges, wire numbers,
connection points and voltage labels."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from tests.unit.support_electrical_shared import PARSER, aspects, parse

_TERMINAL = IdentifierKind.TERMINAL


@pytest.mark.parametrize(
    ("text", "point", "convention"),
    [
        ("-X1:14", "14", Convention.IEC_81346),
        ("X1.14", "14", Convention.IEC_81346),
        ("X1-14", "14", Convention.IEC_81346),
        ("=A1+P2-X1:14", "14", Convention.IEC_81346),
        ("XT1:5", "5", Convention.CN_LETTER_CODES),
        ("1XT-5", "5", Convention.CN_LETTER_CODES),
        ("XT1" + chr(0xFF1A) + "5", "5", Convention.CN_LETTER_CODES),
        ("TB1:2", "2", Convention.NFPA_JIC),
        ("X1:PE", "PE", Convention.IEC_81346),
    ],
)
def test_every_terminal_form_of_the_srs(text: str, point: str, convention: Convention) -> None:
    found = parse(text)
    assert found.kind is _TERMINAL
    assert found.connection_point == point
    assert found.convention is convention
    assert found.valid


def test_one_terminal_in_several_spellings_shares_a_search_key() -> None:
    spellings = ("-X1:14", "X1.14", "X1-14", "X01:014", "=A1+P2-X1:14")
    assert {"X1:14" in parse(t).forms.search_keys for t in spellings} == {True}


def test_the_aspects_of_a_terminal_are_kept() -> None:
    assert aspects(parse("=A1+P2-X1:14")) == ["=A1", "+P2", "-X1"]


def test_a_strip_without_a_terminal_is_a_designation_not_a_terminal() -> None:
    assert parse("-X1").kind is IdentifierKind.REFERENCE_DESIGNATION


@pytest.mark.parametrize("text", ["X1.", "X1:", "1XT-"])
def test_a_terminal_without_its_number_is_flagged(text: str) -> None:
    found = parse(text)
    assert found.kind is _TERMINAL and found.valid is False


def test_bridges() -> None:
    bridge = parse("X1:1~X1:2")
    assert bridge.kind is IdentifierKind.BRIDGE
    assert bridge.connection_point == "1~2"
    short = parse("X1:1~3")  # the right side inherits the strip
    assert short.connection_point == "1~3"
    assert "X1:1~X1:3" in short.forms.search_keys
    assert parse("X1:1~").valid is False
    assert PARSER.parse("X1:1~??") is None


@pytest.mark.parametrize(
    ("text", "variant"),
    [
        ("123", "sequential"),
        ("W123", "sequential"),
        ("12.3-1", "page_column"),
        ("L1-5", "voltage_based"),
        ("U11", "voltage_based"),
        ("V11", "voltage_based"),
        ("W11", "sequential"),  # first matching pattern in the file wins
    ],
)
def test_wire_numbers_by_configured_pattern(text: str, variant: str) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.WIRE_NUMBER
    assert found.variant == variant


def test_the_page_column_wire_needs_a_hint_because_12_3_is_a_cross_reference() -> None:
    assert parse("12.3").kind is IdentifierKind.CROSS_REFERENCE
    hinted = parse("12.3", hint=IdentifierKind.WIRE_NUMBER)
    assert (hinted.kind, hinted.variant) == (IdentifierKind.WIRE_NUMBER, "page_column")


def test_wire_number_search_keys_drop_leading_zeros() -> None:
    assert "W123" in parse("W00123").forms.search_keys


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("A1", "coil"),
        ("A2", "coil"),
        ("13", "aux_no"),
        ("22", "aux_nc"),
        ("1", "main_pole"),
        ("6", "main_pole"),
        ("95", "overload_nc"),
        ("98", "overload_no"),
    ],
)
def test_connection_point_labels_and_pole_roles(text: str, role: str) -> None:
    found = parse(text, hint=IdentifierKind.CONNECTION_POINT)
    assert found.kind is IdentifierKind.CONNECTION_POINT
    assert found.pole_role == role


def test_a_label_that_is_also_a_number_reads_as_a_wire_unless_hinted() -> None:
    assert parse("13").kind is IdentifierKind.WIRE_NUMBER
    assert parse("A1").kind is IdentifierKind.CONNECTION_POINT


@pytest.mark.parametrize(
    "text", ["L1", "L2", "L3", "N", "PE", "L+", "M", "+24V", "0V", "24VDC", "L-"]
)
def test_voltage_labels(text: str) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.VOLTAGE and found.valid


def test_voltage_wins_over_the_aspect_reading_of_plus_24v() -> None:
    assert parse("+24V").kind is IdentifierKind.VOLTAGE
