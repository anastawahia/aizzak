"""Image OCR (ported from alpha `rag/parsers/image_extractor.py`;
docs/migration/refs/parsers.md §1 "OCR/صور").

Two entry points, one engine:

* `parse_image` OCRs one **standalone** image file (a `.png`/`.jpg` upload).
* `parse_pdf_images` / `parse_office_images` OCR the images **embedded** in a
  PDF, a `.docx` (``word/media/``) or an `.xlsx` (``xl/media/``) — plan step 5
  (`P-09` `P-11`), decision س-10. These are alpha's `iter_pdf_image_documents`
  / `iter_docx_image_documents` / `iter_xlsx_image_documents`, all three built
  (as in alpha) on ONE group-merging core: `iter_image_documents`.

**A group is the merge unit.** Alpha emits one `Document` per group, never one
per image, and the reason survives the port: a page's images are usually one
figure cut into pieces by the producing tool, and OCRing them into separate
chunks scatters a single caption across three neighbours that then compete in
retrieval. A group is a **PDF page**; for an Office archive it is the **whole
file**, because `word/media/` says nothing about where in the document a
picture sits (that position lives in the drawing relationship inside
`document.xml`, which alpha does not read either — so neither does this).

**Guardrails (§3.8, decision س-10) live in `Settings`, not in `os.getenv`.**
`Limits.ocr_min_image_px` · `ocr_max_images_per_document` ·
`ocr_max_images_per_page` reach this module as an argument; alpha reads eleven
knobs from the environment through a thread-safe holder, none of them written
down as a decision anywhere. Hitting a cap **declares itself** (`truncated` on
`OcrResult`) and never fails the document: a cap is a cost decision, not a
defect (§3.8). The size filter is alpha's `min_image_width`/`min_image_height`
/`min_image_area` trio collapsed into the plan's single 200x200 knob — icons
and logos are the most numerous embedded images and the least worth OCRing.

**Ported calibrations:** `clean_ocr_text` and `create_page_summary` (fact
ح-٤ — both were missing from this module) · SHA-1 deduplication across the
WHOLE file, so a logo repeated on forty pages is OCRed once · the PDF `xref`
first-pass dedup · the `[Page Context: ...]` prefix, added only when a group
actually contains a textless image · `is_meaningful_text` · the grayscale +
upscale-below-1000px preprocessing.

**Two deliberate divergences from alpha:**

1. *The placeholder is the live branch.* Alpha's `skip_no_text_images` defaults
   to ``1``, which makes its own ``[Diagram/Chart WxHpx]`` placeholder dead
   code — the image is dropped and only a counter remembers it. `P-11` asks
   for the placeholder, so this port takes the other branch: an image whose OCR
   is not meaningful contributes its placeholder to the merged text. That is
   what makes the group's text say *where* the figure sits, which a counter
   cannot.
2. *`parse_image` uses neither the placeholder nor the size filter.* A
   standalone image IS the document: a 150x150 upload is a deliberate act by
   the user, and answering it with ``[Diagram/Chart 150x150px]`` as the whole
   document text is noise, not content. Both apply only to images the user
   never chose to index individually.

**Left behind** (alpha's structure, per the plan's governing rule): the
`_ImageConfig`/`os.getenv` holder · `SAVE_IMAGE_TO_DISK` and the
`debug/image_chunks` JSON dumping · the `print(...)` `[STATS]` block ·
`PDF_MAX_PAGES` · reading from a `Path` instead of bytes.

Risk #2 (parsers.md §6): alpha's own *code* defaults to ``OCR_LANG="eng+ara"``
(its README claims ``"ara+eng"`` — a documented drift). This port picks
**Arabic-first** (``"ara+eng"``), matching the README and this platform's
Arabic-first orientation, as an explicit, documented decision — not a bug
port.

Graceful degradation: nothing here raises. A missing tesseract binary
(`pytesseract.TesseractNotFoundError`), a corrupt image, an encrypted PDF or a
file that is not a zip at all is logged and yields no chunk — the platform must
keep ingesting a file even when its images cannot be read.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.framework.observability import get_logger
from app.framework.settings.settings import Limits
from app.framework.types import Json
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# Pillow decompression-bomb cap (alpha `IMAGE_MAX_PIXELS`, 64 megapixels) —
# Pillow raises `Image.DecompressionBombError` past this, turned into a
# skip-and-log below (never crash).
IMAGE_MAX_PIXELS = 64 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = IMAGE_MAX_PIXELS

# Arabic-first (risk #2 above) — module constant, not env-tunable.
OCR_LANG = "ara+eng"
_TESSERACT_CONFIG = "--oem 1 --psm 6"  # oem=1 LSTM engine, psm=6 uniform text block.

_OCR_UPSCALE_BELOW_PX = 1000
_OCR_UPSCALE_FACTOR = 2.0
_MIN_MEANINGFUL_TEXT_RATIO = 0.3
# below this length OCR output is noise regardless of alnum/Arabic ratio.
_MIN_MEANINGFUL_TEXT_LEN = 10

# Same page-strided axis `pdf_text.py` and `pdf_tables.py` emit on, so the
# three PDF passes interleave into one document-wide sequence without
# renumbering. `999` is the last slot on a page: a page's figures are read
# after its prose, and no page carries 999 text blocks.
_PAGE_ORDER_STRIDE = 1_000
_PAGE_OCR_ORDER_INDEX = 999

# An Office archive's media carries no document position (see the module
# docstring), so its one group sorts after every positioned chunk. Deliberately
# a constant and not "one past the last chunk": `ParsedChunk.order` is a
# STRUCTURAL ordinal, and deriving it from how many chunks survived their
# filters is exactly what the port's contract forbids.
_MEDIA_ORDER = 1_000_000_000

# A pixmap with more than three colour components (beyond its alpha channel)
# is CMYK or a separation space, which PNG cannot carry — alpha's own test,
# and the reason it converts to RGB before serialising.
_MAX_DIRECT_COLOR_COMPONENTS = 3

_MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp")
_ALT_TEXT = "[Diagram/Chart {width}x{height}px]"
_PAGE_CONTEXT = "[Page Context: {summary}]"

# alpha `clean_ocr_text` — collapse OCR's runaway whitespace and the long
# rules a scanner reads out of a table border.
_BLANK_LINES = re.compile(r"\n{3,}")
_RUN_OF_SPACES = re.compile(r" {2,}")
_LONG_RULE = re.compile(r"[_\-]{5,}")


class OfficeMedia(StrEnum):
    """Which OOXML media folder an archive keeps its pictures in."""

    DOCX = "docx"
    XLSX = "xlsx"


_MEDIA_PREFIXES: dict[OfficeMedia, str] = {
    OfficeMedia.DOCX: "word/media/",
    OfficeMedia.XLSX: "xl/media/",
}


@dataclass(frozen=True, slots=True)
class OcrResult:
    """What an embedded-image pass produced.

    `image_count` counts images actually OCRed (post-dedup, post-size-filter),
    not images found — it is the number the caps bound. `truncated` is the
    plan's declared-truncation pattern (§3.8): a document that hit a cap says
    so instead of quietly indexing less.
    """

    chunks: tuple[ParsedChunk, ...]
    image_count: int
    truncated: bool


_EMPTY = OcrResult(chunks=(), image_count=0, truncated=False)


class _Caps(NamedTuple):
    """The three §3.8 guardrails, resolved from `Limits` once per pass."""

    min_px: int
    max_per_document: int
    max_per_group: int


class _RawImage(NamedTuple):
    data: bytes
    metadata: Json


class _Group(NamedTuple):
    """One merge unit: a PDF page, or an Office archive's whole media set."""

    order: int
    metadata: Json
    images: Iterator[_RawImage]


