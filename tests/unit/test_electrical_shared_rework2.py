"""PR-B review round 2: ignorable code points, rail labels, raw label length, controls
that look like whitespace, and bridges across strips written with leading zeros."""

from __future__ import annotations

import time

import pytest

from app.modules.electrical.shared.normalization import canonical_form, text_issues
from app.modules.electrical.shared.rules import RuleError
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import _selection, parse

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
