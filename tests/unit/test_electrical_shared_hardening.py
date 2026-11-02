"""PR-B rework round 1: search-key collisions, non-Latin digits, hidden characters, bidi
controls, length bounds, profile hardening, bridge strips, pattern timing.

Findings covered: #1/S-6/B-3, #2/S-1/B-1, #3, S-5, S-2/#4/B-4, S-3, S-4, #5/S-10, #7.
"""

from __future__ import annotations

import itertools
import re
import time

import pytest

from app.modules.electrical.shared.kinds import IdentifierKind
from app.modules.electrical.shared.normalization import canonical_form, normalize, text_issues
from app.modules.electrical.shared.parser import MAX_TEXT_CHARS
from app.modules.electrical.shared.patterns import compiled
from app.modules.electrical.shared.plc_siemens import parse_plc_address
from app.modules.electrical.shared.rules import (
    MAX_EXTRA_VOLTAGE_LABELS,
    IdentifierRules,
    RuleError,
)
from tests.eval.electrical.m04_identifiers import load_rules, observed
from tests.unit.support_electrical_shared import PARSER, _selection, parse

AR = "".join(chr(0x660 + n) for n in range(10))  # Arabic-Indic digits 0..9
EXT = "".join(chr(0x6F0 + n) for n in range(10))  # extended Arabic-Indic digits 0..9


def _digits(text: str, table: str) -> str:
    return "".join(table[int(ch)] if ch.isdigit() else ch for ch in text)


# --- #1 / S-6 / B-3: a search key never merges two different identifiers ---------------

_COLLISION_GROUPS = [
    ["X1:14", "X11:4", "X114"],  # (a) terminals on different strips
    ["-X1:14", "-X11:4"],
    ["-K1:13", "-K11:3", "-K113"],  # (b)
    ["KM1:13", "KM11:3", "KM113"],
    ["12/3", "1/23", "12.3", "1.23", "/12.3", "123"],  # (c) cross-references and a wire
    ["12.3-1", "1.23-1", "1231"],  # (d)
    ["=A1-K5", "=A1K5"],  # (e) an aspect prefix is not a separator
    ["L-", "L", "L1"],  # (f) a voltage sign is not a separator
    ["XT1:5", "XT15"],
    ["1.1", "11"],
    ["-K-5", "-K5"],  # (g) a designation is not a plain tag
    ["-KM-1", "KM1"],  # (-KM1 and KM1 legitimately share the key KM1, as -K5 and K5)
    ["-KM-1", "-KM1"],
    ["-X-14", "-X14"],
]

# (h) a connection point of a sub-element is not the device's: only the device key K5 is shared
_SUB_ELEMENT_GROUPS = [
    ("-K5-13:A1", "-K5:A1"),
    ("-K5-2:A1", "-K5:A1"),
    ("-K5-13:A1", "=A1-K5:A1"),
    ("-K5-2:A1", "=A1-K5:A1"),
]


@pytest.mark.parametrize("group", _COLLISION_GROUPS, ids=" | ".join)
def test_different_identifiers_share_no_search_key(group: list[str]) -> None:
    keys = {text: set(normalize(text).search_keys) for text in group}
    parsed = {t: PARSER.parse(t) for t in group}
    for text, found in parsed.items():
        if found is not None:
            keys[text] |= set(found.forms.search_keys)
    for a, b in itertools.combinations(group, 2):
        assert keys[a].isdisjoint(keys[b]), f"{a!r} and {b!r} share {keys[a] & keys[b]}"


@pytest.mark.parametrize(("a", "b"), _SUB_ELEMENT_GROUPS, ids=lambda v: v)
def test_a_sub_element_connection_point_does_not_meet_the_devices(a: str, b: str) -> None:
    assert set(parse(a).forms.search_keys) & set(parse(b).forms.search_keys) == {"K5"}


@pytest.mark.parametrize(
    "text", ["X1:14", "-K1:13", "12.3", "12/3", "12.3-1", "L1-5", "=A1+P2-X1:14", "I0.0"]
)
def test_no_key_is_the_canonical_form_with_its_separators_removed(text: str) -> None:
    found = parse(text)
    squeezed = re.sub(r"[-.:/_]", "", found.forms.canonical)
    assert squeezed not in found.forms.search_keys