def parse_image(data: bytes) -> list[ParsedChunk]:
    """OCR a single standalone image into at most one `ParsedChunk`. Never
    raises: a corrupt/oversized image or a non-meaningful OCR result degrades
    to an empty list (logged), matching alpha's per-image fault tolerance.

    Neither the size filter nor the `[Diagram/Chart]` placeholder applies here
    — divergence 2 in the module docstring.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            text = run_ocr(img)
    except Image.DecompressionBombError as exc:
        log.warning("image_ocr.decompression_bomb", extra={"error": str(exc)})
        return []
    except Exception as exc:
        log.warning("image_ocr.open_failed", extra={"error": str(exc)})
        return []

    cleaned = clean_ocr_text(text)
    if not is_meaningful_text(cleaned):
        return []

    return [
        ParsedChunk(
            text=cleaned,
            order=0,
            kind=ParsedChunkKind.OCR,
            metadata={"sha1": sha1_digest(data)},
        )
    ]


def parse_pdf_images(data: bytes, *, limits: Limits | None = None) -> OcrResult:
    """OCR the images embedded in a PDF, one merged chunk per page (alpha
    `iter_pdf_image_documents`). The third leg of the plan's triple PDF path.

    An unreadable or password-protected PDF yields nothing rather than raising:
    the other two passes have already produced this document's prose, and
    losing its figures must not lose the rest with them.
    """
    caps = _caps(limits)
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        log.warning("image_ocr.pdf_open_failed", extra={"error": str(exc)})
        return _EMPTY

    try:
        if pdf.is_encrypted and not pdf.authenticate(""):
            log.warning("image_ocr.pdf_encrypted")
            return _EMPTY
        return _ocr_groups(_pdf_groups(pdf), caps=caps)
    finally:
        pdf.close()


def parse_office_images(
    data: bytes, *, media: OfficeMedia, limits: Limits | None = None
) -> OcrResult:
    """OCR the images embedded in a `.docx`/`.xlsx` — one merged chunk for the
    whole file (alpha `iter_office_image_documents`).

    Both formats are ZIP containers, and `python-docx`/`openpyxl` are bypassed
    on purpose: the media are read straight out of the archive, exactly as
    alpha does, because neither library exposes a picture's raster bytes the
    way the archive does.
    """
    caps = _caps(limits)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        log.warning("image_ocr.archive_open_failed", extra={"error": str(exc)})
        return _EMPTY

    try:
        prefix = _MEDIA_PREFIXES[media]
        names = [
            name
            for name in archive.namelist()
            if name.startswith(prefix) and name.lower().endswith(_MEDIA_EXTS)
        ]
        if not names:
            return _EMPTY
        group = _Group(
            order=_MEDIA_ORDER,
            metadata={
                "section_type": "embedded_images",
                "chunk_type": f"{media.value}_media_images",
            },
            images=_archive_images(archive, names),
        )
        return _ocr_groups([group], caps=caps)
    finally:
        archive.close()


def run_ocr(img: Image.Image) -> str:
    """Grayscale + upscale-if-small, then tesseract OCR. Never raises — see
    the module docstring."""
    try:
        prepared = _upscale_if_small(img.convert("L"))
    except Exception as exc:
        log.warning("image_ocr.preprocess_failed", extra={"error": str(exc)})
        return ""

    try:
        text: str = pytesseract.image_to_string(prepared, lang=OCR_LANG, config=_TESSERACT_CONFIG)
    except pytesseract.TesseractNotFoundError as exc:
        log.warning("image_ocr.tesseract_not_found", extra={"error": str(exc)})
        return ""
    except Exception as exc:
        log.warning("image_ocr.ocr_failed", extra={"error": str(exc)})
        return ""
    return text.strip()


def is_meaningful_text(text: str, *, min_ratio: float = _MIN_MEANINGFUL_TEXT_RATIO) -> bool:
    """Reject OCR noise/broken characters: at least `min_ratio` of the
    (whitespace-stripped) characters must be alphanumeric OR Arabic (alpha
    `is_meaningful_text`, Arabic range ``؀..ۿ``)."""
    if not text or len(text.strip()) < _MIN_MEANINGFUL_TEXT_LEN:
        return False

    clean = text.strip()
    alnum_count = sum(1 for c in clean if c.isalnum() or "؀" <= c <= "ۿ")
    total_count = len(clean.replace(" ", "").replace("\n", ""))
    if total_count == 0:
        return False
    return (alnum_count / total_count) >= min_ratio


def clean_ocr_text(text: str) -> str:
    """Normalize OCR output (alpha `clean_ocr_text`, fact ح-٤): collapse three
    or more blank lines, runs of spaces, and the long ``____``/``----`` rules a
    scanner reads out of a table border."""
    if not text:
        return ""
    text = _BLANK_LINES.sub("\n\n", text)
    text = _RUN_OF_SPACES.sub(" ", text)
    text = _LONG_RULE.sub("", text)
    return text.strip()


def create_page_summary(images: Sequence[Json]) -> str:
    """One line describing what a group's images turned out to be (alpha
    `create_page_summary`, fact ح-٤).

    Alpha's unused `ocr_texts` first parameter is dropped: it reads only the
    metadata list, and a parameter no branch consults is not a calibration.
    """
    total = len(images)
    with_text = sum(1 for image in images if image.get("has_meaningful_text", False))
    without_text = total - with_text

    parts: list[str] = []
    if with_text > 0:
        parts.append(f"This page contains {with_text} image(s) with extracted text")
    if without_text > 0:
        parts.append(f"{without_text} diagram(s)/chart(s) without text")
    return ". ".join(parts) + "." if parts else ""


def sha1_digest(data: bytes) -> str:
    """Non-cryptographic content fingerprint for cross-image dedup (alpha's
    `seen_hashes` set) — see the module docstring."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _caps(limits: Limits | None) -> _Caps:
    resolved = limits or Limits()
    return _Caps(
        min_px=resolved.ocr_min_image_px,
        max_per_document=resolved.ocr_max_images_per_document,
        max_per_group=resolved.ocr_max_images_per_page,
    )


