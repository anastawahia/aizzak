"""Unit tests for the knowledge document parsers (3.k1 — docs/migration/refs/
parsers.md). No committed binary fixtures: PDF/Excel/image inputs are
synthesized in-test via PyMuPDF/openpyxl/Pillow. Plain unit tests — no
``integration`` marker, no Docker — but they require the ``parsers`` optional
dependency group (``pip install -e ".[dev,parsers]"``)."""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Callable

import fitz
import openpyxl
import pytesseract
import pytest
from PIL import Image

from app.framework.errors import UnsupportedTypeError, ValidationError
from app.modules.knowledge.adapters.parsers import (
    excel,
    image_ocr,
    json_doc,
    pdf_tables,
    pdf_text,
    text_plain,
)
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.ports.content_extractor import ContentExtractor, ParsedChunkKind


# --------------------------------------------------------------------------- #
# json_doc — table patterns, classification, encoding                        #
# --------------------------------------------------------------------------- #
def test_parse_json_empty_object_yields_no_chunks() -> None:
    assert json_doc.parse_json(b"{}") == []


def test_parse_json_list_of_dict_table_pattern() -> None:
    data = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    chunks = json_doc.parse_json(json.dumps(data).encode())

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ParsedChunkKind.TABLE
    assert chunk.order == 0
    assert chunk.metadata["source_type"] == "dict_rows"
    assert chunk.metadata["headers"] == ["a", "b"]
    assert json.loads(chunk.text)["rows"] == data


def test_parse_json_grid_list_of_str_table_pattern() -> None:
    data = ["Name\tAge", "Ali\t30", "Sara\t25"]
    chunks = json_doc.parse_json(json.dumps(data).encode())

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ParsedChunkKind.TABLE
    assert chunk.metadata["source_type"] == "structured_text"
    assert chunk.metadata["headers"] == ["Column_1", "Column_2"]
    assert chunk.metadata["num_rows"] == 3


def test_parse_json_kv_scalar_table_pattern() -> None:
    data = {"name": "Ali", "age": 30, "city": "Riyadh", "active": True}
    chunks = json_doc.parse_json(json.dumps(data).encode())

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ParsedChunkKind.TABLE
    assert chunk.metadata["headers"] == ["Key", "Value"]
    assert chunk.metadata["num_rows"] == 4


def test_parse_json_embedded_text_table_inside_string_value() -> None:
    # a table pattern nested INSIDE a dict value (exercises the recursive walk).
    data = {"report": "Name\tAge\nAli\t30\nSara\t25\nOmar\t40"}
    chunks = json_doc.parse_json(json.dumps(data).encode())

    assert len(chunks) == 1
    assert chunks[0].kind is ParsedChunkKind.TABLE
    assert chunks[0].metadata["path"] == "$.report"
    assert chunks[0].metadata["num_rows"] == 4


def test_parse_json_unstructured_text_only() -> None:
    data = {"title": "Hello", "body": "This is a plain description with some prose."}
    chunks = json_doc.parse_json(json.dumps(data).encode())

    assert len(chunks) == 2
    assert all(c.kind is ParsedChunkKind.JSON for c in chunks)
    assert [c.order for c in chunks] == [0, 1]

    n_tables = sum(1 for c in chunks if c.kind is ParsedChunkKind.TABLE)
    n_texts = sum(1 for c in chunks if c.kind is ParsedChunkKind.JSON)
    assert json_doc.classify_json(table_count=n_tables, text_count=n_texts) == "unstructured_json"


@pytest.mark.parametrize(
    ("table_count", "text_count", "expected"),
    [
        (0, 0, "empty_json"),
        (2, 0, "structured_json"),
        (0, 2, "unstructured_json"),
        (8, 2, "structured_json"),  # ratio 0.8 > 0.6
        (3, 7, "semi_structured_json"),  # ratio 0.3, > 0.2
        (1, 9, "unstructured_json"),  # ratio 0.1 <= 0.2
    ],
)
def test_classify_json_thresholds(table_count: int, text_count: int, expected: str) -> None:
    assert json_doc.classify_json(table_count=table_count, text_count=text_count) == expected


def test_decode_bytes_round_trips_utf16() -> None:
    text = "بيانات تجريبية"
    assert json_doc._decode_bytes(text.encode("utf-16")) == text


def test_decode_bytes_round_trips_cp1256() -> None:
    text = "مرحبا"
    assert json_doc._decode_bytes(text.encode("cp1256")) == text


