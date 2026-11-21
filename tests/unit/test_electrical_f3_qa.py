"""QA coverage for F3 (D-37, FR-ID-16/19/20): the bounds, the hostile inputs and the edges the
developer's AC tests do not pin (NFR-F3-5), plus the nine deviations from the design.

Every identifier here is an invented, abstract pattern (F3-AC-28); nothing is a real tag."""

from __future__ import annotations

import copy
import random
import time
from dataclasses import replace
from typing import Any

import pytest
from services.ele_parse import SERVICE_VERSION, contract
from services.ele_parse import inspect as ele_inspect

import app.modules.electrical.shared.parser as parser_module
from app.modules.electrical.shared.connection_points import check_label, is_valid_label
from app.modules.electrical.shared.groups import (
    inherit,
    list_items,
    range_members,
    suffix_members,
)
from app.modules.electrical.shared.kinds import (
    Convention,
    IdentifierKind,
    ParsedIdentifier,
)
from app.modules.electrical.shared.normalization import dedupe, folded, is_space
from app.modules.electrical.shared.parser import (
    MAX_GROUP_KEYS,
    MAX_TEXT_CHARS,
    TALLY_MEMBER_BUDGET,
    IdentifierParser,
)
from app.modules.electrical.shared.ratings import Rating, read_rating
from app.modules.electrical.shared.rules import IdentifierRules
from app.modules.electrical.shared.rules.errors import RuleError
from app.modules.electrical.shared.rules.systems import MAX_KKS_PARTS, GroupRules
from app.modules.electrical.shared.tokens import split_candidates
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import _selection, fw, parser_for
from tests.unit.test_electrical_shared_rules import _raw

_GROUP = IdentifierKind.IDENTIFIER_GROUP
_DEVICE = IdentifierKind.ANSI_DEVICE
RULES = load_rules()
FULL = IdentifierParser(RULES)
IEC = parser_for(Convention.IEC_81346)
CN = parser_for(Convention.CN_LETTER_CODES)
KKS = parser_for(Convention.IEC_81346, Convention.KKS)
ANSI = parser_for(Convention.IEC_81346, Convention.ANSI)
VENDOR = IdentifierParser(
    RULES.narrowed(
        _selection(
            conventions=frozenset({Convention.IEC_81346}),
            wire_number_patterns=("sequential", "digits_letter", "letter_digits"),
        )
    )
)
VENDOR_ANSI = IdentifierParser(
    RULES.narrowed(
        _selection(
            conventions=frozenset({Convention.IEC_81346, Convention.ANSI}),
            wire_number_patterns=("sequential", "digits_letter"),
        )
    )
)
IDEOGRAPHIC_COMMA, FULL_COMMA, SMALL_COMMA, HALF_COMMA = "\u3001", "\uff0c", "\ufe51", "\uff64"
FULL_STOP, FULL_SEMICOLON = "\u3002", "\uff1b"


def _read(parser: IdentifierParser, text: str, **kw: Any) -> ParsedIdentifier:
    found = parser.parse(text, **kw)
    assert found is not None, f"{text!r} did not parse"
    return found


def _group(parser: IdentifierParser, text: str) -> ParsedIdentifier:
    found = _read(parser, text)
    assert found.kind is _GROUP and found.group is not None, (text, found.kind)
    return found


def _texts(found: ParsedIdentifier) -> list[str]:
    assert found.group is not None
    return [m.text for m in found.group.members]


# --- the member cap, before anything is generated (NFR-F3-5, F3-AC-14) ----------------------


@pytest.mark.parametrize(
    ("text", "count"),
    [("QS0-QS63", 64), ("QS1-QS64", 64), ("QS7-QS70", 64), ("QS7-QS69", 63)],
)
def test_a_range_at_the_cap_is_expanded(text: str, count: int) -> None:
    found = _group(CN, text)
    assert found.valid and len(_texts(found)) == count


@pytest.mark.parametrize("text", ["QS1-QS65", "QS7-QS71", "QS0-QS9999", "QS1-QS9999"])
def test_a_range_over_the_cap_generates_nothing(text: str) -> None:
    found = _group(CN, text)
    assert not found.valid and found.issues == ("group.too_many_members",)
    assert _texts(found) == [] and found.convention is None
    assert found.forms.search_keys == (found.forms.canonical,)


