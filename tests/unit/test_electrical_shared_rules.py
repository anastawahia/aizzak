"""INC1-AC-4: the rules come from a versioned file, shape-checked, and a profile narrows them."""

from __future__ import annotations

import copy
import tomllib
from importlib import resources
from typing import Any

import pytest

from app.modules.electrical.shared.kinds import (
    ADDITIVE_CONVENTIONS,
    DEVICE_CONVENTIONS,
    Convention,
    IdentifierKind,
    PlcFamily,
)
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules, RuleError
from app.modules.electrical.shared.rules.systems import CLASS_NAME, checked_ansi_suffixes
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
    assert (
        rules.version == _raw()["version"] == "1.1.0"
    )  # D-36: the KKS, ANSI, confusables, digits-first tables
    assert SHARED_VERSION == "1.3.0"  # D-36: two new kinds, the new order, still opt-in
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
        "plc_address", "cross_reference", "connection_point", "voltage", "kks_tag",
        "ansi_device",
    }  # fmt: skip


# --- F2 (D-36): the KKS, ANSI, confusables and digits-first tables --------------------


def _mutated(mutate: Any) -> dict[str, Any]:
    data = copy.deepcopy(_raw())
    mutate(data)
    return data


def test_the_default_conventions_are_the_device_ones_and_kks_ansi_are_additive() -> None:
    rules = load_rules()
    assert rules.conventions == DEVICE_CONVENTIONS
    assert not rules.conventions & ADDITIVE_CONVENTIONS
    assert rules.with_additive().conventions == frozenset(Convention)
    assert not DEVICE_CONVENTIONS & ADDITIVE_CONVENTIONS


def test_kks_and_ansi_are_enabled_by_name_and_device_conventions_still_narrow() -> None:
    rules = load_rules().narrowed(
        _selection(conventions=frozenset({Convention.GBT_5094, Convention.KKS}))
    )
    assert rules.conventions == {Convention.GBT_5094, Convention.KKS}
    only_device = load_rules().narrowed(_selection(conventions=frozenset({Convention.NFPA_JIC})))
    assert only_device.conventions == {Convention.NFPA_JIC}


def test_f2_ac_12_every_ansi_class_is_a_class_name() -> None:
    rules = load_rules()
    assert rules.ansi.functions and all(
        CLASS_NAME.fullmatch(v) for v in rules.ansi.functions.values()
    )
    assert all(1 <= n <= 99 for n in rules.ansi.functions)
    assert CLASS_NAME.fullmatch("Not A Class") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("kks"),
        lambda d: d.pop("ansi"),
        lambda d: d.pop("confusables"),
        lambda d: d["kks"].update(catalogue={"AB": "a long description of a key"}),
        lambda d: d["ansi"].update(descriptions={"87": "text"}),
        lambda d: d["kks"]["parts"]["system"].update(description="x"),
        lambda d: d["kks"]["parts"]["system"].update(mask="NN(A)"),
        lambda d: d["kks"]["parts"]["system"].update(mask="N" * 9),
        lambda d: d["kks"]["parts"]["system"].update(mask="nNA"),
        lambda d: d["kks"]["parts"]["system"].update(level=4),
        lambda d: d["kks"]["forms"][0].update(parts=["system", "nope"]),
        lambda d: d["kks"]["forms"][0].update(parts=["system", "system"]),
        lambda d: d["kks"]["forms"][0].update(**{"class": "thing"}),
        lambda d: d["kks"]["forms"][0].update(extra="x"),
        lambda d: d["kks"]["forms"][1].update(name=d["kks"]["forms"][0]["name"]),
        lambda d: d["kks"]["prefixes"].update(process="*"),
        lambda d: d["kks"]["prefixes"].update(process="==="),
        lambda d: d["kks"]["prefixes"].update(installation="="),
        lambda d: d["kks"].update(excluded_letters=["ab"]),
        lambda d: d["ansi"].update(suffixes=["T", "T"]),
        lambda d: d["ansi"].update(suffixes=["T4"]),
        lambda d: d["ansi"].update(suffixes=["ABCD"]),
        lambda d: d["ansi"].update(max_compound=1),
        lambda d: d["ansi"].update(max_compound=4),
        lambda d: d["ansi"]["functions"].update({"0": "x_class"}),
        lambda d: d["ansi"]["functions"].update({"100": "x_class"}),
        lambda d: d["ansi"]["functions"].update({"52": "Circuit Breaker"}),
        lambda d: d["confusables"].update(pairs=["O0"]),
        lambda d: d["confusables"].update(pairs=["0"]),
    ],
)
def test_the_kks_and_ansi_tables_refuse_unknown_keys(mutate: Any) -> None:
    """F2-AC-12: no catalogue and no description can enter the rules file."""
    with pytest.raises(RuleError):
        IdentifierRules.from_mapping(_mutated(mutate))


