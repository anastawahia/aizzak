"""FR-ID-03, FR-ID-04, FR-ID-07, FR-ID-08 / INC1-AC-4: terminals, bridges, wire numbers,
connection points and voltage labels."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules, RuleError
from tests.unit.support_electrical_shared import (
    PARSER,
    _selection,
    aspects,
    parse,
    parser_for,
)
from tests.unit.test_electrical_shared_rules import _raw

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


# --- F2 (D-36, A-5): digits-first strips read under the rules' conventions --------------


def test_f2_ac_41_a_digits_first_x_strip_reads_under_iec_and_gbt() -> None:
    iec = parser_for(Convention.IEC_81346).parse("1X-25")
    assert iec is not None and iec.kind is _TERMINAL and iec.valid
    assert iec.convention is Convention.IEC_81346
    assert iec.connection_point == "25" and iec.device_class == "terminal_strip"
    assert iec.forms.search_keys == ("1X-25", "1X:25")
    gbt = parser_for(Convention.GBT_5094).parse("1X-25")
    assert gbt is not None and gbt.convention is Convention.GBT_5094
    both = parser_for(Convention.IEC_81346, Convention.GBT_5094).parse("1X-25")
    assert both is not None and both.convention is Convention.IEC_81346  # the table's order


def test_f2_ac_42_the_chinese_reading_and_the_xt_rule_stay() -> None:
    plain = parse("1X-25")  # no narrowing: cn_letter_codes is first, as on master
    assert plain.kind is _TERMINAL and plain.convention is Convention.CN_LETTER_CODES
    assert parse("1XT-5").convention is Convention.CN_LETTER_CODES
    assert parser_for(Convention.IEC_81346).parse("1XT-5") is None  # XT stays Chinese-only
    assert parser_for(Convention.NFPA_JIC).parse("1X-25") is None
    assert (
        parser_for(Convention.IEC_81346).parse("X1-14") is not None
    )  # the first form is unchanged
    chinese = parser_for(Convention.CN_LETTER_CODES, Convention.IEC_81346).parse("1X-25")
    assert chinese is not None and chinese.convention is Convention.CN_LETTER_CODES


def _read(parser: IdentifierParser, text: str) -> ParsedIdentifier:
    found = parser.parse(text)
    assert found is not None, text
    return found


def test_f3_ac_20_letters_first_conventions_are_data() -> None:
    cn = parser_for(Convention.CN_LETTER_CODES)
    found = _read(cn, "X7:41")
    assert found.kind is _TERMINAL and found.convention is Convention.CN_LETTER_CODES
    # the unnarrowed rules, and IEC / GB/T, still read X under IEC first
    assert parse("X7:41").convention is Convention.IEC_81346
    both = parser_for(Convention.IEC_81346, Convention.CN_LETTER_CODES)
    assert _read(both, "X7:41").convention is Convention.IEC_81346
    gbt = parser_for(Convention.GBT_5094, Convention.CN_LETTER_CODES)
    assert _read(gbt, "X7:41").convention is Convention.GBT_5094
    # XT and TB keep their conventions
    assert _read(cn, "XT1:5").convention is Convention.CN_LETTER_CODES
    assert _read(parser_for(Convention.NFPA_JIC), "TB1:2").convention is Convention.NFPA_JIC
    assert parser_for(Convention.IEC_81346).parse("XT1:5") is None
    # without the table the reading is today's: X is IEC / GB/T only, so a Chinese-only file
    # reads no X strip
    bare = IdentifierRules.from_mapping(_without_letters_first())
    assert bare.terminal_letters_first == {}
    chinese = IdentifierParser(
        bare.narrowed(_selection(conventions=frozenset({Convention.CN_LETTER_CODES})))
    )
    assert chinese.parse("X7:41") is None and chinese.parse("XT1:5") is not None


def _without_letters_first() -> dict[str, Any]:
    data = copy.deepcopy(_raw())
    del data["terminals"]["letters_first"]
    return data


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["terminals"]["letters_first"].update(X=[]),
        lambda d: d["terminals"]["letters_first"].update(X=["nope"]),
        lambda d: d["terminals"]["letters_first"].update(ZZ=["iec_81346"]),
    ],
)
def test_f3_ac_20_a_bad_letters_first_entry_is_a_rule_error(mutate: Any) -> None:
    data = copy.deepcopy(_raw())
    mutate(data)
    with pytest.raises(RuleError):
        IdentifierRules.from_mapping(data)


@pytest.mark.parametrize(
    ("text", "point"), [("1X-37'", "37'"), ('1X-37"', '37"'), ("1X-37" + chr(0x2032), "37'")]
)
def test_f3_ac_19_a_prime_after_the_terminal_number_is_part_of_it(text: str, point: str) -> None:
    iec = parser_for(Convention.IEC_81346)
    found = _read(iec, text)
    assert found.kind is _TERMINAL and found.valid and found.connection_point == point
    plain = _read(iec, "1X-37")
    assert plain.forms.canonical != found.forms.canonical
    assert not set(plain.forms.search_keys) & set(found.forms.search_keys)
    # the chain form and a letter code's point are as before
    assert iec.parse("-K5:13'") is None
    cn_code = _read(parser_for(Convention.CN_LETTER_CODES), "KM1:13'")
    assert not cn_code.valid
