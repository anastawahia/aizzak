"""Page classification (FR-ING-08, ADR-19): pure and deterministic. Stdlib only.

The rules run in order and the first that holds decides the kind:

1. ``EMPTY``: no text, no vector paths and no images.
2. ``RASTER``: fewer than ``min_text_chars`` characters and images covering at least
   ``raster_area_ratio`` of the page.
3. ``VECTOR_GARBLED``: ``(unmapped + pua) / char_count >= garbled_ratio``.
4. ``VECTOR_OUTLINED``: fewer than ``min_text_chars`` characters and at least
   ``outlined_curves_min`` stroke-sized curves or vector paths (one threshold for both).
5. ``MIXED``: sound text, and the image signal or the curve signal above HALF its threshold.
6. ``VECTOR_TEXT``.

A page is ``NEEDS_REVIEW`` (reason ``ambiguous_classification``) when a ratio that bears on its
kind lies within ``ambiguity_margin`` of its threshold: the garbled ratio (when the page has any
text) and the image-area ratio (when the page has little text). The kind stays the deciding
rule's. The thresholds are starting values the characterization report tunes (INC1-R-3).
"""

from __future__ import annotations

from app.modules.electrical.drawings.domain.pages import (
    ClassificationThresholds,
    PageClassification,
    PageKind,
    PageLanguage,
    PageSignals,
    StageReason,
    StageStatus,
)

_NEEDS_OCR = frozenset({PageKind.RASTER, PageKind.VECTOR_OUTLINED, PageKind.VECTOR_GARBLED})


def classify_language(signals: PageSignals, t: ClassificationThresholds) -> PageLanguage:
    """From the share of Chinese and Latin letters among the two (ADR-19)."""
    total = signals.cjk_char_count + signals.latin_char_count
    if total <= 0:
        return PageLanguage.NONE
    cjk = signals.cjk_char_count / total
    latin = signals.latin_char_count / total
    if cjk >= t.cjk_share:
        return PageLanguage.MIXED if latin >= t.script_mix_share else PageLanguage.ZH
    if cjk >= t.script_mix_share:
        return PageLanguage.MIXED
    return PageLanguage.EN


def _garbled_ratio(signals: PageSignals) -> float:
    if signals.char_count <= 0:
        return 0.0
    return (signals.unmapped_char_count + signals.pua_char_count) / signals.char_count


def _kind(signals: PageSignals, t: ClassificationThresholds) -> PageKind:
    little_text = signals.char_count < t.min_text_chars
    if signals.char_count == 0 and signals.vector_path_count == 0 and signals.image_count == 0:
        return PageKind.EMPTY
    if little_text and signals.image_area_ratio >= t.raster_area_ratio:
        return PageKind.RASTER
    if signals.char_count > 0 and _garbled_ratio(signals) >= t.garbled_ratio:
        return PageKind.VECTOR_GARBLED
    if little_text and (
        signals.short_curve_path_count >= t.outlined_curves_min
        or signals.vector_path_count >= t.outlined_curves_min
    ):
        return PageKind.VECTOR_OUTLINED
    if not little_text and (
        signals.image_area_ratio >= t.raster_area_ratio / 2
        or signals.short_curve_path_count >= t.outlined_curves_min / 2
    ):
        return PageKind.MIXED
    return PageKind.VECTOR_TEXT


def _ambiguous(kind: PageKind, signals: PageSignals, t: ClassificationThresholds) -> bool:
    if kind is PageKind.EMPTY:
        return False
    near_garbled = (
        signals.char_count > 0
        and abs(_garbled_ratio(signals) - t.garbled_ratio) <= t.ambiguity_margin
    )
    near_raster = (
        signals.char_count < t.min_text_chars
        and abs(signals.image_area_ratio - t.raster_area_ratio) <= t.ambiguity_margin
    )
    return near_garbled or near_raster


def classify_page(signals: PageSignals, t: ClassificationThresholds) -> PageClassification:
    kind = _kind(signals, t)
    ambiguous = _ambiguous(kind, signals, t)
    return PageClassification(
        page_no=signals.page_no,
        kind=kind,
        language=classify_language(signals, t),
        needs_ocr=kind in _NEEDS_OCR or kind is PageKind.MIXED,
        status=StageStatus.NEEDS_REVIEW if ambiguous else StageStatus.SUCCEEDED,
        reason=StageReason.AMBIGUOUS_CLASSIFICATION if ambiguous else None,
    )