def test_parse_json_never_raises_on_undecodable_looking_bytes() -> None:
    # the candidate chain always has a lossless last resort (latin-1 / utf-8
    # with replacement) — malformed-looking bytes must never raise.
    chunks = json_doc.parse_json(b'{"note": "caf\xe9"}')
    assert isinstance(chunks, list)


# --------------------------------------------------------------------------- #
# text_plain — encoding, clean_text, coarse split, Arabic punctuation         #
# --------------------------------------------------------------------------- #
def test_parse_text_basic_ascii() -> None:
    chunks = text_plain.parse_text(b"Hello world, this is a test.", ".txt")

    assert len(chunks) == 1
    assert chunks[0].order == 0
    assert chunks[0].kind is ParsedChunkKind.TEXT
    assert chunks[0].metadata["source_ext"] == ".txt"
    assert "Hello world" in chunks[0].text


def test_parse_text_empty_bytes_yields_no_chunks() -> None:
    assert text_plain.parse_text(b"", ".txt") == []


@pytest.mark.parametrize("ext", [".txt", ".md", ".csv"])
def test_parse_text_uniform_across_routed_extensions(ext: str) -> None:
    chunks = text_plain.parse_text(b"col1,col2\nval1,val2", ext)
    assert len(chunks) == 1
    assert chunks[0].kind is ParsedChunkKind.TEXT
    assert chunks[0].metadata["source_ext"] == ext


def test_clean_text_strips_page_numbers_and_noise() -> None:
    cleaned = text_plain.clean_text("Page 12\n----- noise ----- \n\n\nReal content")
    assert "Page 12" not in cleaned
    assert "Real content" in cleaned


def test_split_long_text_keeps_short_text_as_single_piece() -> None:
    assert text_plain._split_long_text("short text", max_chars=2000) == ["short text"]


def test_split_long_text_splits_oversized_line_on_arabic_punctuation() -> None:
    line = "جملة أولى، جملة ثانية؛ جملة ثالثة"
    pieces = text_plain._split_long_text(line, max_chars=15)
    assert pieces == ["جملة أولى،", "جملة ثانية؛", "جملة ثالثة"]


def test_parse_text_deterministic_split_index_as_order() -> None:
    long_text = "\n\n".join(f"Paragraph number {i} with some filler content." for i in range(200))
    chunks = text_plain.parse_text(long_text.encode(), ".md")

    assert len(chunks) > 1
    assert [c.order for c in chunks] == list(range(len(chunks)))
    assert all(c.metadata["split_index"] == c.order for c in chunks)


# --------------------------------------------------------------------------- #
# excel — sheet segmentation, _split_large_table boundary, empty workbook     #
# --------------------------------------------------------------------------- #
def _workbook_bytes(build: Callable[[openpyxl.Workbook], None]) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_emits_one_chunk_per_sheet_table() -> None:
    def build(wb: openpyxl.Workbook) -> None:
        ws = wb.active
        ws.title = "Data"
        ws.append(["ID", "Value"])
        for i in range(3):
            ws.append([i, i * 2])

    chunks = excel.parse_excel(_workbook_bytes(build))

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ParsedChunkKind.TABLE
    assert chunk.order == 0
    assert chunk.metadata["sheet_name"] == "Data"
    assert chunk.metadata["num_rows"] == 3
    assert json.loads(chunk.text)["headers"] == ["ID", "Value"]


def test_parse_excel_splits_large_table_at_max_rows_per_chunk_boundary() -> None:
    def build(wb: openpyxl.Workbook) -> None:
        ws = wb.active
        ws.title = "Big"
        ws.append(["ID", "Value"])
        for i in range(excel._MAX_ROWS_PER_CHUNK + 1):
            ws.append([i, i * 2])

    chunks = excel.parse_excel(_workbook_bytes(build))

    assert len(chunks) == 2
    assert chunks[0].metadata["num_rows"] == excel._MAX_ROWS_PER_CHUNK
    assert chunks[1].metadata["num_rows"] == 1
    assert chunks[0].metadata["source_type"] == "excel_sheet_split_chunk"
    assert chunks[1].order == chunks[0].order + 1


def test_parse_excel_empty_workbook_yields_no_chunks() -> None:
    buf = io.BytesIO()
    openpyxl.Workbook().save(buf)  # a fresh workbook's single default sheet is empty
    assert excel.parse_excel(buf.getvalue()) == []


