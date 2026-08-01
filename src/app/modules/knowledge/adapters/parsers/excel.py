"""Excel (.xlsx) table extraction (ported from alpha
`rag/parsers/excel_parser.py::RAGFlowExcelParser`; docs/migration/refs/
parsers.md §1 "Excel").

Per alpha, this parser emits **tables only** — every detected sheet block
becomes a structured table; there is no free-text output (alpha's ``texts``
list is always empty by design, so this port has no "text" branch either).
The "header: value" row-to-sentence serialization is a **node_builder
concern, NOT here** (deferred — parsers.md §3 phase 1); a table stays one
coarse chunk (or a handful, via `_split_large_table`) at this layer, its
``text`` a JSON dump of ``{headers, rows}`` (mirroring how alpha's own
`scanner.py` turns a table dict into a `Document.text` before indexing).

Deferred (3.k1): ``.xls`` (legacy binary format) is not routed here — reading
it needs the ``xlrd`` engine, which is not in the approved dependency list for
this step (only ``openpyxl`` for ``.xlsx``); ``.xls`` stays unsupported until
that dependency is confirmed.

Adapted: alpha's nested per-block header-detection closures are hoisted to
module-level private functions (behaviourally identical, easier to unit
test). Read errors (`pd.read_excel` raising) are no longer swallowed into an
in-band ``excel_read_error:<Type>`` file_type value (alpha's dict-shaped
return had room for that; this parser's `list[ParsedChunk]` return does not)
— they now raise, to be caught and translated by
`adapters/parsers/extractor.py` into a `ValidationError` (10-code-standards
§5: no silent swallowing of a genuine failure).
"""

from __future__ import annotations

import io
import json
import math
from typing import Any

import pandas as pd

from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# alpha RAGFlowExcelParser defaults.
_MIN_ROWS = 2
_MIN_COLS = 2
_MAX_ROWS_PER_CHUNK = 500
# how many rows at the start of a block are scanned for a header-like row
# (alpha `_extract_sheet_tables`, falls back to row 0 when none matches).
_HEADER_SCAN_ROWS = 5

# `_is_header_row` ratio thresholds (alpha `is_header_row`): a row "looks like
# a header" when less than 40% of its cells are numeric-like; that verdict is
# overridden back to "not a header" when the row is at least as numeric as the
# next row, UNLESS the row is overwhelmingly non-numeric (<20%) while the next
# row is overwhelmingly numeric (>50%) — a classic header-then-data signature.
_HEADER_LIKE_MAX_RATIO = 0.4
_HEADER_STRONG_LOW_RATIO = 0.2
_HEADER_STRONG_HIGH_RATIO = 0.5

# `order = sheet_index * _SHEET_ORDER_STRIDE + segment_index` — a deterministic
# structural ordinal (a sheet producing >= 100k table segments is not a
# realistic input; the stride only needs to exceed the segment count per sheet).
_SHEET_ORDER_STRIDE = 100_000


def parse_excel(data: bytes) -> list[ParsedChunk]:
    """Extract structured tables from every sheet of an ``.xlsx`` workbook.

    Raises ``ValueError`` when the bytes cannot be read as a workbook at all
    (see the module docstring for how the caller is expected to translate
    that into an application error).
    """
    try:
        sheets: Any = pd.read_excel(
            io.BytesIO(data), sheet_name=None, dtype=object, header=None, engine="openpyxl"
        )
    except Exception as exc:
        log.warning(
            "excel.read_failed", extra={"error": str(exc), "error_type": type(exc).__name__}
        )
        raise ValueError(f"excel_read_error:{type(exc).__name__}") from exc

    chunks: list[ParsedChunk] = []
    for sheet_index, (sheet_name, df) in enumerate(sheets.items()):
        sheet_tables = _extract_sheet_tables(str(sheet_name), sheet_index, df)
        for segment_index, table in enumerate(sheet_tables):
            order = sheet_index * _SHEET_ORDER_STRIDE + segment_index
            chunks.append(_table_to_chunk(table, order))
    return chunks