def test_the_cap_is_checked_before_a_member_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the cap the parser reads no generated member (a range reads none, a list and a
    suffix list read only the first item, to know the text is a group at all)."""
    calls: list[str] = []
    real = IdentifierParser._member

    def spy(self: IdentifierParser, text: str, cased: str | None = None) -> ParsedIdentifier | None:
        calls.append(text)
        return real(self, text, cased)

    monkeypatch.setattr(IdentifierParser, "_member", spy)
    assert range_members("QS1-QS9999", 64) == ((), True)
    _read(CN, "QS1-QS9999")
    assert calls == []
    _read(IEC, "X7:1" + ",1" * 97)
    assert calls == ["X7:1"]
    calls.clear()
    _read(KKS, "20XYZ10AB131" + "/2" * 70)
    assert calls == ["20XYZ10AB131"]


def test_a_list_and_a_suffix_list_over_the_cap_are_flagged_and_empty() -> None:
    ones = _group(IEC, "X7:1" + ",1" * 97)  # 98 items in 198 characters
    assert not ones.valid and ones.issues == ("group.too_many_members",) and _texts(ones) == []
    tails = _group(KKS, "20XYZ10AB131" + "/2" * 70)
    assert not tails.valid and tails.issues == ("group.too_many_members",)
    assert _texts(tails) == []


@pytest.mark.parametrize("text", ["X7:1" + ",1" * 63, "20XYZ10AB131" + "/2" * 63])
def test_a_list_at_the_cap_is_expanded(text: str) -> None:
    found = _group(KKS if "/" in text else IEC, text)
    assert found.valid and len(_texts(found)) == 64


def test_the_cap_is_the_rules_own_value() -> None:
    tight = replace(RULES, groups=GroupRules(2, RULES.groups.suffix_lists))
    parser = IdentifierParser(
        tight.narrowed(_selection(conventions=frozenset({Convention.CN_LETTER_CODES})))
    )
    assert _group(parser, "QS7-QS8").valid
    over = _group(parser, "QS7-QS9")
    assert over.issues == ("group.too_many_members",) and _texts(over) == []
    assert _group(parser, "QS7,QS8").valid
    assert _group(parser, "QS7,QS8,QS9").issues == ("group.too_many_members",)


def test_a_group_search_keys_stay_within_their_bound() -> None:
    found = _group(CN, "QS1-QS64")
    assert len(found.forms.search_keys) <= MAX_GROUP_KEYS
    assert found.forms.search_keys[0] == "QS1-QS64"  # its own form first, then the members'
    assert found.forms.search_keys[1:3] == ("QS1", "QS2")


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (IEC, "X7:1" + ",1" * 97),
        (IEC, "X7:41" + "," * 190),
        (IEC, "1" + ",1" * 99),
        (CN, "QS0-QS9999"),
        (KKS, "20XYZ10AB131" + "/2" * 40),
        (CN, "QS7" + "-QS8" * 40),
        (CN, "QS7" + "\\8" * 40),
        (IEC, "," * 199),
        (IEC, "-" * 199),
        (IEC, "X7:41," + "\u3001" * 150),
    ],
)
def test_hostile_texts_inside_the_length_bound_are_cheap(
    parser: IdentifierParser, text: str
) -> None:
    assert len(text) <= MAX_TEXT_CHARS
    started = time.perf_counter()
    for _ in range(200):
        parser.parse(text)
    assert time.perf_counter() - started < 3.0


@pytest.mark.parametrize("text", ["X7:1" + ",1" * 99, "QS7" + "-QS8" * 60, "," * 300])
def test_a_text_over_the_length_bound_is_no_identifier(text: str) -> None:
    assert len(text) > MAX_TEXT_CHARS and IEC.parse(text) is None and CN.parse(text) is None


def test_a_flood_of_hostile_texts_is_linear_enough() -> None:
    rng = random.Random(3)
    alphabet = "QSX7:41,-\\/'\u3001\uff0c 8Y"
    started = time.perf_counter()
    for _ in range(1000):
        text = "".join(rng.choice(alphabet) for _ in range(MAX_TEXT_CHARS))
        for parser in (IEC, CN, KKS):
            parser.parse(text)
    assert time.perf_counter() - started < 15.0


# --- reversed, zero-length and malformed ranges ---------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "QS9-QS7",
        "QS7-QS7",
        "QS7-",
        "-QS9",
        "QS7-QS9-QS11",
        "QS7A-QS9B",
        "1-5",
        "Q7-9",
        "QS7-QS10000",
    ],
)
def test_a_text_that_is_no_ascending_range_is_no_group(text: str) -> None:
    found = CN.parse(text)
    assert found is None or found.kind is not _GROUP


def test_range_members_edges() -> None:
    assert range_members("QS7-QS7", 64) is None  # zero length
    assert range_members("QS9-QS7", 64) is None  # reversed
    assert range_members("QS7-QS8", 64) == (("QS7", "QS8"), False)  # the shortest range
    assert range_members("QS07-QS10", 64) == (("QS07", "QS08", "QS09", "QS10"), False)
    assert range_members("QS007-QS010", 64) == (("QS007", "QS008", "QS009", "QS010"), False)
    assert range_members("QS7A-QS9A", 64) == (("QS7A", "QS8A", "QS9A"), False)
    assert range_members("QS7A-QS9B", 64) is None  # a different tail
    assert range_members("QS7-QSA9", 64) is None  # a different head and number shape
    assert range_members("7-9", 64) is None  # no head and no tail: no range
    assert range_members("QS1-QS9999", 64) == ((), True)
    assert range_members("QS1-QS65", 64) == ((), True)
    assert range_members("QS1-QS65", 65) is not None and range_members("QS1-QS65", 65)[1] is False  # type: ignore[index]
    assert range_members("QS1-QS12345", 64) is None  # five digits are no endpoint


def test_a_zero_padded_range_reads_each_member() -> None:
    found = _group(CN, "QS07-QS10")
    assert found.valid and _texts(found) == ["QS07", "QS08", "QS09", "QS10"]


def test_list_and_suffix_helpers_edges() -> None:
    assert list_items("X7:41") is None
    assert list_items("A,B") == ("A", "B")
    assert list_items("A,,B,") == ("A", "", "B", "")
    assert list_items(",") == ("", "")
    assert suffix_members("QS7", ("8", "9")) == ("QS7", "QS8", "QS9")
    assert suffix_members("QS7", ("12",)) is None  # longer than the trailing run
    assert suffix_members("QS", ("1",)) is None  # no trailing digits
    assert suffix_members("QS7", ("",)) is None and suffix_members("QS7", ("a",)) is None
    assert suffix_members("QS7", ("\u0668",)) is None  # an Arabic-Indic digit is no digit here
    assert inherit("X7:41", "41", "42") == "X7:42"
    assert inherit("QS7", "", "8") == "8" and inherit("X7:41", "99", "42") == "42"


@pytest.mark.parametrize(
    "text", ["20XYZ10AB131/1234/5", "20XYZ10AB131/a", "20XYZ10AB131/", "20XYZ10AB131//2"]
)
def test_a_malformed_suffix_list_is_no_group(text: str) -> None:
    found = KKS.parse(text)
    assert found is None or found.kind is not _GROUP


@pytest.mark.parametrize("text", ["QS7\\", "QS7\\a", "QS7\\12"])
def test_a_malformed_backslash_list_is_no_group(text: str) -> None:
    found = CN.parse(text)
    assert found is None or found.kind is not _GROUP


def test_list_edges() -> None:
    assert IEC.parse(",X7:41") is None  # no first item
    found = _group(IEC, "X7:41,42,")
    assert found.issues == ("group.empty_member",) and not found.valid
    found = _group(IEC, "X7:41,,")
    assert found.issues == ("group.empty_member",) and len(_texts(found)) == 3
    found = _group(IEC, "X7:41, 42")  # a space after the comma is folded away
    assert found.valid and _texts(found) == ["X7:41", "X7:42"]
    found = _group(IEC, "X7:41,4$")
    assert found.issues == ("group.member_unreadable",)
    found = _group(IEC, "X7:41,X8:5")  # a full item is read alone
    assert found.valid and _texts(found) == ["X7:41", "X8:5"]
    assert _texts(_group(IEC, "X7:41,41")) == ["X7:41", "X7:41"]  # a repeat is kept, written order
    found = _group(IEC, "X7:41,1234567")  # a seven-digit label is no label
    assert found.issues == ("group.member_unreadable",)


def test_a_group_is_never_read_under_a_hint() -> None:
    assert IEC.parse("X7:41,42", hint=IdentifierKind.TERMINAL) is None
    assert CN.parse("QS7-QS9", hint=IdentifierKind.LETTER_CODE) is None


def test_a_group_member_is_never_a_group() -> None:
    found = _group(IEC, "X7:41,42,43")
    assert found.group is not None
    assert all(m.identifier is not None and m.identifier.group is None for m in found.group.members)
    assert IEC.parse("X7:41,42-44") is not None  # a range inside a list: no nesting
    assert _group(IEC, "X7:41,42-44").issues == ("group.member_unreadable",)


# --- CJK punctuation and full-width folding (F3-AC-11, F3-AC-17, F3-AC-18) -------------------


@pytest.mark.parametrize("comma", [",", FULL_COMMA, IDEOGRAPHIC_COMMA, SMALL_COMMA, HALF_COMMA])
def test_every_comma_spelling_reads_one_list(comma: str) -> None:
    text = f"X7:41{comma}42{comma}43"
    found = _group(IEC, text)
    assert found.valid and found.forms.canonical == "X7:41,42,43" and found.forms.original == text
    assert _texts(found) == ["X7:41", "X7:42", "X7:43"]


def test_a_doubled_wide_comma_is_an_empty_member() -> None:
    found = _group(IEC, f"X7:41{IDEOGRAPHIC_COMMA}{IDEOGRAPHIC_COMMA}42")
    assert found.issues == ("group.empty_member",)


def test_a_fully_wide_list_range_and_suffix_list_read_like_the_ascii_ones() -> None:
    assert _texts(_group(IEC, fw("X7:41,42"))) == ["X7:41", "X7:42"]
    assert _texts(_group(CN, fw("QS7-QS9"))) == ["QS7", "QS8", "QS9"]
    assert _texts(_group(CN, fw("QS7,QS8"))) == ["QS7", "QS8"]
    assert _texts(_group(CN, fw("QS7\\8"))) == ["QS7", "QS8"]
    assert _texts(_group(CN, "QS7\uff0dQS9")) == ["QS7", "QS8", "QS9"]  # a full-width dash
    assert _read(IEC, fw("X7:41")).kind is IdentifierKind.TERMINAL
    assert _read(IEC, "X7:41").forms.original != _read(IEC, fw("X7:41")).forms.original


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        (f"QS7{IDEOGRAPHIC_COMMA}QS8", (f"QS7{IDEOGRAPHIC_COMMA}QS8",)),  # a list, one candidate
        (f"\u300cQS7\u300d{FULL_STOP}", ("QS7",)),
        (f"\u3010QS7\u3011{FULL_STOP}\u3010QS8\u3011", ("QS7", "QS8")),
        (f"QS7{FULL_SEMICOLON}QS8{FULL_SEMICOLON}", ("QS7", "QS8")),
        ("\u201cQS7\u201d", ("QS7",)),
        ("\u2018QS7\u2019", ("QS7",)),
        ("\u300aQS7\u300b", ("QS7",)),
        ("QS7\u3000QS8", ("QS7", "QS8")),  # an ideographic space is a space
        (f"X7:41{FULL_COMMA}42{FULL_STOP}", (f"X7:41{FULL_COMMA}42",)),
        ("QS7:", ("QS7",)),
        ("1X-37\u2032", ("1X-37\u2032",)),  # a typographic prime after a digit stays
        ("", ()),
        ("   ", ()),
        (";;", ()),
        (FULL_STOP, ()),
        ("\u3010\u3011", ()),
        ("((()))", ()),
    ],
)
def test_split_candidates_cjk_edges(word: str, expected: tuple[str, ...]) -> None:
    assert split_candidates(word) == expected


def test_split_candidates_arabic_words_pass_through() -> None:
    word = "\u0645\u0641\u062a\u0627\u062d\u200f"  # Arabic letters and a right-to-left mark
    assert split_candidates(word) == (word,)
    assert split_candidates("(\u0661\u0662\u0663)") == ("\u0661\u0662\u0663",)
    assert FULL.parse("\u0645\u0641\u062a\u0627\u062d") is None
    arabic_digits = _read(FULL, "\u0661\u0662\u0663")  # folded to ASCII, as before F3
    assert arabic_digits.forms.canonical == "123"


def test_split_candidates_pieces_are_stable_and_clean() -> None:
    rng = random.Random(11)
    alphabet = "QS7X:41,-'\" ;()[]\u3001\u3002\uff0c\uff1b\u300c\u300d\u3000\u2032"
    for _ in range(500):
        word = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        pieces = split_candidates(word)
        assert split_candidates(word) == pieces  # deterministic
        for piece in pieces:
            assert piece and not any(ch.isspace() or ch in ";\u3002\uff1b" for ch in piece)


def test_split_candidates_is_linear_on_a_huge_word() -> None:
    word = "(" * 50_000 + "QS7" + ")" * 50_000 + ";" + ("a;" * 20_000)
    started = time.perf_counter()
    pieces = split_candidates(word)
    assert time.perf_counter() - started < 2.0
    assert pieces[0] == "QS7" and len(pieces) == 20_001


@pytest.mark.parametrize(
    "text", ["\u2460", "\u2473", "\u24b6", "\u2776", "\u3251", "\u32b1", "\u2460,\u2461"]
)
def test_an_enclosed_number_is_never_a_wire(text: str) -> None:
    assert FULL.parse(text) is None
    assert FULL.parse(f"QS7,{text}") is None or _read(FULL, f"QS7,{text}").kind is _GROUP


# --- the deviations: 1 page 0 on the wire pattern --------------------------------------------


@pytest.mark.parametrize("text", ["0.7", "00.7", "0.70", "0.7-1", "00.7-12"])
def test_deviation_1_page_zero_is_no_page_wire(text: str) -> None:
    named = IdentifierParser(
        RULES.narrowed(_selection(wire_number_patterns=("sequential", "page_column")))
    )
    assert named.parse(text) is None and FULL.parse(text) is None


@pytest.mark.parametrize("text", ["1.2-3", "10.12-5", "1.1"])
def test_deviation_1_other_pages_stay_wires_when_named(text: str) -> None:
    named = IdentifierParser(
        RULES.narrowed(_selection(wire_number_patterns=("sequential", "page_column")))
    )
    found = _read(named, text)
    assert found.kind in (IdentifierKind.WIRE_NUMBER, IdentifierKind.CROSS_REFERENCE)


@pytest.mark.parametrize(
    "text", ["0.7", "/0.7", "0/3", "0.0", "0.6E", "00.6E", "0.5kV", "0.9", "/0.9", "000.1"]
)
def test_page_zero_is_no_reference_in_any_format(text: str) -> None:
    for parser in (FULL, KKS, ANSI, CN):
        found = parser.parse(text)
        assert found is None or found.kind is not IdentifierKind.CROSS_REFERENCE, text
    assert FULL.tally([text] * 5).cross_reference_formats == {}


@pytest.mark.parametrize(
    ("text", "variant"),
    [("1.1", "page_dot_column"), ("27.6E", "page_dot_location"), (".6E", "same_page_location")],
)
def test_pages_from_one_up_still_read_as_references(text: str, variant: str) -> None:
    found = _read(FULL, text)
    assert found.kind is IdentifierKind.CROSS_REFERENCE and found.variant == variant


def test_a_prefixed_page_zero_is_no_cross_reference() -> None:
    found = FULL.parse("=A1/0.7")
    assert found is None or found.kind is not IdentifierKind.CROSS_REFERENCE
    assert _read(FULL, "=A1/12.3").kind is IdentifierKind.CROSS_REFERENCE


# --- the deviations: 2 the primed label ------------------------------------------------------


def test_deviation_2_the_prime_is_for_terminals_and_bridges_only() -> None:
    assert is_valid_label("37") and not is_valid_label("37'") and not is_valid_label('37"')
    assert is_valid_label("37'", primed=True) and is_valid_label('37"', primed=True)
    assert is_valid_label("123456'", primed=True) and not is_valid_label("1234567'", primed=True)
    assert not is_valid_label("''", primed=True) and not is_valid_label("'", primed=True)
    assert not is_valid_label("", primed=True) and not is_valid_label("3'7", primed=True)
    assert check_label(RULES, "37'") == (None, ("connection_point.malformed",))
    assert check_label(RULES, "37'", primed=True) == ("37'", ())
    assert check_label(RULES, "", primed=True) == (None, ("connection_point.empty",))
    assert check_label(RULES, "3:7'", primed=True)[1] == ("connection_point.multiple",)


@pytest.mark.parametrize("text", ["KM1:13'", "-K5:13'", "37'", '37"', "A1'", "X1'"])
def test_deviation_2_a_prime_elsewhere_is_no_identifier(text: str) -> None:
    assert IEC.parse(text) is None, text
    found = FULL.parse(text)  # with every convention a relay point may read, but never valid
    assert found is None or not found.valid, text


@pytest.mark.parametrize(
    ("text", "point", "valid"),
    [
        ("1X-37'", "37'", True),
        ('1X-37"', '37"', True),
        ("1X-37\u2032", "37'", True),
        ("1X-37\u2033", '37"', True),
        ("X7:41'", "41'", True),
        ("X7:A'", "A'", True),
        ("X7:41''", None, False),
        ("X7:'", None, False),
        ("1X-'", None, False),
        ("X7:1234567'", None, False),
    ],
)
def test_deviation_2_the_terminal_prime(text: str, point: str | None, valid: bool) -> None:
    found = _read(IEC, text)
    assert found.kind is IdentifierKind.TERMINAL and found.valid is valid
    assert found.connection_point == point
    if valid:
        plain = _read(IEC, text.rstrip("'\"\u2032\u2033"))
        assert plain.forms.canonical != found.forms.canonical  # 37' is another terminal
        assert not set(plain.forms.search_keys) & set(found.forms.search_keys)


# --- the deviations: 3 an opening quote closes the piece -------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("'QS7'", ("QS7",)),
        ('"QS7"', ("QS7",)),
        ("'1X-37'", ("1X-37",)),
        ("1X-37'", ("1X-37'",)),
        ('1X-37"', ('1X-37"',)),
        ("1X-37'.", ("1X-37'",)),
        ("1X-37',", ("1X-37'",)),
        ("(1X-37')", ("1X-37'",)),
        ("[1X-37']", ("1X-37'",)),
        ("K1'", ("K1'",)),
        ("'", ()),
        ("''", ()),
        ("QS7'", ("QS7'",)),  # a prime after a digit stays, whatever the tag
        ("QS'", ("QS",)),  # not after a digit: it closes
        ("\"'QS7'\"", ("QS7",)),
    ],
)
def test_deviation_3_quotes_and_primes(word: str, expected: tuple[str, ...]) -> None:
    assert split_candidates(word) == expected


# --- the deviations: 4 a later bare item inherits the first item's head ----------------------


def test_deviation_4_inheritance_needs_a_first_item_with_a_point() -> None:
    inherited = _group(IEC, "X7:41,42,x")
    assert _texts(inherited) == ["X7:41", "X7:42", "X7:X"]  # a label is a label
    plain = _group(CN, "QS7,8")  # a code has no point: 8 stays a bare wire number
    assert _texts(plain) == ["QS7", "8"]
    assert plain.group is not None and plain.group.members[1].identifier is not None
    assert plain.group.members[1].identifier.kind is IdentifierKind.WIRE_NUMBER
    assert plain.convention is None  # the members disagree
    assert _texts(_group(IEC, "1X-37,38")) == ["1X-37", "1X-38"]
    assert _texts(_group(IEC, "1X-37',38")) == [
        "1X-37'",
        "1X-38",
    ]  # inherits the head, not the prime


def test_deviation_4_a_primed_later_item_is_flagged_not_guessed() -> None:
    """Fail-safe: a later primed label is not a plain label, so it is reported unreadable."""
    found = _group(IEC, "1X-37',38'")
    assert not found.valid and found.issues == ("group.member_unreadable",)


# --- the deviations: 5 and 6 the vendor range and the group's keys ---------------------------


def test_deviation_5_a_wire_range_needs_the_named_pattern() -> None:
    unnamed = _group(IEC, "38Y-43Y")
    assert not unnamed.valid and unnamed.issues == ("group.member_unreadable",)
    assert _texts(unnamed) == ["38Y", "39Y", "40Y", "41Y", "42Y", "43Y"]
    assert unnamed.convention is None
    ansi = _group(ANSI, "38Y-43Y")  # ANSI alone does not read 38Y without context either
    assert ansi.issues == ("group.member_unreadable",)
    named = _group(VENDOR, "38Y-43Y")
    assert named.valid and len(_texts(named)) == 6
    assert all(
        m.identifier is not None and m.identifier.kind is IdentifierKind.WIRE_NUMBER
        for m in (named.group.members if named.group else ())
    )
    assert _group(VENDOR_ANSI, "38Y-43Y").valid
    assert ANSI.parse("38Y-43Y", hint=_DEVICE) is None  # a hint reads no group
    reversed_range = VENDOR.parse("43Y-38Y")
    assert reversed_range is None or reversed_range.kind is not _GROUP


def test_deviation_5_the_letter_first_wire_range_is_named_too() -> None:
    found = _group(VENDOR, "Y38-Y41")
    assert found.valid and _texts(found) == ["Y38", "Y39", "Y40", "Y41"]
    assert not _group(IEC, "Y38-Y41").valid


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (IEC, "X7:41,42,43"),
        (CN, "QS7-QS9"),
        (CN, "QS7\\8\\9"),
        (KKS, "20XYZ10AB131/2/3"),
        (VENDOR, "38Y-43Y"),
        (CN, "QS7,QS8"),
    ],
)
def test_deviation_6_a_group_keys_are_its_own_form_then_its_members(
    parser: IdentifierParser, text: str
) -> None:
    found = _group(parser, text)
    assert found.group is not None
    expected = dedupe(
        [
            found.forms.canonical,
            *(m.identifier.forms.canonical for m in found.group.members if m.identifier),
        ]
    )
    assert found.forms.search_keys == expected
    member_keys = {
        k for m in found.group.members if m.identifier for k in m.identifier.forms.search_keys
    }
    assert set(found.forms.search_keys) <= member_keys | {found.forms.canonical}
    assert found.forms.search_keys[0] == found.forms.canonical


def test_deviation_6_the_keys_of_an_invalid_group_hold_only_what_read() -> None:
    found = _group(IEC, "X23-X37")
    assert not found.valid and found.forms.search_keys == (found.forms.canonical,)


# --- the deviations: 7 rating units ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("37kV", "37", "kV"),
        ("37KVA", "37", "kVA"),
        ("37KVAR", "37", "kvar"),
        ("37Kvar", "37", "kvar"),
        ("47HZ", "47", "Hz"),
        ("47hz", "47", "Hz"),
        ("7KW", "7", "kW"),
        ("7kw", "7", "kW"),
        ("13MA", "13", "mA"),
        ("2ph", "2", "PH"),
        ("37mvar", "37", "Mvar"),
        ("37MVAR", "37", "Mvar"),
    ],
)
def test_deviation_7_read_rating_returns_the_rules_spelling(
    text: str, value: str, unit: str
) -> None:
    assert read_rating(folded(text), RULES) == Rating(value, unit)


@pytest.mark.parametrize("text", ["37kv", "37KV", "3ka", "3KA", "29Ka"])
def test_deviation_7_a_unit_that_is_a_letter_code_matches_only_as_written(text: str) -> None:
    cn_rules = RULES.narrowed(_selection(conventions=frozenset({Convention.CN_LETTER_CODES})))
    assert read_rating(folded(text), cn_rules) is None
    found = _read(CN, text)
    assert found.kind is IdentifierKind.LETTER_CODE


@pytest.mark.parametrize(
    "text",
    ["1e5kV", "7-29-31kV", "1234567kV", "0.1234kV", ".7kV", "37-kV", "37kV.", "37k", "kV", "37"],
)
def test_deviation_7_what_is_no_rating(text: str) -> None:
    assert read_rating(folded(text), RULES) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("37kV", "37"),
        ("0.7kV", "0.7"),
        ("7-29kV", "7-29"),
        ("7~29kV", "7~29"),
        ("123456kV", "123456"),
    ],
)
def test_a_rating_keeps_its_value_as_written(text: str, value: str) -> None:
    assert read_rating(folded(text), RULES) == Rating(value, "kV")


@pytest.mark.parametrize(
    "text",
    [
        "37MVA", "47Hz", "17VA", "29kA", "2PH", "7KW", "13mA", "7-29kV", "0.7kV", "37kV",
        "37KVAR", "47hz", "0.5Hz", "7~29kV", "37Mvar",
    ],
)  # fmt: skip
def test_ratings_are_no_identifiers_under_any_rules(text: str) -> None:
    for parser in (FULL, IEC, CN, KKS, ANSI, VENDOR_ANSI):
        found = parser.parse(text)
        assert found is None or found.kind is _GROUP, (text, found)
    assert FULL.tally([text] * 20).conventions == {}


def test_a_rating_beside_an_identifier_leaves_the_identifier_alone() -> None:
    assert _read(CN, "QS7").kind is IdentifierKind.LETTER_CODE
    assert _read(FULL, "24VDC").kind is IdentifierKind.VOLTAGE
    assert _read(FULL, "+24V").kind is IdentifierKind.VOLTAGE
    tally = CN.tally(["QS7", "37kV", "47Hz", "QS8", "0.7", "17VA"])
    assert tally.tokens == 6 and tally.conventions == {Convention.CN_LETTER_CODES: 2}
    # the words around a rating are read apart (a space ends a candidate)
    assert split_candidates("QS7 37kV") == ("QS7", "37kV")
    assert CN.tally(["QS7 37kV"]).conventions == {Convention.CN_LETTER_CODES: 1}


def test_qa_f3_1_a_rating_inside_a_list_is_no_identifier() -> None:
    found = CN.parse("QS7,37kV")
    assert found is not None and found.kind is _GROUP and found.group is not None
    assert not found.valid and "group.member_unreadable" in found.issues
    assert [m.identifier is None for m in found.group.members] == [False, True]


def test_deviation_7_the_units_table_is_data() -> None:
    for mutate, ok in (
        (lambda d: d["ratings"].update(units=["abcde"]), True),  # five letters
        (lambda d: d["ratings"].update(units=["abcdef"]), False),  # six
        (lambda d: d["ratings"].update(units=["k\u0391"]), False),  # a Greek capital
        (lambda d: d["ratings"].update(units=["a1"]), False),
        (lambda d: d["ratings"].update(units=["k\uff36"]), False),  # a full-width letter
    ):
        data = copy.deepcopy(_raw())
        mutate(data)
        if ok:
            assert IdentifierRules.from_mapping(data).ratings.units == ("abcde",)
        else:
            with pytest.raises(RuleError):
                IdentifierRules.from_mapping(data)


# --- the deviations: 8 and 9 the KKS bound and the unused `shortest` -------------------------


def test_deviation_8_the_kks_part_bound_stays_eight() -> None:
    assert MAX_KKS_PARTS == 8
    data = copy.deepcopy(_raw())
    data["kks"]["forms"][0]["parts"] = ["system"] * (MAX_KKS_PARTS + 1)
    with pytest.raises(RuleError):
        IdentifierRules.from_mapping(data)


def test_deviation_9_the_near_miss_gate_reads_its_own_key_not_shortest() -> None:
    kks = RULES.kks
    assert kks.near_miss_min_length == 7
    assert _read(KKS, "20XYZ").kks is not None and _read(KKS, "20XYZ").valid
    assert KKS.parse("20XY") is None and KKS.parse("20XYZ1") is None  # short: no near miss
    near = _read(KKS, "20XYZ10AB123CD4567")
    assert not near.valid and "kks.malformed" in near.issues
    wider = IdentifierParser(
        replace(RULES, kks=replace(kks, near_miss_min_length=20)).narrowed(
            _selection(conventions=frozenset({Convention.IEC_81346, Convention.KKS}))
        )
    )
    assert wider.parse("20XYZ10AB123CD4567") is None  # the gate is the data key


# --- ANSI with and without a class, with and without a hint (F3-AC-1, F3-AC-6) ----------------


@pytest.mark.parametrize("text", ["87T", "50BF", "51N", "27-1", "59G2", "59Y", "87QZ"])
def test_ansi_with_a_class_reads_with_and_without_a_hint(text: str) -> None:
    without = _read(ANSI, text)
    with_hint = _read(ANSI, text, hint=_DEVICE)
    assert without.kind is _DEVICE and with_hint.kind is _DEVICE
    assert without.device_class is not None and without.device_class == with_hint.device_class
    assert without.valid == with_hint.valid


@pytest.mark.parametrize("text", ["8P", "8Y", "11R", "16F", "14B6", "23-4", "4-23", "38Y"])
def test_ansi_without_a_class_needs_a_hint(text: str) -> None:
    assert ANSI.parse(text) is None
    found = _read(ANSI, text, hint=_DEVICE)
    assert found.kind is _DEVICE and found.device_class is None and found.alternatives == ()


def test_ansi_an_unknown_suffix_with_a_class_is_invalid_but_kept() -> None:
    for hint in (None, _DEVICE):
        found = _read(ANSI, "87QZ", hint=hint)
        assert not found.valid and found.issues == ("ansi.unknown_suffix",)
        assert found.device_class == "differential_protection"
    assert _read(ANSI, "16F", hint=_DEVICE).issues == ("ansi.unknown_suffix",)


@pytest.mark.parametrize("text", ["59G2-1", "100T", "0.7", "00", "05"])
def test_ansi_that_is_no_device_even_with_a_hint(text: str) -> None:
    assert ANSI.parse(text, hint=_DEVICE) is None


def test_ansi_a_bare_number_is_a_wire_with_an_ansi_alternative() -> None:
    found = _read(ANSI, "21")
    assert found.kind is IdentifierKind.WIRE_NUMBER
    assert [a.kind if hasattr(a, "kind") else a for a in found.alternatives] == [_DEVICE]
    hinted = _read(ANSI, "21", hint=_DEVICE)
    assert hinted.kind is _DEVICE and hinted.device_class == "distance_protection"
    assert IEC.parse("87T") is None  # ANSI not enabled: nothing


def test_ansi_a_compound_reads_as_a_reference_with_the_device_alternative_only_in_context() -> None:
    plain = _read(ANSI, "12/3")
    assert plain.kind is IdentifierKind.CROSS_REFERENCE and plain.variant == "page_slash_column"
    assert _read(ANSI, "12/3", hint=_DEVICE).kind is _DEVICE
    assert _read(ANSI, "50/51").kind is _DEVICE  # both numbers have a class
    assert _read(ANSI, "50/51", hint=_DEVICE).alternatives == ()
    assert _read(IEC, "50/51").kind is IdentifierKind.CROSS_REFERENCE


def test_a_named_wire_pattern_wins_over_ansi_without_context_only_when_named() -> None:
    for text in ("38Y", "59Y", "87T", "51N", "16F"):
        named = _read(VENDOR_ANSI, text)
        assert named.kind is IdentifierKind.WIRE_NUMBER and named.variant == "digits_letter"
        assert set(named.alternatives) <= {_DEVICE}
    assert _read(VENDOR_ANSI, "50BF").kind is _DEVICE  # no pattern matches a two-letter tail
    assert _read(VENDOR_ANSI, "59G2").kind is _DEVICE  # nor does a trailing digit
    assert ANSI.parse("38Y") is None and _read(ANSI, "59Y").kind is _DEVICE


def test_unnarrowed_rules_never_read_an_opt_in_wire_pattern() -> None:
    for text in ("38Y", "Y38", "100T", "8P", "T100"):
        found = FULL.parse(text)
        assert found is None or found.variant not in {"digits_letter", "letter_digits"}


# --- the F2 evidence rule must still hold (D-36) ---------------------------------------------


def test_evidence_a_rating_letter_suffix_is_no_ansi_evidence_alone_or_in_a_list() -> None:
    assert IEC.tally(["32A"] * 10).conventions == {}
    assert IEC.tally(["32A,33A"] * 10).conventions == {}
    assert IEC.tally(["32A", "(32A)", "32A\u3002"]).conventions == {}


def test_evidence_a_listed_ansi_function_counts_once_per_member() -> None:
    tally = IEC.tally(["87T,50BF,51N"] * 3)
    assert tally.conventions == {Convention.ANSI: 9} and tally.tokens == 3
    assert IEC.tally(["87T", "50BF", "51N"]).conventions == {Convention.ANSI: 3}


def test_evidence_a_system_code_is_a_tag_but_never_evidence() -> None:
    assert _read(KKS, "20XYZ").kks is not None and _read(KKS, "20XYZ").kks.level == 1  # type: ignore[union-attr]
    assert IEC.tally(["20XYZ"] * 30).conventions == {}
    assert IEC.tally(["20XYZ,21XYZ"] * 10).conventions == {}
    assert (
        IEC.tally(["(20XYZ)", f"20XYZ{FULL_STOP}", f"20XYZ{FULL_SEMICOLON}21XYZ"]).conventions == {}
    )


@pytest.mark.parametrize(
    "text",
    [
        "20XYZ10AB123-CD45",
        "20XYZ10-AB123",
        "20XYZ10AB123CD456",
        "20XYZ10AB131/2/3",
    ],
)
def test_evidence_an_equipment_tag_or_deeper_is_evidence_with_the_separator_too(text: str) -> None:
    tally = IEC.tally([text] * 4)
    assert tally.conventions.get(Convention.KKS, 0) >= 4


def test_evidence_the_level_is_the_rules_own_value() -> None:
    shallow = replace(RULES, kks=replace(RULES.kks, evidence_min_level=1))
    parser = IdentifierParser(
        shallow.narrowed(_selection(conventions=frozenset({Convention.IEC_81346})))
    )
    assert parser.tally(["20XYZ"] * 3).conventions == {Convention.KKS: 3}
    assert IEC.tally(["20XYZ"] * 3).conventions == {}


def test_evidence_the_non_evidence_suffixes_are_the_rules_own_value() -> None:
    open_rules = replace(RULES, ansi=replace(RULES.ansi, non_evidence_suffixes=frozenset()))
    parser = IdentifierParser(
        open_rules.narrowed(_selection(conventions=frozenset({Convention.IEC_81346})))
    )
    assert parser.tally(["32A"] * 3).conventions == {Convention.ANSI: 3}
    assert IEC.tally(["32A"] * 3).conventions == {}


def test_evidence_a_rating_and_a_non_ascii_lookalike_prove_nothing() -> None:
    assert IEC.tally(["37kV", "29kA", "47Hz", "17VA", "2PH", "7KW"] * 5).conventions == {}
    assert IEC.tally(["20\u0425YZ10AB123"] * 3).conventions == {}  # a Cyrillic capital Ha
    assert IEC.tally([fw("20XYZ10AB123")] * 3).conventions == {Convention.KKS: 3}  # NFKC folds it


def test_evidence_an_invalid_group_member_counts_only_when_it_read() -> None:
    probe = IEC.tally(["20XYZ10AB131/2/3"])  # KKS is only probed: counted, not parsed
    assert probe.conventions == {Convention.KKS: 3} and probe.identifiers == 0
    tally = KKS.tally(["20XYZ10AB131/2/3"])
    assert tally.conventions == {Convention.KKS: 3} and tally.identifiers == 3
    assert IEC.tally(["QS7-QS99"]).conventions == {}


# --- tally and groups (F3-AC-16) -------------------------------------------------------------


def test_tally_counts_each_member_and_the_tokens_it_saw() -> None:
    tally = IEC.tally(["X7:41,42,43", "X7:51;X7:52", "x"])
    assert tally.conventions == {Convention.IEC_81346: 5}
    assert tally.tokens == 3 and tally.identifiers == 5


def test_tally_over_the_cap_or_unreadable_counts_no_member() -> None:
    assert CN.tally(["QS1-QS65"]).identifiers == 0
    assert CN.tally(["QS1-QS64"]).conventions == {Convention.CN_LETTER_CODES: 64}
    assert IEC.tally(["X23-X37"]).conventions == {}


# --- determinism (NFR-F3-2) ------------------------------------------------------------------

_CORPUS = [
    "X7:41,42,43", "X7:41\uff0c42", "QS7-QS9", "QS7\\8", "20XYZ10AB131/2/3", "38Y-43Y", "QS1-QS99",
    "X23-X37", "X7:41,,42", "27.6E", ".6E", "0.7", "37kV", "37KV", "24VDC", "1X-37'", "1X-37\u2032",
    "59G2", "59Y", "8P", "16F", "12/3", "50/51", "20XYZ", "20XYZ10AB123-CD45", "20XYZ10AB123CD456",
    "\u2460", fw("QS7-QS9"), "\u0645\u0641\u062a\u0627\u062d", "", " ", "-", ",", "QS7,37kV",
]  # fmt: skip


def test_determinism_independent_parsers_agree_on_every_shape() -> None:
    for build in (
        lambda: IdentifierParser(load_rules()),
        lambda: parser_for(Convention.IEC_81346, Convention.KKS, Convention.ANSI),
        lambda: parser_for(Convention.CN_LETTER_CODES),
        lambda: parser_for(Convention.NFPA_JIC, Convention.ANSI),
    ):
        first, second = build(), build()
        for text in _CORPUS:
            assert first.parse(text) == second.parse(text), text
            assert first.parse(text) == first.parse(text), text
            assert first.parse(text, hint=_DEVICE) == second.parse(text, hint=_DEVICE), text


def test_determinism_the_order_of_parsing_changes_nothing() -> None:
    parser = IdentifierParser(load_rules())
    expected = {text: parser.parse(text) for text in _CORPUS}
    shuffled = list(_CORPUS)
    random.Random(5).shuffle(shuffled)
    again = IdentifierParser(load_rules())
    assert {text: again.parse(text) for text in shuffled} == expected


def test_determinism_the_tally_does_not_depend_on_token_order() -> None:
    parser = IdentifierParser(load_rules())
    forward = parser.tally(_CORPUS)
    backward = IdentifierParser(load_rules()).tally(reversed(_CORPUS))
    assert forward == backward
    assert list(forward.conventions) == sorted(forward.conventions)  # sorted, not insertion order


# --- `ele-parse` `_tokens`: the ordering and the long-word split (F3-AC-23, F3-AC-24) ---------


def _word(text: str) -> tuple[float, float, float, float, str]:
    return (0.0, 0.0, 1.0, 1.0, text)


def _words(*texts: str) -> list[Any]:
    return [_word(t) for t in texts]


def test_tokens_digit_tokens_come_first_whatever_their_place() -> None:
    assert ele_inspect._tokens(_words("title", "A1", "page", "B2", "C"), 200) == [
        "A1",
        "B2",
        "title",
        "page",
        "C",
    ]


def test_tokens_the_sample_bound_cuts_the_others_not_the_digit_tokens() -> None:
    words = _words(*[f"w{chr(97 + i)}" for i in range(10)], "K1", "K2", "K3")
    assert ele_inspect._tokens(words, 5) == ["K1", "K2", "K3", "wa", "wb"]
    assert ele_inspect._tokens(words, 3) == ["K1", "K2", "K3"]
    assert ele_inspect._tokens(words, 1) == ["K1"]
    assert ele_inspect._tokens(words, 0) == []


def test_tokens_are_distinct_across_words_and_pieces() -> None:
    word = "20XYZ10AB101" + IDEOGRAPHIC_COMMA + "20XYZ10AB101" + IDEOGRAPHIC_COMMA + "20XYZ10AB102x"
    got = ele_inspect._tokens(_words("20XYZ10AB101", word, "(20XYZ10AB101)"), 200)
    assert got == ["20XYZ10AB101", "20XYZ10AB102x"]


def test_tokens_a_short_word_keeps_its_list_whole() -> None:
    assert ele_inspect._tokens(_words("X7:41\u3001" + "42"), 200) == ["X7:41\u3001" + "42"]
    assert ele_inspect._tokens(_words("QS7,QS8"), 200) == ["QS7,QS8"]


@pytest.mark.parametrize("sep", sorted(contract.LONG_TOKEN_SPLIT))
def test_tokens_every_long_token_separator_splits(sep: str) -> None:
    tags = [f"20XYZ10AB1{n}" for n in range(10, 14)]
    word = sep.join(tags)
    assert len(word) > contract.MAX_TOKEN_CHARS
    assert ele_inspect._tokens(_words(word), 200) == tags


def test_tokens_a_long_word_keeps_its_fitting_pieces_only() -> None:
    fits = "20XYZ10AB101"
    word = f"{fits},{'x' * 40},{fits[:-1]}2{FULL_STOP}{'9' * 33}{FULL_COMMA}{fits[:-1]}3"
    assert ele_inspect._tokens(_words(word), 200) == [fits, fits[:-1] + "2", fits[:-1] + "3"]
    assert ele_inspect._tokens(_words("A" * 33), 200) == []  # no separator: skipped, as before
    edge = "Z" * contract.MAX_TOKEN_CHARS
    assert ele_inspect._tokens(_words(edge), 200) == [edge]  # exactly at the bound stays
    assert ele_inspect._tokens(_words(edge + "1"), 200) == []
    assert ele_inspect._tokens(_words(",,,,"), 200) == []
    assert ele_inspect._tokens(_words("," + "A" * 40 + ","), 200) == []


def test_tokens_split_pieces_with_a_digit_join_the_digit_group() -> None:
    word = "prose1," + "x" * 30 + ",other,K9," + "y" * 10
    assert (
        ele_inspect._tokens(_words("title", word), 200)
        == [
            "prose1",
            "K9",
            "title",
            "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "other",
            "yyyyyyyyyy",
        ][:6]
    )


def test_tokens_unicode_and_arabic_words() -> None:
    arabic = "\u0645\u0641\u062a\u0627\u062d"
    indic = "\u0661\u0662\u0663"  # str.isdigit() is true for Arabic-Indic digits
    got = ele_inspect._tokens(_words(arabic, indic, "QS7", "\u4e2d\u6587"), 200)
    assert got == [indic, "QS7", arabic, "\u4e2d\u6587"]
    assert all(len(t) <= contract.MAX_TOKEN_CHARS for t in got)


def test_tokens_a_huge_word_is_bounded_in_time_and_size() -> None:
    started = time.perf_counter()
    got = ele_inspect._tokens(_words("A" * 1_000_000, "," * 500_000, "K1,K2" * 100_000), 200)
    assert time.perf_counter() - started < 10.0
    assert len(got) <= 200 and all(len(t) <= contract.MAX_TOKEN_CHARS for t in got)


def test_tokens_a_page_of_five_thousand_words_stays_in_reading_order() -> None:
    words = _words(*[f"t{i}" for i in range(5000)])
    got = ele_inspect._tokens(words, 200)
    assert got == [f"t{i}" for i in range(200)]


def test_tokens_the_service_version_moved_with_the_fill_order() -> None:
    assert SERVICE_VERSION == "1.1.0"


# --- re-test 1: ratings inside groups, the text flags on members, the tally budget -----------

_CTRL = ["\x85", "\x1c", "\x1d", "\x1e", "\x1f", "\x00", "\x07", "\u200b"]


def _unreadable(found: ParsedIdentifier | None, count: int, bad: int) -> None:
    assert found is not None and found.kind is _GROUP and found.group is not None
    assert not found.valid and "group.member_unreadable" in found.issues
    assert len(found.group.members) == count
    assert found.group.members[bad].identifier is None


@pytest.mark.parametrize("unit", ["kV", "kVA", "MVA", "Hz", "kA", "kW", "mA", "kvar", "VA"])
def test_rework_a_rating_in_a_list_is_unreadable_for_every_unit(unit: str) -> None:
    for parser in (CN, FULL):
        _unreadable(parser.parse(f"QS7,37{unit}"), 2, 1)
        _unreadable(parser.parse(f"QS7,QS8,37{unit}"), 3, 2)
    for parser in (IEC, KKS, FULL):  # a terminal list: the rating does not inherit the head
        _unreadable(parser.parse(f"X7:41,37{unit}"), 2, 1)
        _unreadable(parser.parse(f"X7:41,0.7{unit}"), 2, 1)


@pytest.mark.parametrize("text", ["37kV,QS7", "37kV,47Hz", "37kV,47Hz,29kA", "7-29kV,QS7"])
def test_rework_a_rating_as_the_first_item_is_no_group(text: str) -> None:
    for parser in (CN, IEC, KKS, FULL):
        assert parser.parse(text) is None


@pytest.mark.parametrize(
    "text", ["1kV-3kV", "1kV-3", "1-3kV", "QS7-QS9kV", "20XYZ10AB131/2kV", "QS7\\8kV"]
)
def test_rework_a_range_or_suffix_list_with_a_rating_end_is_no_group(text: str) -> None:
    for parser in (CN, IEC, KKS, FULL):
        found = parser.parse(text)
        assert found is None or found.kind is not _GROUP


@pytest.mark.parametrize("text", ["QS7,37kV\x85", "QS7,37kV\x1f", "QS7,37\x1ckV", "QS7,37kV\u200b"])
def test_rework_a_control_character_does_not_hide_a_rating_in_a_list(text: str) -> None:
    _unreadable(CN.parse(text), 2, 1)


@pytest.mark.parametrize(
    "text", ["\uff31\uff33\uff17,37kV", "QS7,\uff13\uff17kV", "QS7,37\uff4bV", "QS7\uff0c37kV"]
)
def test_rework_a_wide_spelling_of_a_rating_in_a_list_is_still_a_rating(text: str) -> None:
    _unreadable(CN.parse(text), 2, 1)


def test_rework_a_unit_that_is_a_letter_code_follows_its_written_case_in_a_list_too() -> None:
    """As alone: 37kV is a rating, 37KV and 37kv are the relay code (F3-AC-7)."""
    for written in ("37KV", "37kv", "3KA"):
        found = _read(CN, f"QS7,{written}")
        assert found.valid and found.group is not None
        assert found.group.members[1].identifier is not None
        assert found.group.members[1].identifier.kind is IdentifierKind.LETTER_CODE
        alone = _read(CN, written)
        assert alone.kind is IdentifierKind.LETTER_CODE
    _unreadable(CN.parse("QS7,3kA"), 2, 1)
    _unreadable(CN.parse("QS7,37kV"), 2, 1)


def test_rework_a_mixed_list_flags_only_the_rating_and_keeps_the_rest() -> None:
    found = _read(CN, "QS7,37kV,QS8")
    assert found.group is not None and found.issues == ("group.member_unreadable",)
    assert [m.identifier is None for m in found.group.members] == [False, True, False]
    assert _read(IEC, "X7:41,24VDC").valid and _read(IEC, "X7:41,+24V").valid  # a voltage is not
    assert _read(IEC, "X7:41,42").valid


@pytest.mark.parametrize("ctrl", _CTRL)
def test_rework_a_rating_ending_in_a_control_character_is_still_no_identifier(ctrl: str) -> None:
    for text in (f"37kV{ctrl}", f"{ctrl}37kV", f"37{ctrl}kV", f"47Hz{ctrl}", f"17VA{ctrl}"):
        assert FULL.parse(text) is None, repr(text)
    found = FULL.parse(f"QS7{ctrl}")
    assert found is not None and not found.valid  # the control itself is flagged


def test_rework_u0085_and_the_file_separators_are_not_whitespace() -> None:
    """One predicate for the fold, the split and the rating check (S-4)."""
    for ctrl in ("\x85", "\x1c", "\x1d", "\x1e", "\x1f"):
        assert not is_space(ctrl)
        assert split_candidates(f"QS7{ctrl}QS8") == (f"QS7{ctrl}QS8",)
    for space in ("\t", "\n", "\r", "\v", "\f", " ", "\u3000", "\u00a0", "\u2028", "\u2029"):
        assert is_space(space)
        assert split_candidates(f"QS7{space}QS8") == ("QS7", "QS8")


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (IEC, "X7:41,42,43"),
        (IEC, "X7:41,42\x1f"),
        (CN, "QS7-QS9"),
        (CN, "QS7\\8"),
        (KKS, "20XYZ10AB131/2/3"),
    ],
)
def test_rework_the_text_flags_reach_every_member_of_every_group_form(
    parser: IdentifierParser, text: str
) -> None:
    clean = _group(parser, text.replace("\x1f", ""))
    assert clean.group is not None and clean.valid
    for flagged in (text + "\u200b", "\u200b" + text):
        found = _group(parser, flagged)
        assert found.group is not None and not found.valid
        assert "text.hidden_character" in found.issues
        for member in found.group.members:
            assert member.identifier is not None
            assert "text.hidden_character" in member.identifier.issues
            assert not member.identifier.valid
    bidi = _group(parser, text + "\u202e")
    assert bidi.group is not None and "text.bidi_control" in bidi.issues
    for member in bidi.group.members:
        assert member.identifier is not None
        assert "text.bidi_control" in member.identifier.issues


def test_rework_a_flagged_group_counts_as_its_members_do_alone() -> None:
    """Additive evidence (KKS) needs a clean reading; a device convention counts a flagged one
    alone as it always did, and a group's members now count the same."""
    assert IEC.tally(["20XYZ10AB131/2/3\u200b"] * 3).conventions == {}
    assert KKS.tally(["20XYZ10AB131/2/3\u200b"] * 3).conventions.get(Convention.KKS, 0) == 0
    assert IEC.tally(["20XYZ10AB131/2/3"] * 3).conventions == {Convention.KKS: 9}
    alone = IEC.tally(["X7:41\u200b"] * 3).conventions
    assert IEC.tally(["X7:41,42\u200b"] * 3).conventions == {
        Convention.IEC_81346: 2 * alone.get(Convention.IEC_81346, 0)
    }


