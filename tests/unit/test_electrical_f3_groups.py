"""F3 (FR-ID-19): lists, ranges and suffix lists of identifiers in one text, and how the tally
counts their members. Every example here is invented (F3-AC-28)."""

from __future__ import annotations

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
from app.modules.electrical.shared.parser import IdentifierParser
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