def _extract_sheet_tables(sheet_name: str, sheet_idx: int, df: Any) -> list[dict[str, Any]]:
    """Hybrid multi-table segmentation within one sheet (alpha
    `_extract_sheet_tables`): blank rows close a block; a header-pattern
    change (non-header row followed by a fresh header-like row) also closes a
    block, so a sheet with several stacked tables is split correctly."""
    # remove empty columns, keep empty rows (they are block separators).
    df = df.dropna(axis=1, how="all")
    if df.empty or len(df) < _MIN_ROWS:
        return []
    df = df.reset_index(drop=True)

    blocks = _segment_blocks(df)
    if not blocks:
        return []

    base_sheet_path = f"$.sheets['{sheet_name}']"
    sheet_number = sheet_idx + 1

    tables: list[dict[str, Any]] = []
    for b_idx, block in enumerate(blocks):
        tables.extend(_block_to_tables(df, block, b_idx, base_sheet_path, sheet_name, sheet_number))
    return tables


def _segment_blocks(df: Any) -> list[tuple[int, int]]:
    """Blank rows / header-pattern changes split a sheet into row-range
    blocks (alpha's hybrid multi-table segmentation, hoisted out of
    `_extract_sheet_tables` to stay under the complexity budget)."""
    blocks: list[tuple[int, int]] = []
    current_start: int | None = None
    rows_in_block = 0
    prev_header_like = False
    n_rows = len(df)

    for i in range(n_rows):
        row = df.iloc[i]
        if row.isna().all():
            if current_start is not None and rows_in_block >= _MIN_ROWS:
                blocks.append((current_start, i - 1))
            current_start = None
            rows_in_block = 0
            prev_header_like = False
            continue

        next_row = df.iloc[i + 1] if (i + 1 < n_rows) else None
        header_like = _is_header_row(row, next_row)

        if current_start is None:
            current_start = i
            rows_in_block = 1
        elif header_like and not prev_header_like and rows_in_block >= _MIN_ROWS:
            blocks.append((current_start, i - 1))
            current_start = i
            rows_in_block = 1
        else:
            rows_in_block += 1

        prev_header_like = header_like

    if current_start is not None and rows_in_block >= _MIN_ROWS:
        blocks.append((current_start, n_rows - 1))
    return blocks


def _block_to_tables(
    df: Any,
    block: tuple[int, int],
    b_idx: int,
    base_sheet_path: str,
    sheet_name: str,
    sheet_number: int,
) -> list[dict[str, Any]]:
    """Build the table entry/entries for one segmented block (header
    detection + row-dict building + `_split_large_table` if oversized)."""
    start_idx, end_idx = block
    block_df = df.iloc[start_idx : end_idx + 1].reset_index(drop=True)
    if len(block_df) < _MIN_ROWS:
        return []

    header_local_idx = _find_header_row_index(block_df)
    header_row = block_df.iloc[header_local_idx]
    data_df = block_df.iloc[header_local_idx + 1 :].reset_index(drop=True)

    headers = _row_to_headers(header_row)
    if len(headers) < _MIN_COLS:
        return []

    dict_rows = _rows_to_dicts(data_df, headers)
    if len(dict_rows) < _MIN_ROWS:
        return []

    block_path = f"{base_sheet_path}#block_{b_idx}_rows_{start_idx}-{end_idx}"
    if len(dict_rows) <= _MAX_ROWS_PER_CHUNK:
        return [_build_table_entry(dict_rows, block_path, headers, sheet_name, sheet_number)]
    return _split_large_table(dict_rows, block_path, headers, sheet_name, sheet_number)


def _find_header_row_index(block_df: Any) -> int:
    """Scan the first few rows of a block for a header-like row (alpha scans
    at most 5, falling back to row 0 when none looks like a header)."""
    for i in range(min(_HEADER_SCAN_ROWS, len(block_df))):
        local_row = block_df.iloc[i]
        next_local = block_df.iloc[i + 1] if (i + 1 < len(block_df)) else None
        if _is_header_row(local_row, next_local):
            return i
    return 0


def _row_to_headers(header_row: Any) -> list[str]:
    headers: list[str] = []
    for i, h in enumerate(header_row.tolist()):
        h_str = str(h).strip() if h is not None else ""
        headers.append(h_str or f"Column_{i + 1}")
    return headers


def _rows_to_dicts(data_df: Any, headers: list[str]) -> list[dict[str, Any]]:
    dict_rows: list[dict[str, Any]] = []
    for _, row in data_df.iterrows():
        if row.isna().all():
            continue
        row_dict: dict[str, Any] = {}
        for cidx, col_name in enumerate(headers):
            val = row.iloc[cidx] if cidx < len(row) else None
            row_dict[col_name] = _normalize_cell_value(val)
        dict_rows.append(row_dict)
    return dict_rows


