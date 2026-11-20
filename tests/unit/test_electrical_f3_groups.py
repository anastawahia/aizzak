"""F3 (FR-ID-19): lists, ranges and suffix lists of identifiers in one text, and how the tally
counts their members. Every example here is invented (F3-AC-28)."""

from __future__ import annotations

import itertools
import time

import pytest

from app.modules.electrical.shared.groups import (
    inherit,
    list_items,
    range_members,
    suffix_members,
)
from app.modules.electrical.shared.kinds import (
    Convention,
    GroupForm,
    IdentifierKind,
    ParsedIdentifier,
)
from app.modules.electrical.shared.normalization import folded, normalize
from app.modules.electrical.shared.parser import TALLY_MEMBER_BUDGET, IdentifierParser
from app.modules.electrical.shared.ratings import read_rating
from tests.unit.support_electrical_shared import parser_for
from tests.unit.test_electrical_f2_profile import _record

_GROUP = IdentifierKind.IDENTIFIER_GROUP
_IEC = parser_for(Convention.IEC_81346)
_CN = parser_for(Convention.CN_LETTER_CODES)
_KKS = parser_for(Convention.IEC_81346, Convention.KKS)
_COMMA = chr(0x3001)
_FULL_COMMA = chr(0xFF0C)


def _group(parser: IdentifierParser, text: str) -> ParsedIdentifier:
    found = parser.parse(text)
    assert found is not None and found.kind is _GROUP and found.group is not None, text
    return found


def _members(found: ParsedIdentifier) -> list[str]:
    assert found.group is not None
    return [m.text for m in found.group.members]


def test_f3_ac_11_a_list_of_terminals_inherits_the_head_of_the_first() -> None:
    found = _group(_IEC, "X7:41,42,43")
    assert found.valid and found.convention is Convention.IEC_81346
    assert found.group is not None
    assert (found.group.form, found.group.separator) == (GroupForm.LIST, ",")
    assert _members(found) == ["X7:41", "X7:42", "X7:43"]
    for member in found.group.members:
        assert member.identifier is not None
        assert member.identifier.kind is IdentifierKind.TERMINAL
        assert member.identifier.convention is Convention.IEC_81346
    assert found.forms.canonical == "X7:41,42,43"
    assert {"X7:41", "X7:42", "X7:43"} <= set(found.forms.search_keys)
    assert found.forms.search_keys[0] == "X7:41,42,43"


@pytest.mark.parametrize("separator", [_FULL_COMMA, _COMMA])
def test_f3_ac_11_the_wide_commas_read_the_same_and_keep_the_original(separator: str) -> None:
    text = f"X7:41{separator}42{separator}43"
    found = _group(_IEC, text)
    assert found.forms.canonical == "X7:41,42,43" and found.forms.original == text
    assert _members(found) == ["X7:41", "X7:42", "X7:43"]


def test_f3_ac_11_a_full_item_is_read_alone_and_a_label_that_is_a_wire_still_inherits() -> None:
    found = _group(_IEC, "X7:41,X7:45,46")
    assert _members(found) == ["X7:41", "X7:45", "X7:46"] and found.valid
    # 42 alone reads as a wire number; after a terminal it is the terminal's point
    assert _IEC.parse("42") is not None
    assert [m.identifier.kind for m in _group(_IEC, "X7:41,42").group.members] == [  # type: ignore[union-attr]
        IdentifierKind.TERMINAL
    ] * 2


def test_f3_ac_12_codes_in_a_list_and_a_range() -> None:
    pair = _group(_CN, "QS7,QS8")
    assert pair.group is not None and pair.group.form is GroupForm.LIST
    assert _members(pair) == ["QS7", "QS8"] and pair.valid
    ranged = _group(_CN, "QS7-QS9")
    assert ranged.group is not None and ranged.group.form is GroupForm.RANGE
    assert ranged.group.separator == "-"
    assert _members(ranged) == ["QS7", "QS8", "QS9"] and ranged.valid
    assert [m.identifier.kind for m in ranged.group.members if m.identifier] == [  # type: ignore[union-attr]
        IdentifierKind.LETTER_CODE
    ] * 3
    # a range keeps the width of its first number
    assert _members(_group(_CN, "QS07-QS09")) == ["QS07", "QS08", "QS09"]


def test_f3_ac_12_wires_in_a_range_when_the_profile_names_the_pattern() -> None:
    named = parser_for(Convention.IEC_81346, wire_number_patterns=("digits_letter",))
    found = _group(named, "38Y-43Y")
    assert _members(found) == ["38Y", "39Y", "40Y", "41Y", "42Y", "43Y"] and found.valid
    assert all(
        m.identifier and m.identifier.kind is IdentifierKind.WIRE_NUMBER
        for m in found.group.members  # type: ignore[union-attr]
    )
    # the file does not name it: a range whose members read as nothing, flagged
    unnamed = _group(_IEC, "38Y-43Y")
    assert not unnamed.valid and unnamed.issues == ("group.member_unreadable",)


