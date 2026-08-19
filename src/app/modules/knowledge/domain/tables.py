"""Table row explosion + parent-chunk selection (P-13, rag-indexing-plan.md
§3.3, §3.2). Pure domain: no I/O, no provider calls, and no knowledge of a
parser's raw ``ParsedChunk.text`` being a JSON dump of ``{headers, rows}`` --
decoding that string is ``application/indexing.py``'s job (the one place
that already knows every table parser -- PDF/Excel/DOCX alike -- emits that
exact shape on purpose, plan §7). This module only ever sees the already-
decoded ``headers``/``rows`` structures.

Every table-kind ``ParsedChunk`` explodes into ONE node per row plus (at
most) ONE parent-chunk candidate for the whole table, following the
row-count ladder decided by §3.3 / س-07::

    R <= TABLE_PARENT_MAX_ROWS (20)      -> every row a node; the WHOLE
                                             table (every row's own sentence,
                                             newline-joined) becomes the
                                             parent text
    TABLE_PARENT_MAX_ROWS < R
        <= TABLE_ROW_HARD_CAP (2000)     -> every row a node; only the
                                             HEADER line becomes the parent
                                             text
    R > TABLE_ROW_HARD_CAP               -> the first TABLE_ROW_HARD_CAP
                                             rows explode as above (header
                                             parent); the remainder is
                                             ``truncated`` and handed back as
                                             ONE blob of row-sentence text
                                             (``ExplodedTable.overflow_text``)
                                             for the caller to fold through
                                             the ordinary word-window
                                             chunker, rather than being
                                             dropped silently -- the same
                                             ``truncated`` pattern
                                             ``application/summarization.py``'s
                                             ``SummaryDraft`` already uses.

**Why explosion, not description.** "How much does Ahmad make?" needs
AHMAD's row whole, not a three-row fragment cut in half — the plan calls
this the single highest-leverage retrieval change in the whole indexing
half (§3.3).

**Why the cap.** An unbounded 5000-row sheet would cost 5000 embedding
calls + 5000 Qdrant points + 5000 ``knowledge.chunks`` rows for ONE file;
the cap trades a declared truncation for a bounded blow-up (§3.3, plan
risk #6) — truncation that lies about itself is the one thing this
platform never does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Row-count ladder threshold (§3.3, س-07): tables at or under this many rows
# get the WHOLE table as their parent chunk; larger tables get only the
# header -- a full 2000-row parent would itself be the "N-times blow-up"
# `0005_parent_chunks.py`'s single-parent-row design exists to avoid.
TABLE_PARENT_MAX_ROWS = 20

# Hard cap on rows exploded into their own node, per table (§3.3, plan
# risk #6). Rows beyond this are never dropped -- they fold into
# ``ExplodedTable.overflow_text`` instead (see the module docstring).
TABLE_ROW_HARD_CAP = 2000

# Noise-header drop list (§3.3): row-ordinal/id columns that would otherwise
# render as "No.: 7" or "ID: 42" in every single row's sentence -- pure
# repeated noise, unlike a real data column.
_NOISE_HEADERS = frozenset({"no.", "no", "row", "#", "index", "id"})


def _is_noise_header(header: str) -> bool:
    return header.strip().lower() in _NOISE_HEADERS


def _cell_text(value: Any) -> str:
    """A cell's sentence-ready text, or ``""`` for an empty cell (``None``,
    an empty string, or a whitespace-only string) -- callers drop the empty
    ones rather than render ``"Column: "``."""
    if value is None:
        return ""
    return str(value).strip()


def row_to_sentence(row: Mapping[str, Any]) -> str:
    """Render one table row as ``"العمود: القيمة; العمود: القيمة"`` --
    language-neutral (only the punctuation ``": "``/``"; "``, never an
    English connective word), dropping noise-header columns
    (``_NOISE_HEADERS``, case/whitespace-insensitive) and empty values.

    A row that is entirely noise/empty columns renders as ``""`` -- not an
    error, and not this module's job to drop: `domain/chunking.py`'s node
    filter (P-15, plan step 8) is what removes an empty node from the
    stream.
    """
    parts = [
        f"{header.strip()}: {text}"
        for header, value in row.items()
        if not _is_noise_header(header) and (text := _cell_text(value))
    ]
    return "; ".join(parts)


def _header_line(headers: Sequence[str]) -> str:
    """The header-only parent text (R > ``TABLE_PARENT_MAX_ROWS``): the
    column names alone, noise columns dropped, joined the same
    language-neutral way as a row sentence."""
    kept = [header.strip() for header in headers if header.strip() and not _is_noise_header(header)]
    return "; ".join(kept)


@dataclass(frozen=True, slots=True)
class ExplodedTable:
    """One table's row-explosion result (§3.3).

    ``row_sentences`` is one entry per node the caller should index (never
    more than ``TABLE_ROW_HARD_CAP``, in row order). ``parent_text`` is what
    the caller persists as this table's single ``parent_chunks`` row (empty
    when the table had no rows at all -- nothing to be a parent of).
    ``truncated``/``overflow_text`` carry the declared-not-silent remainder
    past the hard cap (``False``/``""`` when the table never reached it).
    """

    row_sentences: tuple[str, ...]
    parent_text: str
    truncated: bool
    overflow_text: str


def explode_table(headers: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> ExplodedTable:
    """Explode one table's ``rows`` into per-row sentences plus its parent
    text, per the row-count ladder (§3.3, module docstring).

    ``headers`` is only consulted for the ``R > TABLE_PARENT_MAX_ROWS``
    header-only parent -- every row's own sentence is built from THAT row's
    own keys (not ``headers``), matching the parsers: a PDF-table row only
    carries the columns that were non-empty for THAT row
    (``pdf_tables.py::_frame_to_rows``), which can be narrower than the
    table's full header list.
    """
    total = len(rows)
    truncated = total > TABLE_ROW_HARD_CAP
    kept_rows = rows[:TABLE_ROW_HARD_CAP]
    row_sentences = tuple(row_to_sentence(row) for row in kept_rows)

    if total == 0:
        parent_text = ""
    elif total <= TABLE_PARENT_MAX_ROWS:
        parent_text = "\n".join(row_to_sentence(row) for row in rows)
    else:
        parent_text = _header_line(headers)

    overflow_text = ""
    if truncated:
        overflow_text = " ".join(row_to_sentence(row) for row in rows[TABLE_ROW_HARD_CAP:])

    return ExplodedTable(
        row_sentences=row_sentences,
        parent_text=parent_text,
        truncated=truncated,
        overflow_text=overflow_text,
    )