def _ocr_groups(groups: Iterable[_Group], *, caps: _Caps) -> OcrResult:
    """The shared engine (alpha `iter_image_documents`): dedup, filter, OCR and
    merge each group into one chunk, under the caps."""
    chunks: list[ParsedChunk] = []
    seen: set[str] = set()
    used = 0
    truncated = False

    for group in groups:
        if used >= caps.max_per_document:
            truncated = True
            break
        records, texts, hit_cap = _ocr_group(
            group, caps=caps, seen=seen, budget=caps.max_per_document - used
        )
        truncated = truncated or hit_cap
        used += len(records)
        if texts:
            chunks.append(_merge_group(group, texts=texts, records=records))

    if truncated:
        log.info("image_ocr.capped", extra={"images": used})
    return OcrResult(chunks=tuple(chunks), image_count=used, truncated=truncated)


def _ocr_group(
    group: _Group, *, caps: _Caps, seen: set[str], budget: int
) -> tuple[list[Json], list[str], bool]:
    """OCR one group's images. Returns its per-image records, the texts that go
    into the merged chunk, and whether a cap cut the group short."""
    records: list[Json] = []
    texts: list[str] = []
    ceiling = min(caps.max_per_group, budget)

    for raw in group.images:
        if len(records) >= ceiling:
            return records, texts, True
        digest = sha1_digest(raw.data)
        if digest in seen:
            continue
        seen.add(digest)

        size = _measure(raw.data)
        if size is None:
            continue
        width, height = size
        if width < caps.min_px or height < caps.min_px:
            continue

        text = clean_ocr_text(_ocr_bytes(raw.data))
        meaningful = is_meaningful_text(text)
        texts.append(text if meaningful else _ALT_TEXT.format(width=width, height=height))
        records.append(
            {
                **raw.metadata,
                "sha1": digest,
                "image_width": width,
                "image_height": height,
                "has_meaningful_text": meaningful,
            }
        )
    return records, texts, False


