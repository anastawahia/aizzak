"""PR-B review round 2: ignorable code points, rail labels, raw label length, controls
that look like whitespace, and bridges across strips written with leading zeros."""

from __future__ import annotations

import time

import pytest

from app.modules.electrical.shared.normalization import canonical_form, text_issues
from app.modules.electrical.shared.rules import RuleError
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import PARSER, _selection, parse

_BIDI = {0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}

# Unicode DerivedCoreProperties.txt, Default_Ignorable_Code_Point, written out explicitly.
_DEFAULT_IGNORABLE = [
    *(0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5),
    *range(0x180B, 0x1810),
    *range(0x200B, 0x2010),
    *range(0x202A, 0x202F),
    *range(0x2060, 0x2070),
    0x3164,
    *range(0xFE00, 0xFE10),
    0xFEFF,
    0xFFA0,
    *range(0xFFF0, 0xFFF9),
    *range(0x1BCA0, 0x1BCA4),
    *range(0x1D173, 0x1D17B),
    *range(0xE0000, 0xE1000),
]


def test_every_default_ignorable_code_point_is_dropped_and_flagged() -> None:
    bad = []
    for code in _DEFAULT_IGNORABLE:
        text = f"KM1{chr(code)}"
        found = parse(text)
        if canonical_form(text) != "KM1" or found is None:
            bad.append(hex(code))
        elif code in _BIDI:  # reported, the identifier stays valid
            if not found.valid or "text.bidi_control" not in found.issues:
                bad.append(hex(code))
        elif found.valid or "text.hidden_character" not in found.issues:
            bad.append(hex(code))
    assert bad == []


@pytest.mark.parametrize("char", ["\x1c", "\x1d", "\x1e", "\x1f", "\x85"])
def test_controls_that_look_like_whitespace_are_hidden_not_spaces(char: str) -> None:
    assert text_issues(f"-K5{char}") == ("text.hidden_character",)
    found = parse(f"-K5{char}")
    assert found is not None and not found.valid


@pytest.mark.parametrize("char", [" ", "\t", "\u00a0", "\u3000"])
def test_real_whitespace_is_still_dropped_silently(char: str) -> None:
    found = parse(f"-K5{char}")
    assert found is not None and found.valid


@pytest.mark.parametrize("label", ["-48V", "+24V", "-12", "-5", "+9V"])
def test_rail_labels_are_accepted(label: str) -> None:
    assert label in load_rules().narrowed(_selection(extra_voltage_labels=(label,))).voltage_labels


@pytest.mark.parametrize("label", ["+1", "+24", "+12345V", "++24V"])
def test_a_plus_label_requires_the_v_unit(label: str) -> None:
    # INC1-Q-5: ``+1`` is a location aspect of IEC 81346, not a rail
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=(label,)))


def test_a_minus_label_keeps_the_optional_v_unit() -> None:
    rules = load_rules().narrowed(_selection(extra_voltage_labels=("+24V", "-12", "-48V")))
    assert {"+24V", "-12", "-48V"} <= set(rules.voltage_labels)


@pytest.mark.parametrize("label", ["+", "-", "--", "+-", "-" * 16])
def test_a_label_without_a_letter_or_digit_is_rejected(label: str) -> None:
    # R2-2: it says nothing, and the parser would never read anything by it
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=(label,)))


@pytest.mark.parametrize(
    "label", ["-4\u200b8V", "-48\u2065V", "+12\u200bv", "L\u00ad9", "-48V\u202e"]
)
def test_a_label_with_a_hidden_character_is_rejected_not_stripped(label: str) -> None:
    # R2-2: the parser flags the same text invalid; the profile must not silently clean it
    with pytest.raises(RuleError, match="hidden"):
        load_rules().narrowed(_selection(extra_voltage_labels=(label,)))


@pytest.mark.parametrize("label", ["--K5", "+-K5", "-+K5", "-1KM", "-1K5", "-12345V"])
def test_designation_like_labels_are_rejected(label: str) -> None:
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=(label,)))


def test_an_overlong_raw_label_is_rejected_fast() -> None:
    start = time.perf_counter()
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=("ﷺ" * 1_000_000,)))
    assert time.perf_counter() - start < 0.1


def test_a_bridge_across_the_same_strip_written_with_leading_zeros_is_clean() -> None:
    found = parse("X01:1~X1:2")
    assert found is not None
    assert "bridge.different_strips" not in found.issues
    other = parse("X1:1~X2:2")
    assert other is not None and "bridge.different_strips" in other.issues


def test_the_raw_length_error_names_the_raw_limit() -> None:
    with pytest.raises(RuleError, match="at most 64 characters"):
        load_rules().narrowed(_selection(extra_voltage_labels=("A" * 65,)))


def test_non_ascii_leftovers_are_flagged_per_kind() -> None:
    # INC1-Q-6, beside the M-04 cases: every kind keeps its own reading but is invalid
    for text, kind in (
        ("KM1\u0301", "letter_code"),
        ("-K5\u0301", "reference_designation"),
        ("X1:14\u0301", "terminal"),
    ):
        found = parse(text)
        assert found.kind.value == kind and not found.valid
        assert "text.non_ascii" in found.issues


def test_other_scripts_without_an_ascii_form_stay_no_identifier() -> None:
    assert PARSER.parse("\u7ee7\u7535\u5668") is None
    assert PARSER.parse("\u041a\u041c1") is None  # Cyrillic homoglyphs are not a mark


def test_a_flagged_non_ascii_token_is_no_evidence_for_a_convention() -> None:
    tally = PARSER.tally(["-K5", "KM1\u0301", "-K6\u0301"])
    assert tally.identifiers == 3 and sum(tally.conventions.values()) == 1
