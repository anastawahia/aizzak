"""D-36 in the drawings section (US-3, F2-AC-13..18): the profile's KKS / ANSI fields, the
rules they select, their detection, and their effect on the characterization report.

Every identifier here is an abstract pattern (NFR-F2-7)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import pytest

from app.framework.errors import AppError
from app.modules.electrical.drawings.application.projects import parse_profile
from app.modules.electrical.drawings.domain.characterization import (
    Characterization,
    aggregate,
    characterize,
)
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.errors import InvalidProfile
from app.modules.electrical.drawings.domain.pages import ClassificationThresholds
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import (
    _F2_DEFAULTS,
    _TOP_KEYS,
    MAX_ANSI_SUFFIXES,
    DeviceFunctionNumbers,
    DrawingProfile,
    PlantDesignation,
    SymbolStandard,
)
from app.modules.electrical.drawings.domain.profile_detection import (
    _SYMBOL_OF,
    ProfileProposal,
    detect_profile,
    is_mixed,
    ranked_device_conventions,
)
from app.modules.electrical.shared.kinds import (
    ADDITIVE_CONVENTIONS,
    DEVICE_CONVENTIONS,
    Convention,
    ConventionTally,
    IdentifierKind,
)
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules
from app.modules.electrical.shared.version import SHARED_VERSION
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical import make_signals, make_summary
from tests.unit.test_electrical_profile_detection import BASE, pages

RULES = load_rules()
PARSER = IdentifierParser(RULES)
KNOWN = frozenset(RULES.wire_number_patterns)


def body(**fields: object) -> dict[str, object]:
    return {"schema": 1, **fields}


def read(**fields: object) -> DrawingProfile:
    return DrawingProfile.from_body(body(**fields), known_patterns=KNOWN)


def iec(n: int) -> list[str]:
    return [f"-K{i}" for i in range(1, n + 1)]


def kks_tags(n: int) -> list[str]:
    return [f"20XYZ10AB{i:03d}" for i in range(1, n + 1)]


def ansi_numbers(n: int) -> list[str]:
    return [f"{50 + i % 40}{'TNBG'[i % 4]}" for i in range(n)]


def propose(tokens: Sequence[str]) -> ProfileProposal:
    return detect_profile(
        base=BASE,
        summary=make_summary(),
        pages=pages(),
        tally=PARSER.tally(tokens),
        producers=(),
    )


# --- the fields, and what they select --------------------------------------------------
def test_the_defaults_leave_the_body_and_its_fingerprint_unchanged() -> None:
    profile = DrawingProfile.default()
    assert (profile.plant_designation, profile.device_function_numbers) == (
        PlantDesignation.NONE,
        DeviceFunctionNumbers.NONE,
    )
    assert profile.ansi_suffixes == ()
    assert not set(_F2_DEFAULTS) & set(profile.to_body())
    # the fingerprint of the default profile as it was on master (F2-AC-16, byte for byte)
    canonical = json.dumps(profile.to_body(), sort_keys=True, separators=(",", ":"))
    assert profile.fingerprint() == hashlib.sha256(canonical.encode()).hexdigest()
    assert profile.fingerprint() == _MASTER_DEFAULT_FINGERPRINT
    assert DrawingProfile.from_body(profile.to_body(), known_patterns=KNOWN) == profile


# the sha256 of the default profile's canonical JSON, computed on master (de82b80)
_MASTER_DEFAULT_FINGERPRINT = "146d8c74cb6bc6c97abcfa4c295201ae12f31ce5922de07b118180bae543c3d3"


def test_non_default_fields_are_written_and_read_back() -> None:
    profile = read(
        plant_designation="kks", device_function_numbers="ansi", ansi_suffixes=["qz", "AB", "qz"]
    )
    assert profile.plant_designation is PlantDesignation.KKS
    assert profile.device_function_numbers is DeviceFunctionNumbers.ANSI
    assert profile.ansi_suffixes == ("AB", "QZ")  # canonical, sorted, unique
    written = profile.to_body()
    assert written["plant_designation"] == "kks"
    assert written["device_function_numbers"] == "ansi"
    assert written["ansi_suffixes"] == ["AB", "QZ"]
    assert DrawingProfile.from_body(written, known_patterns=KNOWN) == profile
    # suffixes alone are kept even while ANSI is off: read only when it is enabled
    only = read(ansi_suffixes=["QZ"])
    assert only.to_body()["ansi_suffixes"] == ["QZ"] and "plant_designation" not in only.to_body()


def test_f2_ac_13_one_profile_reads_iec_kks_and_ansi_together() -> None:
    profile = read(plant_designation="kks", device_function_numbers="ansi", ansi_suffixes=["QZ"])
    selection = profile.rule_selection()
    assert selection.conventions == {Convention.IEC_81346, Convention.KKS, Convention.ANSI}
    assert selection.extra_ansi_suffixes == ("QZ",)
    parser = IdentifierParser(RULES.narrowed(selection))
    kinds = {
        text: parser.parse(text) for text in ("-K5", "20XYZ10AB123", "87T", "87QZ", "KM1", "52")
    }
    assert kinds["-K5"] is not None and kinds["-K5"].kind is IdentifierKind.REFERENCE_DESIGNATION
    assert kinds["20XYZ10AB123"] is not None
    assert kinds["20XYZ10AB123"].kind is IdentifierKind.KKS_TAG
    assert kinds["87T"] is not None and kinds["87T"].kind is IdentifierKind.ANSI_DEVICE
    assert kinds["87QZ"] is not None and kinds["87QZ"].valid
    assert kinds["KM1"] is None  # the device convention is still the ONE chosen
    assert kinds["52"] is not None and kinds["52"].kind is IdentifierKind.WIRE_NUMBER
    # the default profile reads neither
    plain = IdentifierParser(RULES.narrowed(DrawingProfile.default().rule_selection()))
    assert plain.parse("20XYZ10AB123") is None and plain.parse("87T") is None
    assert DrawingProfile.default().rule_selection().conventions == {Convention.IEC_81346}


@pytest.mark.parametrize("value", ["kks", "ansi", "nope", 3, None, ["iec_81346"]])
def test_identifier_convention_takes_device_conventions_only(value: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        read(identifier_convention=value)
    assert caught.value.path == "identifier_convention"
    assert "iec_81346, gbt_5094, cn_letter_codes, nfpa_jic" in str(caught.value)
    assert "kks" not in str(caught.value).split(":")[-1]


@pytest.mark.parametrize("field", ["plant_designation", "device_function_numbers"])
def test_an_unknown_system_value_is_refused_without_echo(field: str) -> None:
    with pytest.raises(InvalidProfile) as caught:
        read(**{field: "secret-value-123"})
    assert caught.value.path == field and "secret-value-123" not in str(caught.value)


@pytest.mark.parametrize(
    "suffixes",
    [
        [".*"],
        ["("],
        ["ABCD"],
        [""],
        ["A\u200bB"],
        ["a" * 65],
        [1],
        "QZ",
        ["A1"],
        ["A"] * 65,
    ],
)
def test_bad_ansi_suffixes_are_refused_and_never_echoed(suffixes: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        read(ansi_suffixes=suffixes)
    assert caught.value.path.startswith("ansi_suffixes")
    assert ".*" not in str(caught.value)


def test_at_most_64_ansi_suffixes() -> None:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    many = [a + b for a in letters[:7] for b in letters[:10]][: MAX_ANSI_SUFFIXES + 1]
    assert len(set(many)) == MAX_ANSI_SUFFIXES + 1
    assert len(read(ansi_suffixes=many[:MAX_ANSI_SUFFIXES]).ansi_suffixes) == MAX_ANSI_SUFFIXES
    with pytest.raises(InvalidProfile):
        read(ansi_suffixes=many)


# --- detection --------------------------------------------------------------------------
def test_f2_ac_14_kks_and_ansi_are_proposed_and_not_mixed() -> None:
    tokens = iec(30) + kks_tags(25) + ansi_numbers(22)
    tally = PARSER.tally(tokens)
    assert tally.conventions[Convention.KKS] == 25 and tally.conventions[Convention.ANSI] == 22
    assert tally.conventions[Convention.IEC_81346] == 30
    assert tally.identifiers == 30  # the probe adds no identifiers to the base count
    found = propose(tokens)
    assert found.profile.plant_designation is PlantDesignation.KKS
    assert found.profile.device_function_numbers is DeviceFunctionNumbers.ANSI
    assert found.profile.identifier_convention is Convention.IEC_81346
    assert not found.mixed_standards
    assert found.basis["systems"] == ("kks:25", "ansi:22")
    assert found.basis["tokens"] == ("iec_81346:30",)
    assert found.field_confidence["plant_designation"] == pytest.approx(0.25)
    assert found.field_confidence["device_function_numbers"] == pytest.approx(0.22)
    assert found.profile.symbol_standard is SymbolStandard.IEC_60617


def test_f2_ac_14_iec_and_chinese_stay_mixed() -> None:
    assert is_mixed({Convention.IEC_81346: 60, Convention.CN_LETTER_CODES: 30}, 90)
    # KKS beside a device convention is not mixed, however many there are
    assert not is_mixed({Convention.IEC_81346: 60, Convention.KKS: 60, Convention.ANSI: 60}, 60)
    assert ranked_device_conventions({Convention.KKS: 9, Convention.NFPA_JIC: 1}) == [
        (Convention.NFPA_JIC, 1)
    ]


def test_f2_ac_15_under_twenty_tags_kks_has_zero_confidence() -> None:
    found = propose(iec(100) + kks_tags(19))
    assert found.profile.plant_designation is PlantDesignation.KKS
    assert found.field_confidence["plant_designation"] == 0.0
    assert found.confidence == 0.0  # so it is never accepted automatically
    assert found.field_confidence["device_function_numbers"] == 1.0  # none seen: no change


def test_without_kks_or_ansi_the_proposal_is_what_it_was() -> None:
    found = propose(iec(120))
    assert found.profile.plant_designation is PlantDesignation.NONE
    assert found.profile.device_function_numbers is DeviceFunctionNumbers.NONE
    assert found.field_confidence["plant_designation"] == 1.0
    assert found.field_confidence["device_function_numbers"] == 1.0
    assert "systems" not in found.basis
    assert found.profile.to_body() == {**DrawingProfile.default().to_body(), "languages": ["en"]}


def test_a_kks_only_document_keeps_the_base_device_convention() -> None:
    found = propose(kks_tags(60))
    assert found.profile.identifier_convention is Convention.IEC_81346  # the base, no evidence
    assert found.field_confidence["identifier_convention"] == 0.0
    assert found.profile.plant_designation is PlantDesignation.KKS


def test_the_symbol_table_has_device_conventions_only() -> None:
    assert set(_SYMBOL_OF) == DEVICE_CONVENTIONS
    assert not set(_SYMBOL_OF) & ADDITIVE_CONVENTIONS
    assert set(_TOP_KEYS) >= {"plant_designation", "device_function_numbers", "ansi_suffixes"}


# --- the characterization --------------------------------------------------------------
def _record(doc: str, tokens: Sequence[str], rules_version: str = "1.1.0") -> Characterization:
    signals = [make_signals(1)]
    classes = [classify_page(s, ClassificationThresholds()) for s in signals]
    tally = PARSER.tally(tokens)
    summary = make_summary(page_count=1)
    proposal = detect_profile(base=BASE, summary=summary, pages=classes, tally=tally, producers=())
    return characterize(
        doc, "c" * 64, summary, classes, signals, tally, proposal, [], rules_version=rules_version
    )


def test_f2_ac_18_the_release_fingerprint_moves_with_the_versions() -> None:
    record = _record("d1", iec(40) + kks_tags(25))
    assert record.shared_version == SHARED_VERSION == "1.3.0"
    assert record.rules_version == RULES.version == "1.1.0"
    assert record.pipeline_version == PIPELINE_VERSION
    # the profile's own fingerprint does not depend on either
    assert DrawingProfile.default().fingerprint() == _MASTER_DEFAULT_FINGERPRINT


def test_the_characterization_counts_kks_and_ansi_and_claims_no_symbol_standard_from_them() -> None:
    both = _record("d1", iec(40) + kks_tags(25) + ansi_numbers(22))
    assert both.conventions[Convention.KKS] == 25 and both.conventions[Convention.ANSI] == 22
    assert both.symbol_standard_guess is SymbolStandard.IEC_60617
    only_kks = _record("d2", kks_tags(40))
    assert only_kks.conventions == {Convention.KKS: 40}
    assert only_kks.symbol_standard_guess is None  # F0-AC-6: no device convention, no claim
    few = _record("d3", iec(40) + kks_tags(5))
    report = aggregate([both, only_kks, few])
    assert report.conventions == {
        Convention.IEC_81346: 2,  # the device convention, as before
        Convention.KKS: 2,  # documents with at least 20 such identifiers
        Convention.ANSI: 1,
    }
    assert aggregate([both]).versions["shared"] == ("1.3.0",)


def test_a_tally_only_counts_kks_and_ansi_it_can_prove() -> None:
    tally = PARSER.tally(["52", "50/51", "87QZ", "=20XYZ10AB123", "20XY10AB123", "87T", "27-1"])
    assert tally.conventions.get(Convention.ANSI) == 2  # 87T and 27-1 only
    assert tally.conventions.get(Convention.KKS) == 1  # the prefixed tag, valid
    assert isinstance(tally, ConventionTally)


class _Rules:
    def identifier_rules(self) -> IdentifierRules:
        return RULES


def test_a_voltage_label_may_not_hide_an_ansi_number_or_a_kks_tag() -> None:
    """projects.py checks labels under EVERY convention, KKS and ANSI included."""
    for label in ("87T", "20XYZ10AB123"):
        with pytest.raises(AppError):
            parse_profile(_Rules(), {"schema": 1, "voltage_labels": [label]})
    assert parse_profile(_Rules(), {"schema": 1, "voltage_labels": ["-48V"]}).voltage_labels


def test_an_old_body_without_the_new_fields_reads_with_their_defaults() -> None:
    old = {"schema": 1, "identifier_convention": "gbt_5094", "languages": ["zh"]}
    profile = DrawingProfile.from_body(old, known_patterns=KNOWN)
    assert profile.plant_designation is PlantDesignation.NONE
    assert profile.device_function_numbers is DeviceFunctionNumbers.NONE
    assert profile.ansi_suffixes == ()
    assert profile.rule_selection().conventions == {Convention.GBT_5094}
