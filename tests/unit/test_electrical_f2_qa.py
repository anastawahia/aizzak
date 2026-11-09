"""QA exploratory coverage for F2 item 1 (D-36): edges the AC tests do not pin.

Every identifier is an abstract pattern (NFR-F2-7); nothing here is a real tag.
Groups: KKS edges (US-1), ANSI edges (US-2), profile fields (US-3), detection (US-3),
determinism (NFR-F2-1), and the guard that existing profile fingerprints did not move."""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from app.modules.electrical.drawings.domain.errors import InvalidProfile
from app.modules.electrical.drawings.domain.profile import (
    _F2_DEFAULTS,
    DeviceFunctionNumbers,
    DrawingProfile,
    PlantDesignation,
)
from app.modules.electrical.drawings.domain.profile_detection import (
    ProfileProposal,
    detect_profile,
)
from app.modules.electrical.shared.kinds import Convention, IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.parser import MAX_TEXT_CHARS, IdentifierParser
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical import make_summary
from tests.unit.support_electrical_shared import fw, parser_for
from tests.unit.test_electrical_profile_detection import BASE, pages

RULES = load_rules()
FULL = IdentifierParser(RULES)
KNOWN = frozenset(RULES.wire_number_patterns)
KKS = parser_for(Convention.IEC_81346, Convention.KKS)
ANSI = parser_for(Convention.IEC_81346, Convention.ANSI)
IEC = parser_for(Convention.IEC_81346)
ZWSP, RLM, RLO, NBSP = "\u200b", "\u200f", "\u202e", "\u00a0"


def _read(parser: IdentifierParser, text: str, **kw: IdentifierKind) -> ParsedIdentifier | None:
    return parser.parse(text, **kw)


def _valid(parser: IdentifierParser, text: str, kind: IdentifierKind) -> ParsedIdentifier:
    found = parser.parse(text)
    assert found is not None, f"{text!r} did not parse"
    assert found.kind is kind and found.valid, (text, found.kind, found.issues)
    return found


def _is_kks(found: ParsedIdentifier | None) -> bool:
    return found is not None and found.kind is IdentifierKind.KKS_TAG


# --- KKS (US-1) ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "20xyz10ab123",
        "20 XYZ10 AB123",
        "20 XYZ 10 AB 123",
        f"{NBSP}20XYZ10AB123{NBSP}",
        "20XYZ10\tAB123",
        "20XYZ10AB123\n",
        fw("20XYZ10AB123"),
        "20XYZ\uff11\uff10AB123",  # mixed ASCII and full-width
        "\u202a20XYZ10AB123\u202c",  # direction marks around it stay valid
    ],
)
def test_kks_spelling_variants_meet_in_one_canonical_form(text: str) -> None:
    found = KKS.parse(text)
    assert found is not None and found.kind is IdentifierKind.KKS_TAG and found.valid
    assert found.forms.canonical == "20XYZ10AB123"
    assert found.forms.original == text  # FR-ID-09: the original is kept as it came


def test_kks_hidden_character_invalidates_and_a_bidi_mark_does_not() -> None:
    hidden = KKS.parse(f"20{ZWSP}XYZ10AB123")
    assert hidden is not None and hidden.kind is IdentifierKind.KKS_TAG and not hidden.valid
    assert "text.hidden_character" in hidden.issues
    bidi = KKS.parse(f"{RLO}20XYZ10AB123")
    assert bidi is not None and bidi.valid and "text.bidi_control" in bidi.issues


@pytest.mark.parametrize(
    "text",
    [
        "20XYZ10AB123\u0646",  # an Arabic letter stuck to the tag
        "\u0627\u06442XYZ10AB123",
        "20XYZ10AB123\u4e2d",  # a Chinese character stuck to the tag
        "\u4e2d20XYZ10AB123",
        "20XYZ10\u0410B123",  # Cyrillic A where a Latin letter belongs
        "\u0645\u062d\u0648\u0644 20XYZ10AB123",  # Arabic prose before it
    ],
)
def test_other_scripts_never_make_a_valid_kks_tag(text: str) -> None:
    found = KKS.parse(text)
    assert found is None or not found.valid


