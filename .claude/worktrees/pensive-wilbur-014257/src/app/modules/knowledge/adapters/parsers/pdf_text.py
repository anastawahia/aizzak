"""PDF text extraction (ported from alpha `rag/parsers/pdf_layout_extractor.py`
— `iter_pdf_layout_documents`; docs/migration/refs/parsers.md §1 "PDF نصّ").

One coarse chunk per page: ``get_text("text", sort=True)`` — ``sort=True``
restores logical reading order, which matters for RTL/Arabic where raw block
order can be visually scrambled. ``order`` is the 0-based page index, a
deterministic structural ordinal (parsers.md §6 risk #5).

Deferred (3.k1): the table-avoidance branch (`_extract_text_avoiding_tables`,
`PDF_TABLE_OVERLAP_THRESHOLD`) is dropped — it exists only to skip regions
already claimed by the Camelot table extractor (`pdf_table_extractor.py`),
which itself is deferred (see the task scope). Every page's full text is kept
here; re-add table-region avoidance when table extraction lands. Also
deferred: disk-dumping of chunks/summaries (`PDF_SAVE_TEXT_CHUNKS`) — a debug
convenience, not a functional part of the parser contract.
"""

from __future__ import annotations

import fitz  # PyMuPDF

from app.framework.observability import get_logger
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# A page's extracted text shorter than this is treated as empty/noise and
# skipped (alpha `PDF_MIN_BLOCK_CHARS`, default 25).
MIN_BLOCK_CHARS = 25


def parse_pdf_text(data: bytes) -> list[ParsedChunk]:
    """Extract per-page text from a PDF. Never raises: a corrupt/encrypted PDF
    or a single failing page degrades to fewer (or zero) chunks, logged as a
    warning — mirroring alpha's per-file/per-page fault tolerance."""
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        log.warning("pdf_text.open_failed", extra={"error": str(exc)})
        return []

    try:
        if pdf.is_encrypted and not pdf.authenticate(""):
            log.warning("pdf_text.encrypted")
            return []

        # Deferred (3.k1): PDF table extraction (Camelot `pdf_table_extractor.py`)
        # and the table-region-avoidance it feeds into `iter_pdf_layout_documents`.
        chunks: list[ParsedChunk] = []
        for page_index in range(len(pdf)):
            try:
                page = pdf.load_page(page_index)
                # sort=True restores logical (RTL-safe) reading order instead
                # of raw block-discovery order.
                text: str = page.get_text("text", sort=True).strip()
            except Exception as exc:
                log.warning(
                    "pdf_text.page_failed", extra={"page_index": page_index, "error": str(exc)}
                )
                continue

            if len(text) < MIN_BLOCK_CHARS:
                continue

            chunks.append(
                ParsedChunk(
                    text=text,
                    order=page_index,
                    kind=ParsedChunkKind.TEXT,
                    metadata={
                        "page_number": page_index + 1,
                        "page_index": page_index,
                        "section_type": "paragraph",
                        "source_ext": ".pdf",
                    },
                )
            )
        return chunks
    finally:
        pdf.close()
