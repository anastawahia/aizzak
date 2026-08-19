"""`ContentExtractor` adapter: routes a file by extension to the matching
ported parser and assembles the neutral `ParsedDocument`
(docs/migration/refs/parsers.md §2 "الإرسال (Dispatch)", §5 "المطابقة").

The dispatch table below replaces alpha `scanner.py::scan_docs`'s long
if/elif chain on ``p.suffix.lower()`` with a lookup — same routing decision,
data-driven instead of a chain of branches (parsers.md §2 already notes
alpha's chain is "لا كائن سجلّ" / not a registry object; this *is* one).

Genuine parse failures (any exception from a parser) are logged and
translated into `ValidationError` (code ``knowledge.parse_failed``) here —
this is the single seam where alpha's per-parser ``print``/stdout noise
(parsers.md §6 risk #6) is replaced by structured logging, and where alpha's
"log + keep going with degraded output" per-file philosophy becomes "log +
raise a typed application error" (10-code-standards §5: no silent
swallowing of a genuine failure; a caller decides whether to keep going).

Deferred — not routed here: ``.xls`` (needs the `xlrd` engine, not an approved
dependency — see `excel.py`) and ``.tif/.tiff/.bmp/.gif`` (alpha's wider
`IMAGE_EXTS`; only ``.png/.jpg/.jpeg/.webp`` are routed). Both belong to item
`P-12`, **dropped in full** by decision س-12: the formats have no content in
this system, so the routes stay closed rather than pending. ``.docx`` was
deferred with them until plan step 4 (`P-08`) and is routed now.

**The PDF route is a multi-pass path** (plan step 3 / `P-07`), and this module
is where its phases are sequenced — alpha sequences them inside `scanner.py`
for the same reason: only the caller knows that the table pass has to run
*first*, because its output (where the tables sit) is an input to the text
pass. Its third leg, OCR of the embedded images, joined in plan step 5 /
`P-09`; the same pass runs for `.docx` and `.xlsx`, whose pictures live in the
OOXML archive rather than in anything `python-docx`/`openpyxl` return.

**Why `Limits` is injected.** The OCR caps (§3.8, decision س-10) are `Settings`
values, and a router is the layer that holds them: the parsers stay plain
functions over bytes, and the ONE object that knows the deployment's numbers
hands them down. Plan step 14 (`P-01` `P-02`) puts the zip-bomb guard's limits
through the same seam.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from app.framework.errors import UnsupportedTypeError, ValidationError
from app.framework.observability import get_logger
from app.framework.settings.settings import Limits
from app.framework.types import Json
from app.modules.knowledge.adapters.parsers.docx import classify_docx, parse_docx
from app.modules.knowledge.adapters.parsers.excel import parse_excel
from app.modules.knowledge.adapters.parsers.image_ocr import (
    OcrResult,
    OfficeMedia,
    parse_image,
    parse_office_images,
    parse_pdf_images,
)
from app.modules.knowledge.adapters.parsers.json_doc import classify_json, parse_json
from app.modules.knowledge.adapters.parsers.pdf_tables import parse_pdf_tables
from app.modules.knowledge.adapters.parsers.pdf_text import parse_pdf_text
from app.modules.knowledge.adapters.parsers.text_plain import parse_text
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)

log = get_logger(__name__)

_Router = Callable[[bytes, str, Limits], tuple[list[ParsedChunk], Json]]


def _route_pdf(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    """Tables first, then the text blocks that are not part of them, then the
    images neither of them can read.

    The order is the whole point (plan step 3 / `P-07`): the table pass reports
    where its tables sit, and handing those regions to the text pass is what
    stops a table being indexed twice — once structured, once as the garbled
    prose its cells flatten into. A failing table pass returns ``([], {})``
    rather than raising, so the text pass still runs and the document keeps its
    prose.

    Both passes emit `order` on the same page-strided axis, so the combined
    list is already one document-wide sequence and `domain/chunking.py` sorts
    it (stably) without renumbering. Tables lead the list, which is also how
    ties inside a page resolve; ranking a table against a block by geometry is
    the multi-signal key's job (step 10 / `P-17`).

    The OCR pass (step 5 / `P-09`) is last and independent: it reads the page's
    embedded rasters, which neither camelot nor the layout extractor can see at
    all, so it needs nothing from the two passes before it and — like the table
    pass — degrades to nothing rather than costing the document its prose.
    """
    table_chunks, table_regions = parse_pdf_tables(data)
    text_chunks = parse_pdf_text(data, table_regions=table_regions)
    images = parse_pdf_images(data, limits=limits)
    chunks = [*table_chunks, *text_chunks, *images.chunks]
    # A chunk is one text BLOCK now, not a page (plan step 2 / `P-10`), so
    # `page_count` has to be counted over the distinct pages those chunks came
    # from; `len(chunks)` would report blocks as pages. It is still the number
    # of pages that yielded content, not the PDF's total page count.
    pages = {chunk.metadata.get("page_number") for chunk in chunks}
    return chunks, {
        "file_type": _pdf_file_type(
            table_count=len(table_chunks),
            block_count=len(text_chunks),
            image_count=images.image_count,
        ),
        "page_count": len(pages),
        "block_count": len(text_chunks),
        "table_count": len(table_chunks),
        **_ocr_metadata(images),
    }


def _pdf_file_type(*, table_count: int, block_count: int, image_count: int) -> str:
    """Which passes actually produced something — the same in-band
    ``file_type`` reporting the other routers use.

    Images only decide the LAST case. A PDF that has prose is a text PDF whose
    figures were also read; a PDF that has nothing BUT images is the scanned
    document `pdf_empty` used to call empty, which is the one thing it never
    was."""
    if table_count and block_count:
        return "pdf_mixed"
    if table_count:
        return "pdf_tables"
    if block_count:
        return "pdf_text"
    return "pdf_images" if image_count else "pdf_empty"


def _route_docx(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    """One walk, three kinds of chunk: merged text blocks, structured tables,
    and the pictures in `word/media/`.

    Unlike the PDF route there is no sequencing to own here — a DOCX declares
    its own structure, so paragraphs and tables come out of a single walk of
    the document (plan step 4 / `P-08`). The image pass (step 5 / `P-09`) reads
    the same bytes a second time, as a zip: `python-docx` models a picture as
    an inline shape and never hands over its raster.
    """
    chunks = parse_docx(data)
    images = parse_office_images(data, media=OfficeMedia.DOCX, limits=limits)
    table_count = sum(1 for chunk in chunks if chunk.kind is ParsedChunkKind.TABLE)
    block_count = len(chunks) - table_count
    return [*chunks, *images.chunks], {
        "file_type": classify_docx(
            table_count=table_count, text_count=block_count, image_count=images.image_count
        ),
        "block_count": block_count,
        "table_count": table_count,
        **_ocr_metadata(images),
    }


def _route_excel(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    """Sheets, plus whatever is in `xl/media/` (plan step 5 / `P-09`) — a
    workbook whose only content is a pasted screenshot is common enough that
    reporting it as `excel_empty` would be a lie about a document that does
    have chunks."""
    chunks = parse_excel(data)
    images = parse_office_images(data, media=OfficeMedia.XLSX, limits=limits)
    file_type = (
        "excel_structured" if chunks else ("excel_images" if images.chunks else "excel_empty")
    )
    return [*chunks, *images.chunks], {"file_type": file_type, **_ocr_metadata(images)}


def _ocr_metadata(images: OcrResult) -> Json:
    """The two facts every image-bearing route reports: how many images were
    actually read, and whether a §3.8 cap cut the pass short. `ocr_truncated`
    is the plan's declared-truncation pattern scoped to this pass, so it cannot
    be confused with a summary's own `truncated`."""
    return {"image_count": images.image_count, "ocr_truncated": images.truncated}