@pytest.mark.parametrize("text", ["=20XYZ10AB123", "+20XYZ10AB123", "++20XYZ10AB123"])
def test_prefixed_tags_are_never_kks_while_the_feature_is_off(text: str) -> None:
    for parser in (IEC, FULL, parser_for(Convention.IEC_81346, Convention.ANSI)):
        assert not _is_kks(parser.parse(text))
    assert _is_kks(KKS.parse(text))
    assert not _is_kks(FULL.parse("20XYZ10AB123"))


def test_a_doubled_or_unknown_prefix_is_no_kks_tag() -> None:
    for text in ("=+20XYZ10AB123", "+++20XYZ10AB123", "-20XYZ10AB123", "#20XYZ10AB123"):
        found = KKS.parse(text)
        assert not _is_kks(found), text


def test_a_tag_over_the_text_bound_is_no_identifier() -> None:
    assert KKS.parse("20XYZ10AB123" * 17) is None  # 204 characters
    assert KKS.parse("20XYZ10" + "AB123" * 40) is None
    assert KKS.parse("20XYZ10AB123" + " " * MAX_TEXT_CHARS) is None
    assert KKS.parse("9" * (MAX_TEXT_CHARS + 1)) is None


@pytest.mark.parametrize(
    "text", ["20XYZ10456", "20XYZ1045678", "20XYZ10AB12345", "20XYZ10AB123456", "20XYZ10AB1234"]
)
def test_a_cable_suffix_of_the_wrong_length_is_never_a_valid_tag(text: str) -> None:
    found = KKS.parse(text)
    assert found is None or not found.valid, text
    if found is not None:
        assert found.kind is IdentifierKind.KKS_TAG and found.forms.canonical == text


def test_a_four_digit_cable_suffix_is_valid_after_a_system_or_an_equipment_tag() -> None:
    assert _valid(KKS, "20XYZ104567", IdentifierKind.KKS_TAG).kks is not None
    assert _valid(KKS, "20XYZ10AB1234567", IdentifierKind.KKS_TAG).kks is not None


def test_a_third_level_with_a_wrong_count_is_malformed_not_dropped() -> None:
    for text in ("20XYZ10AB123CD4", "20XYZ10AB123CD456", "20XYZ10AB123CD45E"):
        found = KKS.parse(text)
        assert found is not None and not found.valid and "kks.malformed" in found.issues, text


def test_kks_never_claims_text_it_does_not_have_the_shape_of() -> None:
    for text in ("7XY123", "XYZ10AB123", "20", "20XYZ", "20-XYZ10-AB123", "20XYZ10.AB123"):
        assert not _is_kks(KKS.parse(text)), text


def test_enabling_kks_does_not_touch_a_plain_iec_terminal_or_designation() -> None:
    for text in ("X1-14", "-K5", "-Q1", "=A1-K5", "1X-25", "KM1", "M10.1"):
        before, after = IEC.parse(text), KKS.parse(text)
        assert (before is None) == (after is None), text
        if before is not None and after is not None:
            assert (before.kind, before.forms.canonical) == (after.kind, after.forms.canonical)


# --- ANSI (US-2) --------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["0", "00", "000", "01", "100", "0T", "00T", "100T", "100-1"])
def test_ansi_function_numbers_outside_1_to_99_are_never_a_device(text: str) -> None:
    found = ANSI.parse(text)
    assert found is None or found.kind is not IdentifierKind.ANSI_DEVICE, text
    assert ANSI.parse(text, hint=IdentifierKind.ANSI_DEVICE) is None, text


@pytest.mark.parametrize("number", ["1", "9", "10", "52", "99"])
def test_ansi_bounds_are_inclusive_with_context(number: str) -> None:
    plain = ANSI.parse(number)
    assert plain is not None and plain.kind is IdentifierKind.WIRE_NUMBER
    assert IdentifierKind.ANSI_DEVICE in plain.alternatives
    hinted = ANSI.parse(number, hint=IdentifierKind.ANSI_DEVICE)
    assert hinted is not None and hinted.kind is IdentifierKind.ANSI_DEVICE and hinted.valid
    assert hinted.ansi is not None and hinted.ansi.functions[0].number == int(number)