def test_rework_a_group_is_never_read_on_a_hint() -> None:
    for parser, text in ((IEC, "X7:41,42,43"), (CN, "QS7-QS9"), (KKS, "20XYZ10AB131/2/3")):
        assert parser.parse(text, hint=_GROUP) is None
        assert _group(parser, text).valid  # still read from the text alone


# the budget: 2,000 members one tally call may expand (S-2)


def test_rework_the_budget_is_two_thousand() -> None:
    assert TALLY_MEMBER_BUDGET == 2_000


def _count(parser: IdentifierParser, tokens: list[str]) -> tuple[int, int]:
    tally = parser.tally(tokens)
    return sum(tally.conventions.values()), tally.identifiers


def test_rework_the_budget_boundary_at_2000_and_2001_members() -> None:
    full = ["QS1-QS50"] * 40  # exactly 2,000
    assert _count(CN, full) == (2000, 2000)
    assert _count(CN, [*full, "QS1-QS2"]) == (2000, 2000)  # the next group is not read
    assert _count(CN, ["QS1-QS50"] * 39 + ["QS1-QS49"]) == (1999, 1999)  # 1,999
    # 2,001: the group that would pass the budget is skipped whole, never cut
    assert _count(CN, ["QS1-QS50"] * 39 + ["QS1-QS51"]) == (1950, 1950)
    # a small group after a skipped one still fits what is left
    assert _count(CN, ["QS1-QS50"] * 39 + ["QS1-QS51", "QS1-QS2"]) == (1952, 1952)


