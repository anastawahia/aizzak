"""FR-ID-15 / US-1 (F2-AC-1..6, NFR-F2-1): KKS plant designations by structure only.

Every tag here is invented from the abstract pattern ``99AAA99AA999`` (NFR-F2-7)."""

from __future__ import annotations

import copy
import tomllib
from importlib import resources
from typing import Any

import pytest

from app.modules.electrical.shared.kinds import (
    Convention,
    IdentifierKind,
    KksClass,
    ParsedIdentifier,
)
from app.modules.electrical.shared.kks import (
    ISSUE_DIGIT_IN_LETTER,
    ISSUE_LETTER_IN_DIGIT,
    ISSUE_MALFORMED,
)
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules
from tests.eval.electrical.m04_identifiers import load_cases, load_rules, observed, parse_case
from tests.unit.support_electrical_shared import _selection, fw, parser_for

KKS = parser_for(Convention.IEC_81346, Convention.KKS)


def _kks(text: str, parser: IdentifierParser = KKS) -> ParsedIdentifier:
    found = parser.parse(text)
    assert found is not None, f"{text!r} did not parse"
    return found


def test_f2_ac_1_an_equipment_tag_has_its_levels_and_parts() -> None:
    found = _kks("20XYZ10AB123")
    assert found.kind is IdentifierKind.KKS_TAG and found.convention is Convention.KKS
    assert found.valid and not found.issues and found.device_class is None
    tag = found.kks
    assert tag is not None and tag.kks_class is KksClass.EQUIPMENT and tag.level == 2
    assert tag.prefix is None and tag.form == "equipment"
    system, equipment = tag.parts
    assert (system.name, system.level, system.text) == ("system", 1, "20XYZ10")
    assert system.runs == ("20", "XYZ", "10")
    assert (equipment.name, equipment.level, equipment.text) == ("equipment", 2, "AB123")
    assert equipment.runs == ("AB", "123")
    assert found.forms.canonical == "20XYZ10AB123"
    assert found.forms.search_keys == ("20XYZ10AB123",)


@pytest.mark.parametrize(
    ("text", "form", "kks_class", "level"),
    [
        ("20XYZ10", "system", KksClass.SYSTEM, 1),
        ("20XYZ10AB123", "equipment", KksClass.EQUIPMENT, 2),
        ("20XYZ10AB123C", "equipment", KksClass.EQUIPMENT, 2),
        ("20XYZ10AB123CD45", "component", KksClass.COMPONENT, 3),
        ("20XYZ10AB1234567", "equipment_cable", KksClass.CABLE, 2),
        ("20XYZ104567", "system_cable", KksClass.CABLE, 1),
    ],
)
def test_f2_ac_2_each_packaged_form_has_its_class(
    text: str, form: str, kks_class: KksClass, level: int
) -> None:
    tag = _kks(text).kks
    assert tag is not None and tag.form == form and tag.kks_class is kks_class
    assert tag.level == level
    assert "".join(p.text for p in tag.parts) == text
    assert all(p.level is None for p in tag.parts if p.name == "cable")


def test_f2_ac_3_spaces_case_and_full_width_meet_in_one_canonical_form() -> None:
    plain = _kks("20XYZ10AB123")
    for text in ("20 xyz10 ab123", " 20xyz10AB123 ", fw("20XYZ10AB123")):
        found = _kks(text)
        assert found.forms.canonical == plain.forms.canonical == "20XYZ10AB123"
        assert found.forms.original == text  # the original is kept (DATA-06)
        assert found.kks == plain.kks and found.valid


@pytest.mark.parametrize("text", ["20XY10AB123", "20XYZ10AB12"])
def test_f2_ac_4_a_near_miss_is_flagged_malformed_never_dropped(text: str) -> None:
    found = _kks(text)
    assert found.kind is IdentifierKind.KKS_TAG and not found.valid
    assert found.issues == (ISSUE_MALFORMED,)
    assert found.forms.canonical == text and found.kks is None


def test_f2_ac_5_a_letter_o_in_a_digit_position_is_named_not_changed() -> None:
    found = _kks("2OXYZ1OAB12O")
    assert not found.valid and found.issues == (ISSUE_LETTER_IN_DIGIT,)
    assert found.forms.canonical == "2OXYZ1OAB12O"  # the text is never corrected here
    assert _kks("20XYZ1OAB123").issues == (ISSUE_LETTER_IN_DIGIT,)