def _merge_group(group: _Group, *, texts: list[str], records: list[Json]) -> ParsedChunk:
    """One chunk per group, with alpha's `[Page Context: ...]` prefix — added
    only when the group actually holds a textless figure, so a clean page of
    OCRed text is never padded with boilerplate."""
    summary = create_page_summary(records)
    without_text = sum(1 for record in records if not record["has_meaningful_text"])
    body = "\n\n".join(texts)
    text = f"{_PAGE_CONTEXT.format(summary=summary)}\n\n{body}" if without_text else body

    metadata: Json = {
        **group.metadata,
        "image_count": len(records),
        "images_with_text": len(records) - without_text,
        "images_without_text": without_text,
        "page_summary": summary,
        "images": records,
    }
    return ParsedChunk(text=text, order=group.order, kind=ParsedChunkKind.OCR, metadata=metadata)


def _pdf_groups(pdf: fitz.Document) -> Iterator[_Group]:
    """One group per page that carries images (alpha's PDF wrapper)."""
    seen_xrefs: set[int] = set()
    for page_index in range(pdf.page_count):
        images = list(pdf.load_page(page_index).get_images(full=True))
        if not images:
            continue
        yield _Group(
            order=page_index * _PAGE_ORDER_STRIDE + _PAGE_OCR_ORDER_INDEX,
            metadata={
                "page_number": page_index + 1,
                "position_in_doc": page_index,
                "section_type": "page_merged_images",
                "chunk_type": "pdf_page_images",
            },
            images=_page_images(pdf, images, page_index=page_index, seen_xrefs=seen_xrefs),
        )


