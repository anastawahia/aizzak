"""PDF text extraction (ported from alpha `rag/parsers/pdf_layout_extractor.py`
— `iter_pdf_layout_documents`).

**Granularity: one block = one `ParsedChunk`** (plan decision س-08). Alpha
concatenates a page's blocks into a single page-level `Document`; this port
stops one level earlier and emits the blocks themselves. A page-sized segment
forces the downstream window splitter to cut across unrelated paragraphs,
which is the thing block granularity exists to prevent.

What is ported from alpha is the block *reading*: `page.get_text("blocks",
sort=True)` — `sort=True` restores logical reading order, which matters for
RTL/Arabic where raw block-discovery order can be visually scrambled — the
malformed-tuple guard on the block's field count, and `MIN_BLOCK_CHARS = 25`,
which now applies to the **block**. That threshold is exactly what alpha's own
name (`PDF_MIN_BLOCK_CHARS`) always implied and what alpha never did: it
applied the block threshold to a whole page, so a page of noise passed as
easily as a page of prose.

`ParsedChunk.order` is therefore a **block rank**, not a page index:
``page_index * 1000 + block_index_on_page`` — the same page-strided axis
`pdf_tables.py` emits on, so the two passes interleave into one document-wide
sequence without renumbering. It stays a deterministic STRUCTURAL ordinal (the
port's contract): it is computed from the block's position on the page, never
from how many blocks happened to survive the noise filter, so relaxing that
filter later cannot renumber the blocks that already survived it.

Not in this step: table-region avoidance (`PDF_TABLE_OVERLAP_THRESHOLD`, step
3 / `P-07`) — every block is still kept, so until step 3 lands a page's tables
are indexed twice, once structured by `pdf_tables.py` and once as scrambled
text here; OCR of embedded images (step 5 / `P-09`); and the multi-signal
ordering key that consumes `position_in_doc` (step 10 / `P-17`) — which is why
step 2 is not closed before step 10. Left behind, as in alpha: disk-dumping of
chunks and extraction summaries (`PDF_SAVE_TEXT_CHUNKS`), the `PDF_MAX_PAGES`
cap, and `os.getenv` configuration.
"""

from __future__ import annotations

from typing import Any

import fitz  # PyMuPDF

from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# A block's text shorter than this is treated as noise and dropped (alpha
# `PDF_MIN_BLOCK_CHARS`, default 25 — on the block now, per decision س-08).
MIN_BLOCK_CHARS = 25

# `get_text("blocks")` yields (x0, y0, x1, y1, text, block_no, block_type);
# alpha guards on the same field count before touching bbox/text.
_FITZ_BLOCK_MIN_FIELDS = 5
_BLOCK_TEXT_FIELD = 4
_BLOCK_TYPE_FIELD = 6
# PyMuPDF marks image blocks with block_type 1 and gives them a synthetic
# "<image: DeviceRGB, width 800, height 600, bpc 8>" placeholder — comfortably
# longer than MIN_BLOCK_CHARS, so it would be indexed as prose. The pinned
# version's default `blocks` flags do not emit them, which makes this guard
# defensive: it keeps a flag change (or a build that does emit them) from
# quietly poisoning the index. Real OCR of those images is step 5 (`P-09`).
_IMAGE_BLOCK_TYPE = 1

# Shared with `pdf_tables.py`: order = page_index * STRIDE + index-on-page, so
# a text block and a table on the same page sort into one sequence. A page
# with >= STRIDE blocks would otherwise spill into the next page's range; the
# index is clamped instead, which at worst ties the tail of an implausibly
# dense page (a stable sort keeps their relative order) and can never reorder
# blocks across pages.
_PAGE_ORDER_STRIDE = 1_000


def parse_pdf_text(data: bytes) -> list[ParsedChunk]:
    """Extract one chunk per text block from a PDF. Never raises: a corrupt or
    encrypted PDF, or a single failing page, degrades to fewer (or zero)
    chunks, logged as a warning — mirroring alpha's per-file/per-page fault
    tolerance."""
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        log.warning("pdf_text.open_failed", extra={"error": str(exc)})
        return []

    try:
        if pdf.is_encrypted and not pdf.authenticate(""):
            log.warning("pdf_text.encrypted")
            return []

        chunks: list[ParsedChunk] = []
        # Counts every block the document yields, dropped ones included, so it
        # stays a structural position rather than a survivor's index.
        position = 0
        for page_index in range(len(pdf)):
            for block_index, block in enumerate(_page_blocks(pdf, page_index)):
                position_in_doc = position
                position += 1
                text = _block_text(block)
                if len(text) < MIN_BLOCK_CHARS:
                    continue
                chunks.append(
                    ParsedChunk(
                        text=text,
                        order=_block_order(page_index, block_index),
                        kind=ParsedChunkKind.TEXT,
                        metadata=_build_metadata(
                            block=block,
                            page_index=page_index,
                            block_index=block_index,
                            position_in_doc=position_in_doc,
                            chunk_index=len(chunks),
                        ),
                    )
                )
        return chunks
    finally:
        pdf.close()


def _page_blocks(pdf: Any, page_index: int) -> list[Any]:
    """One page's blocks in logical reading order, or `[]` if the page fails.

    A page that cannot be loaded or rendered costs its own blocks and nothing
    else — the rest of the document is still extracted (alpha's per-page
    tolerance).
    """
    try:
        page = pdf.load_page(page_index)
        blocks: list[Any] = page.get_text("blocks", sort=True)
    except Exception as exc:
        log.warning("pdf_text.page_failed", extra={"page_index": page_index, "error": str(exc)})
        return []
    return blocks


def _block_text(block: Any) -> str:
    """The block's stripped text, or `""` when it is malformed or an image."""
    if len(block) < _FITZ_BLOCK_MIN_FIELDS:
        return ""
    if len(block) > _BLOCK_TYPE_FIELD and block[_BLOCK_TYPE_FIELD] == _IMAGE_BLOCK_TYPE:
        return ""
    return str(block[_BLOCK_TEXT_FIELD]).strip()


def _block_order(page_index: int, block_index: int) -> int:
    """Document-wide structural ordinal for a block (see the module docstring)."""
    return page_index * _PAGE_ORDER_STRIDE + min(block_index, _PAGE_ORDER_STRIDE - 1)


def _build_metadata(
    *,
    block: Any,
    page_index: int,
    block_index: int,
    position_in_doc: int,
    chunk_index: int,
) -> Json:
    """Assemble the block's payload.

    `position_in_doc` and `chunk_index` are alpha's own ordering signals
    (`node_builder.py::_order_key`) and are emitted here so step 10 can build
    the multi-signal key on top of them without re-deriving anything.
    """
    return {
        "page_number": page_index + 1,
        "page_index": page_index,
        "block_index": block_index,
        "position_in_doc": position_in_doc,
        "chunk_index": chunk_index,
        # fitz coordinates: top-left origin, y growing downward. Deliberately
        # NOT named `bbox`, because `pdf_tables.py` emits `bbox` in camelot's
        # PDF space (bottom-left origin, y growing upward) and step 3 puts
        # both kinds of chunk in the same document.
        "block_bbox": [float(v) for v in block[:4]],
        "section_type": "paragraph",
        "layout_mode": "pymupdf_blocks",
        "source_ext": ".pdf",
    }