def test_parse_excel_multiple_sheets_order_by_sheet_then_segment() -> None:
    def build(wb: openpyxl.Workbook) -> None:
        first = wb.active
        first.title = "First"
        first.append(["A", "B"])
        for i in range(2):
            first.append([i, i])
        second = wb.create_sheet("Second")
        second.append(["A", "B"])
        for i in range(2):
            second.append([i, i])

    chunks = excel.parse_excel(_workbook_bytes(build))
    assert len(chunks) == 2
    assert chunks[0].order < chunks[1].order
    assert chunks[0].metadata["sheet_name"] == "First"
    assert chunks[1].metadata["sheet_name"] == "Second"


# --------------------------------------------------------------------------- #
# pdf_text — block granularity, structural order, noise guards                 #
# --------------------------------------------------------------------------- #
# Each `insert_text` at its own y position lays out as its own fitz block, so
# a page's block list is exactly the lines written to it, in that order.
def _blocks_pdf(pages: list[list[str]]) -> bytes:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 72 + index * 120), line)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_parse_pdf_text_single_block() -> None:
    text = (
        "Hello world, this is a longer test page with enough characters. "
        "عربي نص تجريبي طويل بما فيه الكفاية."
    )
    chunks = pdf_text.parse_pdf_text(_blocks_pdf([[text]]))

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.order == 0
    assert chunk.kind is ParsedChunkKind.TEXT
    assert chunk.metadata["page_number"] == 1
    assert chunk.metadata["position_in_doc"] == 0
    assert chunk.metadata["source_ext"] == ".pdf"
    assert "Hello world" in chunk.text


def test_parse_pdf_text_emits_one_chunk_per_block_not_per_page() -> None:
    """The granularity change of plan step 2 (decision س-08): a page with three
    paragraphs is three chunks, each carrying only its own paragraph."""
    chunks = pdf_text.parse_pdf_text(
        _blocks_pdf(
            [
                [
                    "First paragraph block with plenty of characters here.",
                    "Second paragraph block, also long enough to survive.",
                    "Third paragraph block, still well past the threshold.",
                ]
            ]
        )
    )

    assert len(chunks) == 3
    assert [c.order for c in chunks] == [0, 1, 2]
    assert [c.metadata["position_in_doc"] for c in chunks] == [0, 1, 2]
    assert [c.metadata["chunk_index"] for c in chunks] == [0, 1, 2]
    assert all(c.metadata["page_number"] == 1 for c in chunks)
    assert chunks[0].text.startswith("First")
    assert "Second" not in chunks[0].text


def test_parse_pdf_text_orders_blocks_on_a_page_strided_axis() -> None:
    """`order` is a document-wide block rank: page 2's first block sorts after
    every block of page 1, on the same axis `pdf_tables.py` emits on."""
    chunks = pdf_text.parse_pdf_text(
        _blocks_pdf(
            [
                [
                    "Page one, block one, comfortably above the threshold.",
                    "Page one, block two, also above the threshold.",
                ],
                ["Page two, block one, comfortably above the threshold."],
            ]
        )
    )

    assert [c.order for c in chunks] == [0, 1, 1000]
    assert [c.metadata["page_number"] for c in chunks] == [1, 1, 2]
    assert [c.metadata["block_index"] for c in chunks] == [0, 1, 0]
    assert [c.order for c in chunks] == sorted(c.order for c in chunks)


def test_parse_pdf_text_skips_blocks_below_min_block_chars() -> None:
    """The threshold moved from the page to the block, and dropping a block
    must NOT renumber the ones that survive it — `order` and `position_in_doc`
    are structural, so the surviving block keeps the rank of its position."""
    chunks = pdf_text.parse_pdf_text(
        _blocks_pdf([["short", "A second block that is long enough to be kept."]])
    )

    assert len(chunks) == 1
    assert chunks[0].order == 1
    assert chunks[0].metadata["block_index"] == 1
    assert chunks[0].metadata["position_in_doc"] == 1
    assert chunks[0].metadata["chunk_index"] == 0


def test_parse_pdf_text_skips_a_page_of_only_short_blocks() -> None:
    assert pdf_text.parse_pdf_text(_blocks_pdf([["short", "tiny"]])) == []


def test_parse_pdf_text_carries_the_block_rectangle_in_fitz_coordinates() -> None:
    chunks = pdf_text.parse_pdf_text(
        _blocks_pdf([["A block whose rectangle should be reported back."]])
    )

    bbox = chunks[0].metadata["block_bbox"]
    assert len(bbox) == 4
    assert all(isinstance(v, float) for v in bbox)
    # fitz origin is top-left with y growing downward, so y0 < y1 and a block
    # written near the top of the page has a small y0.
    assert bbox[0] < bbox[2]
    assert bbox[1] < bbox[3]
    assert bbox[1] < 100