def _page_images(
    pdf: fitz.Document,
    images: list[Any],
    *,
    page_index: int,
    seen_xrefs: set[int],
) -> Iterator[_RawImage]:
    """Rasterize a page's images through PyMuPDF. The `xref` set is alpha's
    cheap first-pass dedup: the same embedded object referenced from several
    pages is decoded once, before the SHA-1 pass ever sees it."""
    for image in images:
        xref = int(image[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            pixmap = fitz.Pixmap(pdf, xref)
            if pixmap.n - pixmap.alpha > _MAX_DIRECT_COLOR_COMPONENTS:
                pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
            data = pixmap.tobytes("png")
        except Exception as exc:
            log.warning(
                "image_ocr.pdf_image_failed",
                extra={"xref": xref, "page_number": page_index + 1, "error": str(exc)},
            )
            continue
        yield _RawImage(data=data, metadata={"xref": xref})


def _archive_images(archive: zipfile.ZipFile, names: list[str]) -> Iterator[_RawImage]:
    """Read an OOXML archive's media entries in archive order — deterministic
    for the same bytes, which is what `ParsedChunk.order` needs."""
    for name in names:
        try:
            data = archive.read(name)
        except Exception as exc:
            log.warning("image_ocr.media_read_failed", extra={"media": name, "error": str(exc)})
            continue
        yield _RawImage(data=data, metadata={"media_name": name})


def _measure(data: bytes) -> tuple[int, int] | None:
    """Image dimensions without decoding the pixels — the size filter runs
    before OCR, so a rejected icon costs a header read and nothing more."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception as exc:
        log.warning("image_ocr.unreadable_image", extra={"error": str(exc)})
        return None


def _ocr_bytes(data: bytes) -> str:
    """OCR raw image bytes. Never raises — see the module docstring."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return run_ocr(img)
    except Exception as exc:
        log.warning("image_ocr.open_failed", extra={"error": str(exc)})
        return ""


def _upscale_if_small(img: Image.Image) -> Image.Image:
    """Upscale a small image so glyphs are large enough for tesseract (alpha
    `_run_ocr` preprocessing)."""
    max_dim = max(img.width, img.height)
    if max_dim < _OCR_UPSCALE_BELOW_PX and _OCR_UPSCALE_FACTOR > 1.0:
        new_size = (int(img.width * _OCR_UPSCALE_FACTOR), int(img.height * _OCR_UPSCALE_FACTOR))
        return img.resize(new_size, Image.Resampling.LANCZOS)
    return img
