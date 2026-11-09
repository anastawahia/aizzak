"""QA, F2 rework 1 (design 11): edge checks of the evidence rule, the voltage-label check and
the `ansi_suffixes` folding. Every identifier is an invented abstract pattern (NFR-F2-7)."""

from __future__ import annotations

import pytest

from app.framework.errors import AppError
from app.modules.electrical.drawings.application.projects import parse_profile
from app.modules.electrical.drawings.domain.profile import (
    DeviceFunctionNumbers,
    PlantDesignation,
)
from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from app.modules.electrical.shared.parser import IdentifierParser
from tests.unit.test_electrical_f2_profile import (
    PARSER,
    RULES,
    _Rules,
    ansi_numbers,
    iec,
    kks_tags,
    propose,
)

_ANSI_ON = {"device_function_numbers": "ansi"}
_KKS_ON = {"plant_designation": "kks"}


def _save(**fields: object):
    return parse_profile(_Rules(), {"schema": 1, **fields})


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ("50N", 1),  # the rating letter is the only suffix left out
        ("50T", 1),
        ("50BF", 1),
        ("50A", 0),
        ("32A-1", 0),  # an instance does not rescue a rating letter
        ("27-1", 1),
        ("27-12", 1),
        ("27", 0),  # a bare number proves nothing
        ("50", 0),
        ("16A", 0),  # no class for 16
        ("1N", 0),
        ("=20XYZ10", 0),  # KKS level 1, with a prefix too
        ("=20XYZ10AB123", 1),  # level 2
    ],
)
def test_tally_counts_each_edge_token_as_the_rule_says(text: str, counted: int) -> None:
    conventions = PARSER.tally([text]).conventions
    assert conventions.get(Convention.ANSI, 0) + conventions.get(Convention.KKS, 0) == counted


def test_the_thresholds_are_per_field_and_exact() -> None:
    both = iec(100) + ansi_numbers(19) + kks_tags(20)
    found = propose(both)
    assert found.profile.plant_designation is PlantDesignation.KKS
    assert found.profile.device_function_numbers is DeviceFunctionNumbers.NONE
    assert found.field_confidence["device_function_numbers"] == 1.0
    assert found.basis["systems"] == ("kks:20",)
    flipped = propose(iec(100) + ansi_numbers(20) + kks_tags(19))
    assert flipped.profile.device_function_numbers is DeviceFunctionNumbers.ANSI
    assert flipped.profile.plant_designation is PlantDesignation.NONE
    assert flipped.basis["systems"] == ("ansi:20",)


def test_noise_in_a_mixed_iec_document_changes_neither_count_nor_proposal() -> None:
    noise = ["16A", "24V", "32A", "4P", "1N", "20XYZ10"] * 10
    tokens = iec(60) + ansi_numbers(20) + noise
    assert PARSER.tally(tokens).conventions[Convention.ANSI] == 20
    assert Convention.KKS not in PARSER.tally(tokens).conventions
    assert propose(tokens).basis["systems"] == ("ansi:20",)
    # one evidence token short: the noise never makes up the difference
    short = iec(60) + ansi_numbers(19)
    assert propose(short + noise) == propose(short)
    assert propose(short + noise).field_confidence["device_function_numbers"] == 1.0


@pytest.mark.parametrize("label", ["24V", "48V", "12VDC"])
@pytest.mark.parametrize("systems", [{}, _ANSI_ON, _KKS_ON, {**_ANSI_ON, **_KKS_ON}])
def test_supply_labels_are_saved_with_every_system_on_or_off(
    label: str, systems: dict[str, str]
) -> None:
    assert _save(voltage_labels=[label], **systems).voltage_labels == (label,)


@pytest.mark.parametrize(
    ("label", "systems", "refused"),
    [
        ("87T", {}, False),
        ("87T", _KKS_ON, False),
        ("87T", _ANSI_ON, True),
        ("87T", {**_ANSI_ON, **_KKS_ON}, True),
        ("50N", _ANSI_ON, True),
        ("20XYZ10AB123", {}, False),
        ("20XYZ10AB123", _ANSI_ON, False),
        ("20XYZ10AB123", _KKS_ON, True),
        ("20XYZ10AB123", {**_ANSI_ON, **_KKS_ON}, True),
        ("16A", _ANSI_ON, False),  # a rating: no evidence, hides no device
    ],
)
def test_a_label_that_hides_evidence_is_refused_only_where_that_system_is_on(
    label: str, systems: dict[str, str], refused: bool
) -> None:
    if not refused:
        assert _save(voltage_labels=[label], **systems).voltage_labels == (label,)
        return
    with pytest.raises(AppError) as caught:
        _save(voltage_labels=[label], **systems)
    detail = str(caught.value.detail)
    assert detail.startswith("voltage_labels") and label not in detail


def test_a_saved_non_evidence_label_is_read_as_a_voltage_under_every_selection() -> None:
    for systems in ({}, _ANSI_ON, _KKS_ON, {**_ANSI_ON, **_KKS_ON}):
        profile = _save(voltage_labels=["24V", "16A"], **systems)
        parser = IdentifierParser(RULES.narrowed(profile.rule_selection()))
        for label in ("24V", "16A"):
            read = parser.parse(label)
            assert read is not None and read.kind is IdentifierKind.VOLTAGE


@pytest.mark.parametrize("raw", ["Q Z", "Q\tZ", "QZ\n", "\uff31\uff3a", "qz", "Q\u00a0Z"])
def test_every_spelling_of_one_suffix_folds_to_one(raw: str) -> None:
    assert _save(ansi_suffixes=[raw, "QZ"], **_ANSI_ON).ansi_suffixes == ("QZ",)


@pytest.mark.parametrize("raw", ["Q" + " " * 70 + "Z", "Q" * 65, "Q\u200bZ", "Q1", ""])
def test_unfoldable_suffixes_are_refused_and_never_echoed(raw: str) -> None:
    with pytest.raises(AppError) as caught:
        _save(ansi_suffixes=[raw])
    assert str(caught.value.detail) == "ansi_suffixes[0]: must be 1..3 letters A-Z"


def test_the_limit_of_64_counts_the_list_not_the_folded_values() -> None:
    names = [f"A{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(64)]
    assert len(_save(ansi_suffixes=names).ansi_suffixes) == 64
    with pytest.raises(AppError) as caught:
        _save(ansi_suffixes=[*names, "QZ"])
    assert str(caught.value.detail) == "ansi_suffixes: at most 64"