def test_f3_ac_12_a_range_whose_members_read_as_nothing_is_flagged_not_a_terminal() -> None:
    found = _group(_IEC, "X23-X37")
    assert not found.valid and found.issues == ("group.member_unreadable",)
    assert len(found.group.members) == 15  # type: ignore[union-attr]
    assert all(m.identifier is None for m in found.group.members)  # type: ignore[union-attr]
    assert found.convention is None and found.kind is not IdentifierKind.TERMINAL


def test_f3_ac_13_a_suffix_list_of_kks_tags() -> None:
    found = _group(_KKS, "20XYZ10AB131/2/3")
    assert found.valid and found.convention is Convention.KKS
    assert found.group is not None and found.group.form is GroupForm.SUFFIX_LIST
    assert found.group.separator == "/"
    assert _members(found) == ["20XYZ10AB131", "20XYZ10AB132", "20XYZ10AB133"]
    assert all(
        m.identifier and m.identifier.kind is IdentifierKind.KKS_TAG for m in found.group.members
    )


def test_f3_ac_13_a_backslash_suffix_list_of_codes() -> None:
    found = _group(_CN, "QS7\\8")
    assert found.group is not None and found.group.form is GroupForm.SUFFIX_LIST
    assert found.group.separator == "\\" and _members(found) == ["QS7", "QS8"]


def test_f3_ac_13_a_slash_between_numbers_is_still_a_cross_reference() -> None:
    ansi_off = parser_for(Convention.IEC_81346, Convention.KKS)
    found = ansi_off.parse("12/3")
    assert found is not None and found.kind is IdentifierKind.CROSS_REFERENCE
    for text in ("50/51", "=A1/12.3", "/12.3"):
        found = ansi_off.parse(text)
        assert found is not None and found.kind is not _GROUP, text
    # without KKS the first item is no KKS tag, so no suffix list
    plain = _IEC.parse("20XYZ10AB131/2/3")
    assert plain is None or plain.kind is not _GROUP
    # a tail longer than the trailing digits of the first is no suffix list
    longer = _KKS.parse("20XYZ10AB131/2345")
    assert longer is None or longer.kind is not _GROUP


def test_f3_ac_14_too_many_members_expand_to_nothing() -> None:
    found = _group(_CN, "QS7-QS99")  # 93 members
    assert not found.valid and found.issues == ("group.too_many_members",)
    assert found.group is not None and found.group.members == ()
    assert found.forms.search_keys == ("QS7-QS99",)
    # exactly at the limit is fine, one more is not
    assert len(_group(_CN, "QS1-QS64").group.members) == 64  # type: ignore[union-attr]
    assert _group(_CN, "QS1-QS65").group.members == ()  # type: ignore[union-attr]
    many = "X7:1" + ",1" * 64  # 65 items
    assert _group(_IEC, many).issues == ("group.too_many_members",)
    assert _group(_IEC, "X7:1" + ",1" * 63).valid  # 64 items


def test_f3_ac_14_a_descending_range_is_no_group_and_an_empty_member_is_flagged() -> None:
    assert _CN.parse("QS9-QS7") is None
    assert _CN.parse("QS7-QS7") is None
    found = _group(_IEC, "X7:41,,42")
    assert not found.valid and found.issues == ("group.empty_member",)
    assert _members(found) == ["X7:41", "", "X7:42"]


def test_f3_ac_14_a_member_that_is_invalid_or_unreadable_invalidates_the_group() -> None:
    found = _group(_CN, "QS7,QS8:1.2.3,ZZZ")
    assert not found.valid
    assert "group.member_invalid" in found.issues or "group.member_unreadable" in found.issues
    first_unreadable = _CN.parse("ZZZ,QS7")
    assert first_unreadable is None  # the first item decides: nothing to anchor a group


def test_f3_ac_14_the_work_is_bounded() -> None:
    texts = [
        "QS1-QS" + "9" * 3,
        ",".join(["QS1"] * 60),
        "20XYZ10AB131" + "/1" * 90,
        "X7:41" + ",1" * 90,
        "QS1-" * 40,
    ]
    started = time.perf_counter()
    for _ in range(100):
        for text in texts:
            _CN.parse(text[:200])
            _KKS.parse(text[:200])
            _IEC.parse(text[:200])
    assert time.perf_counter() - started < 20.0


