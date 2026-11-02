"""The inspection itself, over PyMuPDF: what is on a page, not what it means (LLD 3.3.10).

Runs ONLY inside the child process (``child.py``), under its ``resource`` limits. A page's signals
are counts and a sample of tokens: the characters (mapped, unmapped, Private Use Area, CJK,
Latin), words, vector paths and the stroke-sized curves that mark outlined or SHX text, images and
the share of the page they cover, links, fonts (embedded? a Unicode map?) and up to
``token_sample`` distinct whitespace-delimited tokens in reading order. Floats are rounded and
every list is ordered, so two runs over the same bytes give the same JSON.

This module imports ``pymupdf`` at the top. ``child.py`` imports THIS module only after it has
applied its resource limits, and ``contract.py`` and ``sandbox.py`` never import it, so the
parent server stays free of PyMuPDF.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pymupdf

from services.ele_parse import contract

_CURVE_MAX_EXTENT_PT: Final = 10.0  # a curve this small is a stroke of a glyph, not a drawing
_PDF_HEADER: Final = b"%PDF-"
_HEADER_WINDOW: Final = 1_024  # a PDF may be preceded by junk; the header sits within it
_PUA_RANGES: Final = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
_CJK_RANGES: Final = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_REPLACEMENT: Final = 0xFFFD
_CID_FONT_TYPES: Final = frozenset({"Type0", "CIDFontType0", "CIDFontType2"})
_TOKEN_TRIM: Final = ".,;()[]{}\"'"


class Rejected(Exception):
    """The file is refused: ``code`` is ``not_pdf``, ``corrupt`` or ``encrypted``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _in(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


_LATIN_LIMIT: Final = 0x250  # Basic Latin through Latin Extended-B


def _is_latin_letter(char: str) -> bool:
    return char.isalpha() and ord(char) < _LATIN_LIMIT


@contextmanager
def open_document(path: Path) -> Iterator[Any]:
    """The opened ``pymupdf.Document``, closed after. ``Rejected`` for a file that is not a PDF,
    is damaged, or is encrypted."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEADER_WINDOW)
    except OSError as exc:
        raise Rejected(contract.CORRUPT, f"the file cannot be read: {exc.strerror}") from exc
    if _PDF_HEADER not in head:
        raise Rejected(contract.NOT_PDF, "the file is not a PDF (no %PDF- header)")
    try:
        document = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises several types for a damaged file
        raise Rejected(contract.CORRUPT, f"the PDF cannot be opened: {type(exc).__name__}") from exc
    try:
        if document.needs_pass or document.is_encrypted:
            raise Rejected(contract.ENCRYPTED, "the PDF is encrypted")
        yield document
    finally:
        document.close()


def summarize(document: Any) -> dict[str, Any]:
    """``PdfSummary`` (schema 1)."""
    metadata = document.metadata or {}
    form = str(metadata.get("format") or "").removeprefix("PDF").strip()
    try:
        outline = len(document.get_toc(simple=True))
    except Exception:
        outline = 0
    return {
        "page_count": int(document.page_count),
        "pdf_version": form,
        "encrypted": False,
        "creator": contract.clip(metadata.get("creator"), contract.MAX_METADATA_CHARS),
        "producer": contract.clip(metadata.get("producer"), contract.MAX_METADATA_CHARS),
        "outline_count": outline,
    }


def _characters(page: Any) -> tuple[int, int, int, int, int]:
    """(chars, unmapped, private-use, CJK, Latin letters) over the page's text."""
    chars = unmapped = pua = cjk = latin = 0
    text = page.get_text("text") or ""
    for char in text:
        if char.isspace():
            continue
        code = ord(char)
        chars += 1
        if code in (_REPLACEMENT, 0):
            unmapped += 1
        elif _in(code, _PUA_RANGES):
            pua += 1
        elif _in(code, _CJK_RANGES):
            cjk += 1
        elif _is_latin_letter(char):
            latin += 1
    return chars, unmapped, pua, cjk, latin


def _tokens(words: list[Any], sample: int) -> list[str]:
    """Distinct tokens in reading order (the words come sorted by block, line, word), each at
    most ``MAX_TOKEN_CHARS``; longer ones are prose or noise and are skipped."""
    seen: dict[str, None] = {}
    for word in words:
        token = str(word[4]).strip(_TOKEN_TRIM)
        if token and len(token) <= contract.MAX_TOKEN_CHARS and token not in seen:
            seen[token] = None
            if len(seen) >= sample:
                break
    return list(seen)


def _paths(page: Any) -> tuple[int, int]:
    """(vector paths, stroke-sized curves)."""
    paths = page.get_cdrawings()
    curves = 0
    for path in paths:
        for item in path.get("items", ()):
            if item[0] != "c":
                continue
            xs = [point.x for point in item[1:]]
            ys = [point.y for point in item[1:]]
            if max(max(xs) - min(xs), max(ys) - min(ys)) <= _CURVE_MAX_EXTENT_PT:
                curves += 1
    return len(paths), curves


def _image_area_ratio(page: Any) -> float:
    area = page.rect.width * page.rect.height
    if area <= 0:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        box = page.rect & info["bbox"]
        if not box.is_empty:
            covered += box.width * box.height
    return round(min(1.0, covered / area), 6)


def _fonts(document: Any, page: Any) -> list[dict[str, Any]]:
    fonts: dict[tuple[str, str], dict[str, Any]] = {}
    for xref, ext, kind, basefont, _name, encoding, *_ in page.get_fonts(full=True):
        name = str(basefont)[: contract.MAX_FONT_NAME_CHARS]
        key = (name, str(kind))
        if key in fonts:
            continue
        has_map = document.xref_get_key(xref, "ToUnicode")[0] != "null"
        fonts[key] = {
            "name": name,
            "font_type": str(kind),
            "cid": str(kind) in _CID_FONT_TYPES or str(encoding).startswith("Identity"),
            "embedded": str(ext) != "n/a",
            "has_tounicode": has_map,
        }
    return [fonts[key] for key in sorted(fonts)][: contract.MAX_FONTS]


def page_signals(document: Any, number: int, token_sample: int) -> dict[str, Any]:
    """``PageSignals`` (schema 1) of the 1-based page ``number``."""
    page = document[number - 1]
    chars, unmapped, pua, cjk, latin = _characters(page)
    words = page.get_text("words") or []
    vector_paths, short_curves = _paths(page)
    return {
        "page_no": number,
        "width_pt": round(float(page.rect.width), 2),
        "height_pt": round(float(page.rect.height), 2),
        "rotation": int(page.rotation),
        "char_count": chars,
        "unmapped_char_count": unmapped,
        "pua_char_count": pua,
        "cjk_char_count": cjk,
        "latin_char_count": latin,
        "word_count": len(words),
        "vector_path_count": vector_paths,
        "short_curve_path_count": short_curves,
        "image_count": len(page.get_images(full=True)),
        "image_area_ratio": _image_area_ratio(page),
        "link_count": len(page.get_links()),
        "fonts": _fonts(document, page),
        "tokens": _tokens(words, token_sample),
    }


def inspect_pages(document: Any, first: int, last: int, token_sample: int) -> list[dict[str, Any]]:
    """Signals of pages ``first..last`` (clamped to the document); a range wholly beyond it is
    ``bad_range``."""
    count = int(document.page_count)
    if first > count:
        raise contract.ContractError(422, contract.BAD_RANGE, f"the PDF has {count} pages")
    return [page_signals(document, n, token_sample) for n in range(first, min(last, count) + 1)]
