"""Unit tests for the knowledge module's P-13 table row explosion (plan
§3.3): ``domain/tables.py``'s ``row_to_sentence``/``explode_table``, plus
the P-42 read-back rule that consumes what the ladder mints
(``collapse_parent_runs``, plan §4 step 18 / §3.10). Pure domain -- no I/O,
no fakes, no event loop.
"""

from __future__ import annotations

from app.modules.knowledge.domain.tables import (
    TABLE_PARENT_MAX_ROWS,
    TABLE_ROW_HARD_CAP,
    ChunkParent,
    ExplodedTable,
    ParentedChunkText,
    collapse_parent_runs,
    explode_table,
    row_to_sentence,
)


# --------------------------------------------------------------------------- #
# row_to_sentence                                                             #
# --------------------------------------------------------------------------- #
def test_row_to_sentence_basic_format() -> None:
    assert row_to_sentence({"الاسم": "أحمد", "الراتب": "5000"}) == "الاسم: أحمد; الراتب: 5000"


def test_row_to_sentence_preserves_column_order() -> None:
    row = {"c": "3", "a": "1", "b": "2"}
    assert row_to_sentence(row) == "c: 3; a: 1; b: 2"


def test_row_to_sentence_drops_none_values() -> None:
    assert row_to_sentence({"Name": "Ahmad", "Notes": None}) == "Name: Ahmad"


def test_row_to_sentence_drops_empty_string_values() -> None:
    assert row_to_sentence({"Name": "Ahmad", "Notes": "   "}) == "Name: Ahmad"


def test_row_to_sentence_strips_cell_whitespace() -> None:
    assert row_to_sentence({"Name": "  Ahmad  "}) == "Name: Ahmad"


def test_row_to_sentence_coerces_non_string_values() -> None:
    assert row_to_sentence({"Count": 5, "Score": 3.5, "Active": True}) == (
        "Count: 5; Score: 3.5; Active: True"
    )


def test_row_to_sentence_has_no_english_connective_words() -> None:
    sentence = row_to_sentence({"Name": "Ahmad", "City": "Amman"})
    for word in ("and", "is", "the"):
        assert f" {word} " not in f" {sentence} "


def test_row_to_sentence_drops_noise_headers_case_and_whitespace_insensitive() -> None:
    row = {"No.": "1", " id ": "42", "Row": "x", "#": "y", "Index": "z", "Name": "Ahmad"}
    assert row_to_sentence(row) == "Name: Ahmad"


def test_row_to_sentence_all_noise_or_empty_row_is_empty_string() -> None:
    assert row_to_sentence({"No.": "1", "Notes": None}) == ""


def test_row_to_sentence_empty_row_is_empty_string() -> None:
    assert row_to_sentence({}) == ""


# --------------------------------------------------------------------------- #
# explode_table                                                               #
# --------------------------------------------------------------------------- #
def _row(name: str, salary: str) -> dict[str, str]:
    return {"Name": name, "Salary": salary}


def test_explode_table_row_count_at_or_under_threshold_uses_full_table_as_parent() -> None:
    headers = ["Name", "Salary"]
    rows = [_row("Ahmad", "5000"), _row("Sara", "6000")]

    result = explode_table(headers, rows)

    assert result.row_sentences == ("Name: Ahmad; Salary: 5000", "Name: Sara; Salary: 6000")
    assert result.parent_text == "Name: Ahmad; Salary: 5000\nName: Sara; Salary: 6000"
    assert result.truncated is False
    assert result.overflow_text == ""


def test_explode_table_exactly_at_parent_max_rows_still_uses_full_table() -> None:
    headers = ["Name"]
    rows = [{"Name": f"person-{i}"} for i in range(TABLE_PARENT_MAX_ROWS)]

    result = explode_table(headers, rows)

    assert len(result.row_sentences) == TABLE_PARENT_MAX_ROWS
    assert result.parent_text.count("\n") == TABLE_PARENT_MAX_ROWS - 1
    assert result.truncated is False


def test_explode_table_row_count_over_threshold_uses_header_only_as_parent() -> None:
    headers = ["Name", "Salary"]
    rows = [_row(f"person-{i}", "1000") for i in range(TABLE_PARENT_MAX_ROWS + 1)]

    result = explode_table(headers, rows)

    assert len(result.row_sentences) == TABLE_PARENT_MAX_ROWS + 1
    assert result.parent_text == "Name; Salary"
    assert result.truncated is False


def test_explode_table_header_parent_drops_noise_headers() -> None:
    headers = ["No.", "Name", "Salary"]
    rows = [_row(f"person-{i}", "1000") for i in range(TABLE_PARENT_MAX_ROWS + 1)]

    result = explode_table(headers, rows)

    assert result.parent_text == "Name; Salary"