def test_a_plain_tag_still_meets_its_spellings() -> None:
    shared = {normalize(t).search_keys[-1] for t in ("F0001", "F 0001", "F-0001")}
    assert shared == {"F1"}  # T-21
    assert "X1:14" in parse("X1.14").forms.search_keys
    assert "X1:14" in parse("-X1:14").forms.search_keys


# --- #2 / S-1 / B-1: every Unicode decimal digit folds to ASCII ----------------------


@pytest.mark.parametrize("table", [AR, EXT], ids=["arabic-indic", "extended-arabic-indic"])
@pytest.mark.parametrize(
    ("text", "kind", "canonical"),
    [
        ("KM1", IdentifierKind.LETTER_CODE, "KM1"),
        ("1KM", IdentifierKind.LETTER_CODE, "1KM"),
        ("X1:14", IdentifierKind.TERMINAL, "X1:14"),
        ("-X1:14", IdentifierKind.TERMINAL, "-X1:14"),
        ("X1:1~X1:2", IdentifierKind.BRIDGE, "X1:1~X1:2"),
        ("-K5", IdentifierKind.REFERENCE_DESIGNATION, "-K5"),
        ("W123", IdentifierKind.WIRE_NUMBER, "W123"),
        ("12.3", IdentifierKind.CROSS_REFERENCE, "12.3"),
        ("I0.0", IdentifierKind.PLC_ADDRESS, "I0.0"),
        ("%IW64", IdentifierKind.PLC_ADDRESS, "%IW64"),
        ("DB10.DBX2.3", IdentifierKind.PLC_ADDRESS, "DB10.DBX2.3"),
        ("L1", IdentifierKind.VOLTAGE, "L1"),
    ],
)
def test_non_latin_digits_parse_exactly_like_ascii_digits(
    table: str, text: str, kind: IdentifierKind, canonical: str
) -> None:
    written = _digits(text, table)
    assert written != text
    found = parse(written)
    ascii_found = parse(text)
    assert found.kind is kind and found.valid
    assert found.forms.canonical == canonical
    assert found.forms.original == written  # NFR-I18N-03: the original is kept
    assert found.forms.search_keys == ascii_found.forms.search_keys
    assert found.plc == ascii_found.plc and found.connection_point == ascii_found.connection_point


def test_the_arabic_digits_of_the_review_repros() -> None:
    assert canonical_form("KM" + AR[1]) == "KM1"
    assert parse("X" + AR[1] + ":" + AR[1] + AR[4]).forms.canonical == "X1:14"
    assert parse("I" + AR[0] + "." + AR[0]).forms.canonical == "I0.0"


def test_the_rule_patterns_are_ascii_only() -> None:
    # a digit the fold would miss must not satisfy `\d` in any packaged or code pattern
    rules = load_rules()
    for pattern in (*rules.wire_number_patterns.values(), *rules.cross_reference_formats.values()):
        assert compiled(pattern).flags & re.ASCII
        assert compiled(pattern).fullmatch("\u0661\u0662") is None


# --- #3: a profile naming an unknown extra aspect is an error -------------------------


def test_an_unknown_extra_aspect_is_a_rule_error_not_a_silent_drop() -> None:
    with pytest.raises(RuleError, match="extra aspect"):
        load_rules().narrowed(_selection(extra_aspects=("@@",)))
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_aspects=("==", "@@")))
    kept = load_rules().narrowed(_selection(extra_aspects=("&", "==")))
    assert kept.extra_aspects == ("==", "&")


# --- S-5: extra voltage labels --------------------------------------------------------


def test_extra_voltage_labels_are_normalised_and_deduplicated() -> None:
    rules = load_rules().narrowed(
        _selection(
            extra_voltage_labels=("-48v", "\uff0d48V", "\uff4c\uff19", "+12\u200bv"),
        )
    )
    assert "-48V" in rules.voltage_labels and rules.voltage_labels.count("-48V") == 1
    assert "L9" in rules.voltage_labels and "+12V" in rules.voltage_labels
    assert PARSER.parse("L9") is None  # the shared parser is unchanged
    assert rules.narrowed(_selection()) == rules


