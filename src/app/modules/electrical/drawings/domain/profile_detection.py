"""STG-01's profile proposal (FR-ING-12, LLD 3.3.6). Pure and deterministic. Stdlib and
``shared`` only.

* **Identifier convention** = the one with the most identifiers in ``tally.conventions``. Its
  confidence is the first's margin over the second (as a share of all convention identifiers)
  times a sample factor: zero below ``MIN_IDENTIFIERS`` identifiers, full from
  ``FULL_CONFIDENCE_IDENTIFIERS``. A tie goes to the earlier member of ``Convention``.
* **Symbol standard** is INFERRED, not seen (symbols arrive with INC-4): IEC 81346 -> IEC 60617,
  GB/T 5094 or the Chinese letter codes -> GB/T 4728, NFPA/JIC -> NFPA/JIC, then moved by the
  producer's hint and by Chinese-language pages. Its confidence is below the convention's by
  construction (``SYMBOL_CONFIDENCE_FACTOR``, INC1-R-3).
* **Cross-reference format** = the dominant one; **PLC families** = those seen; **languages**
  = those on more than ``LANGUAGE_PAGE_SHARE`` of the pages. A field with no evidence keeps the
  base profile's value at confidence 1.0 (nothing contradicts it); only the convention can hold
  the proposal back for want of evidence.
* ``mixed_standards`` when the second DEVICE convention holds a quarter of the identifiers and at
  least ``MIXED_MIN_IDENTIFIERS`` of them.
* **KKS and ANSI** (D-36) add to the ONE device convention and never compete with it: the
  convention, the symbol standard and ``mixed_standards`` look at the DEVICE conventions only.
  ``plant_designation`` / ``device_function_numbers`` are proposed from ``MIN_IDENTIFIERS``
  evidence identifiers on, with the sample factor as their confidence; under that (and at zero)
  the base profile's value stays at 1.0, so a stray token never holds the proposal back.

Same inputs, same proposal: no clock, no randomness, ties by enum order.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.electrical.drawings.domain.pages import (
    PageClassification,
    PageLanguage,
    PdfSummary,
)
from app.modules.electrical.drawings.domain.profile import (
    CrossReferenceFormat,
    DeviceFunctionNumbers,
    DrawingProfile,
    PlantDesignation,
    SymbolStandard,
)
from app.modules.electrical.shared.kinds import (
    DEVICE_CONVENTIONS,
    Convention,
    ConventionTally,
)

_TWO: Final = 2  # the first and the second convention
MIN_IDENTIFIERS: Final = 20
FULL_CONFIDENCE_IDENTIFIERS: Final = 100
SYMBOL_CONFIDENCE_FACTOR: Final = 0.8
LANGUAGE_PAGE_SHARE: Final = 0.05
MIXED_SHARE: Final = 0.25
MIXED_MIN_IDENTIFIERS: Final = 20
ZH_PAGES_SHARE: Final = 0.5  # Chinese pages above this share pull the symbols to GB/T 4728
ZH_SYMBOL_WEIGHT: Final = 0.5

_SYMBOL_OF: Final[Mapping[Convention, SymbolStandard]] = {
    Convention.IEC_81346: SymbolStandard.IEC_60617,
    Convention.GBT_5094: SymbolStandard.GBT_4728,
    Convention.CN_LETTER_CODES: SymbolStandard.GBT_4728,
    Convention.NFPA_JIC: SymbolStandard.NFPA_JIC,
}


@dataclass(frozen=True, slots=True)
class ProducerSignature:  # data: drawings/adapters/data/producers.toml
    pattern: str  # case-insensitive substring of Creator/Producer
    family: str  # "eplan", "autocad_electrical", "see_electrical", ...
    symbol_hint: SymbolStandard | None
    weight: float


@dataclass(frozen=True, slots=True)
class ProfileProposal:
    profile: DrawingProfile
    confidence: float  # min of the field confidences
    field_confidence: Mapping[str, float]
    mixed_standards: bool  # FR-ING-12
    # why: "tokens", "producer", "language", "symbol", and "systems" (KKS / ANSI, when seen)
    basis: Mapping[str, tuple[str, ...]]


def matching_producers(
    summary: PdfSummary, producers: Sequence[ProducerSignature]
) -> tuple[ProducerSignature, ...]:
    """The signatures whose pattern occurs in the Creator or Producer, strongest first, ties
    by pattern (so the order never depends on the file's own order)."""
    haystack = f"{summary.creator or ''}\n{summary.producer or ''}".lower()
    hits = [p for p in producers if p.pattern and p.pattern.lower() in haystack]
    return tuple(sorted(hits, key=lambda p: (-p.weight, p.pattern.lower(), p.family)))


def producer_family(summary: PdfSummary, producers: Sequence[ProducerSignature]) -> str | None:
    hits = matching_producers(summary, producers)
    return hits[0].family if hits else None


def ranked_conventions(conventions: Mapping[Convention, int]) -> list[tuple[Convention, int]]:
    """Convention counts, most identifiers first, ties by the enum's order."""
    order = {member: index for index, member in enumerate(Convention)}
    present = [(c, n) for c, n in conventions.items() if n > 0]
    return sorted(present, key=lambda item: (-item[1], order[item[0]]))


def ranked_device_conventions(
    conventions: Mapping[Convention, int],
) -> list[tuple[Convention, int]]:
    """``ranked_conventions`` restricted to DEVICE_CONVENTIONS (KKS and ANSI never compete)."""
    return [(c, n) for c, n in ranked_conventions(conventions) if c in DEVICE_CONVENTIONS]


def is_mixed(conventions: Mapping[Convention, int], identifiers: int) -> bool:
    """FR-ING-12 after D-36: the second DEVICE convention holds a quarter of the identifiers,
    and 20 at least. KKS and ANSI beside one device convention are not mixed."""
    ranked = ranked_device_conventions(conventions)
    if len(ranked) < _TWO or identifiers <= 0:
        return False
    second = ranked[1][1]
    return second >= MIXED_MIN_IDENTIFIERS and second >= MIXED_SHARE * identifiers


def _sample_factor(identifiers: int) -> float:
    if identifiers < MIN_IDENTIFIERS:
        return 0.0
    return min(1.0, identifiers / FULL_CONFIDENCE_IDENTIFIERS)


def _margin(counts: Sequence[int]) -> float:
    """The first's lead over the second, as a share of all (1.0 when alone)."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    second = counts[1] if len(counts) > 1 else 0
    return (counts[0] - second) / total


def _language_shares(pages: Sequence[PageClassification]) -> tuple[float, float]:
    if not pages:
        return 0.0, 0.0
    en = sum(1 for p in pages if p.language in (PageLanguage.EN, PageLanguage.MIXED))
    zh = sum(1 for p in pages if p.language in (PageLanguage.ZH, PageLanguage.MIXED))
    return en / len(pages), zh / len(pages)


def _convention(
    base: DrawingProfile, tally: ConventionTally
) -> tuple[Convention, float, tuple[str, ...]]:
    ranked = ranked_device_conventions(tally.conventions)
    if not ranked:
        return base.identifier_convention, 0.0, ()
    confidence = _margin([n for _, n in ranked]) * _sample_factor(tally.identifiers)
    return ranked[0][0], confidence, tuple(f"{c.value}:{n}" for c, n in ranked)


def _symbol(
    convention: Convention,
    convention_confidence: float,
    hits: Sequence[ProducerSignature],
    zh_share: float,
    *,
    observed: bool,  # a convention was seen in the tokens, not taken from the base profile
) -> tuple[SymbolStandard, float, tuple[str, ...]]:
    scores: defaultdict[SymbolStandard, float] = defaultdict(float)
    scores[_SYMBOL_OF[convention]] += 1.0
    basis = [f"convention:{convention.value}"] if observed else []
    for hit in hits:
        if hit.symbol_hint is not None:
            scores[hit.symbol_hint] += hit.weight
            basis.append(f"producer:{hit.family}")
    if zh_share > ZH_PAGES_SHARE:
        scores[SymbolStandard.GBT_4728] += ZH_SYMBOL_WEIGHT
        basis.append("language:zh")
    order = {member: index for index, member in enumerate(SymbolStandard)}
    chosen = min(scores, key=lambda s: (-scores[s], order[s]))
    share = scores[chosen] / sum(scores.values())
    return chosen, convention_confidence * SYMBOL_CONFIDENCE_FACTOR * share, tuple(basis)


def _systems(
    base: DrawingProfile, tally: ConventionTally
) -> tuple[PlantDesignation, DeviceFunctionNumbers, float, float, tuple[str, ...]]:
    """KKS and ANSI (D-36): proposed from MIN_IDENTIFIERS evidence identifiers on, at the
    sample factor. Under that, and at zero, the base profile's value stays at 1.0 and the
    field is not in basis["systems"], so a stray token never holds the proposal back."""
    kks = tally.conventions.get(Convention.KKS, 0)
    ansi = tally.conventions.get(Convention.ANSI, 0)
    kks_seen, ansi_seen = kks >= MIN_IDENTIFIERS, ansi >= MIN_IDENTIFIERS
    plant = PlantDesignation.KKS if kks_seen else base.plant_designation
    functions = DeviceFunctionNumbers.ANSI if ansi_seen else base.device_function_numbers
    basis = tuple(
        f"{c.value}:{n}"
        for c, n, seen in ((Convention.KKS, kks, kks_seen), (Convention.ANSI, ansi, ansi_seen))
        if seen
    )
    return (
        plant,
        functions,
        _sample_factor(kks) if kks_seen else 1.0,
        _sample_factor(ansi) if ansi_seen else 1.0,
        basis,
    )


def detect_profile(
    *,
    base: DrawingProfile,
    summary: PdfSummary,
    pages: Sequence[PageClassification],
    tally: ConventionTally,
    producers: Sequence[ProducerSignature],
) -> ProfileProposal:
    convention, convention_confidence, token_basis = _convention(base, tally)
    hits = matching_producers(summary, producers)
    en_share, zh_share = _language_shares(pages)
    symbol, symbol_confidence, symbol_basis = _symbol(
        convention, convention_confidence, hits, zh_share, observed=bool(token_basis)
    )

    plant, functions, plant_confidence, functions_confidence, systems_basis = _systems(base, tally)

    xref_counts = sorted(
        ((n, name) for name, n in tally.cross_reference_formats.items() if n > 0),
        key=lambda item: (-item[0], item[1]),
    )
    xref = base.cross_reference_format
    xref_confidence = 1.0
    if xref_counts:
        known = {m.value for m in CrossReferenceFormat}
        top_name = xref_counts[0][1]
        if top_name in known:
            xref = CrossReferenceFormat(top_name)
        xref_confidence = _margin([n for n, _ in xref_counts])

    families = frozenset(f for f, n in tally.plc_families.items() if n > 0) or base.plc_families
    languages = (
        frozenset(
            lang
            for lang, share in (("en", en_share), ("zh", zh_share))
            if share > LANGUAGE_PAGE_SHARE
        )
        or base.languages
    )

    field_confidence = {
        "identifier_convention": convention_confidence,
        "symbol_standard": symbol_confidence,
        "cross_reference_format": xref_confidence,
        "plc_families": 1.0,
        "languages": 1.0,
        "plant_designation": plant_confidence,
        "device_function_numbers": functions_confidence,
    }
    profile = base.with_detected(
        symbol_standard=symbol,
        identifier_convention=convention,
        cross_reference_format=xref,
        plc_families=families,
        languages=languages,
        plant_designation=plant,
        device_function_numbers=functions,
    )
    return ProfileProposal(
        profile=profile,
        confidence=min(field_confidence.values()),
        field_confidence=field_confidence,
        mixed_standards=is_mixed(tally.conventions, tally.identifiers),
        basis={
            "tokens": token_basis,
            "producer": tuple(hit.family for hit in hits),
            "language": (f"en:{en_share:.2f}", f"zh:{zh_share:.2f}"),
            "symbol": symbol_basis,
            **({"systems": systems_basis} if systems_basis else {}),
        },
    )