@pytest.mark.parametrize(
    "block",
    [
        pytest.param((0.0, 0.0, 1.0, 1.0), id="malformed_tuple_too_short"),
        pytest.param(
            (0.0, 0.0, 1.0, 1.0, "<image: DeviceRGB, width 800, height 600, bpc 8>", 0, 1),
            id="image_block",
        ),
    ],
)
def test_block_text_rejects_unusable_blocks(block: tuple[object, ...]) -> None:
    """Both guards are defensive — the pinned PyMuPDF emits neither shape — but
    an image block's synthetic placeholder is longer than MIN_BLOCK_CHARS and
    would be indexed as prose if the `blocks` flags ever changed."""
    assert pdf_text._block_text(block) == ""


def test_parse_pdf_text_skips_an_encrypted_pdf() -> None:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "A secret block, long enough to be kept.")
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()

    assert pdf_text.parse_pdf_text(data) == []


def test_parse_pdf_text_degrades_gracefully_on_corrupt_bytes() -> None:
    assert pdf_text.parse_pdf_text(b"not a pdf") == []


# --------------------------------------------------------------------------- #
# pdf_tables — stream detection, noise guards, cross-page merge, captions      #
# --------------------------------------------------------------------------- #
# Camelot's `stream` flavor infers columns from whitespace, so a fixture only
# has to lay text out in aligned columns. `caption_gap` controls how far above
# the table the caption sits: too close and `edge_tol=50` swallows it INTO the
# table (its text becomes row 0, i.e. the headers) — a real property of the
# flavor, not of this port, and the reason the merge fixture below has no
# caption at all.
_TABLE_COLUMN_X = (72, 202, 332)


def _table_page(doc: fitz.Document, rows: list[tuple[str, ...]], caption: str | None) -> None:
    page = doc.new_page()
    if caption is not None:
        page.insert_text((72, 100), caption, fontsize=13)
    y = 200.0
    for row in rows:
        for x, cell in zip(_TABLE_COLUMN_X, row, strict=False):
            page.insert_text((x, y), cell, fontsize=11)
        y += 22


def _table_pdf_bytes(pages: list[list[tuple[str, ...]]], caption: str | None = None) -> bytes:
    doc = fitz.open()
    for rows in pages:
        _table_page(doc, rows, caption)
    data: bytes = doc.tobytes()
    doc.close()
    return data


_SALARY_ROWS = [
    ("Name", "Dept", "Salary"),
    ("Ali", "Finance", "5000"),
    ("Sara", "HR", "6200"),
    ("Omar", "IT", "7100"),
]


def test_parse_pdf_tables_extracts_a_structured_table() -> None:
    chunks, regions = pdf_tables.parse_pdf_tables(_table_pdf_bytes([_SALARY_ROWS]))

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ParsedChunkKind.TABLE
    assert chunk.order == 0
    assert chunk.metadata["page_number"] == 1
    assert chunk.metadata["source_ext"] == ".pdf"
    assert chunk.metadata["section_type"] == "structured_table"
    assert chunk.metadata["layout_mode"] == "camelot_stream"
    assert chunk.metadata["is_merged_table"] is False
    assert chunk.metadata["headers"] == ["Name", "Dept", "Salary"]
    assert chunk.metadata["total_rows"] == 3

    payload = json.loads(chunk.text)
    assert payload["headers"] == ["Name", "Dept", "Salary"]
    assert payload["rows"][0] == {"Name": "Ali", "Dept": "Finance", "Salary": "5000"}

    assert list(regions) == [1]
    assert regions[1][0].page_number == 1
    assert len(regions[1][0].bbox) == 4


def test_parse_pdf_tables_reads_the_caption_above_the_table_into_title() -> None:
    data = _table_pdf_bytes([_SALARY_ROWS], caption="Employee Salary Table")

    chunks, _regions = pdf_tables.parse_pdf_tables(data)

    assert len(chunks) == 1
    # The caption is metadata only — it is deliberately NOT prepended to the
    # chunk text (plan §7: no metadata injection into node text).
    assert chunks[0].metadata["title"] == "Employee Salary Table"
    assert "Employee Salary Table" not in chunks[0].text