def test_without_ansi_a_hint_for_it_reads_nothing() -> None:
    assert IEC.parse("52", hint=IdentifierKind.ANSI_DEVICE) is None
    assert IEC.parse("87T", hint=IdentifierKind.ANSI_DEVICE) is None
    assert IEC.parse("50/51", hint=IdentifierKind.ANSI_DEVICE) is None


@pytest.mark.parametrize(
    ("text", "valid"),
    [("87T", True), ("50BF", True), ("87TN", True), ("87TTT", False), ("87QZ", False)],
)
def test_ansi_suffix_lengths_one_to_three_are_read_and_checked_against_the_set(
    text: str, valid: bool
) -> None:
    found = ANSI.parse(text)
    assert found is not None and found.kind is IdentifierKind.ANSI_DEVICE
    assert found.valid is valid
    assert found.issues == (() if valid else ("ansi.unknown_suffix",))


def test_a_four_letter_suffix_is_no_device() -> None:
    assert ANSI.parse("87TTTT") is None
    assert ANSI.parse("87TNBF") is None


def _is_ansi_valid(found: ParsedIdentifier | None) -> bool:
    return found is not None and found.kind is IdentifierKind.ANSI_DEVICE and found.valid


@pytest.mark.parametrize(
    ("text", "instance"), [("27-1", 1), ("27-9", 9), ("27-10", 10), ("27-99", 99), ("87T-2", 2)]
)
def test_an_ansi_instance_is_one_or_two_digits_without_a_leading_zero(
    text: str, instance: int
) -> None:
    found = _valid(ANSI, text, IdentifierKind.ANSI_DEVICE)
    assert found.ansi is not None and found.ansi.instance == instance


@pytest.mark.parametrize("text", ["27-0", "27-01", "27-100", "27-2-3", "27--1", "27-", "-27"])
def test_a_bad_ansi_instance_is_not_a_device(text: str) -> None:
    assert not _is_ansi_valid(ANSI.parse(text)), text
    found = ANSI.parse(text)
    assert found is None or found.kind is not IdentifierKind.ANSI_DEVICE


def test_ansi_spelling_variants_meet_in_one_canonical_form() -> None:
    for text in ("87t", "87 T", " 87T ", fw("87T"), f"{NBSP}87T"):
        found = _valid(ANSI, text, IdentifierKind.ANSI_DEVICE)
        assert found.forms.canonical == "87T" and found.forms.original == text
    hidden = ANSI.parse(f"87{ZWSP}T")
    assert hidden is not None and not hidden.valid and "text.hidden_character" in hidden.issues


def test_a_compound_has_two_or_three_functions_each_with_a_known_suffix_only() -> None:
    three = _valid(ANSI, "50/51/27", IdentifierKind.ANSI_DEVICE)
    assert three.ansi is not None and [f.number for f in three.ansi.functions] == [50, 51, 27]
    for text in ("50/51/27/59", "50/", "/51", "50//51", "50QZ/51", "50/51-1", "50/100", "50/0"):
        found = ANSI.parse(text)
        assert found is None or found.kind is not IdentifierKind.ANSI_DEVICE, text
    with_extra = parser_for(Convention.IEC_81346, Convention.ANSI, extra_ansi_suffixes=("QZ",))
    assert _valid(with_extra, "50QZ/51", IdentifierKind.ANSI_DEVICE).ansi is not None


