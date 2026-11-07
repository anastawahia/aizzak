"""STG-01's profile proposal and the characterization built on it (BT17, INC1-AC-7): IEC,
GB/T, Chinese letter codes and NFPA, mixed standards, a small sample at confidence zero,
producer hints and determinism (FR-ING-12)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

import pytest

from app.modules.electrical.drawings.domain.characterization import (
    UNKNOWN_SYMBOL_STANDARD,
    Characterization,
    aggregate,
    characterize,
    document_language,
)
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.pages import (
    ClassificationThresholds,
    FontSignal,
    PageClassification,
    PageKind,
    PageLanguage,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import (
    CrossReferenceFormat,
    DrawingProfile,
    SymbolStandard,
)
from app.modules.electrical.drawings.domain.profile_detection import (
    SYMBOL_CONFIDENCE_FACTOR,
    ProducerSignature,
    ProfileProposal,
    detect_profile,
    is_mixed,
    matching_producers,
    producer_family,
    ranked_conventions,
)
from app.modules.electrical.shared.kinds import Convention, ConventionTally, PlcFamily
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.version import SHARED_VERSION
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical import make_signals, make_summary

RULES = load_rules()
PARSER = IdentifierParser(RULES)
T = ClassificationThresholds()
BASE = DrawingProfile.default()


def iec(n: int) -> list[str]:
    return [f"-K{i}" for i in range(1, n + 1)]


def chinese(n: int) -> list[str]:
    return [f"KM{i}" for i in range(1, n + 1)]


def nfpa(n: int) -> list[str]:
    return [f"CR{i}" for i in range(1, n + 1)]


def pages(language: PageLanguage = PageLanguage.EN, count: int = 4) -> list[PageClassification]:
    signals = {
        PageLanguage.EN: {"latin_char_count": 400, "cjk_char_count": 0},
        PageLanguage.ZH: {"latin_char_count": 0, "cjk_char_count": 400},
        PageLanguage.MIXED: {"latin_char_count": 200, "cjk_char_count": 200},
        PageLanguage.NONE: {"latin_char_count": 0, "cjk_char_count": 0, "char_count": 0},
    }[language]
    return [classify_page(make_signals(n, **signals), T) for n in range(1, count + 1)]


def propose(
    tokens: Sequence[str],
    *,
    producers: Sequence[ProducerSignature] = (),
    creator: str | None = None,
    language: PageLanguage = PageLanguage.EN,
    base: DrawingProfile = BASE,
) -> ProfileProposal:
    return detect_profile(
        base=base,
        summary=make_summary(creator=creator),
        pages=pages(language),
        tally=PARSER.tally(tokens),
        producers=producers,
    )


# --- the four conventions -----------------------------------------------------------
def test_an_iec_drawing_is_proposed_iec_with_iec_symbols() -> None:
    found = propose(iec(120))
    assert found.profile.identifier_convention is Convention.IEC_81346
    assert found.profile.symbol_standard is SymbolStandard.IEC_60617
    assert found.confidence > 0.7  # sure of the convention, less so of the inferred symbols
    assert found.field_confidence["identifier_convention"] == 1.0
    assert not found.mixed_standards


def test_chinese_letter_codes_propose_gbt_symbols() -> None:
    found = propose(chinese(120), language=PageLanguage.ZH)
    assert found.profile.identifier_convention is Convention.CN_LETTER_CODES
    assert found.profile.symbol_standard is SymbolStandard.GBT_4728
    assert found.profile.languages == frozenset({"zh"})


def test_nfpa_codes_propose_nfpa_symbols() -> None:
    found = propose(nfpa(120))
    assert found.profile.identifier_convention is Convention.NFPA_JIC
    assert found.profile.symbol_standard is SymbolStandard.NFPA_JIC


def test_gbt_5094_designations_propose_gbt_symbols() -> None:
    tally = ConventionTally(
        conventions={Convention.GBT_5094: 120},
        plc_families={},
        cross_reference_formats={},
        identifiers=120,
        tokens=120,
    )
    found = detect_profile(
        base=BASE, summary=make_summary(), pages=pages(), tally=tally, producers=()
    )
    assert found.profile.identifier_convention is Convention.GBT_5094
    assert found.profile.symbol_standard is SymbolStandard.GBT_4728


def test_the_symbol_confidence_is_below_the_convention_by_construction() -> None:
    found = propose(iec(120))
    assert (
        found.field_confidence["symbol_standard"] < found.field_confidence["identifier_convention"]
    )
    assert found.confidence == min(found.field_confidence.values())


# --- the sample size and mixing -----------------------------------------------------
def test_a_small_sample_has_confidence_zero_and_keeps_the_base_convention() -> None:
    found = propose(chinese(19))
    assert found.confidence == 0.0
    assert found.field_confidence["identifier_convention"] == 0.0
    assert found.profile.identifier_convention is Convention.CN_LETTER_CODES  # still the guess


def test_no_identifiers_at_all_keeps_the_base_profile() -> None:
    found = propose([])
    assert found.confidence == 0.0
    assert found.profile.identifier_convention is BASE.identifier_convention
    assert found.profile.plc_families == BASE.plc_families


def test_confidence_grows_with_the_sample() -> None:
    twenty, fifty, hundred = (propose(iec(n)).confidence for n in (20, 50, 100))
    assert 0.0 < twenty < fifty < hundred


def test_a_second_convention_with_a_quarter_of_the_identifiers_is_flagged_mixed() -> None:
    mixed = propose(iec(60) + chinese(40))
    assert mixed.mixed_standards
    assert mixed.profile.identifier_convention is Convention.IEC_81346
    assert mixed.confidence < propose(iec(100)).confidence  # the margin is smaller


def test_mixing_needs_a_quarter_and_twenty_identifiers() -> None:
    assert not propose(iec(100) + chinese(19)).mixed_standards  # under twenty
    assert not propose(iec(200) + chinese(40)).mixed_standards  # under a quarter
    assert propose(iec(60) + chinese(20)).mixed_standards  # 20 of 80 is exactly a quarter
    assert is_mixed({Convention.IEC_81346: 60, Convention.NFPA_JIC: 20}, 80)
    assert not is_mixed({Convention.IEC_81346: 60}, 60)
    assert not is_mixed({}, 0)


def test_a_tie_between_conventions_goes_to_the_earlier_enum_member() -> None:
    found = propose(chinese(60) + iec(60))
    assert found.profile.identifier_convention is Convention.IEC_81346
    assert found.field_confidence["identifier_convention"] == 0.0  # no margin at all
    assert ranked_conventions(
        {Convention.NFPA_JIC: 5, Convention.CN_LETTER_CODES: 5, Convention.IEC_81346: 5}
    ) == [(Convention.IEC_81346, 5), (Convention.CN_LETTER_CODES, 5), (Convention.NFPA_JIC, 5)]


# --- the other fields ---------------------------------------------------------------
def test_the_dominant_cross_reference_format_is_proposed() -> None:
    tokens = [*iec(100), "/12.3", "/13.1", "/14.2", "15/4"]
    found = propose(tokens)
    assert found.profile.cross_reference_format is CrossReferenceFormat.SLASH_PAGE_DOT_COLUMN
    assert found.field_confidence["cross_reference_format"] == pytest.approx(0.5)
    assert propose(iec(100)).profile.cross_reference_format is CrossReferenceFormat.AUTO


def test_plc_families_are_those_that_appeared() -> None:
    found = propose([*iec(100), "I0.0", "Q0.1", "%I0.0"])
    assert found.profile.plc_families == frozenset({PlcFamily.S7_EN, PlcFamily.TIA})


def test_languages_are_those_above_five_percent_of_the_pages() -> None:
    assert propose(iec(100), language=PageLanguage.EN).profile.languages == frozenset({"en"})
    assert propose(iec(100), language=PageLanguage.MIXED).profile.languages == frozenset(
        {"en", "zh"}
    )
    no_text = propose(iec(100), language=PageLanguage.NONE)
    assert no_text.profile.languages == BASE.languages  # no evidence: the base stands


def test_the_fields_that_cannot_be_detected_are_kept() -> None:
    custom = DrawingProfile.from_body(
        {"schema": 1, "voltage_labels": ["-48V"], "auto_accept_confidence": 0.8},
        known_patterns=frozenset(),
    )
    found = propose(chinese(120), base=custom)
    assert found.profile.voltage_labels == ("-48V",)
    assert found.profile.auto_accept_confidence == 0.8
    assert found.profile.classification == custom.classification


def test_a_vendor_library_is_dropped_when_the_standard_stops_being_vendor() -> None:
    vendor = DrawingProfile.from_body(
        {"schema": 1, "symbol_standard": "vendor", "vendor_library": "Acme"},
        known_patterns=frozenset(),
    )
    found = propose(iec(120), base=vendor)
    assert found.profile.symbol_standard is SymbolStandard.IEC_60617
    assert found.profile.vendor_library is None


# --- the producer -------------------------------------------------------------------
EPLAN = ProducerSignature("EPLAN", "eplan", SymbolStandard.IEC_60617, 0.5)
CAD = ProducerSignature("autocad electrical", "autocad_electrical", SymbolStandard.NFPA_JIC, 1.0)
PLAIN = ProducerSignature("cairo", "cairo", None, 0.2)


def test_the_producer_is_matched_case_insensitively_in_creator_or_producer() -> None:
    summary = make_summary(creator="eplan electric P8", producer="Cairo 1.16")
    hits = matching_producers(summary, [PLAIN, EPLAN, CAD])
    assert [h.family for h in hits] == ["eplan", "cairo"]  # strongest first
    assert producer_family(summary, [PLAIN, EPLAN, CAD]) == "eplan"
    assert producer_family(make_summary(), [EPLAN]) is None


def test_a_producer_hint_moves_the_inferred_symbols_but_not_the_convention() -> None:
    plain = propose(chinese(120))
    hinted = propose(chinese(120), producers=[EPLAN], creator="EPLAN Electric P8")
    assert plain.profile.symbol_standard is SymbolStandard.GBT_4728
    assert hinted.profile.symbol_standard is SymbolStandard.GBT_4728  # 1.0 vs 0.5
    strong = propose(
        chinese(120),
        producers=[ProducerSignature("EPLAN", "eplan", SymbolStandard.IEC_60617, 2.0)],
        creator="EPLAN",
    )
    assert strong.profile.symbol_standard is SymbolStandard.IEC_60617
    assert strong.profile.identifier_convention is Convention.CN_LETTER_CODES
    assert strong.field_confidence["symbol_standard"] < plain.field_confidence["symbol_standard"]
    assert "producer:eplan" in strong.basis["symbol"]
    assert strong.basis["producer"] == ("eplan",)


def test_chinese_pages_pull_iec_designations_towards_gbt_symbols() -> None:
    western = propose(iec(120), language=PageLanguage.EN)
    chinese_pages = propose(iec(120), language=PageLanguage.ZH)
    assert western.profile.symbol_standard is SymbolStandard.IEC_60617
    assert chinese_pages.profile.symbol_standard is SymbolStandard.IEC_60617  # 1.0 vs 0.5
    assert "language:zh" in chinese_pages.basis["symbol"]
    assert (
        chinese_pages.field_confidence["symbol_standard"]
        < western.field_confidence["symbol_standard"]
    )


# --- determinism --------------------------------------------------------------------
def test_the_same_inputs_give_the_same_bytes() -> None:
    def run() -> str:
        found = propose(
            iec(60) + chinese(30) + ["/12.3", "I0.0"],
            producers=[EPLAN, PLAIN],
            creator="EPLAN cairo",
            language=PageLanguage.MIXED,
        )
        return json.dumps(
            {
                "profile": found.profile.to_body(),
                "confidence": found.confidence,
                "fields": dict(found.field_confidence),
                "mixed": found.mixed_standards,
                "basis": {k: list(v) for k, v in found.basis.items()},
            },
            sort_keys=True,
        )

    first = run()
    assert first == run()
    # the order of the producers file never matters
    reordered = propose(
        iec(60) + chinese(30) + ["/12.3", "I0.0"],
        producers=[PLAIN, EPLAN],
        creator="EPLAN cairo",
        language=PageLanguage.MIXED,
    )
    assert json.loads(first)["basis"]["producer"] == list(reordered.basis["producer"])


# --- the characterization built on the proposal (FR-EVAL-14) ------------------------
def test_a_document_is_characterized_from_what_stg_01_holds() -> None:
    fonts = (
        FontSignal("F1", "Type0", cid=True, embedded=True, has_tounicode=False),
        FontSignal("F2", "Type0", cid=True, embedded=True, has_tounicode=True),
        FontSignal("F1", "Type0", cid=True, embedded=True, has_tounicode=False),
    )
    signals = [
        make_signals(1, fonts=fonts),
        make_signals(2, char_count=0, latin_char_count=0, vector_path_count=0),
        make_signals(3, cjk_char_count=300, latin_char_count=100),
    ]
    classes = [classify_page(s, T) for s in signals]
    tally = PARSER.tally([*chinese(60), *iec(30), "/12.3", "I0.0"])
    summary = make_summary(page_count=3, creator="EPLAN Electric P8")
    proposal = detect_profile(
        base=BASE, summary=summary, pages=classes, tally=tally, producers=[EPLAN]
    )
    record = characterize(
        "d1", "a" * 64, summary, classes, signals, tally, proposal, [EPLAN], rules_version="9.9.9"
    )
    assert record.producer_family == "eplan" and record.creator == "EPLAN Electric P8"
    assert record.page_count == 3
    assert dict(record.text_kinds) == {PageKind.VECTOR_TEXT: 2, PageKind.EMPTY: 1}
    assert dict(record.page_languages) == {
        PageLanguage.EN: 1,
        PageLanguage.MIXED: 1,
        PageLanguage.NONE: 1,
    }
    assert record.document_language == "bilingual"
    assert record.conventions == {Convention.CN_LETTER_CODES: 60, Convention.IEC_81346: 30}
    assert record.symbol_standard_guess is proposal.profile.symbol_standard
    assert record.symbol_standard_basis == proposal.basis["symbol"]
    assert dict(record.plc_families) == {PlcFamily.S7_EN: 1}
    assert dict(record.cross_reference_formats) == {"slash_page_dot_column": 1}
    assert record.ocr_needed_pages == 0
    assert record.cid_fonts_without_tounicode == 1  # F1 twice is one font
    assert (record.pipeline_version, record.shared_version, record.rules_version) == (
        PIPELINE_VERSION,
        SHARED_VERSION,
        "9.9.9",
    )
    assert record.mixed_standards is proposal.mixed_standards is True


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({}, "none"),
        ({PageLanguage.NONE: 3}, "none"),
        ({PageLanguage.EN: 3}, "en"),
        ({PageLanguage.ZH: 1, PageLanguage.NONE: 2}, "zh"),
        ({PageLanguage.EN: 1, PageLanguage.ZH: 1}, "bilingual"),
        ({PageLanguage.MIXED: 1}, "bilingual"),
    ],
)
def test_the_document_language(counts: dict[PageLanguage, int], expected: str) -> None:
    assert document_language(counts) == expected


def test_a_report_aggregates_documents_and_lists_every_version_seen() -> None:
    def record(
        doc: str, tokens: list[str], creator: str | None, rules: str, ocr: bool = False
    ) -> Characterization:
        signals = [
            make_signals(1),
            make_signals(
                2, **({"char_count": 3, "image_count": 1, "image_area_ratio": 0.9} if ocr else {})
            ),
        ]
        classes = [classify_page(s, T) for s in signals]
        tally = PARSER.tally(tokens)
        summary = make_summary(page_count=2, creator=creator)
        proposal = detect_profile(
            base=BASE, summary=summary, pages=classes, tally=tally, producers=[EPLAN]
        )
        return characterize(
            doc, "b" * 64, summary, classes, signals, tally, proposal, [EPLAN], rules_version=rules
        )

    items = [
        record("d1", iec(100), "EPLAN", "1.0.0"),
        record("d2", chinese(100), None, "1.0.0", ocr=True),
        record("d3", iec(60) + chinese(40), "EPLAN", "1.1.0"),
    ]
    report = aggregate(items)
    assert (report.documents, report.pages) == (3, 6)
    assert dict(report.producers) == {"eplan": 2, "unknown": 1}
    assert report.conventions == {Convention.IEC_81346: 2, Convention.CN_LETTER_CODES: 1}
    assert report.ocr_needed_ratio == pytest.approx(1 / 6)
    assert report.mixed_standard_documents == 1
    assert report.versions["rules"] == ("1.0.0", "1.1.0")  # a mixed set says so
    assert report.versions["shared"] == (SHARED_VERSION,)
    assert sum(report.text_kinds.values()) == 6
    assert sum(report.document_languages.values()) == 3
    assert aggregate([]).documents == 0 and aggregate([]).ocr_needed_ratio == 0.0
    assert aggregate(items) == aggregate(list(reversed(items)))  # order never matters


def test_inc1_r16_the_default_threshold_is_unreachable_until_the_samples_tune_it() -> None:
    """INC1-R-16 (owner decision, 2026-11-02): the symbol standard is inferred, never seen, so its
    confidence is at most ``SYMBOL_CONFIDENCE_FACTOR``; the overall confidence is the lowest
    field's, and the default ``auto_accept_confidence`` is 0.90. A default document therefore
    always awaits a person, however clean its identifiers. P1 tunes the factor or the threshold
    on real drawings; this test fails the day either moves, so it never changes by accident."""
    assert SYMBOL_CONFIDENCE_FACTOR < BASE.auto_accept_confidence == 0.90
    clean = ConventionTally(
        conventions={Convention.IEC_81346: 5_000},
        plc_families={},
        cross_reference_formats={},
        identifiers=5_000,
        tokens=5_000,
    )
    proposal = detect_profile(
        base=BASE,
        summary=make_summary(creator="EPLAN Electric P8"),
        pages=[],
        tally=clean,
        producers=[ProducerSignature("EPLAN", "eplan", SymbolStandard.IEC_60617, 1.0)],
    )
    assert proposal.confidence <= SYMBOL_CONFIDENCE_FACTOR < BASE.auto_accept_confidence


# --- F0: failed pages and the symbol claim that needs evidence ------------------------
def _record(
    tokens: Sequence[str],
    *,
    page_count: int,
    classified: int,
    kind_signals: dict[str, int] | None = None,
    doc: str = "d",
    producers: Sequence[ProducerSignature] = (),
    creator: str | None = None,
) -> Characterization:
    signals = [make_signals(n, **(kind_signals or {})) for n in range(1, classified + 1)]
    classes = [classify_page(s, T) for s in signals]
    tally = PARSER.tally(tokens)
    summary = make_summary(page_count=page_count, creator=creator)
    proposal = detect_profile(
        base=BASE, summary=summary, pages=classes, tally=tally, producers=producers
    )
    return characterize(
        doc, "c" * 64, summary, classes, signals, tally, proposal, producers, rules_version="1"
    )


def test_f0_ac_3_a_page_with_no_classification_is_failed_and_an_empty_one_is_not() -> None:
    assert _record(iec(60), page_count=3, classified=2).failed_pages == 1
    assert _record(iec(60), page_count=3, classified=3).failed_pages == 0
    empty_page = {"char_count": 0, "latin_char_count": 0, "vector_path_count": 0}
    record = _record([], page_count=2, classified=2, kind_signals=empty_page)
    assert dict(record.text_kinds) == {PageKind.EMPTY: 2} and record.failed_pages == 0


def test_f0_ac_3_the_report_sums_the_failed_pages_of_its_documents() -> None:
    items = [
        _record(iec(60), page_count=3, classified=2, doc="d1"),
        _record(iec(60), page_count=2, classified=2, doc="d2"),
        _record(iec(60), page_count=4, classified=1, doc="d3"),
    ]
    report = aggregate(items)
    assert report.failed_pages == 1 + 0 + 3
    assert report.pages == 9  # the failed pages stay in the denominator


def test_f0_ac_6_without_an_observed_convention_there_is_no_symbol_claim() -> None:
    found = propose([])
    assert found.profile.symbol_standard is SymbolStandard.IEC_60617  # the proposal as it was
    assert not any(b.startswith("convention:") for b in found.basis["symbol"])
    hinted = propose(
        [],
        producers=[ProducerSignature("EPLAN", "eplan", SymbolStandard.IEC_60617, 0.5)],
        creator="EPLAN",
    )
    assert "producer:eplan" in hinted.basis["symbol"]
    assert not any(b.startswith("convention:") for b in hinted.basis["symbol"])
    assert _record([], page_count=2, classified=2).symbol_standard_guess is None
    assert (
        _record(
            [], page_count=2, classified=2, producers=[EPLAN], creator="EPLAN"
        ).symbol_standard_guess
        is None
    )
    seen = _record(iec(60), page_count=2, classified=2)
    assert seen.symbol_standard_guess is SymbolStandard.IEC_60617
    assert any(b.startswith("convention:") for b in seen.symbol_standard_basis)


def test_f0_ac_6_the_report_counts_unclaimed_documents_under_unknown() -> None:
    items = [
        _record([], page_count=2, classified=2, doc="a"),
        _record([], page_count=2, classified=2, doc="b"),
        _record(iec(60), page_count=2, classified=2, doc="c"),
        _record(chinese(60), page_count=2, classified=2, doc="d"),
    ]
    report = aggregate(items)
    assert report.symbol_standards == {
        SymbolStandard.IEC_60617.value: 1,
        SymbolStandard.GBT_4728.value: 1,
        UNKNOWN_SYMBOL_STANDARD: 2,
    }
    assert sum(report.symbol_standards.values()) == report.documents
    assert aggregate(items[:2]).symbol_standards == {"unknown": 2}


def test_f0_ac_7_the_proposed_profile_and_its_confidence_do_not_change_without_symbols() -> None:
    found = propose([])
    assert found.profile.symbol_standard is SymbolStandard.IEC_60617
    assert found.confidence == 0.0
    assert found.field_confidence["symbol_standard"] == 0.0
    # nothing else was detected but the languages, so the profile is the base plus those
    same = dataclasses.replace(BASE, languages=found.profile.languages)
    assert found.profile.fingerprint() == same.fingerprint()
