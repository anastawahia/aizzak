"""Page classification (BT16, INC1-AC-7): one case per kind and its boundaries, the ambiguity
margin, OCR need, language and determinism (FR-ING-08, ADR-19)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.electrical.drawings.domain.classification import classify_language, classify_page
from app.modules.electrical.drawings.domain.pages import (
    ClassificationThresholds,
    PageKind,
    PageLanguage,
    StageReason,
    StageStatus,
)
from tests.unit.support_electrical import make_signals

T = ClassificationThresholds()


def kind_of(**changes: Any) -> PageKind:
    return classify_page(make_signals(**changes), T).kind


def empty(**changes: Any) -> dict[str, Any]:
    """A page with no text, paths or images, plus ``changes``."""
    base = {
        "char_count": 0,
        "latin_char_count": 0,
        "word_count": 0,
        "vector_path_count": 0,
    }
    return {**base, **changes}


# --- one case per rule, in the order the rules run ---------------------------------
def test_a_page_with_nothing_on_it_is_empty() -> None:
    found = classify_page(make_signals(**empty()), T)
    assert found.kind is PageKind.EMPTY and not found.needs_ocr
    assert found.status is StageStatus.SUCCEEDED and found.reason is None
    assert found.language is PageLanguage.NONE


def test_a_single_path_or_image_makes_it_not_empty() -> None:
    assert kind_of(**empty(vector_path_count=1)) is PageKind.VECTOR_TEXT
    assert kind_of(**empty(image_count=1, image_area_ratio=0.01)) is PageKind.VECTOR_TEXT


def test_a_scan_is_raster_and_needs_ocr() -> None:
    found = classify_page(make_signals(**empty(image_count=1, image_area_ratio=0.95)), T)
    assert found.kind is PageKind.RASTER and found.needs_ocr
    assert found.status is StageStatus.SUCCEEDED


def test_raster_needs_both_few_characters_and_enough_image_area() -> None:
    few = {"char_count": T.min_text_chars - 1, "latin_char_count": T.min_text_chars - 1}
    assert kind_of(**few, image_area_ratio=0.60, image_count=1) is PageKind.RASTER  # boundary in
    assert kind_of(**few, image_area_ratio=0.59, image_count=1) is not PageKind.RASTER
    enough = {"char_count": T.min_text_chars, "latin_char_count": T.min_text_chars}
    assert kind_of(**enough, image_area_ratio=0.95, image_count=1) is not PageKind.RASTER


def test_text_without_a_sane_unicode_mapping_is_garbled() -> None:
    garbled = classify_page(make_signals(unmapped_char_count=200, pua_char_count=50), T)
    assert garbled.kind is PageKind.VECTOR_GARBLED and garbled.needs_ocr
    assert kind_of(unmapped_char_count=100) is PageKind.VECTOR_GARBLED  # 100/500 = 0.20, boundary
    assert kind_of(unmapped_char_count=99) is not PageKind.VECTOR_GARBLED


def test_private_use_characters_count_towards_garbled() -> None:
    assert kind_of(pua_char_count=60, unmapped_char_count=40) is PageKind.VECTOR_GARBLED


def test_text_drawn_as_curves_is_outlined() -> None:
    low = {"char_count": 3, "latin_char_count": 3}
    found = classify_page(make_signals(**low, short_curve_path_count=2000), T)
    assert found.kind is PageKind.VECTOR_OUTLINED and found.needs_ocr
    assert kind_of(**low, short_curve_path_count=400) is PageKind.VECTOR_OUTLINED  # boundary in
    assert kind_of(**low, short_curve_path_count=399) is PageKind.VECTOR_TEXT
    # a page with real text and many curves is not outlined: the text is readable
    assert kind_of(short_curve_path_count=2000) is not PageKind.VECTOR_OUTLINED


def test_f0_ac_5_many_vector_paths_with_little_text_are_outlined() -> None:
    low = {"char_count": 3, "latin_char_count": 3}
    assert kind_of(**low, vector_path_count=400) is PageKind.VECTOR_OUTLINED  # boundary in
    assert kind_of(**low, vector_path_count=399) is PageKind.VECTOR_TEXT
    no_text = classify_page(make_signals(**empty(vector_path_count=5000)), T)
    assert no_text.kind is PageKind.VECTOR_OUTLINED and no_text.needs_ocr
    assert no_text.status.value != "needs_review"  # near the paths threshold is not ambiguous
    near = classify_page(make_signals(**low, vector_path_count=401), T)
    assert near.kind is PageKind.VECTOR_OUTLINED and near.reason is None


def test_f0_ac_5_the_paths_arm_uses_the_configured_threshold() -> None:
    custom = ClassificationThresholds(outlined_curves_min=50)
    low = make_signals(char_count=3, latin_char_count=3, vector_path_count=50)
    assert classify_page(low, custom).kind is PageKind.VECTOR_OUTLINED
    below = make_signals(char_count=3, latin_char_count=3, vector_path_count=49)
    assert classify_page(below, custom).kind is PageKind.VECTOR_TEXT


def test_f0_ac_5_sound_text_with_many_paths_stays_vector_text_and_other_kinds_hold() -> None:
    assert kind_of(vector_path_count=5000) is PageKind.VECTOR_TEXT  # not MIXED
    # RASTER and VECTOR_GARBLED come before the new rule
    scan = {"char_count": 3, "latin_char_count": 3, "image_count": 1, "image_area_ratio": 0.9}
    assert kind_of(**scan, vector_path_count=5000) is PageKind.RASTER
    garbled = {"char_count": 100, "unmapped_char_count": 100, "vector_path_count": 5000}
    assert kind_of(**garbled) is PageKind.VECTOR_GARBLED


def test_sound_text_with_a_big_image_or_many_curves_is_mixed() -> None:
    by_image = classify_page(make_signals(image_count=1, image_area_ratio=0.30), T)
    assert by_image.kind is PageKind.MIXED and by_image.needs_ocr
    assert kind_of(image_count=1, image_area_ratio=0.29) is PageKind.VECTOR_TEXT
    assert kind_of(short_curve_path_count=200) is PageKind.MIXED  # half of 400
    assert kind_of(short_curve_path_count=199) is PageKind.VECTOR_TEXT


def test_a_clean_vector_page_is_vector_text() -> None:
    found = classify_page(make_signals(), T)
    assert found.kind is PageKind.VECTOR_TEXT and not found.needs_ocr
    assert found.page_no == 1 and found.reason is None


def test_the_first_matching_rule_decides() -> None:
    # RASTER before GARBLED: a scan with a little garbled text is still a scan
    both = make_signals(
        char_count=10,
        latin_char_count=2,
        unmapped_char_count=8,
        image_count=1,
        image_area_ratio=0.9,
    )
    assert classify_page(both, T).kind is PageKind.RASTER
    # GARBLED before OUTLINED
    garbled_curves = make_signals(
        char_count=10, latin_char_count=2, unmapped_char_count=8, short_curve_path_count=900
    )
    assert classify_page(garbled_curves, T).kind is PageKind.VECTOR_GARBLED


# --- the ambiguity margin ----------------------------------------------------------
def test_a_garbled_ratio_near_its_threshold_needs_review_and_keeps_the_ruling_kind() -> None:
    near_below = classify_page(make_signals(unmapped_char_count=60), T)  # 0.12, within 0.10
    assert near_below.kind is PageKind.VECTOR_TEXT
    assert near_below.status is StageStatus.NEEDS_REVIEW
    assert near_below.reason is StageReason.AMBIGUOUS_CLASSIFICATION
    near_above = classify_page(make_signals(unmapped_char_count=140), T)  # 0.28
    assert near_above.kind is PageKind.VECTOR_GARBLED
    assert near_above.status is StageStatus.NEEDS_REVIEW
    far = classify_page(make_signals(unmapped_char_count=5), T)  # 0.01
    assert far.status is StageStatus.SUCCEEDED and far.reason is None


def test_an_image_ratio_near_its_threshold_matters_only_for_a_page_with_little_text() -> None:
    few = {"char_count": 5, "latin_char_count": 5, "image_count": 1}
    near = classify_page(make_signals(**few, image_area_ratio=0.55), T)
    assert near.kind is PageKind.VECTOR_TEXT and near.status is StageStatus.NEEDS_REVIEW
    clear = classify_page(make_signals(**few, image_area_ratio=0.95), T)
    assert clear.kind is PageKind.RASTER and clear.status is StageStatus.SUCCEEDED
    texty = classify_page(make_signals(image_count=1, image_area_ratio=0.55), T)
    assert texty.status is StageStatus.SUCCEEDED  # plenty of text: not in question


def test_the_margin_is_a_threshold() -> None:
    tight = ClassificationThresholds(ambiguity_margin=0.0)
    signals = make_signals(unmapped_char_count=60)
    assert classify_page(signals, tight).status is StageStatus.SUCCEEDED
    assert classify_page(signals, T).status is StageStatus.NEEDS_REVIEW


def test_an_empty_page_is_never_ambiguous() -> None:
    assert classify_page(make_signals(**empty()), T).status is StageStatus.SUCCEEDED


# --- language (ADR-19) -------------------------------------------------------------
@pytest.mark.parametrize(
    ("cjk", "latin", "expected"),
    [
        (0, 0, PageLanguage.NONE),
        (0, 300, PageLanguage.EN),
        (10, 300, PageLanguage.EN),  # 3 % Chinese: noise
        (45, 255, PageLanguage.MIXED),  # 15 % : the mix boundary
        (44, 256, PageLanguage.EN),
        (150, 150, PageLanguage.MIXED),  # 50 % Chinese, 50 % Latin
        (300, 0, PageLanguage.ZH),
        (290, 10, PageLanguage.ZH),  # 3 % Latin: noise
        (270, 30, PageLanguage.ZH),  # 10 % Latin, below the mix share
        (255, 45, PageLanguage.MIXED),  # 15 % Latin
    ],
)
def test_language_follows_the_share_of_each_script(
    cjk: int, latin: int, expected: PageLanguage
) -> None:
    assert (
        classify_language(make_signals(cjk_char_count=cjk, latin_char_count=latin), T) is expected
    )


def test_the_thresholds_move_the_language_boundaries() -> None:
    strict = ClassificationThresholds(cjk_share=0.5, script_mix_share=0.3)
    signals = make_signals(cjk_char_count=100, latin_char_count=200)  # 33 % Chinese
    assert classify_language(signals, T) is PageLanguage.MIXED
    assert classify_language(signals, strict) is PageLanguage.MIXED  # >= 0.3 but < 0.5
    assert classify_language(make_signals(cjk_char_count=10, latin_char_count=90), strict) is (
        PageLanguage.EN
    )


def test_classification_carries_the_language() -> None:
    zh = classify_page(make_signals(cjk_char_count=480, latin_char_count=0), T)
    assert zh.language is PageLanguage.ZH


# --- determinism -------------------------------------------------------------------
def test_the_same_signals_give_the_same_classification() -> None:
    signals = make_signals(unmapped_char_count=60, cjk_char_count=100)
    assert classify_page(signals, T) == classify_page(signals, T)
    assert hash(classify_page(signals, T)) == hash(classify_page(signals, T))