@pytest.mark.parametrize(
    ("fmt", "kind", "alternative"),
    [
        (None, IdentifierKind.ANSI_DEVICE, IdentifierKind.CROSS_REFERENCE),
        ("page_slash_column", IdentifierKind.CROSS_REFERENCE, IdentifierKind.ANSI_DEVICE),
        ("page_dot_column", IdentifierKind.ANSI_DEVICE, IdentifierKind.CROSS_REFERENCE),
    ],
)
def test_the_pinned_cross_reference_format_decides_50_slash_51(
    fmt: str | None, kind: IdentifierKind, alternative: IdentifierKind
) -> None:
    parser = parser_for(Convention.IEC_81346, Convention.ANSI, cross_reference_format=fmt)
    found = parser.parse("50/51")
    assert found is not None and found.kind is kind and found.valid
    if fmt == "page_dot_column":
        assert found.alternatives == ()  # that format cannot read a slash: no second reading
    else:
        assert found.alternatives == (alternative,)


def test_a_caller_with_context_reads_the_compound_even_when_the_format_is_pinned() -> None:
    parser = parser_for(
        Convention.IEC_81346, Convention.ANSI, cross_reference_format="page_slash_column"
    )
    hinted = parser.parse("50/51", hint=IdentifierKind.ANSI_DEVICE)
    assert hinted is not None and hinted.kind is IdentifierKind.ANSI_DEVICE
    plain = parser.parse("50/51")
    assert plain is not None and plain.kind is IdentifierKind.CROSS_REFERENCE


def test_ansi_off_leaves_every_slash_text_a_cross_reference_as_before() -> None:
    for text in ("50/51", "12/3", "1/2"):
        found = IEC.parse(text)
        assert found is None or found.kind is not IdentifierKind.ANSI_DEVICE
    assert _read(IEC, "50/51") == _read(FULL, "50/51")


def test_ansi_does_not_take_the_letter_codes_that_the_full_rules_read() -> None:
    on = IdentifierParser(RULES.with_additive())
    for text in ("1KM", "2QF", "3KM1", "1XT-5", "1X-25"):
        before, after = FULL.parse(text), on.parse(text)
        assert (before is None) == (after is None), text
        if before is not None and after is not None:
            assert (before.kind, before.convention) == (after.kind, after.convention), text


# --- the profile's three fields (US-3) ----------------------------------------------------
def _profile(**fields: object) -> DrawingProfile:
    return DrawingProfile.from_body({"schema": 1, **fields}, known_patterns=KNOWN)