def test_f2_ac_43_digits_first_conventions_are_data() -> None:
    parser = IdentifierParser(
        load_rules().narrowed(_selection(conventions=frozenset({Convention.IEC_81346})))
    )
    assert load_rules().terminal_digits_first["X"][0] is Convention.CN_LETTER_CODES

    def drop(d: dict[str, Any]) -> None:
        del d["terminals"]["digits_first"]

    bare = IdentifierRules.from_mapping(_mutated(drop))
    assert bare.terminal_digits_first == {}
    assert parser.rules.terminal_digits_first["X"]
    # changing the table alone changes the reading, with no code change
    only_iec = IdentifierRules.from_mapping(
        _mutated(lambda d: d["terminals"]["digits_first"].update(X=["iec_81346"]))
    )
    read = IdentifierParser(only_iec).parse("1X-25")
    assert read is not None and read.convention is Convention.IEC_81346
    for mutate in (
        lambda d: d["terminals"]["digits_first"].update(ZZ=["iec_81346"]),
        lambda d: d["terminals"]["digits_first"].update(X=["nope"]),
        lambda d: d["terminals"]["digits_first"].update(X=[]),
        lambda d: d["terminals"]["digits_first"].update(X=["kks"]),
        lambda d: d["terminals"]["digits_first"].update(X=["iec_81346", "iec_81346"]),
    ):
        with pytest.raises(RuleError):
            IdentifierRules.from_mapping(_mutated(mutate))


def test_extra_ansi_suffixes_are_checked_normalised_and_never_a_pattern() -> None:
    assert checked_ansi_suffixes(("qz", "QZ", "k")) == ("K", "QZ")
    for bad in ((".*",), ("(",), ("ABCD",), ("",), ("A\u200bB",), ("A" * 65,)):
        with pytest.raises(RuleError):
            checked_ansi_suffixes(bad)
    with pytest.raises(RuleError):
        checked_ansi_suffixes(tuple("A" for _ in range(65)))
    rules = load_rules().narrowed(_selection(extra_ansi_suffixes=("qz",)))
    assert "QZ" in rules.ansi.suffixes and "QZ" not in load_rules().ansi.suffixes


def test_a_pinned_cross_reference_format_is_remembered() -> None:
    assert not load_rules().cross_reference_pinned
    assert not load_rules().narrowed(_selection()).cross_reference_pinned
    pinned = load_rules().narrowed(_selection(cross_reference_format="page_slash_column"))
    assert pinned.cross_reference_pinned


