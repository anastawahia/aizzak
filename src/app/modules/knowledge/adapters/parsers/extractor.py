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

Deferred (3.k1) — not routed here: ``.xls`` (needs the `xlrd` engine, not an
approved dependency for this step — see `excel.py`), ``.docx`` (needs
`python-docx`; DOCX is deferred per this step's scope), and
``.tif/.tiff/.bmp/.gif`` (alpha's wider `IMAGE_EXTS`; only
``.png/.jpg/.jpeg/.webp`` are routed). A PDF image-OCR pass (alpha's
`iter_pdf_image_documents`, run *after* text extraction) is optional and not
wired here — `pdf_text.py` covers the PDF route alone for now.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from app.framework.errors import UnsupportedTypeError, ValidationError
from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.adapters.parsers.excel import parse_excel
from app.modules.knowledge.adapters.parsers.image_ocr import parse_image
from app.modules.knowledge.adapters.parsers.json_doc import classify_json, parse_json
from app.modules.knowledge.adapters.parsers.pdf_text import parse_pdf_text
from app.modules.knowledge.adapters.parsers.text_plain import parse_text
from app.modules.knowledge.ports.content_extractor import (
    ParsedChunk,
    ParsedChunkKind,
    ParsedDocument,
)

log = get_logger(__name__)

_Router = Callable[[bytes, str], tuple[list[ParsedChunk], Json]]


def _route_pdf(data: bytes, ext: str) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_pdf_text(data)
    # A chunk is one text BLOCK now, not a page (plan step 2 / `P-10`), so
    # `page_count` has to be counted over the distinct pages those blocks came
    # from; `len(chunks)` would report blocks as pages. It is still the number
    # of pages that yielded content, not the PDF's total page count.
    pages = {chunk.metadata.get("page_number") for chunk in chunks}
    return chunks, {
        "file_type": "pdf_text" if chunks else "pdf_empty",
        "page_count": len(pages),
        "block_count": len(chunks),
    }


def _route_excel(data: bytes, ext: str) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_excel(data)
    return chunks, {"file_type": "excel_structured" if chunks else "excel_empty"}


def _route_json(data: bytes, ext: str) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_json(data)
    n_tables = sum(1 for c in chunks if c.kind is ParsedChunkKind.TABLE)
    n_texts = sum(1 for c in chunks if c.kind is ParsedChunkKind.JSON)
    return chunks, {"file_type": classify_json(table_count=n_tables, text_count=n_texts)}


def _route_text(data: bytes, ext: str) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_text(data, ext)
    return chunks, {"file_type": f"text_{ext.lstrip('.')}" if chunks else "text_empty"}


def _route_image(data: bytes, ext: str) -> tuple[list[ParsedChunk], Json]:
    chunks = parse_image(data)
    return chunks, {"file_type": "image_ocr" if chunks else "image_empty"}


# Deferred (3.k1): .docx (needs python-docx) and .xls (needs xlrd) are not
# keyed here, so both fall through to UnsupportedTypeError in `extract()`.
_ROUTES: dict[str, _Router] = {
    ".pdf": _route_pdf,
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
    ".pdf": "pdf_text",
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
            raw_chunks, doc_metadata = router(data, ext)
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

        chunks = _enrich(raw_chunks, ext=ext, stem=Path(filename).stem)
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


def _enrich(chunks: list[ParsedChunk], *, ext: str, stem: str) -> tuple[ParsedChunk, ...]:
    """Ensure every chunk carries `source_ext`, and every TABLE-kind chunk a
    `table_name` — this is the one layer that knows the filename, so it is
    cheaper to guarantee both here than to thread the filename into every
    format-specific parser."""
    enriched: list[ParsedChunk] = []
    table_index = 0
    for chunk in chunks:
        metadata: Json = {**chunk.metadata, "source_ext": ext}
        if chunk.kind is ParsedChunkKind.TABLE and "table_name" not in metadata:
            metadata["table_name"] = f"{stem}__table_{table_index}"
            table_index += 1
        enriched.append(dataclasses.replace(chunk, metadata=metadata))
    return tuple(enriched)