@pytest.mark.parametrize(
    "label",
    [
        *("", " ", "x" * 17, "A B:C", "L1/2", "-K5", "KM1", "x1:14", "-X1:14", "=A1-K5"),
        *("W11", "I0.0", "--K5", "+-K5", "-+K5", "-1KM", "-1K5"),
    ],
)
def test_an_invalid_or_hijacking_voltage_label_is_a_rule_error(label: str) -> None:
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=(label,)))


def test_at_most_sixty_four_extra_voltage_labels() -> None:
    ok = tuple(f"VA{n}V" for n in range(MAX_EXTRA_VOLTAGE_LABELS))
    load_rules().narrowed(_selection(extra_voltage_labels=ok))
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(extra_voltage_labels=(*ok, "ONEMORE")))


def test_a_label_cannot_reclassify_a_device_through_the_profile() -> None:
    rules = load_rules()
    with pytest.raises(RuleError):
        rules.narrowed(_selection(extra_voltage_labels=("KM1",)))
    assert PARSER.parse("KM1").kind is IdentifierKind.LETTER_CODE  # type: ignore[union-attr]


# --- S-2 / #4 / B-4: hidden characters are flagged, never silently dropped ------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("-K5\x00", IdentifierKind.REFERENCE_DESIGNATION),
        ("KM1\ufe0f", IdentifierKind.LETTER_CODE),
        ("K\u034fM1", IdentifierKind.LETTER_CODE),
        ("KM1\x07", IdentifierKind.LETTER_CODE),
        ("\u115fKM1", IdentifierKind.LETTER_CODE),
        ("KM\u31641", IdentifierKind.LETTER_CODE),
        ("KM\uffa01", IdentifierKind.LETTER_CODE),
        ("KM1\u2800", IdentifierKind.LETTER_CODE),
        ("KM1\u200b", IdentifierKind.LETTER_CODE),
        ("KM1\U000e0100", IdentifierKind.LETTER_CODE),
    ],
)
def test_a_hidden_character_flags_the_identifier_instead_of_dropping_it(
    text: str, kind: IdentifierKind
) -> None:
    found = parse(text)
    assert found.kind is kind
    assert found.valid is False and "text.hidden_character" in found.issues
    assert found.forms.original == text
    assert "\x00" not in found.forms.canonical and found.forms.canonical.isascii()


def test_plain_whitespace_is_not_a_hidden_character() -> None:
    found = parse(" \tKM1\n")
    assert found.valid and not found.issues
    assert text_issues("k m 1") == ()


def test_hidden_characters_alone_are_still_no_identifier() -> None:
    assert PARSER.parse("\x00\ufe0f\u200b") is None


# --- S-3: bidi controls are reported, the identifier stays valid ----------------------


@pytest.mark.parametrize(
    "control", ["\u202a", "\u202e", "\u2066", "\u2069", "\u200e", "\u200f", "\u061c"]
)
def test_a_bidi_control_is_reported_but_does_not_invalidate(control: str) -> None:
    found = parse(f"KM{control}1")
    assert found.forms.canonical == "KM1"
    assert found.valid is True
    assert found.issues == ("text.bidi_control",)
    assert found.forms.original == f"KM{control}1"


def test_bidi_and_hidden_together_report_both_and_invalidate() -> None:
    found = parse("KM\u202e1\x00")
    assert found.valid is False
    assert found.issues == ("text.hidden_character", "text.bidi_control")


def test_a_malformed_identifier_keeps_its_own_issues_before_the_text_ones() -> None:
    found = parse("-K5:\u202e")
    assert found.valid is False
    assert found.issues == ("connection_point.empty", "text.bidi_control")


# --- S-4: the bound holds after normalisation, and numbers are bounded ----------------


def test_text_that_expands_under_nfkc_is_bounded_after_normalisation() -> None:
    assert len("\ufdfa") == 1 and len(canonical_form("\ufdfa")) > 10
    text = "\ufdfa" * MAX_TEXT_CHARS  # within the raw bound, 15x over it once normalised
    assert len(text) <= MAX_TEXT_CHARS and len(canonical_form(text)) > MAX_TEXT_CHARS
    assert PARSER.parse(text) is None
    assert PARSER.parse("KM1" + "\ufdfa" * 3) is None
    # a text that WOULD parse as a designation if only the raw length were checked
    roman = "=" + "\u2162" * 70  # 71 characters; "=" + "III" * 70 = 211 once normalised
    assert len(roman) <= MAX_TEXT_CHARS and len(canonical_form(roman)) > MAX_TEXT_CHARS
    assert PARSER.parse(roman) is None


