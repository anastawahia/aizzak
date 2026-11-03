"""The characterization report (FR-EVAL-14): what a document is, so the thresholds and rules can
be tuned on evidence. Pure. Stdlib and ``shared`` only.

``characterize`` builds one document's record from what STG-01 already holds (summary, page
classifications and signals, the convention tally and the proposal); ``aggregate`` folds many
into a project's (or the offline tool's) report. Every version that produced a record rides on
it, and the report lists every version seen, so a mixed set says so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.electrical.drawings.domain.pages import (
    PageClassification,
    PageKind,
    PageLanguage,
    PageSignals,
    PdfSummary,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import SymbolStandard
from app.modules.electrical.drawings.domain.profile_detection import (
    ProducerSignature,
    ProfileProposal,
    producer_family,
    ranked_conventions,
)
from app.modules.electrical.shared.kinds import Convention, ConventionTally, PlcFamily
from app.modules.electrical.shared.version import SHARED_VERSION

UNKNOWN_PRODUCER: Final = "unknown"


@dataclass(frozen=True, slots=True)
class Characterization:
    document_id: str
    content_sha256: str
    producer_family: str | None
    creator: str | None
    producer: str | None
    page_count: int
    text_kinds: Mapping[PageKind, int]
    page_languages: Mapping[PageLanguage, int]
    document_language: str  # en | zh | bilingual | none
    conventions: Mapping[Convention, int]
    symbol_standard_guess: SymbolStandard
    symbol_standard_basis: tuple[str, ...]
    plc_families: Mapping[PlcFamily, int]
    cross_reference_formats: Mapping[str, int]
    ocr_needed_pages: int
    cid_fonts_without_tounicode: int
    pipeline_version: str
    shared_version: str
    rules_version: str
    # Additive to the LLD shape (last, defaulted): FR-ING-12's flag, which `aggregate` counts
    # without re-deriving it from a tally it no longer holds.
    mixed_standards: bool = False


@dataclass(frozen=True, slots=True)
class CharacterizationReport:  # a project's, or the offline tool's
    documents: int
    pages: int
    producers: Mapping[str, int]  # documents per producer family
    text_kinds: Mapping[PageKind, int]  # pages
    document_languages: Mapping[str, int]
    conventions: Mapping[Convention, int]  # documents by proposed convention
    symbol_standards: Mapping[SymbolStandard, int]
    plc_families: Mapping[PlcFamily, int]  # documents that use the family
    ocr_needed_ratio: float
    mixed_standard_documents: int
    versions: Mapping[str, tuple[str, ...]]  # every version seen, so a mixed set says so


def document_language(languages: Mapping[PageLanguage, int]) -> str:
    """``en`` | ``zh`` | ``bilingual`` | ``none`` from the pages' languages."""
    en = languages.get(PageLanguage.EN, 0)
    zh = languages.get(PageLanguage.ZH, 0)
    if languages.get(PageLanguage.MIXED, 0) > 0 or (en > 0 and zh > 0):
        return "bilingual"
    if zh > 0:
        return "zh"
    if en > 0:
        return "en"
    return "none"


def _count[K](keys: Sequence[K], order: Sequence[K]) -> dict[K, int]:
    """Counts in ``order`` (so the mapping's order never depends on the input's)."""
    counted = Counter(keys)
    return {key: counted[key] for key in order if counted[key] > 0}


def characterize(
    document_id: str,
    content_sha256: str,
    summary: PdfSummary,
    pages: Sequence[PageClassification],
    signals: Sequence[PageSignals],
    tally: ConventionTally,
    proposal: ProfileProposal,
    producers: Sequence[ProducerSignature],
    *,
    rules_version: str,
) -> Characterization:
    """``rules_version`` is keyword-only and additive to the LLD signature: neither the
    tally nor the proposal knows which rules produced them."""
    languages = _count([p.language for p in pages], list(PageLanguage))
    fonts = {
        font.name for page in signals for font in page.fonts if font.cid and not font.has_tounicode
    }
    return Characterization(
        document_id=document_id,
        content_sha256=content_sha256,
        producer_family=producer_family(summary, producers),
        creator=summary.creator,
        producer=summary.producer,
        page_count=summary.page_count,
        text_kinds=_count([p.kind for p in pages], list(PageKind)),
        page_languages=languages,
        document_language=document_language(languages),
        conventions=dict(ranked_conventions(tally.conventions)),
        symbol_standard_guess=proposal.profile.symbol_standard,
        symbol_standard_basis=proposal.basis.get("symbol", ()),
        plc_families={f: n for f, n in sorted(tally.plc_families.items()) if n > 0},
        cross_reference_formats=dict(sorted(tally.cross_reference_formats.items())),
        ocr_needed_pages=sum(1 for p in pages if p.needs_ocr),
        cid_fonts_without_tounicode=len(fonts),
        pipeline_version=PIPELINE_VERSION,
        shared_version=SHARED_VERSION,
        rules_version=rules_version,
        mixed_standards=proposal.mixed_standards,
    )


def aggregate(items: Sequence[Characterization]) -> CharacterizationReport:
    producers = Counter(i.producer_family or UNKNOWN_PRODUCER for i in items)
    text_kinds: Counter[PageKind] = Counter()
    plc: Counter[PlcFamily] = Counter()
    conventions: Counter[Convention] = Counter()
    for item in items:
        text_kinds.update(item.text_kinds)
        plc.update(f for f, n in item.plc_families.items() if n > 0)
        ranked = ranked_conventions(item.conventions)
        if ranked:
            conventions[ranked[0][0]] += 1
    pages = sum(i.page_count for i in items)
    ocr = sum(i.ocr_needed_pages for i in items)
    return CharacterizationReport(
        documents=len(items),
        pages=pages,
        producers=dict(sorted(producers.items())),
        text_kinds={k: text_kinds[k] for k in PageKind if text_kinds[k] > 0},
        document_languages=dict(sorted(Counter(i.document_language for i in items).items())),
        conventions={c: conventions[c] for c in Convention if conventions[c] > 0},
        symbol_standards=dict(
            sorted(
                Counter(i.symbol_standard_guess for i in items).items(),
                key=lambda kv: list(SymbolStandard).index(kv[0]),
            )
        ),
        plc_families={f: plc[f] for f in PlcFamily if plc[f] > 0},
        ocr_needed_ratio=ocr / pages if pages else 0.0,
        mixed_standard_documents=sum(1 for i in items if i.mixed_standards),
        versions={
            "pipeline": tuple(sorted({i.pipeline_version for i in items})),
            "shared": tuple(sorted({i.shared_version for i in items})),
            "rules": tuple(sorted({i.rules_version for i in items})),
        },
    )