@pytest.mark.parametrize(
    "text", ["X1-14", "L1-5", "27-1", "12.3-1", "-K5-13", "1X-25", "X1:1~X1:2"]
)
def test_f3_ac_15_the_readings_of_today_are_no_group(text: str) -> None:
    for parser in (_IEC, _CN, _KKS, parser_for(Convention.IEC_81346, Convention.ANSI)):
        found = parser.parse(text)
        assert found is None or found.kind is not _GROUP, (text, found)
    assert _IEC.parse("X1-14") is not None and _IEC.parse("X1-14").kind is IdentifierKind.TERMINAL  # type: ignore[union-attr]


def test_f3_ac_16_the_tally_counts_the_members_of_a_group() -> None:
    tally = _IEC.tally(["X7:41,42,43"])
    assert tally.tokens == 1 and tally.identifiers == 3
    assert tally.conventions == {Convention.IEC_81346: 3}


def test_f3_ac_16_the_characterization_carries_the_members() -> None:
    record = _record("doc", ["X7:41,42,43"])
    assert record.conventions[Convention.IEC_81346] == 3


def test_a_group_member_that_is_kks_evidence_counts_toward_the_probe() -> None:
    """The probe (KKS and ANSI seen though not enabled) counts the members too."""
    tally = _IEC.tally(["20XYZ10AB131/2/3"])
    assert tally.conventions.get(Convention.KKS) == 3


def test_group_helpers() -> None:
    assert list_items("A,B") == ("A", "B") and list_items("AB") is None
    assert list_items("A,,B") == ("A", "", "B")
    assert range_members("QS7-QS9", 64) == (("QS7", "QS8", "QS9"), False)
    assert range_members("QS7-QS99", 64) == ((), True)
    for text in ("X1-14", "27-1", "QS9-QS7", "QS7-QS7", "QS7", "-K5-13", "A-B-C", "QS7-RS9"):
        assert range_members(text, 64) is None, text
    assert inherit("X7:41", "41", "42") == "X7:42"
    assert inherit("-X7:41", "41", "9") == "-X7:9"
    assert suffix_members("20XYZ10AB131", ("2", "3")) == (
        "20XYZ10AB131",
        "20XYZ10AB132",
        "20XYZ10AB133",
    )
    assert suffix_members("QS7", ("12",)) is None and suffix_members("QS", ("1",)) is None
    assert suffix_members("QS7", ("a",)) is None and suffix_members("QS7", ("",)) is None


# --- rework 1: ratings in groups, text flags, budget, fast path ----------------------------


def _unread(parser: IdentifierParser, text: str) -> list[bool]:
    found = parser.parse(text)
    assert found is not None and found.group is not None, text
    return [m.identifier is None for m in found.group.members]


@pytest.mark.parametrize(
    ("text", "flags"), [("QS7,37kV", [False, True]), ("37kV,38kV", [True, True])]
)
def test_rework_1_a_rating_in_a_list_reads_as_nothing(text: str, flags: list[bool]) -> None:
    found = _CN.parse(text)
    if flags == [True, True]:
        assert found is None  # the first item decides: a rating anchors nothing
    else:
        assert _unread(_CN, text) == flags
        assert not found.valid  # type: ignore[union-attr]


@pytest.mark.parametrize("text", ["1kV-3kV", "10kA-12kA"])
def test_rework_1_a_range_of_ratings_is_no_group(text: str) -> None:
    for parser in (_CN, _IEC, parser_for(Convention.NFPA_JIC)):
        found = parser.parse(text)
        assert found is None or found.kind is not _GROUP, (text, found)


def test_rework_1_a_rating_takes_no_inherited_head() -> None:
    found = _group(_IEC, "X7:41,37kV")
    assert _members(found) == ["X7:41", "37KV"]
    assert [m.identifier is None for m in found.group.members] == [False, True]  # type: ignore[union-attr]
    assert not found.valid
    other = _group(_IEC, "X7:41,42,37kV")
    assert _members(other)[1] == "X7:42"
    # the unit in another case is a relay's code, not a rating, and still reads
    assert _CN.parse("37KV,QS7") is not None


def test_rework_2_a_rating_followed_by_a_control_character_is_still_a_rating() -> None:
    controls = [chr(c) for c in (*range(0, 9), *range(14, 28), 127)]
    for control in controls:
        assert _CN.parse("37kV" + control) is None, hex(ord(control))
        assert _EVERYTHING_IEC.parse("47Hz" + control) is None, hex(ord(control))


_EVERYTHING_IEC = parser_for(Convention.IEC_81346, Convention.KKS, Convention.ANSI)


def test_rework_2_the_fast_path_agrees_with_the_slow_one() -> None:

    rules = _CN.rules
    alphabet = ["3", "7", "k", "K", "V", "A", ".", "-", " ", "\t", chr(0), chr(127), "x"]

    for size in range(1, 5):
        for chars in itertools.product(alphabet, repeat=size):
            text = "".join(chars)
            if not text.strip():
                continue
            slow = read_rating(folded(text), rules) is not None
            canonical = "".join(c for c in text if c not in " \t\x00\x7f").upper()
            fast = _CN._rated(canonical, text)
            assert fast == slow or canonical in rules.voltage_labels, repr(text)