def test_explode_table_hard_cap_keeps_first_2000_rows_as_nodes() -> None:
    rows = [{"Name": f"p{i}"} for i in range(TABLE_ROW_HARD_CAP + 50)]

    result = explode_table(["Name"], rows)

    assert len(result.row_sentences) == TABLE_ROW_HARD_CAP
    assert result.row_sentences[0] == "Name: p0"
    assert result.row_sentences[-1] == f"Name: p{TABLE_ROW_HARD_CAP - 1}"


def test_explode_table_hard_cap_declares_truncated_and_returns_overflow() -> None:
    rows = [{"Name": f"p{i}"} for i in range(TABLE_ROW_HARD_CAP + 3)]

    result = explode_table(["Name"], rows)

    assert result.truncated is True
    assert result.overflow_text == (
        f"Name: p{TABLE_ROW_HARD_CAP} Name: p{TABLE_ROW_HARD_CAP + 1} "
        f"Name: p{TABLE_ROW_HARD_CAP + 2}"
    )


def test_explode_table_hard_cap_still_uses_header_only_parent() -> None:
    rows = [{"Name": f"p{i}"} for i in range(TABLE_ROW_HARD_CAP + 3)]

    result = explode_table(["Name"], rows)

    assert result.parent_text == "Name"


def test_explode_table_exactly_at_hard_cap_is_not_truncated() -> None:
    rows = [{"Name": f"p{i}"} for i in range(TABLE_ROW_HARD_CAP)]

    result = explode_table(["Name"], rows)

    assert result.truncated is False
    assert result.overflow_text == ""
    assert len(result.row_sentences) == TABLE_ROW_HARD_CAP


def test_explode_table_no_rows_returns_empty_result() -> None:
    result = explode_table(["Name"], [])
    assert result == ExplodedTable(
        row_sentences=(),
        parent_text="",
        parent_is_complete=False,
        truncated=False,
        overflow_text="",
    )


def test_explode_table_row_sentence_uses_the_rows_own_keys_not_headers() -> None:
    """A PDF-table row only carries the columns non-empty for THAT row
    (``pdf_tables.py::_frame_to_rows``) -- narrower than the table's full
    header list. ``explode_table`` must render exactly what the row has."""
    headers = ["Name", "Salary", "Notes"]
    rows = [{"Name": "Ahmad", "Salary": "5000"}]  # "Notes" column absent for this row

    result = explode_table(headers, rows)

    assert result.row_sentences == ("Name: Ahmad; Salary: 5000",)


# --------------------------------------------------------------------------- #
# explode_table's parent_is_complete (the ladder rung, read back by P-42)     #
# --------------------------------------------------------------------------- #
def test_explode_table_whole_table_parent_is_marked_complete() -> None:
    """The ``R <= TABLE_PARENT_MAX_ROWS`` rung really does hold every row it
    parents -- the only shape allowed to stand in place of those rows."""
    rows = [_row(f"person-{i}", "1000") for i in range(TABLE_PARENT_MAX_ROWS)]

    result = explode_table(["Name", "Salary"], rows)

    assert result.parent_is_complete is True


def test_explode_table_header_only_parent_is_marked_incomplete() -> None:
    """One row past the threshold and the parent is column NAMES with not a
    single value under them -- ``is_complete`` is the bit that stops P-42
    from feeding that line to a summariser as if it were the table."""
    rows = [_row(f"person-{i}", "1000") for i in range(TABLE_PARENT_MAX_ROWS + 1)]

    result = explode_table(["Name", "Salary"], rows)

    assert result.parent_text == "Name; Salary"
    assert result.parent_is_complete is False


def test_explode_table_hard_cap_parent_is_marked_incomplete() -> None:
    rows = [{"Name": f"person-{i}"} for i in range(TABLE_ROW_HARD_CAP + 3)]

    result = explode_table(["Name"], rows)

    assert result.truncated is True
    assert result.parent_is_complete is False


def test_explode_table_no_rows_has_no_parent_and_is_not_complete() -> None:
    """No rows, no parent text -- and ``is_complete`` must not claim
    otherwise about a parent that will never be written."""
    result = explode_table(["Name"], [])

    assert result.parent_text == ""
    assert result.parent_is_complete is False


# --------------------------------------------------------------------------- #
# collapse_parent_runs (P-42, plan §4 step 18 / §3.10)                        #
# --------------------------------------------------------------------------- #
def _leaf(text: str) -> ParentedChunkText:
    return ParentedChunkText(text=text)


def _under(parent: ChunkParent, text: str) -> ParentedChunkText:
    return ParentedChunkText(text=text, parent=parent)


def test_collapse_parent_runs_no_rows_is_no_text() -> None:
    assert collapse_parent_runs([]) == []


