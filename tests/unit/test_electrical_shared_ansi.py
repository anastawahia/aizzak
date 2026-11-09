"""FR-ID-16 / US-2 (F2-AC-8..12): ANSI device function numbers, by number, suffix and our own
class names. Every example here is an abstract pattern (NFR-F2-7)."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.ansi import (
    ISSUE_UNKNOWN_SUFFIX,
    is_ansi_evidence,
    is_bare_ansi_number,
)
from app.modules.electrical.shared.kinds import Convention, IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules.systems import CLASS_NAME
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import parser_for

ANSI = parser_for(Convention.IEC_81346, Convention.ANSI)
_DEVICE = IdentifierKind.ANSI_DEVICE


def _ansi(
    text: str, parser: IdentifierParser = ANSI, hint: IdentifierKind | None = None
) -> ParsedIdentifier:
    found = parser.parse(text, hint=hint)
    assert found is not None, f"{text!r} did not parse"
    return found


@pytest.mark.parametrize(
    ("text", "number", "suffix", "instance", "function_class"),
    [
        ("87T", 87, "T", None, "differential_protection"),
        ("50BF", 50, "BF", None, "overcurrent_protection"),
        ("51N", 51, "N", None, "overcurrent_protection"),
        ("27-1", 27, "", 1, "voltage_protection"),
        ("87T-2", 87, "T", 2, "differential_protection"),
        ("21G", 21, "G", None, "distance_protection"),
        ("12T", 12, "T", None, None),  # a valid number without a class stays valid
    ],
)
def test_f2_ac_8_numbers_suffixes_instances_and_classes(
    text: str, number: int, suffix: str, instance: int | None, function_class: str | None
) -> None:
    found = _ansi(text)
    assert found.kind is _DEVICE and found.convention is Convention.ANSI
    assert found.valid and not found.issues and found.alternatives == ()
    assert found.ansi is not None and found.ansi.instance == instance
    (function,) = found.ansi.functions
    assert (function.number, function.suffix, function.function_class) == (
        number,
        suffix,
        function_class,
    )
    assert found.device_class == function_class
    assert found.needs_context  # a function, not a device (Q2)


def test_the_canonical_form_has_no_space_and_upper_case() -> None:
    for text in ("87 t", "87t", " 87T "):
        found = _ansi(text)
        assert found.forms.canonical == "87T" and found.forms.original == text


def test_f2_ac_9_a_bare_number_is_a_wire_unless_the_caller_has_context() -> None:
    plain = _ansi("52")
    assert plain.kind is IdentifierKind.WIRE_NUMBER
    assert plain.alternatives == (_DEVICE,) and plain.needs_context
    with_context = _ansi("52", hint=_DEVICE)
    assert with_context.kind is _DEVICE and with_context.alternatives == ()
    assert with_context.device_class == "circuit_breaker" and with_context.needs_context
    assert with_context.ansi is not None and with_context.ansi.functions[0].number == 52
    # a hint resolves it; ANSI off, the hint finds nothing and a wire stays a wire
    assert parser_for(Convention.IEC_81346).parse("52", hint=_DEVICE) is None
    wire = parser_for(Convention.IEC_81346).parse("52")
    assert wire is not None and not wire.needs_context


def test_f2_ac_10_a_compound_against_a_cross_reference() -> None:
    # auto format: the compound wins, the cross-reference is the alternative
    auto = _ansi("50/51")
    assert auto.kind is _DEVICE and auto.alternatives == (IdentifierKind.CROSS_REFERENCE,)
    assert auto.ansi is not None and [f.number for f in auto.ansi.functions] == [50, 51]
    assert auto.device_class is None and auto.forms.search_keys == ("50/51", "50", "51")
    # the profile pinned the format that reads it: a cross-reference, the compound as alternative
    pinned = _ansi(
        "50/51",
        parser_for(
            Convention.IEC_81346, Convention.ANSI, cross_reference_format="page_slash_column"
        ),
    )
    assert pinned.kind is IdentifierKind.CROSS_REFERENCE and pinned.alternatives == (_DEVICE,)
    # pinned to a format that does not read it: still the compound
    other = _ansi(
        "50/51",
        parser_for(
            Convention.IEC_81346, Convention.ANSI, cross_reference_format="slash_page_dot_column"
        ),
    )
    assert other.kind is _DEVICE and other.alternatives == ()
    # ANSI off: the cross-reference, as before
    off = parser_for(Convention.IEC_81346).parse("50/51")
    assert off is not None and off.kind is IdentifierKind.CROSS_REFERENCE
    assert off.alternatives == ()
    # in context the stacked label is the compound even when the format is pinned
    context = _ansi(
        "50/51",
        parser_for(Convention.ANSI, cross_reference_format="page_slash_column"),
        hint=_DEVICE,
    )
    assert context.kind is _DEVICE and context.alternatives == ()
    # three functions at most
    assert _ansi("50/51/87T").ansi is not None
    assert ANSI.parse("50/51/87T/21") is None


def test_f2_ac_11_unknown_suffix_profile_suffix_and_out_of_range() -> None:
    unknown = _ansi("87QZ")
    assert unknown.kind is _DEVICE and not unknown.valid
    assert unknown.issues == (ISSUE_UNKNOWN_SUFFIX,)
    extended = parser_for(Convention.IEC_81346, Convention.ANSI, extra_ansi_suffixes=("qz",))
    assert _ansi("87QZ", extended).valid
    for text in ("100T", "0T", "T87"):
        assert ANSI.parse(text) is None, text
    # a number with a leading zero is no ANSI number (it stays a wire number)
    for text in ("00", "05"):
        found = _ansi(text)
        assert found.kind is IdentifierKind.WIRE_NUMBER and found.alternatives == ()
    assert not is_bare_ansi_number("05") and not is_bare_ansi_number("100")
    assert is_bare_ansi_number("1") and is_bare_ansi_number("99")


def test_ansi_is_off_unless_named() -> None:
    off = parser_for(Convention.IEC_81346)
    for text in ("87T", "50BF", "27-1"):
        assert off.parse(text) is None
    assert IdentifierParser(load_rules()).parse("87T") is None


def test_a_letter_code_keeps_its_text_when_ansi_is_on() -> None:
    both = parser_for(Convention.CN_LETTER_CODES, Convention.ANSI)
    for text in ("1KM", "2QF"):
        found = both.parse(text)
        assert found is not None and found.kind is IdentifierKind.LETTER_CODE, text


def test_f2_ac_12_every_ansi_class_is_a_class_name() -> None:
    rules = load_rules()
    assert rules.ansi.functions
    assert all(CLASS_NAME.fullmatch(name) for name in rules.ansi.functions.values())
    assert all(name.islower() for name in rules.ansi.functions.values())


@pytest.mark.parametrize(
    ("text", "hint", "evidence"),
    [
        ("87T", None, True),
        ("50BF", None, True),
        ("51N", None, True),
        ("27-1", None, True),
        ("32A", None, False),  # the rating letter
        ("25A", None, False),
        ("50A", None, False),
        ("16A", None, False),  # no class for 16
        ("4P", None, False),
        ("2P", None, False),
        ("1N", None, False),
        ("24V", None, False),
        ("52", IdentifierKind.ANSI_DEVICE, False),  # a bare number proves nothing
        ("50/51", None, False),  # a compound proves nothing
        ("87QZ", None, False),  # invalid: an unknown suffix
    ],
)
def test_the_ansi_evidence_rule(text: str, hint: IdentifierKind | None, evidence: bool) -> None:
    found = _ansi(text, hint=hint)
    assert found.kind is _DEVICE
    assert is_ansi_evidence(found, ANSI.rules) is evidence