def test_rework_a_normal_page_counts_exactly() -> None:
    assert _count(IEC, ["X7:41,42,43"] * 300) == (900, 900)
    assert _count(IEC, ["X7:41,42,43"] * 666) == (1998, 1998)
    assert _count(CN, ["QS7,QS8"] * 1000) == (2000, 2000)
    assert _count(CN, ["QS7,QS8"] * 1001) == (2000, 2000)


def test_rework_the_budget_is_per_call_and_counts_the_probe_too() -> None:
    assert _count(CN, ["QS1-QS50"] * 40) == _count(CN, ["QS1-QS50"] * 40) == (2000, 2000)
    assert IEC.tally(["20XYZ10AB131/2/3"] * 666).conventions == {Convention.KKS: 1998}
    assert IEC.tally(["20XYZ10AB131/2/3"] * 700).conventions == {Convention.KKS: 1998}


def test_rework_over_the_cap_groups_cost_no_budget() -> None:
    assert _count(CN, ["QS1-QS9999"] * 5000) == (0, 0)
    assert _count(CN, ["QS1-QS9999"] * 100 + ["QS1-QS50"] * 40) == (2000, 2000)
    over_list = "QS1" + ",QS1" * 70  # 71 items: over the cap, so it costs nothing (rework 2)
    assert _count(CN, [over_list] * 100 + ["QS1-QS50"] * 40) == (2000, 2000)


