"""FR-ID-09 / INC1-AC-4: the three forms of an identifier (original, canonical, search keys)."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.normalization import (
    canonical_form,
    normalize,
    strip_leading_zeros,
)
from tests.unit.support_electrical_shared import fw


def test_the_original_is_kept_exactly_as_it_appeared() -> None:
    for text in (fw("KM1"), " -K5 ", fw("XT1:5"), "x 1 . 14"):
        assert normalize(text).original == text


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        (fw("KM1"), "KM1"),
        (fw("XT1:5"), "XT1:5"),
        (fw("-K5"), "-K5"),
        ("km1", "KM1"),
        ("= A1 + P2 - K5", "=A1+P2-K5"),
        ("X1" + chr(0x2013) + chr(0x2014) + "14", "X1--14"),  # en dash, em dash
        ("X1" + chr(0x2212) + "14", "X1-14"),  # minus sign
        ("K" + chr(0x200B) + "M1", "KM1"),  # zero-width space is dropped
        (fw("%I0.0"), "%I0.0"),
    ],
)
def test_the_canonical_form_folds_width_dashes_space_and_case(text: str, canonical: str) -> None:
    assert normalize(text).canonical == canonical
    assert canonical_form(text) == canonical


def test_search_keys_drop_the_leading_dash_and_zeros_but_never_a_separator() -> None:
    keys = normalize("-X01:014").search_keys
    assert keys == ("-X01:014", "X01:014", "X1:14")


def test_f0001_f_0001_and_f_minus_0001_share_a_search_key() -> None:
    # T-21: F0001 = F 0001 = F-0001
    shared = {normalize(t).search_keys[-1] for t in ("F0001", "F 0001", "F-0001")}
    assert shared == {"F1"}


@pytest.mark.parametrize(
    ("text", "stripped"),
    [
        ("F0001", "F1"),
        ("10.05", "10.5"),
        ("0V", "0V"),
        ("100", "100"),
        ("0.0", "0.0"),
        ("007", "7"),
    ],
)
def test_leading_zeros_go_but_a_lone_zero_stays(text: str, stripped: str) -> None:
    assert strip_leading_zeros(text) == stripped


def test_search_keys_are_unique_non_empty_and_start_with_the_canonical_form() -> None:
    for text in ("-K5", "KM1", "X1.14", "12.3", "0V", "+24V"):
        forms = normalize(text)
        assert forms.search_keys[0] == forms.canonical
        assert len(set(forms.search_keys)) == len(forms.search_keys)
        assert all(forms.search_keys)


def test_normalising_is_deterministic_and_idempotent_on_the_canonical_form() -> None:
    for text in (fw("KM1"), "x1.14", "-K05"):
        assert normalize(text) == normalize(text)
        assert normalize(normalize(text).canonical).canonical == normalize(text).canonical
