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

**The read-back half (P-42, plan §4 step 18, §3.10).** The ladder above is
also why ``collapse_parent_runs`` lives in THIS module rather than beside
its only caller (``adapters/sql_repository.py::chunk_texts``): P-13 is the
only writer of ``parent_chunks``, so "may a parent stand in place of the
rows under it?" is a question about the ladder, not about SQL. Keeping the
rule that CONSUMES the header-only parent next to the rung that MINTS it is
what stops the two halves from drifting apart — they already did once, and
the summary of every data file silently became a list of column names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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

# The strings a cell carries when it was EMPTY in the source rather than when
# it said anything (port-fidelity audit §3-هـ, restoring alpha
# ``table_processor.py:47``): a parser that stringified a ``None``/SQL
# ``NULL`` before this module ever saw the value. Rendering them writes
# "Notes: None" into an embedded sentence -- a claim the table never made,
# and one that then competes for retrieval against the rows that did.
#
# ⚠️ ``"nan"`` is deliberately NOT in this set, even though every table
# parser drops it. Each parser drops it where the TYPE is still visible
# (``excel.py``'s ``math.isnan``, ``pdf_tables.py``'s pandas-rendered
# ``"nan"``); by the time a value reaches this module it is a bare string,
# and a chemistry sheet's literal "NaN" reading is then the same three
# characters as a missing float. Dropping both would delete a measurement in
# order to tidy a placeholder. ``none``/``null`` carry no such reading.
_EMPTY_CELL_TEXTS = frozenset({"none", "null"})


def _fold_whitespace(text: str) -> str:
    r"""Collapse every run of whitespace to ONE space and strip the ends.

    ``row_to_sentence`` renders a row as a single LINE (``"; "``-joined), so a
    newline inside one cell does not merely read untidily -- it breaks the
    sentence that gets embedded into pieces. Camelot's ``stream`` flavour
    produces such cells in bulk: a multi-line cell is how it represents a
    wrapped column. Headers get the same treatment for the same reason, and
    it matters more there, not less: a wrapped column NAME lands in every
    single row's sentence, not in one.

    This is alpha's ``str(val).replace("\n", " ")``
    (``table_processor.py:46``) widened by one step, on purpose. A bare
    ``replace`` leaves the ``\r`` of a ``\r\n`` standing in the text, and
    leaves the double space it just created -- it repairs the shape it was
    aimed at and not the one beside it.
    """
    return " ".join(text.split())


def _is_noise_header(header: str) -> bool:
    # Folded, not merely stripped, so this predicate reads the SAME text the
    # renderers below emit. It decides nothing differently today -- no name in
    # `_NOISE_HEADERS` contains whitespace, so folding an interior run can
    # never turn a non-match into a match -- and that is the point: the
    # normalisation lives in one function rather than being re-chosen at each
    # of the three call sites, where the next entry added to the set would
    # find them already agreeing.
    return _fold_whitespace(header).lower() in _NOISE_HEADERS


def _cell_text(value: Any) -> str:
    """A cell's sentence-ready text, or ``""`` for an empty cell -- ``None``,
    an empty or whitespace-only string, or one of ``_EMPTY_CELL_TEXTS``.
    Callers drop the empty ones rather than render ``"Column: "``.

    Whatever survives is whitespace-FOLDED (``_fold_whitespace``), so one
    cell contributes exactly one line's worth of text to the sentence it
    lands in.
    """
    if value is None:
        return ""
    folded = _fold_whitespace(str(value))
    return "" if folded.lower() in _EMPTY_CELL_TEXTS else folded


def row_to_sentence(row: Mapping[str, Any]) -> str:
    """Render one table row as ``"العمود: القيمة; العمود: القيمة"`` --
    language-neutral (only the punctuation ``": "``/``"; "``, never an
    English connective word), dropping noise-header columns
    (``_NOISE_HEADERS``, case/whitespace-insensitive) and empty values
    (``_cell_text``, which also decides what "empty" covers).

    The result is ONE line by construction: both halves of every pair are
    whitespace-folded, so no cell and no column name can split the sentence
    this row is embedded and retrieved as.

    A row that is entirely noise/empty columns renders as ``""`` -- not an
    error, and not this module's job to drop: `domain/chunking.py`'s node
    filter (P-15, plan step 8) is what removes an empty node from the
    stream.
    """
    parts = [
        f"{_fold_whitespace(header)}: {text}"
        for header, value in row.items()
        if not _is_noise_header(header) and (text := _cell_text(value))
    ]
    return "; ".join(parts)