def test_parse_pdf_tables_merges_a_table_spanning_two_pages() -> None:
    data = _table_pdf_bytes(
        [
            [("Name", "Dept", "Salary"), ("Ali", "Finance", "5000"), ("Sara", "HR", "6200")],
            [("Name", "Dept", "Salary"), ("Omar", "IT", "7100"), ("Lina", "Legal", "6800")],
        ]
    )

    chunks, regions = pdf_tables.parse_pdf_tables(data)

    # One table, not two — the whole point of the cross-page merge.
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.metadata["is_merged_table"] is True
    assert chunk.metadata["source_pages"] == [1, 2]
    assert chunk.metadata["total_source_tables"] == 2
    assert chunk.metadata["headers"] == ["Name", "Dept", "Salary"]

    # The repeated header on page 2 is dropped, not merged in as a data row.
    names = [row["Name"] for row in json.loads(chunk.text)["rows"]]
    assert names == ["Ali", "Sara", "Omar", "Lina"]

    # A merged table occupies a region on every page it spans — the text pass
    # must avoid all of them (plan step 3).
    assert sorted(regions) == [1, 2]


def test_parse_pdf_tables_drops_prose_misdetected_as_a_table() -> None:
    doc = fitz.open()
    page = doc.new_page()
    y = 100.0
    for _ in range(12):
        page.insert_text(
            (72, y),
            "This is an ordinary paragraph of running prose with no table structure.",
            fontsize=11,
        )
        y += 20
    data = doc.tobytes()
    doc.close()

    # A single column of prose must not survive as a table: it would be indexed
    # garbled AND removed from the text pass, which avoids table regions.
    assert pdf_tables.parse_pdf_tables(data) == ([], {})


def test_parse_pdf_tables_skips_an_encrypted_pdf() -> None:
    doc = fitz.open()
    _table_page(doc, _SALARY_ROWS, None)
    data: bytes = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret")
    doc.close()

    assert pdf_tables.parse_pdf_tables(data) == ([], {})


def test_parse_pdf_tables_degrades_gracefully_on_corrupt_bytes() -> None:
    assert pdf_tables.parse_pdf_tables(b"not a pdf") == ([], {})


@pytest.mark.parametrize(
    ("cols1", "cols2", "expected"),
    [
        (["Name", "Dept"], ["Name", "Dept"], 1.0),  # identical
        (["Name", "Dept"], ["name", " dept "], 1.0),  # case/whitespace insensitive
        (["Name", "Dept"], ["Employee Name", "Dept"], 0.85),  # containment scores 0.7
        (["Name", "Dept"], ["Salary", "Year"], 0.0),  # unrelated
        (["Name", "Dept"], ["Name"], 0.0),  # different arity is never a match
        ([], [], 0.0),
    ],
)
def test_column_similarity_calibration(cols1: list[str], cols2: list[str], expected: float) -> None:
    assert pdf_tables._column_similarity(cols1, cols2) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# image_ocr — dedup, quality gate, upscaling, graceful degradation            #
# --------------------------------------------------------------------------- #
def test_sha1_digest_is_stable_and_distinguishes_content() -> None:
    a = image_ocr.sha1_digest(b"same-bytes")
    b = image_ocr.sha1_digest(b"same-bytes")
    c = image_ocr.sha1_digest(b"different-bytes")
    assert a == b
    assert a != c


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("هذا نص عربي حقيقي وواضح", True),
        ("!@#$ %^&* () __ --", False),
        ("hi", False),
        ("", False),
    ],
)
def test_is_meaningful_text(text: str, expected: bool) -> None:
    assert image_ocr.is_meaningful_text(text) is expected


def test_upscale_if_small_doubles_small_images() -> None:
    img = Image.new("L", (100, 50), color=255)
    assert image_ocr._upscale_if_small(img).size == (200, 100)


def test_upscale_if_small_leaves_large_images_untouched() -> None:
    img = Image.new("L", (1200, 800), color=255)
    assert image_ocr._upscale_if_small(img).size == (1200, 800)


def test_run_ocr_upscales_before_calling_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_image_to_string(
        img: Image.Image, lang: str | None = None, config: str | None = None
    ) -> str:
        captured["size"] = img.size
        captured["lang"] = lang
        return "  stubbed text  "

    monkeypatch.setattr(image_ocr.pytesseract, "image_to_string", fake_image_to_string)

    result = image_ocr.run_ocr(Image.new("L", (100, 50), color=255))

    assert result == "stubbed text"
    assert captured["size"] == (200, 100)
    assert captured["lang"] == image_ocr.OCR_LANG


