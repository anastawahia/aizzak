"""INC1-AC-4 (QA additions): robustness of the ONE parser on hostile input.

Covers what the per-kind tests do not: no exception on any text, no catastrophic
regex backtracking (the patterns are repository data, but their inputs are not),
invisible/width/script handling, the 200-character bound, determinism across fresh
parsers, and "flagged, never dropped" for every malformed form of an Appendix A example.
"""

from __future__ import annotations

import random
import time

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from app.modules.electrical.shared.parser import MAX_TEXT_CHARS, IdentifierParser
from app.modules.electrical.shared.rules import RuleError, RuleSelection
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import PARSER, fw, parse

_ADVERSARIAL = [
    "1" * 199,
    "A" * 199,
    "=" + "A1=" * 49,
    "-" * 199,
    "X1:" + "1~" * 90,
    "1.1" * 60,
    "W" * 199,
    "%I" + "0" * 190,
    "DB" + "1" * 100 + ".DBX" + "1" * 50,
    "-K5:" + "A" * 190,
    "+" * 199,
    "1/" * 90,
    "L1-" * 60,
    "KM" * 99,
    "X" + "1." * 95,
    " " * 190 + "K",
    "-X1:" + "1~-X1:" * 30,
    "1" * 5000,
    "=A1+P2-K5" * 1000,
]


def test_adversarial_input_is_fast_and_never_raises() -> None:
    started = time.perf_counter()
    for text in _ADVERSARIAL:
        found = PARSER.parse(text)
        assert found is None or found.forms.original == text
    assert time.perf_counter() - started < 1.0  # ReDoS guard: patterns are anchored and bounded


def test_text_over_the_bound_is_prose_and_the_bound_itself_is_still_parsed() -> None:
    assert PARSER.parse("1" * (MAX_TEXT_CHARS + 1)) is None
    at_bound = "=" + "A" * (MAX_TEXT_CHARS - 1)
    assert PARSER.parse(at_bound) is not None and PARSER.parse(at_bound + "A") is None


def test_random_unicode_never_raises_and_is_deterministic() -> None:
    rng = random.Random(20261102)
    alphabet = (
        "KMQFXTW=+-:/.%~&# 0123456789AIEDBLPNVU"  # identifier alphabet
        + "".join(chr(c) for c in (0x200B, 0xFEFF, 0xA0, 0xFF1A, 0xFF0D, 0xFF2B, 0x0661, 0x4E2D))
        + "\x00\t\ńßıﬁ﷽"
    )
    fresh = IdentifierParser(load_rules())
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        first = PARSER.parse(text)
        assert first == PARSER.parse(text) == fresh.parse(text)
        if first is not None:
            assert first.forms.original == text
            assert first.forms.canonical and first.forms.search_keys[0] == first.forms.canonical
            assert first.valid == (not first.issues)


@pytest.mark.parametrize(
    "text", ["\u200bKM1", "KM\u200b1", "K\u00a0M1", "\ufeffKM1", " \tKM1\n", "k m 1"]
)
def test_invisible_characters_and_spacing_do_not_change_the_identifier(text: str) -> None:
    found = parse(text)
    assert found.forms.canonical == "KM1" and found.forms.original == text
    assert found.kind is IdentifierKind.LETTER_CODE


_LOOK_ALIKES = ["\u041a\u041c1", "\u039a\u039c1", "\u043a\u04321", "-\u041a5"]  # Cyrillic/Greek


@pytest.mark.parametrize("text", _LOOK_ALIKES)
def test_look_alike_scripts_are_not_read_as_latin_identifiers(text: str) -> None:
    assert PARSER.parse(text) is None


def test_the_full_width_spelling_of_every_ascii_designation_equals_the_ascii_parse() -> None:
    for text in ("-K5", "=A1+P2-K5", "-X1:14", "KM1:A1", "%I0.0", "DB10.DBX2.3", "L1-5", "/12.3"):
        ascii_found, wide_found = parse(text), parse(fw(text))
        assert wide_found.forms.canonical == ascii_found.forms.canonical
        assert wide_found.forms.search_keys == ascii_found.forms.search_keys
        assert wide_found.kind is ascii_found.kind
        assert wide_found.forms.original == fw(text)


def test_the_cable_designations_of_appendix_a_are_valid_designations() -> None:
    for text, expected in (("-W1", ["-W1"]), ("=A1-W12", ["=A1", "-W12"])):
        found = parse(text)
        assert found.kind is IdentifierKind.REFERENCE_DESIGNATION and found.valid
        assert found.convention is Convention.IEC_81346
        assert [a.prefix + a.code for a in found.aspects] == expected


@pytest.mark.parametrize(
    "text", ["-K5:", "KM1:", "-X1:", "X1.", "1XT-", "-K5:A1:B2", "=A1-", "I0.8", "DB10.DBX2.8"]
)
def test_a_malformed_form_of_an_appendix_a_example_is_flagged_not_dropped(text: str) -> None:
    found = parse(text)
    assert found.valid is False and found.issues
    assert found.forms.original == text


def test_parsing_the_canonical_form_gives_the_same_identifier() -> None:
    for text in ("=A1+P2-K5:13", fw("KM1"), "XT1" + chr(0xFF1A) + "5", "q4.7", "-k05", "1xt-5"):
        first = parse(text)
        again = parse(first.forms.canonical)
        assert (again.kind, again.valid, again.issues, again.connection_point) == (
            first.kind,
            first.valid,
            first.issues,
            first.connection_point,
        )
        assert again.forms.search_keys == first.forms.search_keys


def test_a_document_profile_cannot_make_the_parser_slow_or_compile_user_input() -> None:
    # the profile selects patterns by NAME; a regex-looking name is an unknown name, not code
    selection = RuleSelection(
        conventions=frozenset(),
        plc_families=frozenset(),
        extra_aspects=(),
        wire_number_patterns=("(a+)+$",),
        cross_reference_format=None,
        extra_voltage_labels=(),
    )
    with pytest.raises(RuleError):
        PARSER.rules.narrowed(selection)