@pytest.mark.parametrize(
    "text",
    [
        "I0." + "9" * 5000,
        "%I0." + "9" * 5000,
        "DB1.DBX0." + "9" * 5000,
        "V0." + "9" * 5000,
        "I" + "9" * 5000 + ".0",
        "DB" + "9" * 5000 + ".DBX0.0",
        "AIW" + "9" * 5000,
    ],
    ids=["i-bit", "tia-bit", "db-bit", "v-bit", "i-byte", "db-number", "aiw"],
)
def test_per_kind_parsers_do_not_raise_on_long_digit_runs(text: str) -> None:
    forms = normalize(text)
    found = parse_plc_address(forms.canonical, forms, PARSER.rules)  # no ValueError from int()
    assert found is None or found.valid is False


def test_a_bit_beyond_three_digits_is_not_a_plc_address() -> None:
    assert PARSER.parse("I0.999") is not None and not PARSER.parse("I0.999").valid  # type: ignore[union-attr]
    assert PARSER.parse("I0.9999") is None


# --- #5 / S-10: patterns compile once, and none is slow ------------------------------


def test_a_pattern_is_compiled_once() -> None:
    rules = load_rules()
    pattern = rules.wire_number_patterns["sequential"]
    assert compiled(pattern) is compiled(pattern)


def _adversarial_inputs() -> list[str]:
    n = MAX_TEXT_CHARS
    return [
        "1" * n, "A" * n, "W" * n, "-" * n, "." * n, "/" * n, "1." * (n // 2), "1-" * (n // 2),
        "L1-" * (n // 3), "=A1" * (n // 3), "1/" * (n // 2), "L" + "1" * (n - 1) + "!",
        "1" * (n - 1) + "x", "0" * n, ("1" * 9 + ".") * 20 + "x", "U" + "1-" * 90 + "x",
    ]  # fmt: skip


def test_every_packaged_pattern_is_fast_on_adversarial_input() -> None:
    rules = load_rules()
    patterns = [*rules.wire_number_patterns.values(), *rules.cross_reference_formats.values()]
    assert len(patterns) >= 7
    started = time.perf_counter()
    for pattern in patterns:
        regex = compiled(pattern)
        for text in _adversarial_inputs():
            for repeat in (1, 15):  # 15x: the most NFKC can expand a character
                regex.fullmatch(text * repeat)
    assert time.perf_counter() - started < 1.0


def test_the_whole_parser_is_fast_on_adversarial_input_at_the_bound() -> None:
    started = time.perf_counter()
    for text in _adversarial_inputs():
        for hint in (None, *IdentifierKind):
            PARSER.parse(text, hint=hint)
    assert time.perf_counter() - started < 2.0


# --- #7: a bridge across two strips is flagged ---------------------------------------


def test_a_bridge_across_two_strips_is_flagged_not_accepted_with_a_strip_lost() -> None:
    found = parse("X1:1~X2:3")
    assert found.kind is IdentifierKind.BRIDGE
    assert found.valid is False and "bridge.different_strips" in found.issues
    assert parse("-X1:1~-X2:3").issues == ("bridge.different_strips",)


@pytest.mark.parametrize("text", ["X1:1~X1:2", "-X1:1~-X1:2", "X1:1~3", "X1:1~-X1:2"])
def test_a_bridge_inside_one_strip_stays_valid(text: str) -> None:
    assert parse(text).valid


def test_the_rules_object_is_unchanged_by_failed_narrowing() -> None:
    rules: IdentifierRules = load_rules()
    before = rules.voltage_labels
    with pytest.raises(RuleError):
        rules.narrowed(_selection(extra_voltage_labels=("-K5",)))
    assert rules.voltage_labels == before


def test_m04_compares_a_data_block_numbered_zero() -> None:
    plc = observed(parse("DB0.DBX0.0"))["plc"]
    assert plc["db_number"] == 0 and plc["byte"] == 0 and plc["bit"] == 0
    assert "db_number" not in observed(parse("I0.0"))["plc"]