def test_run_ocr_returns_empty_string_when_tesseract_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_not_found(
        img: Image.Image, lang: str | None = None, config: str | None = None
    ) -> str:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(image_ocr.pytesseract, "image_to_string", raise_not_found)

    assert image_ocr.run_ocr(Image.new("L", (50, 50), color=255)) == ""


def test_run_ocr_returns_empty_string_on_generic_ocr_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_generic(img: Image.Image, lang: str | None = None, config: str | None = None) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(image_ocr.pytesseract, "image_to_string", raise_generic)

    assert image_ocr.run_ocr(Image.new("L", (50, 50), color=255)) == ""


def test_parse_image_blank_image_yields_no_chunk() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not available")

    img = Image.new("RGB", (200, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    assert image_ocr.parse_image(buf.getvalue()) == []


def test_parse_image_degrades_gracefully_on_corrupt_bytes() -> None:
    assert image_ocr.parse_image(b"not an image") == []


# --------------------------------------------------------------------------- #
# extractor — dispatch, unsupported type, empty guard, end-to-end assembly    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "filename",
    [
        "report.pdf",
        "sheet.xlsx",
        "data.json",
        "notes.txt",
        "readme.md",
        "table.csv",
        "photo.png",
        "photo.jpg",
        "photo.jpeg",
        "photo.webp",
    ],
)
def test_extractor_supports_every_routed_extension(filename: str) -> None:
    extractor = DocumentContentExtractor()
    assert extractor.supports(content_type="application/octet-stream", filename=filename) is True


@pytest.mark.parametrize("filename", ["archive.xls", "doc.docx", "image.gif", "data.bin"])
def test_extractor_does_not_support_deferred_or_unknown_extensions(filename: str) -> None:
    extractor = DocumentContentExtractor()
    assert extractor.supports(content_type="application/octet-stream", filename=filename) is False


def test_extractor_raises_unsupported_type_for_unroutable_extension() -> None:
    extractor = DocumentContentExtractor()
    with pytest.raises(UnsupportedTypeError):
        extractor.extract(
            data=b"whatever", filename="archive.xls", content_type="application/octet-stream"
        )


def test_extractor_raises_validation_error_on_empty_bytes() -> None:
    extractor = DocumentContentExtractor()
    with pytest.raises(ValidationError):
        extractor.extract(data=b"", filename="notes.txt", content_type="text/plain")


def test_extractor_raises_validation_error_on_genuine_parse_failure() -> None:
    extractor = DocumentContentExtractor()
    with pytest.raises(ValidationError):
        extractor.extract(
            data=b"not a real workbook", filename="broken.xlsx", content_type="application/xlsx"
        )


def test_extractor_routes_json_end_to_end() -> None:
    extractor = DocumentContentExtractor()
    payload = json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]).encode()

    doc = extractor.extract(data=payload, filename="data.json", content_type="application/json")

    assert doc.source_ext == ".json"
    assert doc.content_type == "application/json"
    assert doc.metadata["file_type"] == "structured_json"
    assert doc.metadata["parser"] == "json"
    assert len(doc.chunks) == 1
    assert doc.chunks[0].metadata["table_name"] == "data__table_0"


def test_extractor_counts_pdf_pages_not_blocks() -> None:
    """Block granularity (plan step 2) would otherwise report three blocks on
    two pages as a five-page document."""
    extractor = DocumentContentExtractor()
    data = _blocks_pdf(
        [
            [
                "Page one, block one, comfortably above the threshold.",
                "Page one, block two, also above the threshold.",
            ],
            ["Page two, block one, comfortably above the threshold."],
        ]
    )

    doc = extractor.extract(data=data, filename="report.pdf", content_type="application/pdf")

    assert doc.metadata["file_type"] == "pdf_text"
    assert doc.metadata["page_count"] == 2
    assert doc.metadata["block_count"] == 3
    assert len(doc.chunks) == 3


def test_extractor_routes_text_end_to_end() -> None:
    extractor = DocumentContentExtractor()
    doc = extractor.extract(data=b"hello world", filename="notes.txt", content_type="text/plain")

    assert doc.source_ext == ".txt"
    assert doc.metadata["parser"] == "text_plain"
    assert doc.chunks[0].metadata["source_ext"] == ".txt"


def test_content_extractor_protocol_conformance() -> None:
    # Structural (Protocol) conformance, verified statically by mypy and
    # exercised here at runtime.
    extractor: ContentExtractor = DocumentContentExtractor()
    assert extractor.supports(content_type="application/pdf", filename="a.pdf") is True
