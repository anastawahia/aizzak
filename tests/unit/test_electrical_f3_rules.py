"""F3 (FR-ID-16, FR-ID-20, FR-ID-06, D-37): ANSI numbers without context, ratings, page zero,
location references and determinism. Every example here is invented (F3-AC-28)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.normalization import folded
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.ratings import Rating, read_rating
from tests.eval.electrical.m04_identifiers import (
    expected_shape,
    load_cases,
    load_rules,
    observed,
    parse_case,
)
from tests.unit.support_electrical_shared import parser_for

_DEVICE = IdentifierKind.ANSI_DEVICE
_ANSI = parser_for(Convention.IEC_81346, Convention.ANSI)


def _read(
    text: str, parser: IdentifierParser = _ANSI, hint: IdentifierKind | None = None
) -> ParsedIdentifier:
    found = parser.parse(text, hint=hint)
    assert found is not None, f"{text!r} did not parse"
    return found


# --- ANSI without context (F3-Q1 (c), F3-Q2 (b)) ---------------------------------------


@pytest.mark.parametrize("text", ["8Y", "8P", "11R", "16F", "23-4", "4-23"])
def test_f3_ac_1_ansi_without_a_class_needs_context(text: str) -> None:
    assert _ANSI.parse(text) is None
    # with the context (a bubble, a stacked label) it is read as before: a function, no class
    found = _read(text, hint=_DEVICE)
    assert found.kind is _DEVICE and found.device_class is None and found.alternatives == ()


def test_f3_ac_1_a_compound_of_numbers_outside_the_table_is_a_cross_reference() -> None:
    found = _read("23/4")
    assert found.kind is IdentifierKind.CROSS_REFERENCE and found.variant == "page_slash_column"
    assert found.alternatives == (_DEVICE,)
    in_context = _read("23/4", hint=_DEVICE)
    assert in_context.kind is _DEVICE and in_context.alternatives == ()
    # a compound where ONE number is outside the table is no device either
    assert _read("50/23").kind is IdentifierKind.CROSS_REFERENCE
    assert _read("50/51").kind is _DEVICE


def test_f3_ac_1_16f_is_invalid_with_context_for_its_suffix() -> None:
    found = _read("16F", hint=_DEVICE)
    assert not found.valid and found.issues == ("ansi.unknown_suffix",)


@pytest.mark.parametrize("text", ["87T", "50BF", "51N", "27-1", "50/51", "87QZ"])
def test_f3_ac_2_classed_ansi_reads_as_in_f2(text: str) -> None:
    found = _read(text)
    assert found.kind is _DEVICE and found.convention is Convention.ANSI
    assert found.valid is (text != "87QZ")
    assert (found.ansi is not None) and found.ansi.functions[0].function_class is not None
    off = parser_for(Convention.IEC_81346).parse(text)
    assert off is None or off.kind is not _DEVICE


def test_f3_ac_2_the_m04_cases_change_only_at_c226() -> None:
    """The core cases (c001..c192, no `select`) read the same with ANSI on or off, except for
    the added alternative; and c226 (12/3, ANSI on) is now a cross-reference with the ANSI
    alternative, like c227, where F2 read it as the compound."""
    cases = [c for c in load_cases() if "select" not in c and int(str(c["id"])[1:]) <= 192]
    rules = load_rules()
    on = IdentifierParser(replace(rules, conventions=rules.conventions | {Convention.ANSI}))
    off = IdentifierParser(rules)
    for case in cases:
        after, before = observed(parse_case(on, case)), observed(parse_case(off, case))
        if after == before:
            continue
        assert after["kind"] == before["kind"], case["id"]
        assert {k: v for k, v in after.items() if k != "alternatives"} == before, case["id"]
        assert after["alternatives"] == ["ansi_device"], case["id"]

    c226 = next(c for c in load_cases() if c["id"] == "c226")
    assert c226["text"] == "12/3" and c226["kind"] == "cross_reference"
    assert c226["alternatives"] == ["ansi_device"]
    assert observed(parse_case(off, c226)) == expected_shape(c226)
    assert expected_shape(c226)["kind"] == "cross_reference"


# --- a named vendor wire pattern wins (F3-AC-3) -----------------------------------------


def test_f3_ac_3_a_named_vendor_wire_pattern_wins_over_ansi() -> None:
    named = parser_for(
        Convention.IEC_81346,
        Convention.ANSI,
        wire_number_patterns=("sequential", "digits_letter"),
    )
    for text in ("38Y", "59Y"):  # 59 has a class; the named pattern still wins
        found = _read(text, named)
        assert found.kind is IdentifierKind.WIRE_NUMBER and found.variant == "digits_letter"
        assert found.alternatives == (_DEVICE,)
    # ... but not with the context
    assert _read("59Y", named, hint=_DEVICE).kind is _DEVICE
    # the profile does not name it: 38Y is nothing, 59Y a device
    plain = parser_for(Convention.IEC_81346, Convention.ANSI)
    assert plain.parse("38Y") is None
    assert _read("59Y", plain).kind is _DEVICE
    # a profile that names patterns but not this one reads as the plain one
    only_sequential = parser_for(
        Convention.IEC_81346, Convention.ANSI, wire_number_patterns=("sequential",)
    )
    assert only_sequential.parse("38Y") is None and _read("59Y", only_sequential).kind is _DEVICE


def test_f3_ac_3_letter_digits_is_read_only_when_named() -> None:
    assert parser_for(Convention.IEC_81346).parse("P12") is None
    named = parser_for(Convention.IEC_81346, wire_number_patterns=("letter_digits",))
    found = _read("P12", named)
    assert found.kind is IdentifierKind.WIRE_NUMBER and found.variant == "letter_digits"


def test_opt_in_patterns_never_read_unnarrowed() -> None:
    """The packaged rules without a selection never read an opt-in pattern, with any
    convention on, so the cases without `select` cannot see them."""
    rules = load_rules()
    wide = IdentifierParser(replace(rules, conventions=frozenset(Convention)))
    for case in load_cases():
        if "select" in case:
            continue
        found = parse_case(wide, case)
        assert found is None or found.variant not in {"digits_letter", "letter_digits"}
    for text in ("38Y", "P12", "9Z"):
        found = wide.parse(text)
        assert found is None or found.kind is not IdentifierKind.WIRE_NUMBER


def test_f3_ac_5_a_digits_first_terminal_is_no_ansi_under_nfpa() -> None:
    nfpa = parser_for(Convention.NFPA_JIC, Convention.ANSI)
    assert nfpa.parse("3X-41") is None
    iec = _read("3X-41")
    assert iec.kind is IdentifierKind.TERMINAL and iec.convention is Convention.IEC_81346


# --- ratings are no identifiers (F3-AC-7, 8) ---------------------------------------------

_EVERYTHING = parser_for(
    Convention.IEC_81346,
    Convention.GBT_5094,
    Convention.CN_LETTER_CODES,
    Convention.NFPA_JIC,
    Convention.KKS,
    Convention.ANSI,
)
_CN = parser_for(Convention.CN_LETTER_CODES)


@pytest.mark.parametrize(
    "text", ["37MVA", "47Hz", "17VA", "29kA", "2PH", "7KW", "13mA", "7-29kV", "0.7kV"]
)
def test_f3_ac_7_ratings_are_no_identifiers(text: str) -> None:
    assert _EVERYTHING.parse(text) is None
    assert parser_for(Convention.IEC_81346).parse(text) is None


def test_f3_ac_7_a_unit_that_is_a_letter_code_matches_as_written() -> None:
    assert _CN.parse("37kV") is None
    assert _CN.parse("29kA") is None
    for text in ("3KA", "37KV"):  # the shape of a relay, not the rules' unit
        found = _read(text, _CN)
        assert found.kind is IdentifierKind.LETTER_CODE, text
        assert found.convention is Convention.CN_LETTER_CODES
    # the case does not matter when no enabled table has the code
    assert parser_for(Convention.IEC_81346).parse("3KA") is None


def test_f3_ac_7_a_rating_is_a_value_of_its_own() -> None:
    rules = _EVERYTHING.rules
    assert read_rating(folded("7-29kV"), rules) == Rating("7-29", "kV")
    assert read_rating(folded("0.7 KW"), rules) == Rating("0.7", "kW")
    for text in ("", "kV", "37", "37k", "37xyz", "37kV2", "1.2.3kV", "1234567kV", "37 kV x"):
        assert read_rating(folded(text), rules) is None, text


@pytest.mark.parametrize("text", ["24VDC", "+24V", "0V"])
def test_f3_ac_8_voltage_labels_win(text: str) -> None:
    found = _read(text, _EVERYTHING)
    assert found.kind is IdentifierKind.VOLTAGE and found.valid


def test_f3_ac_8_a_voltage_label_the_profile_added_stays_a_voltage() -> None:
    plain = parser_for(Convention.IEC_81346)
    assert plain.parse("12VDC") is None  # a rating, until the profile says it is a label
    added = parser_for(Convention.IEC_81346, extra_voltage_labels=("12VDC",))
    assert _read("12VDC", added).kind is IdentifierKind.VOLTAGE


def test_a_rating_with_a_hint_is_left_to_the_hinted_reader() -> None:
    """The barrier is for the text without context only: a caller with a hint resolved it."""
    found = _ANSI.parse("37MVA", hint=_DEVICE)
    assert found is not None and found.kind is _DEVICE and not found.valid


# --- page zero is no page (F3-AC-10) ------------------------------------------------------


@pytest.mark.parametrize("text", ["0.7", "0.9", "/0.7", "00.5", "0/3"])
def test_f3_ac_10_page_zero_is_no_page(text: str) -> None:
    assert IdentifierParser(load_rules()).parse(text) is None, text


@pytest.mark.parametrize(
    ("text", "variant"),
    [
        ("12.3", "page_dot_column"),
        ("/12.3", "slash_page_dot_column"),
        ("=A1/12.3", "prefixed_slash"),
        ("12/3", "page_slash_column"),
        ("10.05", "page_dot_column"),
    ],
)
def test_f3_ac_10_other_pages_read_as_before(text: str, variant: str) -> None:
    found = IdentifierParser(load_rules()).parse(text)
    assert found is not None and found.kind is IdentifierKind.CROSS_REFERENCE
    assert found.variant == variant