def test_collapse_parent_runs_keeps_every_parentless_chunk_with_no_dedup() -> None:
    """The common case (no table in the document): every leaf survives, in
    order -- including two that happen to hold identical text, which are two
    chunks and not one."""
    rows = [_leaf("intro"), _leaf("body"), _leaf("intro")]

    assert collapse_parent_runs(rows) == ["intro", "body", "intro"]


def test_collapse_parent_runs_collapses_a_complete_parents_run_into_one_appearance() -> None:
    """The "~40 sections instead of ~240 fragments" win (§3.10): a table of
    at most ``TABLE_PARENT_MAX_ROWS`` rows has a parent holding all of them,
    so the rows themselves would only repeat it."""
    parent = ChunkParent(id="p1", text="Name: Ahmad; Salary: 5000", is_complete=True)
    rows = [_under(parent, "row 0"), _under(parent, "row 1"), _under(parent, "row 2")]

    assert collapse_parent_runs(rows) == ["Name: Ahmad; Salary: 5000"]


def test_collapse_parent_runs_keeps_every_row_under_an_incomplete_parent() -> None:
    """**The step 7 x step 18 defect.** A table past ``TABLE_PARENT_MAX_ROWS``
    gets a HEADER-ONLY parent; collapsing that run would feed the summariser
    the single line ``"Name; Salary"`` and drop every row's values. P-42's
    own words: falls back to the leaf text, with no dedup, so no content is
    lost."""
    header_parent = ChunkParent(id="p1", text="Name; Salary", is_complete=False)
    rows = [
        _under(header_parent, "Name: Ahmad; Salary: 5000"),
        _under(header_parent, "Name: Sara; Salary: 6000"),
        _under(header_parent, "Name: Omar; Salary: 7000"),
    ]

    assert collapse_parent_runs(rows) == [
        "Name: Ahmad; Salary: 5000",
        "Name: Sara; Salary: 6000",
        "Name: Omar; Salary: 7000",
    ]


def test_collapse_parent_runs_never_substitutes_an_incomplete_parents_text() -> None:
    """Not only "do not collapse": an incomplete parent must not REPLACE a
    single leaf's text either -- three copies of the header line lose the
    same content the collapse would, just more verbosely."""
    header_parent = ChunkParent(id="p1", text="Name; Salary", is_complete=False)

    result = collapse_parent_runs([_under(header_parent, "Name: Ahmad; Salary: 5000")])

    assert result == ["Name: Ahmad; Salary: 5000"]
    assert "Name; Salary" not in result


def test_collapse_parent_runs_keeps_prose_around_a_collapsed_table() -> None:
    """A document with a table AND prose keeps every prose chunk while the
    table's rows still collapse -- P-42's two halves in one document."""
    parent = ChunkParent(id="p1", text="table body", is_complete=True)
    rows = [_leaf("intro"), _under(parent, "row a"), _under(parent, "row b"), _leaf("intro")]

    assert collapse_parent_runs(rows) == ["intro", "table body", "intro"]


def test_collapse_parent_runs_keeps_two_adjacent_tables_apart() -> None:
    """Two tables back to back are two runs: the key is the parent's id, so
    neither table's text swallows the other's."""
    first = ChunkParent(id="p1", text="first table", is_complete=True)
    second = ChunkParent(id="p2", text="second table", is_complete=True)
    rows = [_under(first, "a"), _under(first, "b"), _under(second, "c"), _under(second, "d")]

    assert collapse_parent_runs(rows) == ["first table", "second table"]


def test_collapse_parent_runs_reopens_a_complete_run_after_an_incomplete_one() -> None:
    """An incomplete parent's rows pass through as leaves AND close the open
    run: the complete parent that follows must still appear once, not be
    mistaken for a continuation of anything before it."""
    header_parent = ChunkParent(id="p1", text="Name; Salary", is_complete=False)
    full_parent = ChunkParent(id="p2", text="whole table", is_complete=True)
    rows = [
        _under(header_parent, "Name: Ahmad; Salary: 5000"),
        _under(full_parent, "row a"),
        _under(full_parent, "row b"),
    ]

    assert collapse_parent_runs(rows) == ["Name: Ahmad; Salary: 5000", "whole table"]


def test_collapse_parent_runs_writes_an_interrupted_parent_once() -> None:
    """ "Same parent, not adjacent" IS a shape this pipeline produces since
    P-34/س-27 = أ: a PDF page's table and its prose blocks get different
    parents and interleave in ``seq``. The page's text is written once, at
    its first chunk -- the second appearance would be the same bytes, so
    this is de-duplication, not the dropping-from-the-middle a parentless or
    incomplete-parent chunk is still spared."""
    parent = ChunkParent(id="p1", text="page body", is_complete=True)
    rows = [_under(parent, "a"), _leaf("table row"), _under(parent, "b")]

    assert collapse_parent_runs(rows) == ["page body", "table row"]