def _header_line(headers: Sequence[str]) -> str:
    """The header-only parent text (R > ``TABLE_PARENT_MAX_ROWS``): the
    column names alone, noise columns dropped, folded and joined the same
    language-neutral way as a row sentence."""
    kept = [
        folded
        for header in headers
        if (folded := _fold_whitespace(header)) and not _is_noise_header(header)
    ]
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

    ``parent_is_complete`` says WHICH rung of the ladder minted
    ``parent_text``: ``True`` only for the ``R <= TABLE_PARENT_MAX_ROWS``
    whole-table parent, which really does contain every row it parents;
    ``False`` for the header-only parent, which contains the column names
    and NOT ONE of the values under them. Every consumer that lets a parent
    stand IN PLACE OF its rows (``collapse_parent_runs`` below, P-42) must
    read this bit first -- see that function's docstring for what happens
    when it does not.
    """

    row_sentences: tuple[str, ...]
    parent_text: str
    parent_is_complete: bool
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
        parent_is_complete = False
    elif total <= TABLE_PARENT_MAX_ROWS:
        parent_text = "\n".join(row_to_sentence(row) for row in rows)
        parent_is_complete = True
    else:
        parent_text = _header_line(headers)
        parent_is_complete = False

    overflow_text = ""
    if truncated:
        overflow_text = " ".join(row_to_sentence(row) for row in rows[TABLE_ROW_HARD_CAP:])

    return ExplodedTable(
        row_sentences=row_sentences,
        parent_text=parent_text,
        parent_is_complete=parent_is_complete,
        truncated=truncated,
        overflow_text=overflow_text,
    )


@dataclass(frozen=True, slots=True)
class ChunkParent:
    """The ``parent_chunks`` row a ``Chunk`` was cut from, as
    ``collapse_parent_runs`` needs to see it: the parent's own ``id`` (the
    run key), its ``text``, and whether that text is COMPLETE -- i.e.
    ``ExplodedTable.parent_is_complete``, carried through
    ``knowledge.parent_chunks.is_complete``."""

    id: str
    text: str
    is_complete: bool


@dataclass(frozen=True, slots=True)
class ParentedChunkText:
    """One ``knowledge.chunks`` row as read back for summarisation: its own
    leaf ``text``, plus the ``parent`` it hangs under (``None`` for every
    chunk that never came from a table row explosion -- the common case)."""

    text: str
    parent: ChunkParent | None = None


def collapse_parent_runs(rows: Iterable[ParentedChunkText]) -> list[str]:
    """P-42 (plan §4 step 18, §3.10): turn a document's chunk rows, in
    ``seq`` (reading) order, into the coarser text sections a summariser
    should read -- "~40 coherent sections instead of ~240 fragments".

    Every chunk under one COMPLETE parent collapses into a SINGLE appearance
    of that parent's text, at the position of the first such chunk: a
    complete parent already holds every one of their texts, so keeping the
    leaves too would only repeat the same content twice.

    That rule is keyed on the parent's id for the WHOLE document, not on a
    consecutive run, since ``P-34``/س-27 = أ made "same parent, not adjacent"
    a shape this pipeline genuinely produces. A table and the prose blocks
    around it share a page yet get DIFFERENT parents (a page parent never
    swallows a table, ``_attach_text_parents``), and both parents' chunks
    interleave in ``seq``: on a PDF page, a table's structural ordinal and a
    text block's are drawn from the same per-page stride. Collapsing per run
    would then write that page's text once per interruption. Nothing is lost
    by writing it once instead -- the second appearance would be the same
    bytes as the first, which is exactly why this is de-duplication and not
    the dropping-from-the-middle the leaf branch below still refuses.

    Everything else keeps its OWN leaf text, on its own line, with NO dedup
    between rows -- the "falls back to the leaf text so no content is lost"
    half of P-42, and it covers TWO cases, not one:

    * a chunk with no parent at all (ordinary prose), and
    * a chunk whose parent is INCOMPLETE (``is_complete=False``) -- the
      header-only parent of a table with more than ``TABLE_PARENT_MAX_ROWS``
      rows.

    The second case is the whole reason this function exists rather than a
    ``coalesce`` + "drop consecutive duplicates" pair in SQL. A header-only
    parent holds the column names and none of the values: letting it stand
    in for its rows would feed a 30-row table into the summariser as the
    single line ``"Name; Salary; Dept"`` and drop all thirty rows -- and
    since an Excel sheet chunks at up to 500 rows a block, the summary of an
    ordinary data file would become a list of column headings. Both halves
    of the rule -- WHICH text represents a row, and WHETHER a run collapses
    -- therefore turn on ``is_complete``, and both are decided here, once,
    over a plain LEFT JOIN's rows.
    """
    texts: list[str] = []
    written_parents: set[str] = set()
    for row in rows:
        parent = row.parent
        if parent is None or not parent.is_complete:
            # No parent, or one that cannot speak for its rows: the leaf's
            # own text, never collapsed against its neighbours -- including
            # a neighbour holding byte-identical text (two chunks that
            # happen to read the same are two chunks).
            texts.append(row.text)
            continue
        if parent.id in written_parents:
            continue
        texts.append(parent.text)
        written_parents.add(parent.id)
    return texts
