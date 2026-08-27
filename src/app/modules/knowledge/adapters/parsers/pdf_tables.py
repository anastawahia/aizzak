"""PDF table extraction (ported from alpha `rag/parsers/pdf_table_extractor.py`
— `extract_tables_from_pdf`; docs/rag-indexing-plan.md §3.1, step 1, item P-06).

What is ported is the **algorithm and its calibration**, not alpha's structure:
the accuracy floor, the minimum-column guard, cross-page table merging, and the
descriptive-caption lookup. Alpha's environment-variable configuration,
`print`-based reporting, and JSON/XLSX disk dumping (`PDF_SAVE_TABLES_TO_DISK`)
are debug infrastructure and are left behind.

**Stream flavor only** (decision س-04). Alpha runs a `lattice`-first / `stream`-
fallback race and keeps the higher-scoring flavor; `lattice` is dropped here for
a licensing reason, not a technical one — in camelot's 0.x line it pulled
ghostscript (**AGPL**) while this project declares itself `Proprietary`. The
declared price: bordered tables are detected more weakly than `stream` would
suggest, and those are common in official Arabic documents. Dropping the flavor
race also removes `_mean_accuracy` and `_min_accuracy_for` — with one flavor
there is nothing to compare.

**Dependency note (plan §6 risk 1, resolved in this step).** The plan named
`camelot-py[base]`; that extra does not exist. In camelot 0.11 it pulled
ghostscript *and* opencv *and* pdftopng — the opposite of its intent — and in
the 1.0+/2.0 line pip warns and ignores it. The line this project pins
(`>=2.0,<3`, which is also the version alpha actually runs) has no ghostscript
at all, so the AGPL trigger behind س-04 is gone; it does carry
`opencv-python-headless` (Apache-2.0) as a **core** dependency, which is the
accepted cost, confined to the optional `parsers` group.

**A temporary file is unavoidable.** `camelot.read_pdf` reads from a *path*,
while this port is object-shaped and only ever holds bytes
(`ports/content_extractor.py`: "never a filesystem path"). The file is written
and unlinked in a `finally` — an implementation detail, not a design choice.

The returned region map is consumed by `pdf_text.py` (wired in plan step 3 /
P-07): a block overlapping one of these regions is the table's own text, and
dropping it is what keeps a table from being indexed twice, once structured
and once as garbled prose.

Not in this step: table-row explosion into one node per row (plan step 7 /
P-13).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

import fitz  # PyMuPDF
import pandas as pd

# From the defining module, not the package: camelot ships `py.typed`, so
# strict mypy's `no_implicit_reexport` rejects `camelot.read_pdf` (its
# `__init__` re-exports without an `__all__`).
from camelot.io import read_pdf

from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.domain.tables import placeholder_header
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# Minimum camelot `accuracy` (0-100) for a `stream` table to survive. 60 is
# alpha's `PDF_STREAM_MIN_ACCURACY`, and it is a threshold on camelot's OWN
# accuracy metric (plan fact ح-15) — it is meaningless outside camelot, which is
# precisely why camelot was kept rather than replaced.
STREAM_MIN_ACCURACY = 60.0

# Camelot's `stream` flavor routinely boxes a column of ordinary prose as a
# single-column "table" and scores it ~100% accurate, so the accuracy floor
# alone cannot catch it. This is the single most valuable noise guard in the
# port: it turns a misdetection into a *missing* table (the text pass will
# index the prose) instead of a *false* one (garbled table, and the text pass
# skips the region — see plan §6 risk 2).
TABLE_MIN_COLUMNS = 2

# The false-table guard (decision س-30, docs/rag-fidelity-audit.md §4-هـ-1).
# A table whose DATA rows are at least half dot-leader rows is a table of
# contents, and is dropped WHOLE -- not row by row, which would leave a
# shredded ToC skeleton and its header behind.
#
# Measured on the live corpus: the one real ToC scores 92.7% (38 of 41 rows;
# the other 3 are a page-break header and footer) and every other table in the
# corpus scores 0%. The gap is total, so 0.5 is a point in empty space rather
# than a value calibrated on the sample -- which is also why this did not have
# to wait on `P-38`'s evaluation set. Anything in 0.05..0.90 would classify
# this corpus identically; 0.5 is chosen for being the least surprising number
# in that range, not for sitting on a measured boundary.
#
# ⚠️ `Column_N` prevalence was TESTED AS A SIGNAL AND REJECTED. It looks like
# the obvious one (37.3% of chunks carry it) but most of those are legitimate
# two-column data whose header row was eaten by the running page header --
# `Definitions and Abbreviations: HMI; Column_2: Human Machine Interface`, 62
# chunks of that shape alone. A guard on it deletes real content. The 37% is a
# HEADER-POLLUTION symptom (د-1/د-3/د-4), not a false-table one.
#
# Why the ToC is the worst false table there is: its rows are lexically
# IDENTICAL to what users ask ("3.9 Mine Auxiliary Transformer") and carry a
# page number instead of an answer -- so they win the sparse leg exactly where
# it is asked, and return nothing. And they occupy `k` slots that cannot be
# bought back, because `default_k` is frozen permanently (§4-أ-1).
_TOC_DOT_LEADER_ROW_RATIO = 0.5

# Six dots is past every ordinary use of the character -- an ellipsis, a
# decimal chain, a truncation marker -- and short of nothing a real leader
# draws. ONLY the literal dot is matched, because the dot is what the corpus
# measurement was taken on: a document that rules its leader with `…` or `·`
# is not covered, and generalizing to those on the assumption they behave the
# same is exactly the unmeasured reasoning that made `Column_N` look good.
_DOT_LEADER = re.compile(r"\.{6,}")

# alpha's `camelot.read_pdf(..., flavor='stream', edge_tol=50)`.
_STREAM_EDGE_TOL = 50

# --------------------------------------------------------------------------- #
# د-3: the running header/footer band, excluded BEFORE camelot looks           #
# --------------------------------------------------------------------------- #
# The root of the `Column_N` epidemic, and the only one of د-1..د-4 that fixes
# it at source. `stream` infers a table's columns from the whitespace gaps in
# whatever text it is given; a running page header sitting above a table joins
# that text, shifts the row it thinks is the header row, and `_headers_and_data`
# then mints `Column_N` for every cell of the real header row it just displaced.
# 37.3% of this corpus's chunks carry one. Nothing downstream can undo it: by
# then the header row is DATA and the header names are gone.
#
# The band is removed from the BYTES, and camelot is handed the result. Neither
# of camelot's own options does this job, and both were tried:
#
# * `table_areas` declares "this rectangle IS one table", which fuses two
#   independent tables on a page into one frame.
# * `table_regions` only steers DETECTION. Measured on a 3-page fixture: with
#   the region set, the detected bbox correctly moved down (842.71 -> 805.72)
#   and the header was still the header row, because
#   `Stream._generate_columns_and_rows` re-runs `text_in_bbox` over the FULL
#   page text inside whatever bbox detection padded its way to. Excluding text
#   from detection does not exclude it from extraction.
#
# Redaction is what د-3 actually asks for -- "exclude the band BEFORE calling
# camelot" -- and it is immune to camelot's bbox padding because the ink is
# simply not there any more. Page geometry is untouched, which matters: the
# `TableRegion` bboxes handed to the text pass, and `_extract_caption`, are all
# in original page coordinates, and a CropBox would have shifted every one of
# them. The ORIGINAL bytes are what captions are still read from.

# Under this many pages "repeats on most pages" is not a measurement. Two
# pages sharing a line is a coincidence with one degree of freedom; three is a
# pattern. A short PDF simply keeps today's behaviour.
_BAND_MIN_PAGES = 3

# Only text in the outer 15% of the page height is a band candidate at all --
# the outer bound on how much this may ever cut, not the cut itself, which the
# gap rule below decides.
_BAND_MAX_FRACTION = 0.15

# A line has to repeat on at least this share of pages to count as running.
# Below it, it is a heading that happens to recur and the cut would follow the
# document's content rather than its furniture.
_BAND_PAGE_RATIO = 0.5

# The gap that ENDS the band, and the rule that makes this safe.
#
# ⚠️ Repetition alone cannot tell a running header from body text, and a probe
# on a synthetic 3-page fixture proved it: the table's own `Name Dept Salary`
# row repeats on every page and sits in the top margin, so a repetition-only
# rule cut the table's header off -- the exact harm د-3 exists to prevent,
# committed by د-3's own guard.
#
# What separates furniture from body is TYPOGRAPHIC, not statistical: a
# running header is followed by the page's top whitespace. So the band ends at
# the last repeated line that has a real gap under it. Inside a table or a
# paragraph, consecutive line boxes nearly touch -- the synthetic table's rows
# are 22pt apart with 15pt line boxes, a 6.9pt gap -- while the same fixture's
# header/body separation measured 25.5pt. 10pt sits well clear of the first
# and well under the second.
_BAND_MIN_GAP_PT = 10.0

# camelot applies one region to every page in the call. Pages of differing size
# would have that region land in a different place on each, so a mixed-size PDF
# is left alone rather than cropped by a rectangle that fits one page.
_PAGE_SIZE_TOLERANCE_PT = 1.0

# "Page 3 of 40" and "Page 4 of 40" are the SAME running header. Folding every
# digit run to one placeholder is what lets the page number -- the thing that
# makes a header look unique on every page -- stop hiding the repetition.
_DIGIT_RUN = re.compile(r"\d+")

# Cross-page continuation geometry (alpha `detect_continued_tables`): the two
# tables must sit at the same horizontal position, within this many points of
# each other's centre, and their widths within 10% of the wider one.
_HORIZONTAL_TOLERANCE = 30.0
_WIDTH_TOLERANCE_RATIO = 0.1

# A continuation page that repeats the header scores above this against the
# first table's headers, and its first row is then dropped as a duplicate
# header rather than merged as data (alpha `merge_continued_tables`).
_HEADER_REPEAT_SIMILARITY = 0.7

# Column-name similarity credit when one name contains the other rather than
# matching exactly (alpha `calculate_column_similarity`).
_PARTIAL_NAME_MATCH_WEIGHT = 0.7

# A caption is a heading, so it is short; longer text above a table is body
# prose and captioning with it would mislabel the table (alpha, `max_len`).
_CAPTION_MAX_LEN = 100

# A fitz text block is `(x0, y0, x1, y1, text, ...)`; anything shorter is not
# one and is skipped rather than unpacked.
_FITZ_BLOCK_MIN_FIELDS = 5

# Building the two-level breadcrumb for a weak (":"-terminated) caption needs
# a second line to prepend — within the nearest block, or from the block above.
_BREADCRUMB_MIN_LINES = 2

# How far above the table's top edge a block may still start and count as
# "above" it, in points — absorbs rounding in the coordinate flip.
_ABOVE_TOLERANCE_PT = 2

# `order = (page_number - 1) * STRIDE + index_on_page`, mirroring `excel.py`'s
# `sheet_index * STRIDE + segment_index`: a deterministic STRUCTURAL ordinal,
# not production order (`ports/content_extractor.py`, parsers.md §6 risk #5).
# A page carrying >= 1000 tables is not a realistic input.
_PAGE_ORDER_STRIDE = 1_000


@dataclass(frozen=True, slots=True)
class TableRegion:
    """Where a detected table sits on its page, so a later pass can avoid it.

    ``bbox`` is camelot's ``(x1, y1_bottom, x2, y2_top)`` in **PDF coordinates**
    (origin bottom-left, y growing upward) — NOT fitz's top-left convention.
    The conversion belongs to whoever compares it against fitz blocks (plan
    step 3), so the raw value is carried across unchanged.
    """

    page_number: int
    bbox: tuple[float, float, float, float]


def parse_pdf_tables(data: bytes) -> tuple[list[ParsedChunk], dict[int, list[TableRegion]]]:
    """Extract structured tables from a PDF.

    Returns the table chunks and a ``{page_number: [TableRegion, ...]}`` map of
    the regions they occupy. Never raises: an encrypted, corrupt or table-free
    PDF degrades to ``([], {})``, logged — a failing table pass must not cost
    the document its text (mirroring `pdf_text.parse_pdf_text`).
    """
    if _is_unreadable(data):
        return [], {}

    tables = _read_stream_tables(data)
    if not tables:
        return [], {}

    tables = _filter_noise(tables)
    if not tables:
        log.info("pdf_tables.all_filtered")
        return [], {}

    groups = _detect_continued_tables(tables)
    return _build_chunks(data, tables, groups)


# --------------------------------------------------------------------------- #
# camelot invocation                                                           #
# --------------------------------------------------------------------------- #
def _is_unreadable(data: bytes) -> bool:
    """Probe with PyMuPDF first: camelot fails opaquely on an encrypted PDF
    (alpha probes for the same reason). A probe that itself fails is not a
    verdict — camelot gets its own chance."""
    try:
        with fitz.open(stream=data, filetype="pdf") as probe:
            if probe.is_encrypted and not probe.authenticate(""):
                log.warning("pdf_tables.encrypted")
                return True
    except Exception as exc:
        log.warning("pdf_tables.probe_failed", extra={"error": str(exc)})
    return False


def _band_signature(text: str) -> str:
    """What makes two lines on two pages "the same line" (د-3).

    Whitespace folded, digits folded, lowercased. The digit fold is the load-
    bearing one: a page number is precisely the part of a running header that
    differs on every page, so comparing the raw text would find no repetition
    at all in the one place repetition is guaranteed.
    """
    return _DIGIT_RUN.sub("#", " ".join(text.split())).lower()


def _page_lines(page: Any) -> list[tuple[float, float, str]]:
    """Every text line on ``page`` as ``(y_top, y_bottom, text)``, fitz's
    top-left origin, sorted down the page.

    LINES and not blocks. fitz groups a column of cells into one block, so a
    block's bbox can span a running header and the table under it at once --
    and a band computed from block extents cuts wherever that grouping happened
    to end. The line bboxes are what actually sit where the ink is.
    """
    lines: list[tuple[float, float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            text = "".join(span["text"] for span in line.get("spans", ()))
            if not text.strip():
                continue
            bbox = line["bbox"]
            lines.append((float(bbox[1]), float(bbox[3]), text))
    lines.sort()
    return lines


def _edge_cut(
    lines: list[tuple[float, float, str]],
    running: frozenset[str],
    *,
    limit: float,
    from_top: bool,
) -> float | None:
    """How deep this page's running furniture reaches in from one edge, or
    ``None`` if it reaches nowhere (د-3).

    Walks IN from the edge over lines that are (a) inside ``limit`` and (b) in
    ``running``, stopping at the first line that is neither -- furniture is
    contiguous with the page edge, so a repeated line with body text above it
    is a recurring heading, not a header.

    The cut is then placed at the last line of that run which has at least
    ``_BAND_MIN_GAP_PT`` of clear space beyond it. That is the rule doing the
    real work: it is what keeps a repeated TABLE header, which touches its own
    first data row, from being mistaken for page furniture that stands alone.
    """
    ordered = lines if from_top else [(-y1, -y0, t) for y0, y1, t in reversed(lines)]
    bound = limit if from_top else -limit
    cut: float | None = None
    for index, (_y0, y1, text) in enumerate(ordered):
        inside = y1 <= bound if from_top else y1 <= -bound
        if not inside or _band_signature(text) not in running:
            break
        # The gap AFTER this line: to the next line further in, or -- if this
        # is the last line on the page -- to nothing, which cannot end a band.
        if index + 1 >= len(ordered):
            break
        if ordered[index + 1][0] - y1 >= _BAND_MIN_GAP_PT:
            cut = y1
    return cut


def _running_band(data: bytes) -> tuple[float, float] | None:
    """Where this PDF's running header and footer end, as
    ``(header_cut, footer_cut)`` in fitz's top-left coordinates -- or ``None``
    when there is no furniture to cut (د-3).

    Detection, not a constant: a band height guessed once would be wrong for
    every document but the one it was measured on. A line is running furniture
    only if it satisfies all three of -- it sits in the outer margin, it
    repeats (digits folded) on at least half the pages, and it is separated
    from what follows by real whitespace. No table header, section heading or
    repeated body row satisfies the three together; see `_BAND_MIN_GAP_PT` for
    the measurement that made the third one necessary.

    The document-wide cut is the deepest one that at least a quorum of pages
    SUPPORT, so a title page or a landscape plate with no furniture cannot veto
    the band, and a single page with an unusually deep header cannot impose it.

    Never raises: a probe that fails is not a verdict, and the caller falls
    back to parsing the whole page exactly as it did before د-3.
    """
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
            if pages < _BAND_MIN_PAGES:
                return None
            width, height = float(doc[0].rect.width), float(doc[0].rect.height)
            if any(
                abs(float(doc[n].rect.width) - width) > _PAGE_SIZE_TOLERANCE_PT
                or abs(float(doc[n].rect.height) - height) > _PAGE_SIZE_TOLERANCE_PT
                for n in range(pages)
            ):
                log.info("pdf_tables.band_skipped_mixed_page_sizes", extra={"pages": pages})
                return None

            per_page = [_page_lines(doc[n]) for n in range(pages)]
    except Exception as exc:
        log.warning("pdf_tables.band_probe_failed", extra={"error": str(exc)})
        return None

    top_limit = height * _BAND_MAX_FRACTION
    bottom_limit = height * (1.0 - _BAND_MAX_FRACTION)
    # `max(2, ...)`: on a 3-page PDF half is 1.5, and a line on ONE page is not
    # running by any reading of the word.
    quorum = max(2, int(pages * _BAND_PAGE_RATIO))

    seen: dict[str, set[int]] = {}
    for n, lines in enumerate(per_page):
        for y0, y1, text in lines:
            if y1 <= top_limit or y0 >= bottom_limit:
                seen.setdefault(_band_signature(text), set()).add(n)
    running = frozenset(key for key, on in seen.items() if len(on) >= quorum)
    if not running:
        return None

    header_cuts = sorted(
        (_edge_cut(lines, running, limit=top_limit, from_top=True) or 0.0 for lines in per_page),
        reverse=True,
    )
    footer_cuts = sorted(
        -(cut) if (cut := _edge_cut(lines, running, limit=bottom_limit, from_top=False)) else height
        for lines in per_page
    )
    header_cut = header_cuts[quorum - 1]
    footer_cut = footer_cuts[quorum - 1]
    if header_cut <= 0.0 and footer_cut >= height:
        return None

    log.info(
        "pdf_tables.running_band_detected",
        extra={"header_cut": header_cut, "footer_cut": footer_cut, "pages": pages},
    )
    return header_cut, footer_cut


def _without_running_band(data: bytes, band: tuple[float, float]) -> bytes | None:
    """``data`` with the running header/footer redacted away, or ``None`` if
    the rewrite fails (د-3).

    Redaction and not a CropBox: it deletes the ink and leaves the page
    rectangle alone, so every coordinate produced downstream -- camelot's table
    bboxes, the `TableRegion`s the text pass avoids, the caption lookup's fitz
    rects -- still means what it meant. A CropBox would have moved the origin
    under all three at once.

    Images are left in place (`PDF_REDACT_IMAGE_NONE`): the target is a line of
    running text, and a figure that merely reaches into the margin is content.

    Nothing here can widen the cut into the body. `_edge_cut` only places a cut
    that has `_BAND_MIN_GAP_PT` of clear space beyond it, so no body line's
    bbox can intersect the rectangles below.
    """
    header_cut, footer_cut = band
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                rect = page.rect
                if header_cut > 0.0:
                    page.add_redact_annot(fitz.Rect(rect.x0, rect.y0, rect.x1, header_cut))
                if footer_cut < rect.y1:
                    page.add_redact_annot(fitz.Rect(rect.x0, footer_cut, rect.x1, rect.y1))
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            stripped: bytes = doc.tobytes()
        return stripped
    except Exception as exc:
        log.warning("pdf_tables.band_strip_failed", extra={"error": str(exc)})
        return None


def _read_stream_tables(data: bytes) -> list[Any]:
    """Run camelot's `stream` flavor over a temporary copy of the bytes, with
    the running header/footer band stripped out first when one was detected
    (د-3).

    The strip is best-effort in both directions: no band detected, or a rewrite
    that fails, both fall back to the original bytes and to exactly the
    behaviour this had before د-3.
    """
    tmp_path: str | None = None
    try:
        band = _running_band(data)
        stripped = _without_running_band(data, band) if band is not None else None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(stripped if stripped is not None else data)
            tmp_path = handle.name
        tables = list(read_pdf(tmp_path, pages="all", flavor="stream", edge_tol=_STREAM_EDGE_TOL))
        log.info("pdf_tables.detected", extra={"table_count": len(tables)})
        return tables
    except Exception as exc:
        log.warning("pdf_tables.read_failed", extra={"error": str(exc)})
        return []
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _filter_noise(tables: list[Any]) -> list[Any]:
    """Drop single-column misdetections, then low-accuracy tables.

    Both filters are **unconditional**. Alpha learned the accuracy one the hard
    way and left the reasoning in place: an earlier "keep the set if filtering
    would empty it" guard meant that on a text-only PDF, the whole page of prose
    that `stream` had boxed as a ~30%-accurate table survived as the sole
    table — demoting the page into a garbled table AND removing it from the
    clean text pass, which avoids table regions. Dropping it lets the page fall
    through and be indexed as real paragraphs.
    """
    multi_col = [t for t in tables if t.df.shape[1] >= TABLE_MIN_COLUMNS]
    if len(multi_col) < len(tables):
        log.info("pdf_tables.dropped_single_column", extra={"count": len(tables) - len(multi_col)})

    kept = [t for t in multi_col if float(getattr(t, "accuracy", 100.0)) >= STREAM_MIN_ACCURACY]
    if len(kept) < len(multi_col):
        log.info(
            "pdf_tables.dropped_low_accuracy",
            extra={"count": len(multi_col) - len(kept), "min_accuracy": STREAM_MIN_ACCURACY},
        )
    return kept


def _is_table_of_contents(df: Any) -> bool:
    """Whether this frame is a table of contents (decision س-30).

    The ratio is taken over DATA rows and **after any cross-page merge**,
    because that is the unit the 92.7%/0% split was measured on: a ToC broken
    over a page boundary is one ToC, and judging its fragments one by one would
    let a fragment diluted by its own preamble rows slip through alone.

    Cells are joined with no separator rather than with a space -- camelot's
    `stream` flavor can cut a leader at a column edge, and re-joining restores
    the run it broke. The only false positive that costs anything here is one
    cell ending in six dots meeting another starting with them, which is a
    leader.
    """
    total = len(df)
    if not total:
        return False
    leaders = sum(1 for _, row in df.iterrows() if _DOT_LEADER.search("".join(str(v) for v in row)))
    return leaders / total >= _TOC_DOT_LEADER_ROW_RATIO


def _is_leader_cell(value: Any) -> bool:
    """Whether ONE cell is a table-of-contents leader rather than a dotted
    placeholder -- the distinction the row guard below is built on.

    A leader is a typographic bridge: it has a LABEL and then dots running out
    to a page number, so the cell reads `"3.9 Mine Auxiliary Transformer
    ........"`. A placeholder is the whole cell -- `".........."` in a Notes
    column, a blank somebody is meant to write into -- and it means only that
    the cell was left open.

    So: dots, AND something else in the same cell. Strip the dot runs and ask
    whether any text is left.

    ⚠️ This predicate is deliberately NARROWER than `_is_table_of_contents`,
    which counts any leader anywhere in the joined row. It has to be, because
    the two guards pay different prices for a mistake. The table-level one
    drops a frame that is 50% leaders -- at that ratio there is no real table
    left to lose. This one drops a single row out of a table that is otherwise
    real, so a false positive here deletes `Cable | .......... | 120`: a
    product, a price, and a dotted blank between them.

    That is the same trade `Column_N` failed and د-1 was rewritten over
    (§4-هـ-1): a guard is only worth having if what it deletes is reliably
    worthless. A cell of pure dots is not a leader, and this returns False for
    it.

    A leader split across two cells by `stream` (`"1. Intro ..."` + `"... 5"`)
    is NOT caught -- neither half carries six dots, and re-joining the row to
    find them would put the placeholder back in range. That is a deliberate
    miss: a split leader means a real ToC, which the table-level ratio already
    drops whole, and a stray row is the one case worth conceding to stay off
    real content.
    """
    text = str(value)
    if not _DOT_LEADER.search(text):
        return False
    return bool(_DOT_LEADER.sub(" ", text).strip())


def _drop_leader_rows(df: Any) -> tuple[Any, int]:
    """Drop the individual dot-leader rows a table keeps after SURVIVING the
    ToC guard (د-4's residue), returning the frame and how many went.

    `_is_table_of_contents` judges a whole frame and drops it whole, which is
    right for a real ToC and blind to the case underneath it: a legitimate data
    table carrying one or two leader rows, because a list of contents ran into
    its top or a section index shares its box. Those rows are the same harm in
    miniature -- lexically identical to what a user asks, answering with a page
    number -- and at two rows in thirty the table-level ratio cannot reach them
    without also deleting the twenty-eight.

    One leader CELL (`_is_leader_cell`, and see there for why that is stricter
    than the ratio's test) is enough to take its row: a ToC entry puts its
    label and its leader in one cell and its page number in the next, so the
    evidence never spans more than the one cell it is asked for.
    """
    if df.empty:
        return df, 0
    leaders = df.apply(lambda row: any(_is_leader_cell(v) for v in row), axis=1)
    dropped = int(leaders.sum())
    if not dropped:
        return df, 0
    return df[~leaders].reset_index(drop=True), dropped


# --------------------------------------------------------------------------- #
# cross-page continuation (alpha detect_continued_tables / merge_continued)    #
# --------------------------------------------------------------------------- #
def _bbox_info(table: Any) -> dict[str, float]:
    bbox = table._bbox  # camelot exposes the table's PDF-coordinate bbox here only
    return {
        "width": float(bbox[2]) - float(bbox[0]),
        "center_x": (float(bbox[0]) + float(bbox[2])) / 2,
    }


def _column_similarity(cols1: list[str], cols2: list[str]) -> float:
    """Similarity (0..1) between two column-name rows (alpha
    `calculate_column_similarity`): exact match scores 1, containment 0.7."""
    if len(cols1) != len(cols2) or not cols1:
        return 0.0
    matches = 0.0
    for c1, c2 in zip(cols1, cols2, strict=True):
        a, b = str(c1).strip().lower(), str(c2).strip().lower()
        if a == b:
            matches += 1
        elif a and b and (a in b or b in a):
            matches += _PARTIAL_NAME_MATCH_WEIGHT
    return matches / len(cols1)


def _first_row(table: Any) -> list[str]:
    df = table.df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        return []
    return [str(c).strip() for c in df.iloc[0]]


def _continues(previous: Any, candidate: Any) -> bool:
    """Whether ``candidate`` continues ``previous`` on the next page.

    Alpha computes a column-name similarity here too, then gates on
    ``similarity > 0.7 or similarity <= 0.7`` — a tautology, with a comment
    explaining that the geometry below is already strong evidence and that a
    dual gate would drop the ambiguous 0.3-0.7 band and break tables whose
    continuation row partially echoes the header. That intent is kept; the
    tautology is not reproduced. Similarity still decides whether the
    continuation's first row is a repeated header, in `_merge_group`.
    """
    if candidate.page - previous.page != 1:
        return False
    if candidate.df.shape[1] != previous.df.shape[1]:
        return False

    prev_box, cand_box = _bbox_info(previous), _bbox_info(candidate)
    if abs(prev_box["center_x"] - cand_box["center_x"]) > _HORIZONTAL_TOLERANCE:
        return False
    width_tolerance = max(prev_box["width"], cand_box["width"]) * _WIDTH_TOLERANCE_RATIO
    return abs(prev_box["width"] - cand_box["width"]) <= width_tolerance


def _detect_continued_tables(tables: list[Any]) -> dict[int, list[int]]:
    """Group tables that are one table spanning several pages.

    Returns ``{first_index: [indices...]}`` for groups of two or more; a table
    absent from every group stands alone. A group chains forward — the last
    accepted table becomes the reference for the next page — so a table running
    across three pages is one table, not three (alpha's stated motivation).
    """
    groups: dict[int, list[int]] = {}
    claimed: set[int] = set()

    for i, first in enumerate(tables):
        if i in claimed:
            continue
        group = [i]
        reference = first
        for j in range(i + 1, len(tables)):
            if j in claimed or not _continues(reference, tables[j]):
                continue
            group.append(j)
            claimed.add(j)
            reference = tables[j]
        if len(group) > 1:
            groups[i] = group
            claimed.update(group)
            log.info(
                "pdf_tables.continued_group",
                extra={"pages": [tables[k].page for k in group]},
            )
    return groups


def _headers_and_data(raw: Any) -> tuple[list[str], Any]:
    """Split a raw camelot frame into header names and a data frame (alpha
    `extract_headers_and_data`): the first row is the header, blank names
    become ``Column_N``, everything below is data."""
    df = raw.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        return [], pd.DataFrame()

    # `placeholder_header` and not a local f-string: `domain/tables.py` has to
    # RECOGNISE what is minted here in order to render it bare (د-1 reworded),
    # and two independent spellings of one name is exactly the drift that
    # would turn a placeholder back into a column somebody appears to have
    # named.
    headers = [
        name if (name := str(c).strip()) and name != "nan" else placeholder_header(i + 1)
        for i, c in enumerate(df.iloc[0])
    ]
    if len(df) == 1:
        return headers, pd.DataFrame(columns=headers)

    data = df.iloc[1:].copy()
    data.columns = headers
    data = data.map(lambda x: str(x).strip() if pd.notna(x) else "")
    data.reset_index(drop=True, inplace=True)
    return headers, data


def _clean_frame(df: Any) -> Any:
    """Drop empty rows/columns and strip every cell (alpha
    `clean_dataframe_simple`) — headers are already set and stay untouched."""
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df = df.map(lambda x: str(x).strip() if pd.notna(x) else "")
    df = df[df.apply(lambda row: any(row != ""), axis=1)]
    df.reset_index(drop=True, inplace=True)
    return df


def _merge_group(tables: list[Any], indices: list[int]) -> tuple[Any, Json]:
    """Merge a continuation group into one frame (alpha
    `merge_continued_tables`). The first table supplies the headers; each
    continuation contributes rows, minus a repeated header row if it has one."""
    headers, first_data = _headers_and_data(tables[indices[0]].df.copy())
    if not headers:
        return pd.DataFrame(), {}

    rows: list[list[str]] = [] if first_data.empty else first_data.values.tolist()
    source_pages: list[int] = [tables[indices[0]].page]
    rows_per_source: list[int] = [len(rows)]

    for idx in indices[1:]:
        table = tables[idx]
        df = table.df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        source_pages.append(table.page)

        first = [str(c).strip() for c in df.iloc[0]]
        repeated_header = _column_similarity(headers, first) > _HEADER_REPEAT_SIMILARITY
        raw_rows = (df.iloc[1:] if repeated_header else df).values.tolist()

        clean = [
            [str(v).strip() if pd.notna(v) else "" for v in row]
            for row in raw_rows
            if any(str(v).strip() for v in row if pd.notna(v))
        ]
        rows.extend(clean)
        rows_per_source.append(len(clean))

    if not rows:
        return pd.DataFrame(), {}

    merged = _clean_frame(pd.DataFrame(rows, columns=headers))
    return merged, {
        "source_pages": source_pages,
        "total_source_tables": len(indices),
        "rows_per_source": rows_per_source,
    }


# --------------------------------------------------------------------------- #
# caption lookup (alpha _extract_table_caption)                                #
# --------------------------------------------------------------------------- #
def _blocks_above(page: Any, bbox: tuple[float, float, float, float]) -> list[tuple[float, str]]:
    """Text blocks sitting above the table and overlapping its x-span, nearest
    first. Camelot's bbox is bottom-left-origin, fitz's blocks are top-left —
    hence the ``page_height - y_top`` flip."""
    table_top = page.rect.height - float(bbox[3])
    x1, x2 = float(bbox[0]), float(bbox[2])

    above: list[tuple[float, str]] = []
    for block in page.get_text("blocks", sort=True):
        if len(block) < _FITZ_BLOCK_MIN_FIELDS:
            continue
        bx0, _by0, bx1, by1, text = block[0], block[1], block[2], block[3], block[4]
        if not str(text).strip():
            continue
        if by1 > table_top + _ABOVE_TOLERANCE_PT:  # not above the table
            continue
        if bx1 < x1 or bx0 > x2:  # does not overlap the table's columns
            continue
        above.append((float(by1), str(text)))

    above.sort(key=lambda item: item[0], reverse=True)  # nearest to the table first
    return above


def _extract_caption(page: Any, bbox: tuple[float, float, float, float]) -> str:
    """Derive a descriptive caption from the text nearest above the table, so
    the chunk carries its heading (e.g. "الملحق — التأمين على الحياة") instead
    of only a positional id. Structural — no phrase list.

    A line ending in ':' is a weak caption ("Quick Reference Table:"): generic,
    not the descriptive section. Alpha then builds a two-level breadcrumb by
    prepending the heading above it, which is kept here.
    """
    try:
        above = _blocks_above(page, bbox)
        if not above:
            return ""

        def lines_of(text: str) -> list[str]:
            return [ln.strip() for ln in text.splitlines() if ln.strip()]

        near_lines = lines_of(above[0][1])
        if not near_lines:
            return ""
        near = near_lines[-1]  # the line closest to the table

        if near.endswith(":"):
            descriptive = ""
            if len(near_lines) >= _BREADCRUMB_MIN_LINES:
                descriptive = near_lines[-2]
            elif len(above) >= _BREADCRUMB_MIN_LINES:
                outer = lines_of(above[1][1])
                descriptive = outer[-1] if outer else ""
            weak = descriptive.endswith(":") or len(descriptive) > _CAPTION_MAX_LEN
            if descriptive and not weak:
                return f"{descriptive} — {near}"

        return near if len(near) <= _CAPTION_MAX_LEN else ""
    except Exception as exc:
        log.warning("pdf_tables.caption_failed", extra={"error": str(exc)})
        return ""


# --------------------------------------------------------------------------- #
# chunk assembly                                                               #
# --------------------------------------------------------------------------- #
def _frame_to_rows(df: Any) -> list[dict[str, str]]:
    """Serialize a frame to row dicts, dropping empty cells and empty rows
    (alpha `create_table_json_text`)."""
    headers = list(df.columns)
    rows: list[dict[str, str]] = []
    for _, row in df.iterrows():
        entry = {
            col: value for col in headers if (value := str(row[col]).strip()) not in ("", "nan")
        }
        if entry:
            rows.append(entry)
    return rows


def _build_metadata(
    *,
    page_number: int,
    table_index: int,
    df: Any,
    caption: str,
    merge_info: Json,
    source: Any,
) -> Json:
    """Assemble the chunk payload.

    The caption lands in ``title`` and is deliberately NOT prepended to the
    chunk text: metadata injected into node text pollutes both the embedding
    and IDF, and source labelling belongs to the display side (plan §7). Alpha
    does prepend it — that is the one place this port diverges from it.
    """
    metadata: Json = {
        "page_number": page_number,
        "page_index": page_number - 1,
        "table_index": table_index,
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "headers": [str(c) for c in df.columns],
        "section_type": "structured_table",
        "layout_mode": "camelot_stream",
        "chunk_type": "pdf_table",
        "source_ext": ".pdf",
        "is_merged_table": bool(merge_info),
    }
    if caption:
        metadata["title"] = caption
    if merge_info:
        metadata.update(merge_info)
    else:
        metadata["accuracy"] = float(getattr(source, "accuracy", 0.0))
        metadata["bbox"] = [float(v) for v in source._bbox]
    return metadata


def _surviving_frame(df: Any, *, table_index: int, page_number: int) -> Any | None:
    """The two false-content guards, in the one order they may run, applied to
    one already-hydrated frame. ``None`` means "emit no chunk for this table".

    **س-30 first, and this is not arbitrary.** The ToC guard is a verdict on
    the WHOLE frame; the leader-row drop is a verdict on single rows. Run the
    row drop first and a real table of contents would be emptied one row at a
    time and fall out of the caller as merely "empty" -- losing س-30's log
    line, its rows-dropped count, and the reasoning that distinguishes a false
    table from a table that parsed to nothing.

    Neither guard touches the REGION the caller has already recorded. That is
    deliberate for the ToC (the text pass must skip it too, rather than be
    handed the same lines back as prose) and irrelevant for a leader row, which
    is a row inside a region that stays either way.
    """
    # س-30. Unlike the two guards in `_filter_noise`, this one KEEPS the region
    # already recorded, so the text pass skips the ToC as well. Those guards
    # hand a misdetection back to the text pass because the content under them
    # is real prose that belongs in the index; a ToC is not. Its harm is
    # lexical -- rows that read exactly like the question and answer it with a
    # page number -- and re-indexing it as paragraphs would carry that harm
    # across intact, merely reshaped. Nothing is lost: a ToC's titles are
    # already in the body as headings, and its page numbers were never an
    # answer.
    if _is_table_of_contents(df):
        log.info(
            "pdf_tables.dropped_table_of_contents",
            extra={"table_index": table_index, "page_number": page_number, "rows": len(df)},
        )
        return None

    # د-4's residue: what reaches here is by definition not a ToC, so a leader
    # row in it is a stray and goes on its own.
    kept, leader_rows = _drop_leader_rows(df)
    if leader_rows:
        log.info(
            "pdf_tables.dropped_leader_rows",
            extra={"table_index": table_index, "page_number": page_number, "rows": leader_rows},
        )
    if kept.empty:
        log.info("pdf_tables.skipped_all_leader_rows", extra={"table_index": table_index})
        return None
    return kept


def _build_chunks(
    data: bytes, tables: list[Any], groups: dict[int, list[int]]
) -> tuple[list[ParsedChunk], dict[int, list[TableRegion]]]:
    """Turn the filtered/grouped camelot tables into chunks + region map."""
    try:
        caption_doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # captions are a bonus, never a reason to lose tables
        log.warning("pdf_tables.caption_doc_failed", extra={"error": str(exc)})
        caption_doc = None

    chunks: list[ParsedChunk] = []
    regions: dict[int, list[TableRegion]] = {}
    per_page_count: dict[int, int] = {}
    claimed = {idx for group in groups.values() for idx in group}

    try:
        for index, table in enumerate(tables):
            if index in claimed and index not in groups:
                continue  # already merged into the group that starts earlier

            if index in groups:
                df, merge_info = _merge_group(tables, groups[index])
                sources = [tables[i] for i in groups[index]]
            else:
                _headers, df = _headers_and_data(table.df.copy())
                merge_info = {}
                sources = [table]

            if df.empty:
                log.info("pdf_tables.skipped_empty", extra={"table_index": index})
                continue

            source = sources[0]
            page_number = int(source.page)

            # A merged table occupies a region on EVERY page it spans, and the
            # text pass must avoid all of them, not just the first. Recorded
            # BEFORE the false-table guard below, deliberately: see there.
            for src in sources:
                regions.setdefault(int(src.page), []).append(
                    TableRegion(
                        page_number=int(src.page),
                        bbox=tuple(float(v) for v in src._bbox),  # type: ignore[arg-type]
                    )
                )

            df = _surviving_frame(df, table_index=index, page_number=page_number)
            if df is None:
                continue

            caption = ""
            if caption_doc is not None:
                caption = _extract_caption(caption_doc[page_number - 1], source._bbox)

            seq = per_page_count.get(page_number, 0)
            per_page_count[page_number] = seq + 1
            chunks.append(
                ParsedChunk(
                    text=json.dumps(
                        {"headers": [str(c) for c in df.columns], "rows": _frame_to_rows(df)},
                        ensure_ascii=False,
                    ),
                    order=(page_number - 1) * _PAGE_ORDER_STRIDE + seq,
                    kind=ParsedChunkKind.TABLE,
                    metadata=_build_metadata(
                        page_number=page_number,
                        table_index=index,
                        df=df,
                        caption=caption,
                        merge_info=merge_info,
                        source=source,
                    ),
                )
            )
        return chunks, regions
    finally:
        if caption_doc is not None:
            caption_doc.close()