def _route_json(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_json(data)
    n_tables = sum(1 for c in chunks if c.kind is ParsedChunkKind.TABLE)
    n_texts = sum(1 for c in chunks if c.kind is ParsedChunkKind.JSON)
    return chunks, {"file_type": classify_json(table_count=n_tables, text_count=n_texts)}


def _route_text(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_text(data, ext)
    return chunks, {"file_type": f"text_{ext.lstrip('.')}" if chunks else "text_empty"}


def _route_image(data: bytes, ext: str, limits: Limits) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_image(data)
    return chunks, {"file_type": "image_ocr" if chunks else "image_empty"}


# `.xls` and the wider image extensions are not keyed here, so they fall
# through to UnsupportedTypeError in `extract()` — see the module docstring.
_ROUTES: dict[str, _Router] = {
    ".pdf": _route_pdf,
    ".docx": _route_docx,
    ".xlsx": _route_excel,
    ".json": _route_json,
    ".txt": _route_text,
    ".md": _route_text,
    ".csv": _route_text,
    ".png": _route_image,
    ".jpg": _route_image,
    ".jpeg": _route_image,
    ".webp": _route_image,
}

_PARSER_NAMES: dict[str, str] = {
    # Two modules answer for a PDF now (tables + text blocks), so the name
    # reports the path, not one of them (plan step 3 / `P-07`).
    ".pdf": "pdf_multipass",
    ".docx": "docx",
    ".xlsx": "excel",
    ".json": "json",
    ".txt": "text_plain",
    ".md": "text_plain",
    ".csv": "text_plain",
    ".png": "image_ocr",
    ".jpg": "image_ocr",
    ".jpeg": "image_ocr",
    ".webp": "image_ocr",
}


class DocumentContentExtractor:
    """`ContentExtractor` adapter (structural — see the port's `Protocol`)."""

    def __init__(self, *, limits: Limits | None = None) -> None:
        """``limits`` carries the deployment's numbers to the routes that need
        them — today the three OCR caps of §3.8. It defaults to `Limits()` so a
        test (and any caller that does not care) constructs this the way it
        always did, while the worker's composition root passes the settings it
        already holds."""
        self._limits = limits or Limits()

    def supports(self, *, content_type: str, filename: str) -> bool:
        """Routing is by extension: alpha dispatches on ``p.suffix.lower()``,
        not content-type (parsers.md §2). ``content_type`` is accepted for
        Protocol conformance and carried through into the returned
        `ParsedDocument`, not consulted for this decision."""
        return _extension_of(filename) in _ROUTES

    def extract(self, *, data: bytes, filename: str, content_type: str) -> ParsedDocument:
        # Deferred (3.k1): alpha's per-file SIGALRM parse timeout
        # (`scanner.py::_arm_parse_timeout`) is NOT ported — it is main-thread/
        # Unix-only and unusable from an async worker; a worker-level
        # timeout/cancellation replaces it later.
        ext = _extension_of(filename)
        router = _ROUTES.get(ext)
        if router is None:
            raise UnsupportedTypeError(
                f"unsupported file type: {filename!r}", code="knowledge.unsupported_type"
            )
        if not data:
            raise ValidationError("file has no content to parse", code="knowledge.empty_content")

        try:
            raw_chunks, doc_metadata = router(data, ext, self._limits)
        except Exception as exc:
            log.warning(
                "knowledge.parse_failed",
                # NOTE: "filename" collides with the stdlib LogRecord's own
                # reserved attribute of that name — use "file_name" instead.
                extra={"file_name": filename, "source_ext": ext, "error": str(exc)},
            )
            raise ValidationError(
                f"failed to parse {filename!r}: {exc}", code="knowledge.parse_failed"
            ) from exc

        chunks = _enrich(raw_chunks, ext=ext, filename=filename)
        if not chunks:
            log.info("knowledge.parse_empty", extra={"file_name": filename, "source_ext": ext})

        return ParsedDocument(
            source_ext=ext,
            content_type=content_type,
            chunks=chunks,
            metadata={**doc_metadata, "parser": _PARSER_NAMES[ext]},
        )


def _extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def _enrich(chunks: list[ParsedChunk], *, ext: str, filename: str) -> tuple[ParsedChunk, ...]:
    """Ensure every chunk carries `source_ext` and `file_name`, and every
    TABLE-kind chunk a `table_name` too — this is the one layer that knows
    the filename (`extract()`'s own parameter), so it is cheaper to
    guarantee all three here than to thread the filename into every
    format-specific parser (rag-indexing-plan.md §3.9/§4 step 11's carried
    gap: `_CITATION_KEYS` in `application/indexing.py` has allowlisted
    `file_name` since step 11, but no producer emitted it until this
    line -- `_enrich` runs for every route, so this closes the gap for
    every producer at once: PDF text, PDF tables, DOCX, Excel, JSON, images,
    OCR).

    `file_name` is stamped unconditionally (not "if absent", unlike
    `table_name` below): no parser has any legitimate way to know the
    source filename on its own, so there is nothing for this layer to
    defer to -- it is the one and only source of truth for it.
    """
    stem = Path(filename).stem
    enriched: list[ParsedChunk] = []
    table_index = 0
    for chunk in chunks:
        metadata: Json = {**chunk.metadata, "source_ext": ext, "file_name": filename}
        if chunk.kind is ParsedChunkKind.TABLE and "table_name" not in metadata:
            metadata["table_name"] = f"{stem}__table_{table_index}"
            table_index += 1
        enriched.append(dataclasses.replace(chunk, metadata=metadata))
    return tuple(enriched)