def test_rework_parse_has_no_budget() -> None:
    assert len(_texts(_group(CN, "QS0-QS63"))) == 64 and _group(CN, "QS0-QS63").valid


def test_rework_the_group_keys_hold_the_form_and_sixty_four_members() -> None:
    found = _group(CN, "QS1-QS64")
    assert MAX_GROUP_KEYS == 65 and len(found.forms.search_keys) == 65
    assert found.forms.search_keys[-1] == "QS64"  # M-2 of the first QA round, closed


# ele-parse (S-3)


def test_rework_a_long_word_never_overfills_the_sample() -> None:
    word = ",".join(f"K{i}" for i in range(1, 60))
    assert ele_inspect._tokens(_words(word), 5) == ["K1", "K2", "K3", "K4", "K5"]
    assert ele_inspect._tokens(_words("title", word), 5) == ["K1", "K2", "K3", "K4", "K5"]
    assert ele_inspect._tokens(_words(word), 1) == ["K1"]
    assert len(ele_inspect._tokens(_words(word), 200)) == 59  # a sample not reached: all
    huge = ",".join(f"K{i}" for i in range(200_000))
    started = time.perf_counter()
    assert len(ele_inspect._tokens(_words(huge), 200)) == 200
    assert time.perf_counter() - started < 2.0


