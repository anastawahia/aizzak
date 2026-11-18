"""F3 (gaps 14, 27, 34): splitting a drawing's word into candidates, the enclosed number, the
ideographic comma and the prime. Every example here is invented (F3-AC-28)."""

from __future__ import annotations

import time

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from app.modules.electrical.shared.normalization import canonical_form, has_enclosed_number
from app.modules.electrical.shared.tokens import split_candidates
from tests.unit.support_electrical_shared import parser_for

_COMMA = chr(0x3001)  # ideographic comma
_FULL_COMMA = chr(0xFF0C)
_FULL_SEMICOLON = chr(0xFF1B)
_IDEOGRAPHIC_STOP = chr(0x3002)
_PRIME = chr(0x2032)
_DOUBLE_PRIME = chr(0x2033)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        (f"QS7{_IDEOGRAPHIC_STOP}", ("QS7",)),
        (f"QS7{_FULL_SEMICOLON}QS8", ("QS7", "QS8")),
        ("(20XYZ)", ("20XYZ",)),
        (chr(0xFF08) + "QS7" + chr(0xFF09), ("QS7",)),
        ("1X-37'", ("1X-37'",)),
        (f"X7:41{_FULL_COMMA}42", (f"X7:41{_FULL_COMMA}42",)),
        ("QS7 QS8", ("QS7", "QS8")),
    ],
)
def test_f3_ac_17_split_candidates(word: str, expected: tuple[str, ...]) -> None:
    assert split_candidates(word) == expected


def test_f3_ac_17_a_list_is_one_candidate_and_marks_at_the_end_are_dropped() -> None:
    assert split_candidates("X7:41,42,43") == ("X7:41,42,43",)
    assert split_candidates(f"X7:41{_COMMA}42") == (f"X7:41{_COMMA}42",)
    assert split_candidates("QS7,") == ("QS7",)
    assert split_candidates("QS7;QS8;") == ("QS7", "QS8")
    assert split_candidates("[QS7]:") == ("QS7",)
    assert split_candidates('"QS7"') == ("QS7",)  # a quote that opened the piece closes it
    assert split_candidates("'QS7'") == ("QS7",)
    assert split_candidates('1X-37"') == ('1X-37"',)
    assert split_candidates("12.3.") == ("12.3",)
    assert split_candidates("+24V") == ("+24V",) and split_candidates(".6E") == (".6E",)
    assert split_candidates("") == () and split_candidates(" \t ") == ()
    assert split_candidates("()") == () and split_candidates(";;") == ()


def test_f3_ac_17_the_split_is_linear() -> None:
    word = ("(" * 50_000) + "QS7" + (")" * 50_000) + (" X1:1" * 20_000)
    started = time.perf_counter()
    pieces = split_candidates(word)
    assert time.perf_counter() - started < 2.0
    assert pieces[0] == "QS7" and len(pieces) == 20_001


_CN = parser_for(Convention.CN_LETTER_CODES)


@pytest.mark.parametrize(
    "text", [chr(c) for c in (0x2470, 0x2473, 0x2776, 0x3251, 0x32B1, 0x1F100)]
)
def test_f3_ac_18_an_enclosed_number_is_no_wire(text: str) -> None:
    assert has_enclosed_number(text)
    assert parser_for(Convention.IEC_81346).parse(text) is None  # 17 / 20 as a wire: gone
    # with a hint the caller has the context, as before
    assert not has_enclosed_number("17") and not has_enclosed_number("QS7")


def test_f3_ac_18_a_full_width_digit_is_still_a_digit() -> None:
    found = _CN.parse("QS" + chr(0xFF17))
    assert found is not None and found.forms.canonical == "QS7"
    assert found.kind is IdentifierKind.LETTER_CODE


def test_the_ideographic_comma_and_the_prime_fold_in_the_canonical_form() -> None:
    assert canonical_form(f"X7:41{_COMMA}42") == "X7:41,42"
    assert canonical_form(f"X7:41{_FULL_COMMA}42") == "X7:41,42"
    assert canonical_form("1X-37" + _PRIME) == "1X-37'"
    assert canonical_form("1X-37" + _DOUBLE_PRIME) == '1X-37"'


def test_tally_reads_the_candidates_of_a_word_not_the_word() -> None:
    parser = parser_for(Convention.CN_LETTER_CODES)
    tally = parser.tally([f"QS7{_FULL_SEMICOLON}QS8", f"(QS9){_IDEOGRAPHIC_STOP}", "prose words"])
    assert tally.tokens == 3
    assert tally.identifiers == 3
    assert tally.conventions[Convention.CN_LETTER_CODES] == 3
