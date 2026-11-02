"""INC1-AC-4: the rules come from a versioned file, shape-checked, and a profile narrows them."""

from __future__ import annotations

import copy
import tomllib
from importlib import resources
from typing import Any

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, PlcFamily
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules, RuleError
from app.modules.electrical.shared.version import SHARED_VERSION
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical_shared import _selection


def _raw() -> dict[str, Any]:
    text = (
        resources.files("app.modules.electrical.shared.rules")
        .joinpath("identifiers.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def test_the_rules_file_is_packaged_and_versioned() -> None:
    rules = load_rules()
    assert rules.version == _raw()["version"] == "1.0.0"  # the rules DATA did not change
    assert SHARED_VERSION == "1.1.2"  # the normalisation output did (PR-B rework 3)
    assert rules.aspects == ("=", "+", "-")
    assert set(rules.extra_aspects) == {"==", "++", "&", "#"}
    assert rules.cn_letter_codes["KM"] == "contactor"
    assert rules.nfpa_codes["CR"] == "control_relay"
    assert rules.connection_point_roles["A1"] == "coil"
    assert rules.connection_point_roles["95"] == "overload_nc"
    assert "+24V" in rules.voltage_labels


def test_the_rules_object_is_immutable_data() -> None:
    rules = load_rules()
    mapping: Any = rules.cn_letter_codes
    with pytest.raises(TypeError):
        mapping["ZZ"] = "x"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("version"),
        lambda d: d.pop("wire_numbers"),
        lambda d: d["wire_numbers"].update(bad="(unclosed"),
        lambda d: d["cross_references"].update(bad="[z-a]"),
        lambda d: d["reference_designation"].update(aspects=["x"]),
        lambda d: d["reference_designation"].update(extra_aspects=["="]),
        lambda d: d["reference_designation"].update(connection_point_separator="::"),
        lambda d: d["cn_letter_codes"].update(km="contactor"),
        lambda d: d["terminals"].update(prefixes="X"),
        lambda d: d["voltages"].pop("labels"),
    ],
)
def test_a_malformed_rules_mapping_is_a_rule_error(mutate: Any) -> None:
    data = copy.deepcopy(_raw())
    mutate(data)
    with pytest.raises(RuleError):
        IdentifierRules.from_mapping(data)


def test_a_profile_narrows_conventions_by_name() -> None:
    rules = load_rules().narrowed(_selection(conventions=frozenset({Convention.NFPA_JIC})))
    parser = IdentifierParser(rules)
    assert parser.parse("CR1") is not None
    assert parser.parse("KM1") is None  # Chinese letter codes are off
    assert parser.parse("-K5") is None  # so is IEC


def test_a_profile_narrows_plc_families() -> None:
    parser = IdentifierParser(
        load_rules().narrowed(_selection(plc_families=frozenset({PlcFamily.S7_EN})))
    )
    assert parser.parse("I0.0") is not None
    assert parser.parse("E0.0") is None
    assert parser.parse("%I0.0") is None
    assert parser.parse("DB10.DBX2.3") is None


def test_a_profile_selects_wire_patterns_and_the_cross_reference_format_by_name() -> None:
    rules = load_rules().narrowed(
        _selection(
            wire_number_patterns=("voltage_based",), cross_reference_format="page_slash_column"
        )
    )
    parser = IdentifierParser(rules)
    assert list(rules.wire_number_patterns) == ["voltage_based"]
    assert parser.parse("U11") is not None
    assert parser.parse("123") is None  # sequential is not selected
    assert parser.parse("12/3") is not None
    assert parser.parse("/12.3") is None  # other formats are off


def test_a_profile_can_add_voltage_labels_and_pick_extra_aspects() -> None:
    rules = load_rules().narrowed(_selection(extra_voltage_labels=("-48v",), extra_aspects=("==",)))
    parser = IdentifierParser(rules)
    assert "-48V" in rules.voltage_labels
    assert parser.parse("-48V") is not None
    assert parser.parse("==PLANT=A1-K5") is not None
    assert parser.parse("&X1-K5") is None  # "&" is no longer an aspect prefix


def test_an_unknown_name_in_a_profile_is_a_rule_error_never_a_regex() -> None:
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(wire_number_patterns=("(?:evil)+",)))
    with pytest.raises(RuleError):
        load_rules().narrowed(_selection(cross_reference_format="nope"))


def test_an_empty_selection_changes_nothing() -> None:
    rules = load_rules()
    assert rules.narrowed(_selection()) == rules


def test_the_hint_kind_values_are_stable() -> None:
    assert {k.value for k in IdentifierKind} == {
        "reference_designation", "letter_code", "terminal", "bridge", "wire_number",
        "plc_address", "cross_reference", "connection_point", "voltage",
    }  # fmt: skip