def _numeric_ratio(values: list[str]) -> float:
    """How many cells look numeric-like (alpha `_numeric_ratio`)."""
    if not values:
        return 1.0
    numeric_like = 0
    for s in values:
        pure = s.replace(".", "", 1).replace(",", "")
        if pure.isdigit() or any(c.isdigit() for c in s):
            numeric_like += 1
    return numeric_like / len(values)


def _is_header_row(row: Any, next_row: Any | None) -> bool:
    """Strong header detector (alpha `is_header_row`)."""
    values = [str(v).strip() for v in row.tolist()]
    non_empty = [v for v in values if v]
    if not non_empty:
        return False

    first = non_empty[0].lower()
    has_dash = "-" in first
    has_digit = any(c.isdigit() for c in first)
    has_alpha = any(c.isalpha() for c in first)
    if has_dash and has_digit and has_alpha:
        return False
    if "---" in first:
        return False

    cur_ratio = _numeric_ratio(non_empty)

    next_ratio: float | None = None
    if next_row is not None:
        next_vals = [str(v).strip() for v in next_row.tolist()]
        next_non_empty = [v for v in next_vals if v]
        next_ratio = _numeric_ratio(next_non_empty) if next_non_empty else None

    looks_like_header = cur_ratio < _HEADER_LIKE_MAX_RATIO
    if (
        next_ratio is not None
        and cur_ratio >= next_ratio
        and not (cur_ratio < _HEADER_STRONG_LOW_RATIO and next_ratio > _HEADER_STRONG_HIGH_RATIO)
    ):
        looks_like_header = False
    return looks_like_header


def _normalize_cell_value(val: Any) -> Any:
    """JSON-safe cell normalization (alpha `_normalize_cell_value`)."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if hasattr(val, "isoformat"):
        iso: Any = val.isoformat()
        return iso
    if isinstance(val, str):
        s = val.strip()
        return s or None
    return val


def _build_table_entry(
    rows: list[dict[str, Any]],
    path: str,
    headers: list[str],
    sheet_name: str,
    sheet_number: int,
) -> dict[str, Any]:
    return {
        "path": path,
        "headers": headers,
        "num_rows": len(rows),
        "num_cols": len(headers),
        "table": rows,
        "meta": {
            "type": "excel_table",
            "source_type": "excel_sheet",
            "sheet_name": sheet_name,
            "sheet_number": sheet_number,
            "location": path,
        },
    }


def _split_large_table(
    rows: list[dict[str, Any]],
    base_path: str,
    headers: list[str],
    sheet_name: str,
    sheet_number: int,
) -> list[dict[str, Any]]:
    """Split a block exceeding `_MAX_ROWS_PER_CHUNK` rows (alpha
    `_split_large_table`, ``max_rows=500``)."""
    chunks: list[dict[str, Any]] = []
    total = len(rows)
    for i in range(0, total, _MAX_ROWS_PER_CHUNK):
        subset = rows[i : i + _MAX_ROWS_PER_CHUNK]
        chunk_path = f"{base_path}#rows_{i}-{i + len(subset) - 1}"
        chunks.append(
            {
                "path": chunk_path,
                "headers": headers,
                "num_rows": len(subset),
                "num_cols": len(headers),
                "table": subset,
                "meta": {
                    "type": "excel_table",
                    "source_type": "excel_sheet_split_chunk",
                    "sheet_name": sheet_name,
                    "sheet_number": sheet_number,
                    "parent_path": base_path,
                    "rows_range": f"{i}-{i + len(subset) - 1}",
                },
            }
        )
    return chunks


def _table_to_chunk(table: dict[str, Any], order: int) -> ParsedChunk:
    text = json.dumps({"headers": table["headers"], "rows": table["table"]}, ensure_ascii=False)
    metadata: Json = {
        "path": table["path"],
        "headers": table["headers"],
        "num_rows": table["num_rows"],
        "num_cols": table["num_cols"],
        "source_ext": ".xlsx",
        **table["meta"],
    }
    return ParsedChunk(text=text, order=order, kind=ParsedChunkKind.TABLE, metadata=metadata)