def test_rework_s1_a_group_carries_the_text_flags_to_its_members() -> None:
    combining = chr(0x0301)
    zwsp = chr(0x200B)
    lookalike = f"X7:4{combining}1,42,43"
    for text in (lookalike, f"20XYZ10AB13{zwsp}1/2/3", f"X7:41,4{combining}2,43"):
        parser = _KKS
        tally = parser.tally([text])
        assert tally.conventions.get(Convention.IEC_81346, 0) == 0, text
        assert tally.conventions.get(Convention.KKS, 0) == 0, text
        found = parser.parse(text)
        assert found is not None and found.group is not None and not found.valid
        assert all(m.identifier is None or not m.identifier.valid for m in found.group.members), (
            text
        )
    ranged = _CN.tally([f"QS{combining}7-QS9"])
    assert ranged.conventions == {}
    probe = parser_for(Convention.IEC_81346).tally([f"20XYZ10AB131/{zwsp}2/3"])
    assert probe.conventions.get(Convention.KKS, 0) == 0
    clean = _KKS.tally(["X7:41,42,43"])
    assert clean.conventions == {Convention.IEC_81346: 3}


def test_rework_s2_a_tally_expands_a_bounded_number_of_members() -> None:

    page = ["QS1-QS64"] * 100
    tally = _CN.tally(page)
    assert tally.conventions[Convention.CN_LETTER_CODES] <= TALLY_MEMBER_BUDGET
    assert tally.tokens == 100
    # a normal page is counted exactly
    normal = ["X7:41,42,43"] * 20 + ["QS7-QS9"] * 20 + ["-K5"] * 100
    counts = parser_for(Convention.IEC_81346, Convention.CN_LETTER_CODES).tally(normal).conventions
    assert counts[Convention.IEC_81346] == 160 and counts[Convention.CN_LETTER_CODES] == 60
    started = time.perf_counter()
    _CN.tally(["10A-73A"] * 200)
    assert time.perf_counter() - started < 1.0


def test_rework_6_a_group_is_never_read_from_a_hint() -> None:
    assert _IEC.parse("X7:41,42,43", hint=_GROUP) is None


def test_rework_5_the_64th_member_keeps_its_key() -> None:
    found = _group(_CN, "QS1-QS64")
    assert len(found.group.members) == 64  # type: ignore[union-attr]
    assert "QS64" in found.forms.search_keys and len(found.forms.search_keys) == 65


# --- rework 2: a refused group is an unread token, and a list pays only when it reads -----


def test_rework_2_s6_a_range_the_budget_refused_never_reads_as_a_terminal() -> None:
    drain = ["X1-X50"] * 41  # 41 x 50 members: past the budget of 2,000
    before = _IEC.tally(drain)
    after = _IEC.tally([*drain, "X23-X37"] * 1)
    assert after.conventions == before.conventions  # X23-X37 is not a terminal X37 on X23
    assert after.identifiers == before.identifiers and after.tokens == before.tokens + 1
    cn = parser_for(Convention.CN_LETTER_CODES)
    drain_cn = ["QS1-QS50"] * 41
    base = cn.tally(drain_cn)
    assert cn.tally([*drain_cn, "1X-64X"]).conventions == base.conventions
    # with the budget intact the same text is flagged as a group, not read as a terminal
    assert _IEC.tally(["X23-X37"]).conventions == {}


def test_rework_2_s6_the_refusal_is_not_a_group_and_not_a_fall_through() -> None:
    forms = normalize("X23-X37")
    assert _IEC._first(forms, None, "X23-X37", budget=[0]) is None
    parsed = _IEC.parse("X23-X37")  # parse itself has no budget: the flagged group
    assert parsed is not None and parsed.kind is _GROUP


def test_rework_2_t1_over_the_cap_lists_cost_no_budget() -> None:
    over = "X7:1" + ",1" * 70  # 71 items: over the cap of 64
    assert _IEC.tally([over] * 40).identifiers == 0
    normal = ["X7:41,42,43"] * 10
    assert _IEC.tally([over] * 40 + normal).conventions == {Convention.IEC_81346: 30}


def test_rework_2_t1_unreadable_lists_do_not_drain_the_budget() -> None:
    unreadable = "ZZZ," + ",".join(["1"] * 63)  # the first item reads as nothing
    normal = ["X7:41,42,43"] * 10
    tally = _IEC.tally([unreadable] * 100 + normal)
    assert tally.conventions == {Convention.IEC_81346: 30}
