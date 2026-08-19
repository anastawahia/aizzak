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

**Table-region avoidance** (plan step 3 / `P-07`). When the table pass has
already run, the regions it found are handed in here and a block sitting
inside one is dropped: without that, every table is indexed **twice** — once
structured by `pdf_tables.py`, and once more as the scrambled prose a table
reads as when its cells are flattened into a text block. The test is alpha's
and it is on **area**, not on a mere touch: camelot's bbox is generous, and a
paragraph brushing a table's edge is still a paragraph. Alpha's "table-only
page" rule comes with it (`TABLE_PAGE_MIN_TEXT_CHARS`).

Not in this step: OCR of embedded images (step 5 / `P-09`), the third leg of
the plan's triple PDF path; and the multi-signal ordering key that consumes
`position_in_doc` (step 10 / `P-17`) — which is why step 2 is not closed
before step 10. Left behind, as in alpha: disk-dumping of
chunks and extraction summaries (`PDF_SAVE_TEXT_CHUNKS`), the `PDF_MAX_PAGES`
cap, and `os.getenv` configuration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

import fitz  # PyMuPDF

from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

if TYPE_CHECKING:
    # Type-only, deliberately: `TableRegion` is a plain record produced by the
    # table pass, and importing it under `TYPE_CHECKING` keeps this module's
    # runtime dependency down to PyMuPDF — the caller supplies the regions, so
    # nothing here needs camelot to be installed to run.
    from app.modules.knowledge.adapters.parsers.pdf_tables import TableRegion

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

# Fraction of a block's own area that must fall inside a detected table before
# the block is treated as that table's text and dropped (alpha
# `PDF_TABLE_OVERLAP_THRESHOLD`, default 0.5). Dropping on any intersection
# would cost the paragraph that merely brushes a generous camelot bbox.
TABLE_OVERLAP_THRESHOLD = 0.5

# On a page whose table regions were avoided, this much surviving text (summed
# over its blocks) is required before any of it is kept — alpha's "table-only
# page" rule, `MIN_BLOCK_CHARS * 2`. What is left on such a page is stray cell
# text that camelot's bbox did not quite cover: it reads as prose block by
# block, but as a document it is the table again, unstructured. The rule fires
# ONLY on pages that had tables; an ordinary sparse page keeps its blocks.
TABLE_PAGE_MIN_TEXT_CHARS = MIN_BLOCK_CHARS * 2


class _Block(NamedTuple):
    """A block that survived the filters, held until its page is judged."""

    text: str
    raw: Any
    block_index: int
    position_in_doc: int


def parse_pdf_text(
    data: bytes,
    *,
    table_regions: Mapping[int, Sequence[TableRegion]] | None = None,
) -> list[ParsedChunk]:
    """Extract one chunk per text block from a PDF.

    ``table_regions`` is the ``{page_number: [TableRegion, ...]}`` map returned
    by `pdf_tables.parse_pdf_tables`; blocks falling inside those regions are
    dropped as the tables' own text (see the module docstring). Omitting it
    extracts every block — which is what a caller that never ran the table pass
    wants, and what step 2 did.

    Never raises: a corrupt or encrypted PDF, or a single failing page,
    degrades to fewer (or zero) chunks, logged as a warning — mirroring alpha's
    per-file/per-page fault tolerance.
    """
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
            page, blocks = _page_blocks(pdf, page_index)
            regions = list((table_regions or {}).get(page_index + 1, ()))
            table_rects = _table_rects(page, regions)

            kept: list[_Block] = []
            for block_index, block in enumerate(blocks):
                position_in_doc = position
                position += 1
                text = _block_text(block)
                if len(text) < MIN_BLOCK_CHARS:
                    continue
                if _overlaps_a_table(block, table_rects):
                    continue
                kept.append(_Block(text, block, block_index, position_in_doc))

            if table_rects and sum(len(item.text) for item in kept) < TABLE_PAGE_MIN_TEXT_CHARS:
                log.info(
                    "pdf_text.table_only_page",
                    extra={"page_index": page_index, "tables_avoided": len(table_rects)},
                )
                continue

            for item in kept:
                chunks.append(
                    ParsedChunk(
                        text=item.text,
                        order=_block_order(page_index, item.block_index),
                        kind=ParsedChunkKind.TEXT,
                        metadata=_build_metadata(
                            block=item.raw,
                            page_index=page_index,
                            block_index=item.block_index,
                            position_in_doc=item.position_in_doc,
                            chunk_index=len(chunks),
                            tables_avoided=len(table_rects),
                        ),
                    )
                )
        return chunks
    finally:
        pdf.close()


def _page_blocks(pdf: Any, page_index: int) -> tuple[Any | None, list[Any]]:
    """One page and its blocks in logical reading order, or `(None, [])`.

    A page that cannot be loaded or rendered costs its own blocks and nothing
    else — the rest of the document is still extracted (alpha's per-page
    tolerance). The page itself comes back too: converting a table region into
    fitz coordinates needs the page's height.
    """
    try:
        page = pdf.load_page(page_index)
        blocks: list[Any] = page.get_text("blocks", sort=True)
    except Exception as exc:
        log.warning("pdf_text.page_failed", extra={"page_index": page_index, "error": str(exc)})
        return None, []
    return page, blocks


def _table_rects(page: Any | None, regions: Sequence[TableRegion]) -> list[Any]:
    """The page's table regions as fitz rectangles.

    Camelot reports ``(x1, y1_bottom, x2, y2_top)`` in PDF coordinates (origin
    bottom-left, y growing upward) while fitz blocks are top-left with y
    growing downward, so the y axis is flipped around the page height — the
    conversion `pdf_tables.py` deliberately left to whoever compares the two.
    """
    if page is None or not regions:
        return []
    height = float(page.rect.height)
    return [
        fitz.Rect(
            float(region.bbox[0]),
            height - float(region.bbox[3]),  # camelot's top edge -> smaller fitz y
            float(region.bbox[2]),
            height - float(region.bbox[1]),  # camelot's bottom edge -> larger fitz y
        )
        for region in regions
    ]


def _overlaps_a_table(block: Any, table_rects: list[Any]) -> bool:
    """Whether enough of the block's area lies inside some table region."""
    if not table_rects or len(block) < _FITZ_BLOCK_MIN_FIELDS:
        return False
    rect = fitz.Rect(block[:4])
    area = rect.get_area()
    if area <= 0:
        return False
    for table_rect in table_rects:
        intersection = rect & table_rect
        if intersection.is_empty:
            continue
        if intersection.get_area() / area >= TABLE_OVERLAP_THRESHOLD:
            return True
    return False


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
    tables_avoided: int,
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
        # How many table regions this page's blocks had to dodge (alpha keeps
        # the same counter): a chunk from a page with tables is a leftover, and
        # that is worth knowing when its neighbours look truncated.
        "tables_avoided": tables_avoided,
        "section_type": "paragraph",
        "layout_mode": "pymupdf_blocks",
        "source_ext": ".pdf",
    }