@pytest.mark.parametrize(
    "value", [3, None, True, 1.5, ["kks"], {"kks": 1}, "KKS", " kks", "", "ansi"]
)
def test_plant_designation_takes_only_its_two_names(value: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        _profile(plant_designation=value)
    assert caught.value.path == "plant_designation"
    assert "must be one of: none, kks" in str(caught.value)


@pytest.mark.parametrize("value", [3, None, False, 1.5, ["ansi"], {"ansi": 1}, "ANSI", "", "kks"])
def test_device_function_numbers_takes_only_its_two_names(value: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        _profile(device_function_numbers=value)
    assert caught.value.path == "device_function_numbers"
    assert "must be one of: none, ansi" in str(caught.value)


@pytest.mark.parametrize("value", [None, {"a": 1}, 3, "QZ", True])
def test_ansi_suffixes_must_be_a_list(value: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        _profile(ansi_suffixes=value)
    assert caught.value.path == "ansi_suffixes"


@pytest.mark.parametrize("item", [None, 7, ["A"], {"a": 1}, True])
def test_ansi_suffix_items_must_be_strings(item: object) -> None:
    with pytest.raises(InvalidProfile) as caught:
        _profile(ansi_suffixes=["AB", item])
    assert caught.value.path == "ansi_suffixes[1]"


_METACHARACTERS = [
    "A|B",
    "A*",
    "A+",
    "A?",
    "[A-Z]",
    "(A)",
    "\\w",
    "\\d",
    "A.",
    "^A",
    "A$",
    ".*",
    "(?i)",
    "A{2}",
    "A\\",
]
_HIDDEN = [
    f"A{ZWSP}",
    f"A{RLM}",
    f"{RLO}QZ",
    "Q\u00adZ",  # soft hyphen
    "Q\u200dZ",  # zero-width joiner
    "Q\u2060Z",  # word joiner
    "Q\tZ",
    "QZ\n",
    "Q\x00Z",
    "Q\u202aZ",
]


@pytest.mark.parametrize("item", _METACHARACTERS + _HIDDEN)
def test_a_metacharacter_or_a_hidden_character_is_refused_and_never_echoed(item: str) -> None:
    with pytest.raises(InvalidProfile) as caught:
        _profile(ansi_suffixes=["ZZ", item])
    assert caught.value.path == "ansi_suffixes[1]"
    text = f"{caught.value.path}: {caught.value}"
    assert item not in text and "ZZ" not in text.replace("ansi_suffixes", "")


@pytest.mark.parametrize(
    "item", ["\u0642", "\u0642\u0627", "\u4e2d", "A1", "A-B", "Q\u0301", "AB\u0646"]
)
def test_non_latin_and_non_letter_suffixes_are_refused(item: str) -> None:
    with pytest.raises(InvalidProfile):
        _profile(ansi_suffixes=[item])


def test_the_suffix_limit_counts_what_was_sent_and_it_is_64_entries_at_most() -> None:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sixty_four = [a + b for a in letters[:7] for b in letters[:10]][:64]
    assert len(_profile(ansi_suffixes=sixty_four).ansi_suffixes) == 64
    with pytest.raises(InvalidProfile) as caught:
        _profile(ansi_suffixes=[*sixty_four, "ZZZ"])
    assert caught.value.path == "ansi_suffixes"
    with pytest.raises(InvalidProfile):  # duplicates are counted before they collapse
        _profile(ansi_suffixes=["A"] * 65)
    assert _profile(ansi_suffixes=["a", "A", "a"]).ansi_suffixes == ("A",)


def test_an_oversized_suffix_is_refused_before_it_is_normalised() -> None:
    with pytest.raises(InvalidProfile):
        _profile(ansi_suffixes=["A" * 10_000])
    with pytest.raises(InvalidProfile):
        _profile(ansi_suffixes=["\ufb03" * 100])  # NFKC would expand each one


def test_a_suffix_is_membership_only_never_a_pattern() -> None:
    """A metacharacter list cannot be stored, and an allowed one reads exactly that text."""
    profile = _profile(device_function_numbers="ansi", ansi_suffixes=["QZ"])
    parser = IdentifierParser(RULES.narrowed(profile.rule_selection()))
    assert _is_ansi_valid(parser.parse("87QZ"))
    for text in ("87W", "87WQZ", "87QQZ", "87QZ1"):
        found = parser.parse(text)
        assert not (found is not None and found.valid and found.kind is IdentifierKind.ANSI_DEVICE)


def test_only_the_fields_that_differ_from_their_defaults_reach_the_body() -> None:
    cases: list[tuple[dict[str, object], set[str]]] = [
        ({}, set()),
        (
            {"plant_designation": "none", "device_function_numbers": "none", "ansi_suffixes": []},
            set(),
        ),
        ({"plant_designation": "kks"}, {"plant_designation"}),
        ({"device_function_numbers": "ansi"}, {"device_function_numbers"}),
        ({"ansi_suffixes": ["QZ"]}, {"ansi_suffixes"}),
    ]
    for fields, expected in cases:
        assert set(_profile(**fields).to_body()) & set(_F2_DEFAULTS) == expected, fields


def test_an_existing_profile_keeps_its_body_and_its_fingerprint() -> None:
    """The digest was computed on master (de82b80) from the same fields: a profile saved before
    D-36 reads and fingerprints identically, so no stored version looks changed."""
    profile = _profile(
        identifier_convention="gbt_5094",
        symbol_standard="gbt_4728",
        languages=["zh", "en"],
        extra_aspects=["="],
        voltage_labels=["-48V"],
        plc_families=["tia"],
        cross_reference_format="page_dot_column",
        auto_accept_confidence=0.8,
    )
    assert set(profile.to_body()).isdisjoint(_F2_DEFAULTS)
    assert profile.fingerprint() == _MASTER_GBT_FINGERPRINT
    assert DrawingProfile.from_body(profile.to_body(), known_patterns=KNOWN) == profile


_MASTER_GBT_FINGERPRINT = "69a75c7dcba6baf1c4cb9091a06fe6a7f83a84207d10d2d7972d3b3c486c38c3"


def test_the_fingerprint_changes_when_a_new_field_does() -> None:
    plain = _profile().fingerprint()
    prints = {
        _profile(plant_designation="kks").fingerprint(),
        _profile(device_function_numbers="ansi").fingerprint(),
        _profile(ansi_suffixes=["QZ"]).fingerprint(),
    }
    assert len(prints | {plain}) == 4


def test_the_selection_adds_kks_and_ansi_beside_exactly_one_device_convention() -> None:
    for device in ("iec_81346", "gbt_5094", "cn_letter_codes", "nfpa_jic"):
        selection = _profile(
            identifier_convention=device, plant_designation="kks", device_function_numbers="ansi"
        ).rule_selection()
        assert selection.conventions == {Convention(device), Convention.KKS, Convention.ANSI}


# --- detection (US-3) ---------------------------------------------------------------------
def _iec(n: int) -> list[str]:
    return [f"-K{i}" for i in range(1, n + 1)]


def _cn(n: int) -> list[str]:
    return [f"{i}KM" for i in range(1, n + 1)]


def _kks(n: int) -> list[str]:
    return [f"20XYZ10AB{i:03d}" for i in range(1, n + 1)]


def _ansi(n: int) -> list[str]:
    return [f"{50 + i % 40}{'TNBG'[i % 4]}" for i in range(n)]


def _propose(tokens: Sequence[str]) -> ProfileProposal:
    return detect_profile(
        base=BASE, summary=make_summary(), pages=pages(), tally=FULL.tally(tokens), producers=()
    )


def test_iec_with_chinese_letter_codes_stays_mixed_through_the_real_tally() -> None:
    found = _propose(_iec(60) + _cn(30))
    assert found.mixed_standards
    assert found.basis["tokens"] == ("iec_81346:60", "cn_letter_codes:30")


def test_kks_and_ansi_in_bulk_neither_create_nor_hide_the_mix() -> None:
    plain = _propose(_iec(60) + _kks(120) + _ansi(120))
    assert not plain.mixed_standards
    assert plain.profile.identifier_convention is Convention.IEC_81346
    mixed = _propose(_iec(60) + _cn(30) + _kks(120) + _ansi(120))
    assert mixed.mixed_standards  # still mixed: 30 of 90 device identifiers are the second one


def test_kks_alone_or_ansi_alone_is_not_mixed_and_proposes_no_device_convention() -> None:
    for tokens in (_kks(80), _ansi(80), _kks(40) + _ansi(40)):
        found = _propose(tokens)
        assert not found.mixed_standards
        assert found.field_confidence["identifier_convention"] == 0.0


def test_ambiguous_ansi_texts_are_no_evidence_of_ansi() -> None:
    bare = [str(n) for n in range(1, 100)]
    compounds = ["50/51", "12/3", "1/2", "27/59"] * 10
    tally = FULL.tally(bare + compounds + ["87QZ", "87TTT", "100T", "00"])
    assert Convention.ANSI not in tally.conventions


def test_lookalike_and_hidden_text_is_no_evidence_of_kks_or_ansi() -> None:
    tally = FULL.tally(
        [
            *["20XYZ10AB123\u0301", f"20{ZWSP}XYZ10AB123", "20XY10AB123", "2OXYZ1OAB12O"],
            *["87T\u0301", f"87{ZWSP}T", "87TTT", "87QZ"],
        ]
    )
    assert Convention.KKS not in tally.conventions and Convention.ANSI not in tally.conventions


def test_the_tally_does_not_depend_on_token_order() -> None:
    tokens = (
        _iec(30)
        + _cn(10)
        + _kks(25)
        + _ansi(22)
        + ["52", "50/51", "x", "", "\u0662\u0660XYZ10AB123"]
    )
    shuffled = tokens[:]
    random.Random(11).shuffle(shuffled)
    assert FULL.tally(tokens) == FULL.tally(shuffled)


def test_the_probe_leaves_the_base_counts_alone() -> None:
    without = FULL.tally(_iec(30) + _cn(10))
    with_extra = FULL.tally(_iec(30) + _cn(10) + _kks(40) + _ansi(40))
    assert with_extra.identifiers == without.identifiers
    for convention in (Convention.IEC_81346, Convention.CN_LETTER_CODES):
        assert with_extra.conventions[convention] == without.conventions[convention]
    assert with_extra.tokens == without.tokens + 80


def test_a_tally_with_both_enabled_counts_the_same_as_the_probe() -> None:
    tokens = _iec(30) + _kks(25) + _ansi(22) + ["52", "50/51"]
    probed = FULL.tally(tokens)
    enabled = IdentifierParser(RULES.with_additive()).tally(tokens)
    assert enabled.conventions[Convention.KKS] == probed.conventions[Convention.KKS] == 25
    assert enabled.conventions[Convention.ANSI] == probed.conventions[Convention.ANSI] == 22


def test_a_detected_proposal_applies_both_fields_and_keeps_the_suffixes() -> None:
    base = DrawingProfile.from_body(
        {"schema": 1, "ansi_suffixes": ["QZ"], "plant_designation": "kks"}, known_patterns=KNOWN
    )
    found = detect_profile(
        base=base,
        summary=make_summary(),
        pages=pages(),
        tally=FULL.tally(_iec(40)),
        producers=(),
    )
    assert found.profile.ansi_suffixes == ("QZ",)
    assert found.profile.plant_designation is PlantDesignation.KKS  # no evidence against it
    assert found.profile.device_function_numbers is DeviceFunctionNumbers.NONE


def test_detection_is_deterministic() -> None:
    tokens = _iec(30) + _cn(10) + _kks(25) + _ansi(22)
    first, second = _propose(tokens), _propose(list(tokens))
    assert first == second
    assert first.profile.fingerprint() == second.profile.fingerprint()


# --- determinism (NFR-F2-1) ---------------------------------------------------------------
_EDGE_TEXTS = (
    "20XYZ10AB123",
    "=20XYZ10AB123",
    "20 xyz10 ab123",
    "20XY10AB123",
    "2OXYZ1OAB12O",
    "20XYZ104567",
    "87T",
    "87QZ",
    "50/51",
    "12/3",
    "52",
    "00",
    "1X-25",
    f"20{ZWSP}XYZ10AB123",
    "20XYZ10AB123\u0646",
)


@pytest.mark.parametrize("hint", [None, IdentifierKind.ANSI_DEVICE, IdentifierKind.KKS_TAG])
def test_parsing_twice_and_with_a_fresh_parser_gives_the_same_result(
    hint: IdentifierKind | None,
) -> None:
    again = IdentifierParser(load_rules().with_additive())
    for text in _EDGE_TEXTS:
        first = again.parse(text, hint=hint)
        assert first == again.parse(text, hint=hint), text
        assert first == IdentifierParser(load_rules().with_additive()).parse(text, hint=hint), text


def test_a_hint_of_kks_reads_only_kks() -> None:
    on = IdentifierParser(RULES.with_additive())
    found = on.parse("20XYZ10AB123", hint=IdentifierKind.KKS_TAG)
    assert found is not None and found.kind is IdentifierKind.KKS_TAG
    assert on.parse("87T", hint=IdentifierKind.KKS_TAG) is None


def test_a_variation_selector_in_a_suffix_is_dropped_and_the_stored_value_is_clean() -> None:
    """QA note: U+FE0F is a default-ignorable mark, not a forbidden category, so it is
    normalised away instead of refused. What is stored is the clean ASCII suffix."""
    profile = _profile(ansi_suffixes=["Q\ufe0fZ"])
    assert profile.ansi_suffixes == ("QZ",)
    assert profile.to_body()["ansi_suffixes"] == ["QZ"]