# --- re-test 2: a group the budget refuses is an unread token, never another kind ------------

_GROUP_TEXTS = [
    "X23-X37",  # a range: no member reads under IEC, it must not read as a terminal either
    "1X-64X",  # a range that reads as a terminal or a code when it is not one
    "QS7-QS9",
    "X7:41,42,43",
    "QS7,QS8",
    "X7:41,37KV",
    "20XYZ10AB131/2/3",
    "QS7\\8",
    "QS7\\8\\9",
]
_PARSERS = {
    "iec": IEC,
    "cn": CN,
    "kks": KKS,
    "ansi": ANSI,
    "full": FULL,
    "vendor": VENDOR,
    "vendor_ansi": VENDOR_ANSI,
}


@pytest.mark.parametrize("name", sorted(_PARSERS))
@pytest.mark.parametrize("text", _GROUP_TEXTS)
def test_rework2_a_refused_group_falls_through_to_no_kind(name: str, text: str) -> None:
    parser = _PARSERS[name]
    free = parser._parse(text, None, None)
    refused = parser._parse(text, None, [0])
    if free is not None and free.kind is _GROUP:
        assert refused is None, (name, text, refused)
    else:
        assert refused == free  # a text that is no group is not touched by the budget


@pytest.mark.parametrize("name", sorted(_PARSERS))
def test_rework2_a_tally_with_no_budget_counts_no_group_and_nothing_else(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser_module, "TALLY_MEMBER_BUDGET", 0)
    parser = _PARSERS[name]
    for text in _GROUP_TEXTS:
        free = parser.parse(text)
        if free is None or free.kind is not _GROUP:
            continue
        tally = parser.tally([text] * 5)
        assert tally.conventions == {} and tally.identifiers == 0, (name, text)
        assert tally.cross_reference_formats == {} and tally.plc_families == {}
    # a token that is no group still counts: the budget never touches a plain identifier
    assert parser.tally(["X7:41", "QS7"]).tokens == 2


