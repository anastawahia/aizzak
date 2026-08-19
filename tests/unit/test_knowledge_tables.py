"""Unit tests for the knowledge module's P-13 table row explosion (plan
§3.3): ``domain/tables.py``'s ``row_to_sentence``/``explode_table``. Pure
domain -- no I/O, no fakes, no event loop.
"""

from __future__ import annotations

from app.modules.knowledge.domain.tables import (
    TABLE_PARENT_MAX_ROWS,
    TABLE_ROW_HARD_CAP,
    ExplodedTable,
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
        row_sentences=(), parent_text="", truncated=False, overflow_text=""
    )


def test_explode_table_row_sentence_uses_the_rows_own_keys_not_headers() -> None:
    """A PDF-table row only carries the columns non-empty for THAT row
    (``pdf_tables.py::_frame_to_rows``) -- narrower than the table's full
    header list. ``explode_table`` must render exactly what the row has."""
    headers = ["Name", "Salary", "Notes"]
    rows = [{"Name": "Ahmad", "Salary": "5000"}]  # "Notes" column absent for this row

    result = explode_table(headers, rows)

    assert result.row_sentences == ("Name: Ahmad; Salary: 5000",)