def test_a_zero_in_a_letter_position_is_named_too() -> None:
    found = _kks("20XYZ10A0123")
    assert not found.valid and found.issues == (ISSUE_DIGIT_IN_LETTER,)
    assert found.forms.canonical == "20XYZ10A0123"
    # each reason is its own trial (design 3.5 step 6): a text that needs both swaps at once
    # matches neither trial and stays malformed
    assert _kks("2OXYZ10A0123").issues == (ISSUE_MALFORMED,)


def test_f2_ac_6_a_prefixed_tag_is_kks_only_when_enabled_and_complete() -> None:
    found = _kks("=20XYZ10AB123")
    assert found.kind is IdentifierKind.KKS_TAG and found.valid
    assert found.kks is not None and found.kks.prefix == "=" and found.kks.prefix_kind == "process"
    assert found.alternatives == (IdentifierKind.REFERENCE_DESIGNATION,)
    assert found.forms.search_keys == ("=20XYZ10AB123", "20XYZ10AB123")
    # not enabled: IEC reads it, as before
    plain = parser_for(Convention.IEC_81346).parse("=20XYZ10AB123")
    assert plain is not None and plain.kind is IdentifierKind.REFERENCE_DESIGNATION
    assert plain.alternatives == ()
    # enabled but the structure is not complete: IEC reads it with one aspect, as before
    partial = _kks("=20XYZ10AB12")
    assert partial.kind is IdentifierKind.REFERENCE_DESIGNATION and partial.valid
    # KKS alone: no alternative, nothing else reads it
    alone = parser_for(Convention.KKS).parse("=20XYZ10AB123")
    assert alone is not None and alone.alternatives == ()
    location, installation = _kks("++20XYZ10").kks, _kks("+20XYZ10").kks
    assert location is not None and location.prefix == "++" and location.prefix_kind == "location"
    assert installation is not None and installation.prefix_kind == "installation"


def test_kks_is_off_unless_named() -> None:
    for text in ("20XYZ10", "20XYZ10AB123", "20XY10AB123", "2OXYZ1OAB12O"):
        assert parser_for(Convention.IEC_81346).parse(text) is None
    assert IdentifierParser(load_rules()).parse("20XYZ10AB123") is None


def test_too_short_or_not_tag_shaped_text_is_no_identifier() -> None:
    for text in ("7XY123", "AB123", "20XYZ", "2020", "20-XYZ10", "20XYZ10AB123-1"):
        found = KKS.parse(text)
        assert found is None or found.kind is not IdentifierKind.KKS_TAG, text


def test_excluded_letters_are_data_and_invalidate() -> None:
    data = copy.deepcopy(_raw())
    data["kks"]["excluded_letters"] = ["Q"]
    rules = IdentifierRules.from_mapping(data).narrowed(
        _selection(conventions=frozenset({Convention.IEC_81346, Convention.KKS}))
    )
    parser = IdentifierParser(rules)
    bad = parser.parse("20XQZ10AB123")
    assert bad is not None and bad.kind is IdentifierKind.KKS_TAG and not bad.valid
    assert bad.issues == ("kks.excluded_letter",) and bad.kks is not None
    ok = parser.parse("20XYZ10AB123")
    assert ok is not None and ok.valid


def _raw() -> dict[str, Any]:
    text = (
        resources.files("app.modules.electrical.shared.rules")
        .joinpath("identifiers.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def test_the_keys_keep_the_leading_zeros_so_two_tags_never_meet() -> None:
    a, b = _kks("20XYZ10AB012"), _kks("20XYZ10AB123")
    assert a.forms.search_keys != b.forms.search_keys
    assert "20XYZ10AB012" in a.forms.search_keys


def test_nfr_f2_1_every_f2_case_parses_twice_the_same() -> None:
    cases = [c for c in load_cases() if int(str(c["id"])[1:]) >= 193]
    assert cases
    for case in cases:
        first = IdentifierParser(load_rules())
        second = IdentifierParser(load_rules())
        assert observed(parse_case(first, case)) == observed(parse_case(second, case)), case["id"]
