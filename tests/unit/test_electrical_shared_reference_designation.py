"""FR-ID-01 / INC1-AC-4: IEC 81346 and GB/T 5094 reference designations with aspects."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import RuleSelection
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import PARSER, aspects, fw, parse

_KIND = IdentifierKind.REFERENCE_DESIGNATION


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-K5", ["-K5"]),
        ("=A1-K5", ["=A1", "-K5"]),
        ("=A1+P2-K5", ["=A1", "+P2", "-K5"]),
        ("==PLANT=A1++SITE+P2-K5", ["==PLANT", "=A1", "++SITE", "+P2", "-K5"]),
        ("=A1=B2", ["=A1", "=B2"]),  # hierarchy
        ("-W1", ["-W1"]),  # a cable
        ("=A1-W12", ["=A1", "-W12"]),
        ("&AREA#7-Q1", ["&AREA", "#7", "-Q1"]),  # configured extra aspects
    ],
)
def test_the_aspect_chain_is_split_in_order(text: str, expected: list[str]) -> None:
    found = parse(text)
    assert found.kind is _KIND
    assert found.valid and not found.issues
    assert aspects(found) == expected
    assert found.convention is Convention.IEC_81346


@pytest.mark.parametrize(
    ("text", "point", "role"),
    [("-K5:A1", "A1", "coil"), ("-K5:13", "13", "aux_no"), ("-Q1:2", "2", "main_pole")],
)
def test_the_connection_point_and_its_pole_role(text: str, point: str, role: str) -> None:
    found = parse(text)
    assert found.kind is _KIND
    assert (found.connection_point, found.pole_role) == (point, role)


def test_the_product_alone_is_a_search_key_with_and_without_the_point() -> None:
    keys = parse("=A1+P2-K5:A1").forms.search_keys
    assert "K5" in keys and "K5:A1" in keys


def test_gbt_5094_is_chosen_when_only_it_is_enabled() -> None:
    rules = load_rules().narrowed(
        RuleSelection(frozenset({Convention.GBT_5094}), frozenset(), (), (), None, ())
    )
    found = IdentifierParser(rules).parse("=A1-K5")
    assert found is not None and found.convention is Convention.GBT_5094


def test_a_chinese_code_in_the_product_suggests_a_device_class() -> None:
    assert parse("-KM1").device_class == "contactor"
    assert parse("-K5").device_class is None


@pytest.mark.parametrize(
    ("text", "issue"),
    [
        ("-K5:", "connection_point.empty"),
        ("-K5:A1:B2", "connection_point.multiple"),
        ("-K5:TOOLONG1", "connection_point.malformed"),
        ("=A1-", "aspect.malformed"),
        ("--K5", "aspect.malformed"),
    ],
)
def test_a_malformed_designation_is_flagged_not_dropped(text: str, issue: str) -> None:
    found = parse(text)
    assert found.kind is _KIND
    assert found.valid is False
    assert issue in found.issues
    assert found.forms.original == text


def test_full_width_designations_parse_like_their_ascii_form() -> None:
    wide, narrow = parse(fw("=A1+P2-K5")), parse("=A1+P2-K5")
    assert aspects(wide) == aspects(narrow)
    assert wide.forms.canonical == narrow.forms.canonical
    assert wide.forms.original == fw("=A1+P2-K5")


def test_prose_is_not_a_designation() -> None:
    assert PARSER.parse("-hello, world!", hint=_KIND) is None
    assert PARSER.parse("price: $5") is None