def test_nfr_f2_2_a_new_kks_form_and_a_new_suffix_need_no_code() -> None:
    def extend(data: dict[str, Any]) -> None:
        data["kks"]["forms"].append(
            {"name": "system_component", "parts": ["system", "component"], "class": "component"}
        )
        data["ansi"]["suffixes"].append("QQ")

    rules = IdentifierRules.from_mapping(_mutated(extend)).narrowed(
        _selection(conventions=frozenset({Convention.IEC_81346, Convention.KKS, Convention.ANSI}))
    )
    parser = IdentifierParser(rules)
    tag = parser.parse("20XYZ10AB12")
    assert tag is not None and tag.kind is IdentifierKind.KKS_TAG and tag.valid
    assert tag.kks is not None and tag.kks.form == "system_component"
    device = parser.parse("87QQ")
    assert device is not None and device.kind is IdentifierKind.ANSI_DEVICE and device.valid
    stock = IdentifierParser(
        load_rules().narrowed(
            _selection(conventions=frozenset({Convention.IEC_81346, Convention.KKS}))
        )
    )
    stale = stock.parse("20XYZ10AB12")
    assert (
        stale is not None and not stale.valid
    )  # malformed in the packaged file  # the packaged file has no such form


def test_the_evidence_rules_are_data() -> None:
    """NFR-F2-2, D-36: what counts as evidence is the rules file's, not code."""
    stock = IdentifierParser(
        load_rules().narrowed(_selection(conventions=frozenset({Convention.IEC_81346})))
    )
    assert stock.tally(["32A", "20XYZ10"]).conventions.get(Convention.ANSI) is None
    assert stock.tally(["32A", "20XYZ10"]).conventions.get(Convention.KKS) is None

    def no_rating_letters(d: dict[str, Any]) -> None:
        d["ansi"]["non_evidence_suffixes"] = []

    def any_level(d: dict[str, Any]) -> None:
        d["kks"]["evidence_min_level"] = 1

    rated = IdentifierParser(IdentifierRules.from_mapping(_mutated(no_rating_letters)))
    assert rated.tally(["32A"]).conventions.get(Convention.ANSI) == 1
    shallow = IdentifierParser(IdentifierRules.from_mapping(_mutated(any_level)))
    assert shallow.tally(["20XYZ10"]).conventions.get(Convention.KKS) == 1

    for mutate in (
        lambda d: d["kks"].pop("evidence_min_level"),
        lambda d: d["ansi"].pop("non_evidence_suffixes"),
        lambda d: d["kks"].update(evidence_min_level=0),
        lambda d: d["kks"].update(evidence_min_level=4),
        lambda d: d["kks"].update(evidence_min_level=True),
        lambda d: d["kks"].update(evidence_min_level="2"),
        lambda d: d["ansi"].update(non_evidence_suffixes=["ZZ"]),
        lambda d: d["ansi"].update(non_evidence_suffixes=["A", "A"]),
        lambda d: d["ansi"].update(non_evidence_suffixes="A"),
    ):
        with pytest.raises(RuleError):
            IdentifierRules.from_mapping(_mutated(mutate))


def test_s1_optional_positions_are_capped() -> None:
    """S-1: a mask or a form may not multiply the patterns the parser builds."""
    with pytest.raises(RuleError, match="optional"):
        IdentifierRules.from_mapping(
            _mutated(lambda d: d["kks"]["parts"]["system"].update(mask="NNnnn"))
        )

    def three_parts(d: dict[str, Any]) -> None:
        parts = d["kks"]["parts"]
        parts["pa"], parts["pb"], parts["pc"] = (
            {"mask": "Nnn"},
            {"mask": "Ann"},
            {"mask": "Nnn"},
        )
        d["kks"]["forms"].append({"name": "wide", "parts": ["pa", "pb", "pc"], "class": "system"})

    with pytest.raises(RuleError, match="optional"):
        IdentifierRules.from_mapping(_mutated(three_parts))

    def at_the_limit(d: dict[str, Any]) -> None:
        parts = d["kks"]["parts"]
        parts["pa"], parts["pb"] = {"mask": "Nnn"}, {"mask": "Ann"}
        d["kks"]["forms"].append({"name": "wide", "parts": ["pa", "pb"], "class": "system"})

    assert IdentifierRules.from_mapping(_mutated(at_the_limit)).kks.forms[-1].name == "wide"
    assert load_rules().kks.forms  # the packaged rules are inside the limits