@pytest.mark.parametrize(
    ("parser", "fillers", "text"),
    [
        (IEC, ["X7:1" + ",1" * 49] * 40, "X23-X37"),
        (CN, ["QS1-QS50"] * 40, "1X-64X"),
        (CN, ["QS1-QS50"] * 40, "QS1-QS3"),
        (CN, ["QS1-QS50"] * 40, "QS7,QS8"),
        (CN, ["QS1-QS50"] * 40, "QS7\\8"),
        (KKS, ["20XYZ10AB131" + "/2" * 49] * 40, "20XYZ10AB131/2/3"),
        (IEC, ["20XYZ10AB131" + "/2" * 49] * 40, "20XYZ10AB131/2/3"),  # the probe's budget
        (IEC, ["X7:1" + ",1" * 49] * 40, "X7:41,42,43"),
    ],
)
def test_rework2_after_the_budget_is_spent_at_exactly_2000_nothing_more_counts(
    parser: IdentifierParser, fillers: list[str], text: str
) -> None:
    base = parser.tally(fillers)
    more = parser.tally([*fillers, text, text])
    assert sum(base.conventions.values()) == sum(more.conventions.values())
    assert base.identifiers == more.identifiers
    assert more.tokens == base.tokens + 2


def test_rework2_a_single_token_that_is_no_group_still_counts_after_the_budget() -> None:
    fillers = ["QS1-QS50"] * 40
    assert CN.tally(fillers).identifiers == 2000
    assert CN.tally([*fillers, "1X-37"]).identifiers == 2001  # a terminal-shaped single reading
    assert CN.tally([*fillers, "QS7"]).identifiers == 2001


