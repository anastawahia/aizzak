"""F0 QA additions (electrical-drawings INC-1): the boundaries the developer tests leave open.

Synthetic data only. ``_paths`` runs on real PyMuPDF pages built in memory; the rest is pure
domain and contract checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest
import yaml
from services.ele_parse.inspect import _paths

from app.api.v1.dto.electrical import CharacterizationOut, CharacterizationReportOut
from app.modules.electrical.drawings.domain.characterization import (
    UNKNOWN_SYMBOL_STANDARD,
    Characterization,
    aggregate,
)
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.pages import ClassificationThresholds, PageKind
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import SymbolStandard
from tests.unit.support_electrical import make_signals

T = ClassificationThresholds()
OPENAPI = Path(__file__).resolve().parents[2] / "docs" / "design" / "openapi.yaml"


# --------------------------------------------------------------------- _paths boundaries
def _page_with_bezier(doc: Any, width: float) -> Any:
    """A real (non-collinear) Bezier whose horizontal extent is ``width`` pt."""
    page = doc.new_page()
    shape = page.new_shape()
    shape.draw_bezier((0, 0), (0, 1), (width, 1), (width, 0))
    shape.finish()
    shape.commit()
    return page


def test_f0_qa_curve_extent_boundary_is_ten_points_inclusive() -> None:
    doc = pymupdf.open()
    assert _paths(_page_with_bezier(doc, 9.9)) == (1, 1)
    assert _paths(_page_with_bezier(doc, 10.0)) == (1, 1)  # <= 10 pt counts
    assert _paths(_page_with_bezier(doc, 10.5)) == (1, 0)  # a bigger curve is a drawing


def test_f0_qa_two_curves_in_one_path_are_two_curves_and_one_path() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    shape = page.new_shape()
    shape.draw_bezier((10, 10), (12, 8), (14, 8), (16, 10))
    shape.draw_bezier((16, 10), (18, 12), (20, 12), (22, 10))
    shape.finish()
    shape.commit()
    assert _paths(page) == (1, 2)


def test_f0_qa_a_page_with_no_drawings_has_no_paths() -> None:
    assert _paths(pymupdf.open().new_page()) == (0, 0)


def test_f0_qa_a_degenerate_curve_does_not_raise() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    shape = page.new_shape()
    shape.draw_bezier((100, 100), (100, 100), (100, 100), (100, 100))
    shape.finish()
    shape.commit()
    paths, curves = _paths(page)  # PyMuPDF may store it as a line; either way no crash
    assert paths == 1 and curves in (0, 1)


def test_f0_qa_curves_inside_a_form_xobject_are_read() -> None:
    src = pymupdf.open()
    src_page = src.new_page()
    shape = src_page.new_shape()
    shape.draw_bezier((10, 10), (12, 8), (14, 8), (16, 10))
    shape.finish()
    shape.commit()
    doc = pymupdf.open()
    page = doc.new_page()
    page.show_pdf_page(page.rect, src, 0)  # the drawing now sits in a form XObject
    assert _paths(page) == (1, 1)


# --------------------------------------------------------------------- classification
def test_f0_qa_pipeline_version_is_1_1_0() -> None:
    assert PIPELINE_VERSION == "1.1.0"  # F0-AC-5: no other test pinned the bump


def test_f0_qa_no_text_many_paths_and_a_small_image_is_outlined() -> None:
    # the shape of the real outlined drawings: no characters, thousands of paths, a logo
    page = make_signals(
        char_count=0,
        latin_char_count=0,
        word_count=0,
        vector_path_count=5000,
        short_curve_path_count=0,
        image_count=4,
        image_area_ratio=0.001,
    )
    found = classify_page(page, T)
    assert found.kind is PageKind.VECTOR_OUTLINED and found.needs_ocr


def test_f0_qa_a_big_image_beats_many_paths_and_an_image_only_page_is_not_empty() -> None:
    big = make_signals(
        char_count=0,
        latin_char_count=0,
        word_count=0,
        vector_path_count=5000,
        image_count=1,
        image_area_ratio=0.95,
    )
    assert classify_page(big, T).kind is PageKind.RASTER  # RASTER runs before the new rule
    nothing = make_signals(
        char_count=0, latin_char_count=0, word_count=0, vector_path_count=0, image_count=0
    )
    assert classify_page(nothing, T).kind is PageKind.EMPTY


# --------------------------------------------------------------------- failed_pages / report
def _record(**changes: Any) -> Characterization:
    fields: dict[str, Any] = {
        "document_id": "d",
        "content_sha256": "a" * 64,
        "producer_family": None,
        "creator": None,
        "producer": None,
        "page_count": 3,
        "text_kinds": {PageKind.VECTOR_TEXT: 2},
        "page_languages": {},
        "document_language": "none",
        "conventions": {},
        "symbol_standard_guess": None,
        "symbol_standard_basis": (),
        "plc_families": {},
        "cross_reference_formats": {},
        "ocr_needed_pages": 0,
        "cid_fonts_without_tounicode": 0,
        "pipeline_version": "1.1.0",
        "shared_version": "1",
        "rules_version": "1",
    }
    return Characterization(**{**fields, **changes})


def test_f0_qa_failed_pages_is_never_negative_and_zero_pages_is_zero() -> None:
    assert _record(page_count=0, text_kinds={}).failed_pages == 0
    # more classified pages than page_count: clamped to 0 (it hides the inconsistency)
    assert _record(page_count=1, text_kinds={PageKind.VECTOR_TEXT: 3}).failed_pages == 0
    assert _record(page_count=3, text_kinds={}).failed_pages == 3


def test_f0_qa_an_empty_project_aggregates_to_zero_and_no_symbol_standards() -> None:
    report = aggregate([])
    assert report.documents == 0 and report.failed_pages == 0 and report.pages == 0
    assert report.symbol_standards == {}


def test_f0_qa_symbol_standards_orders_known_standards_before_unknown() -> None:
    items = [
        _record(document_id="a", symbol_standard_guess=None),
        _record(document_id="b", symbol_standard_guess=SymbolStandard.GBT_4728),
        _record(document_id="c", symbol_standard_guess=SymbolStandard.IEC_60617),
    ]
    keys = list(aggregate(items).symbol_standards)
    assert keys == [SymbolStandard.IEC_60617.value, SymbolStandard.GBT_4728.value, "unknown"]
    assert keys[-1] == UNKNOWN_SYMBOL_STANDARD


# --------------------------------------------------------------------- the published contract
def test_f0_qa_the_api_schemas_and_openapi_agree_on_nullable_and_required() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]
    one, report = spec["CharacterizationOut"], spec["CharacterizationReportOut"]
    assert "failed_pages" in one["required"] and "failed_pages" in report["required"]
    assert "symbol_standard_guess" in one["required"]  # required, but may be null
    assert one["properties"]["symbol_standard_guess"]["nullable"] is True
    assert one["properties"]["failed_pages"]["type"] == "integer"
    assert report["properties"]["failed_pages"]["type"] == "integer"
    guess = CharacterizationOut.model_json_schema()["properties"]["symbol_standard_guess"]
    assert {"type": "null"} in guess["anyOf"]
    assert "failed_pages" in CharacterizationOut.model_json_schema()["required"]
    assert "failed_pages" in CharacterizationReportOut.model_json_schema()["required"]


@pytest.mark.parametrize("basis", [(), ("producer:eplan",)])
def test_f0_qa_an_empty_symbol_basis_is_valid_in_the_api_model(basis: tuple[str, ...]) -> None:
    out = CharacterizationOut(
        document_id="00000000-0000-0000-0000-000000000000",  # type: ignore[arg-type]
        content_sha256="a" * 64,
        producer_family=None,
        creator=None,
        producer=None,
        page_count=1,
        text_kinds={},
        page_languages={},
        document_language="none",
        conventions={},
        symbol_standard_guess=None,
        symbol_standard_basis=list(basis),
        plc_families={},
        cross_reference_formats={},
        ocr_needed_pages=0,
        cid_fonts_without_tounicode=0,
        pipeline_version="1.1.0",
        shared_version="1",
        rules_version="1",
        mixed_standards=False,
        failed_pages=1,
    )
    assert out.model_dump()["symbol_standard_guess"] is None