def test_rework2_the_last_two_members_of_the_budget_fit_exactly() -> None:
    base = ["QS1-QS50"] * 39 + ["QS1-QS48"]  # 1,998 members: two are left
    assert _count(CN, base) == (1998, 1998)
    assert _count(CN, [*base, "QS7,QS8"]) == (2000, 2000)  # the list takes them
    assert _count(CN, [*base, "QS7,QS8,QS9"]) == (1998, 1998)  # three do not fit, none are read
    assert _count(CN, [*base, "QS7\\8"]) == (2000, 2000)
    assert _count(CN, [*base, "QS7-QS9"]) == (1998, 1998)
    one_left = ["QS1-QS50"] * 39 + ["QS1-QS49"]
    assert _count(CN, [*one_left, "QS7,QS8"]) == (1999, 1999)


def test_rework2_a_list_pays_only_after_it_reads_and_is_within_the_cap() -> None:
    spent = ["QS1-QS50"] * 39 + ["QS1-QS49"]  # one member left
    # an unreadable first item reads no group and costs nothing: a later single still fits
    assert _count(CN, [*spent, "4$,QS8", "QS9"]) == (2000, 2000)
    assert _count(CN, ["QS1-QS50"] * 40 + ["4$,QS8"]) == (2000, 2000)
    # an over-cap list is flagged, expands nothing, and costs nothing
    over = "X7:1" + ",1" * 97
    assert _count(IEC, [over] * 100 + ["X7:1" + ",1" * 49] * 40) == (2000, 2000)
    assert _count(IEC, ["X7:1" + ",1" * 49] * 40 + [over]) == (2000, 2000)
    found = IEC._parse(over, None, [0])  # even with no budget left it is the flagged group
    assert (
        found is not None
        and found.group is not None
        and found.issues == ("group.too_many_members",)
    )
    # an over-cap range likewise
    assert _count(CN, ["QS1-QS50"] * 40 + ["QS1-QS9999"]) == (2000, 2000)
    refused = CN._parse("QS1-QS9999", None, [0])
    assert refused is not None and refused.issues == ("group.too_many_members",)


def test_rework2_a_refused_group_is_not_a_terminal_or_a_code_under_any_convention() -> None:
    for name, parser in _PARSERS.items():
        for text in ("X23-X37", "1X-64X", "QS7-QS9"):
            free = parser._parse(text, None, None)
            if free is not None and free.kind is _GROUP:
                assert parser._parse(text, None, [0]) is None, (name, text)
            refused = parser._parse(text, None, [1])  # a budget too small for the range
            if free is not None and free.kind is _GROUP and free.group and free.group.members:
                assert refused is None, (name, text)
