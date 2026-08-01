"""JSON/JSONL structural parser (ported from alpha
`rag/parsers/json_parser.py::RAGFlowJsonParser` + `JsonTableExtractor`;
docs/migration/refs/parsers.md §1 "JSON").

Detects three table shapes inside arbitrary JSON — ``list[dict]`` (rows),
``list[str]``/an embedded large string forming an aligned text grid (tab or
2+-space delimited, one consistent column count), and a "KV-scalar" ``dict``
(every value scalar, so a real key/value table rather than a structural
container) — and treats everything else as free text, consolidated and
smart-split to `MAX_CHUNK_SIZE`. ``order`` is a **deterministic traversal
order**: tables first (in their recursive-discovery order), then text chunks
(in their consolidated/split order) — both phases are, byte-for-byte,
reproducible for the same input (parsers.md §6 risk #5).

Adapted: alpha's `RAGFlowJsonParser`/`JsonTableExtractor` classes (mutable
instance state: ``self.ignored_paths``, tunable constructor args normally
overridable via ``RAG_JSON_*`` env vars) become plain module-level functions
with a `set[str]` threaded explicitly for the ignored-paths bookkeeping — the
env-var tuning knobs are dropped (10-code-standards §9: no direct env reads
outside `infrastructure/config`); every threshold is a fixed module constant
instead. Dropped: the optional `charset_normalizer`/`chardet` auto-detection
step in encoding detection — not in this step's approved dependency list; the
explicit-candidate fallback chain (ending in a lossless latin-1 safety net) is
preserved faithfully. Dropped: the `RAG_JSON_ENABLE_KV_TABLES` disable
toggle — KV-table detection is always on here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.framework.observability import get_logger
from app.framework.types import Json
from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

log = get_logger(__name__)

# alpha RAGFlowJsonParser(max_chunk_size=4000) default.
MAX_CHUNK_SIZE = 4000

_MIN_ROWS = 2
_MIN_COLS = 2
# alpha's default effectively disables pre-splitting for realistic tables (the
# row-by-row explosion is a node_builder concern, deferred — see module
# docstring); kept as a safety net for pathological inputs, not a normal path.
_MAX_ROWS_PER_CHUNK = 100_000
_KV_MIN_ROWS = 4
_KV_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)
# cap recursion so a pathologically deep/cyclic-looking JSON degrades
# gracefully (stop + warn) instead of raising RecursionError mid-parse.
_MAX_JSON_DEPTH = 200

_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "utf-16", "cp1256", "latin-1")

# classify_json ratio thresholds (alpha `_classify_json`): mostly tables ->
# structured, a meaningful minority of tables -> semi-structured, otherwise
# unstructured (the 0/2-table and 2/0-text edge cases short-circuit above).
_STRUCTURED_RATIO = 0.6
_SEMI_STRUCTURED_RATIO = 0.2

# _is_jsonl: at least this many lines must each independently parse as JSON
# (alpha samples the first 20 lines, requiring >= 2 valid ones).
_JSONL_MIN_VALID_LINES = 2
_JSONL_SAMPLE_LINES = 20

# _adaptive_consolidate_blocks: "too many" / "too short on average" triggers
# section-level merging before smart-splitting (alpha `_adaptive_consolidate_blocks`).
_CONSOLIDATE_MANY_BLOCKS = 80
_CONSOLIDATE_SHORT_RATIO = 0.6


def parse_json(data: bytes) -> list[ParsedChunk]:
    """Parse JSON/JSONL/concatenated-JSON-stream bytes into table + text
    chunks. Tables are ordered first (recursive-discovery order), then text
    chunks (consolidated/split order) — see the module docstring."""
    raw_text = _decode_bytes(data)
    data_items = _load_jsonl(raw_text) if _is_jsonl(raw_text) else _load_json_or_stream(raw_text)

    all_tables: list[dict[str, Any]] = []
    all_raw_blocks: list[dict[str, Any]] = []
    ignored_paths: set[str] = set()

    for idx, item in enumerate(data_items):
        base_path = "$" if len(data_items) == 1 else f"$[{idx}]"
        all_tables.extend(_extract_tables(item, base_path, ignored_paths))
        all_raw_blocks.extend(_extract_text_blocks(item, base_path, ignored_paths))

    consolidated = _adaptive_consolidate_blocks(all_raw_blocks)
    text_blocks = _smart_split_blocks(consolidated)

    chunks: list[ParsedChunk] = []
    order = 0
    for table in all_tables:
        chunks.append(_table_to_chunk(table, order))
        order += 1
    for block in text_blocks:
        chunks.append(_text_block_to_chunk(block, order))
        order += 1
    return chunks


def classify_json(*, table_count: int, text_count: int) -> str:
    """File-type classification by table/text ratio (alpha `_classify_json`)."""
    if table_count == 0 and text_count == 0:
        return "empty_json"
    if table_count > 0 and text_count == 0:
        return "structured_json"
    if table_count == 0 and text_count > 0:
        return "unstructured_json"

    ratio = table_count / max(table_count + text_count, 1)
    if ratio > _STRUCTURED_RATIO:
        return "structured_json"
    if ratio > _SEMI_STRUCTURED_RATIO:
        return "semi_structured_json"
    return "unstructured_json"


# --------------------------------------------------------------------------- #
# Encoding + JSON/JSONL loading                                               #
# --------------------------------------------------------------------------- #
def _decode_bytes(data: bytes) -> str:
    """Try, in order, the first encoding that decodes without loss (alpha
    `_read_file_any_encoding`)."""
    if not data:
        return ""
    for encoding in _ENCODING_CANDIDATES:
        try:
            return data.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    log.warning("json.no_clean_encoding")
    return data.decode("utf-8", errors="replace").strip()


def _is_jsonl(raw: str) -> bool:
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < _JSONL_MIN_VALID_LINES:
        return False
    valid = 0
    for ln in lines[:_JSONL_SAMPLE_LINES]:
        try:
            json.loads(ln)
            valid += 1
        except json.JSONDecodeError:
            return False
    return valid >= _JSONL_MIN_VALID_LINES


def _load_jsonl(raw: str) -> list[Any]:
    out: list[Any] = []
    for ln in raw.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return out or [{}]


def _load_json_or_stream(raw: str) -> list[Any]:
    """A single JSON value, or a concatenated stream (``{...}{...}``)."""
    raw = raw.strip()
    if not raw:
        return [{}]

    try:
        return [json.loads(raw)]
    except json.JSONDecodeError:
        pass

    items: list[Any] = []
    decoder = json.JSONDecoder()
    idx = 0
    n = len(raw)
    skipped = 0

    while idx < n:
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, end_idx = decoder.raw_decode(raw, idx)
            items.append(obj)
            idx = end_idx
        except json.JSONDecodeError:
            # jump to the next plausible value start ({ or [) instead of
            # crawling one byte at a time.
            candidates = [i for i in (raw.find("{", idx + 1), raw.find("[", idx + 1)) if i != -1]
            if not candidates:
                skipped += n - idx
                break
            nxt = min(candidates)
            skipped += nxt - idx
            idx = nxt

    if skipped:
        log.warning("json.stream_skipped_bytes", extra={"skipped": skipped})
    if not items:
        log.warning("json.stream_empty")
        return [{}]
    return items


# --------------------------------------------------------------------------- #
# Table detection (recursive)                                                 #
# --------------------------------------------------------------------------- #
# the 4 table-shape detectors, tried in this fixed order (alpha's original
# if/elif cascade) — each returns ``None`` (not this pattern, keep checking)
# or the resulting (always non-empty) list of table dicts.
_TableDetector = Callable[[Any, str, "set[str]"], "list[dict[str, Any]] | None"]


def _extract_tables(
    data: Any, path: str, ignored_paths: set[str], depth: int = 0
) -> list[dict[str, Any]]:
    if depth > _MAX_JSON_DEPTH:
        log.warning("json.max_depth_exceeded", extra={"path": path, "depth": depth})
        return []

    detectors: tuple[_TableDetector, ...] = (
        _try_dict_rows_table,
        _try_grid_table,
        _try_kv_table,
        _try_embedded_table,
    )
    for detector in detectors:
        found = detector(data, path, ignored_paths)
        if found is not None:
            return found

    return _recurse_tables(data, path, ignored_paths, depth)


def _recurse_tables(
    data: Any, path: str, ignored_paths: set[str], depth: int
) -> list[dict[str, Any]]:
    """Continue the recursive search inside a structure that matched none of
    the table-shape detectors (alpha `_extract_tables_recursive` step 5)."""
    found: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            found.extend(_extract_tables(v, f"{path}.{k}", ignored_paths, depth + 1))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found.extend(_extract_tables(v, f"{path}[{i}]", ignored_paths, depth + 1))
    return found


def _try_dict_rows_table(
    data: Any, path: str, ignored_paths: set[str]
) -> list[dict[str, Any]] | None:
    """Pattern 1: ``list[dict]`` — headers in stable first-seen order."""
    if not (isinstance(data, list) and len(data) >= _MIN_ROWS):
        return None
    dict_rows: list[dict[str, Any]] = [r for r in data if isinstance(r, dict)]
    if len(dict_rows) < _MIN_ROWS:
        return None
    headers = list(dict.fromkeys(k for r in dict_rows for k in r))
    if len(headers) < _MIN_COLS:
        return None

    ignored_paths.add(path)
    if len(dict_rows) > _MAX_ROWS_PER_CHUNK:
        return _split_large_table(dict_rows, path, headers, ignored_paths)
    return [_build_table_entry(dict_rows, path, headers)]


def _try_grid_table(data: Any, path: str, ignored_paths: set[str]) -> list[dict[str, Any]] | None:
    """Pattern 2: ``list[str]`` forming a consistent tab/2+-space grid."""
    if not (isinstance(data, list) and all(isinstance(x, str) for x in data)):
        return None
    parsed = _parse_text_block(data)
    if not parsed:
        return None

    ignored_paths.add(path)
    table_dicts = [dict(zip(parsed["headers"], row, strict=False)) for row in parsed["rows"]]
    if parsed["num_rows"] > _MAX_ROWS_PER_CHUNK:
        return _split_large_table(table_dicts, path, parsed["headers"], ignored_paths)
    return [_build_text_table_entry(path, parsed, table_dicts)]


def _try_kv_table(data: Any, path: str, ignored_paths: set[str]) -> list[dict[str, Any]] | None:
    """Pattern 3: a dict where every value is scalar — a real KV table, not a
    structural container (which should keep recursing instead)."""
    if not (isinstance(data, dict) and _looks_like_kv_table(data)):
        return None
    ignored_paths.add(path)
    table = [{"Key": k, "Value": v} for k, v in data.items()]
    return [_build_table_entry(table, path, ["Key", "Value"])]


def _try_embedded_table(
    data: Any, path: str, ignored_paths: set[str]
) -> list[dict[str, Any]] | None:
    """Pattern 4: a text table embedded inside a single large string value."""
    if not (isinstance(data, str) and _looks_like_embedded_table(data)):
        return None
    parsed = _parse_embedded_text_table(data)
    if not parsed:
        return None

    ignored_paths.add(path)
    table_dicts = [dict(zip(parsed["headers"], row, strict=False)) for row in parsed["rows"]]
    return [_build_text_table_entry(path, parsed, table_dicts)]


def _looks_like_kv_table(d: dict[Any, Any]) -> bool:
    if len(d) < _KV_MIN_ROWS:
        return False
    if not all(isinstance(k, str) for k in d):
        return False
    return all(v is None or isinstance(v, _KV_SCALAR_TYPES) for v in d.values())


def _looks_like_embedded_table(text: str) -> bool:
    # detect an embedded grid by repeated TAB / multi-space column alignment
    # across multiple lines — LANGUAGE-AGNOSTICALLY (no reliance on Latin
    # letters/digits, so this also matches Arabic tables).
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < _MIN_ROWS:
        return False
    aligned = sum(1 for ln in lines if re.search(r"\t|\S\s{2,}\S", ln))
    return aligned >= _MIN_ROWS


def _parse_text_block(lines: list[str]) -> dict[str, Any]:
    """Accept a block as a table only when every row splits (on TAB/2+
    spaces) into the SAME column count — a genuine grid, not ragged prose.
    Returns ``{}`` (falsy) when the lines do not form a table."""
    split_rows: list[list[str]] = []
    for line in lines:
        if not line or not line.strip():
            continue
        parts = [p.strip() for p in re.split(r"\t|\s{2,}", line.strip()) if p.strip()]
        if len(parts) >= _MIN_COLS:
            split_rows.append(parts)

    if len(split_rows) < _MIN_ROWS:
        return {}

    col_counts = {len(r) for r in split_rows}
    if len(col_counts) != 1:
        return {}

    num_cols = next(iter(col_counts))
    if num_cols < _MIN_COLS:
        return {}

    headers = [f"Column_{i + 1}" for i in range(num_cols)]
    return {
        "headers": headers,
        "rows": split_rows,
        "num_rows": len(split_rows),
        "num_cols": num_cols,
    }


def _parse_embedded_text_table(text: str) -> dict[str, Any]:
    return _parse_text_block(text.split("\n"))


def _build_table_entry(rows: list[dict[str, Any]], path: str, headers: list[str]) -> dict[str, Any]:
    return {
        "path": path,
        "headers": headers,
        "num_rows": len(rows),
        "num_cols": len(headers),
        "table": rows,
        "meta": {"type": "json_table", "source_type": "dict_rows", "location": path},
    }


def _build_text_table_entry(
    path: str, parsed: dict[str, Any], table_dicts: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "path": path,
        "headers": parsed["headers"],
        "num_rows": parsed["num_rows"],
        "num_cols": parsed["num_cols"],
        "table": table_dicts,
        "meta": {
            "type": "json_table",
            "source_type": "structured_text",
            "location": path,
            "size": f"{parsed['num_rows']}x{parsed['num_cols']}",
        },
    }


def _split_large_table(
    rows: list[dict[str, Any]], path: str, headers: list[str], ignored_paths: set[str]
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    total = len(rows)
    ignored_paths.add(path)
    for i in range(0, total, _MAX_ROWS_PER_CHUNK):
        subset = rows[i : i + _MAX_ROWS_PER_CHUNK]
        chunk_path = f"{path}#rows_{i}-{i + len(subset) - 1}"
        ignored_paths.add(chunk_path)
        chunks.append(
            {
                "path": chunk_path,
                "headers": headers,
                "num_rows": len(subset),
                "num_cols": len(headers),
                "table": subset,
                "meta": {
                    "type": "json_table",
                    "source_type": "split_chunk",
                    "parent_path": path,
                    "rows_range": f"{i}-{i + len(subset) - 1}",
                },
            }
        )
    return chunks


# --------------------------------------------------------------------------- #
# Text-block extraction (ignores table regions), consolidation, splitting     #
# --------------------------------------------------------------------------- #
def _extract_text_blocks(
    data: Any, base_path: str, ignored_paths: set[str]
) -> list[dict[str, Any]]:
    collected: dict[str, list[str]] = {}

    def is_under_table(real_path: str) -> bool:
        for t in ignored_paths:
            if t and (
                real_path == t or real_path.startswith(t + ".") or real_path.startswith(t + "[")
            ):
                return True
        return False

    # `real_path` (WITH [i] indices) matches the table extractor's own path
    # scheme exactly; `path` (WITHOUT indices) is the grouping key that
    # preserves the original consolidation semantics (list elements grouped).
    def walk(node: Any, path: str, real_path: str, depth: int = 0) -> None:
        if depth > _MAX_JSON_DEPTH:
            log.warning(
                "json.max_depth_exceeded_text_walk", extra={"path": real_path, "depth": depth}
            )
            return
        if is_under_table(real_path):
            return
        if isinstance(node, str):
            txt = node.strip()
            if txt:
                collected.setdefault(path, []).append(txt)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}", f"{real_path}.{k}", depth + 1)
            return
        if isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path, f"{real_path}[{i}]", depth + 1)
            return

    walk(data, base_path, base_path)

    blocks: list[dict[str, Any]] = []
    for path, pieces in collected.items():
        merged = "\n".join(pieces).strip()
        if merged:
            blocks.append({"path": path, "content": merged, "type": "json_text"})
    return blocks


def _compute_stats(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    if not blocks:
        return {
            "num": 0,
            "total_len": 0,
            "avg_len": 0,
            "min_len": 0,
            "max_len": 0,
            "short_ratio": 0.0,
        }

    lengths = [len(b["content"]) for b in blocks]
    num = len(lengths)
    short_threshold = max(200, MAX_CHUNK_SIZE // 3)
    short_count = sum(1 for length in lengths if length < short_threshold)
    return {
        "num": num,
        "total_len": sum(lengths),
        "avg_len": sum(lengths) / num,
        "min_len": min(lengths),
        "max_len": max(lengths),
        "short_ratio": short_count / num,
    }


def _adaptive_consolidate_blocks(raw_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge many/short/small-average blocks up to a shared section key
    (alpha `_adaptive_consolidate_blocks`) so downstream splitting works on
    fewer, more substantial pieces."""
    stats = _compute_stats(raw_blocks)
    if stats["num"] == 0:
        return []

    min_chunk_len = max(300, MAX_CHUNK_SIZE // 4)
    need_strong_merge = (
        stats["num"] > _CONSOLIDATE_MANY_BLOCKS
        or stats["short_ratio"] > _CONSOLIDATE_SHORT_RATIO
        or stats["avg_len"] < min_chunk_len
    )
    if not need_strong_merge:
        return raw_blocks

    grouped: dict[str, list[str]] = {}
    for blk in raw_blocks:
        section_key = _get_section_key(blk["path"], depth=2)
        grouped.setdefault(section_key, []).append(blk["content"])

    merged_blocks: list[dict[str, Any]] = []
    for section_path, texts in grouped.items():
        big_text = "\n".join(texts).strip()
        if big_text:
            merged_blocks.append({"path": section_path, "content": big_text, "type": "json_text"})
    return merged_blocks


def _get_section_key(path: str, depth: int = 2) -> str:
    if path == "$":
        return "$"
    p = path.lstrip("$")
    if p.startswith("."):
        p = p[1:]
    if not p:
        return "$"
    return "$." + ".".join(p.split(".")[:depth])


def _smart_split_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if not blocks:
        return chunks

    min_len = max(300, MAX_CHUNK_SIZE // 4)
    max_len = MAX_CHUNK_SIZE
    target_len = int(MAX_CHUNK_SIZE * 0.75)

    for blk in blocks:
        text = blk["content"].strip()
        path = blk["path"]
        if not text:
            continue

        # equivalent to alpha's two branches ("min<=len<=max" and "len<min"
        # both keep the block as-is; together that is exactly "len<=max").
        if len(text) <= max_len:
            chunks.append({"path": path, "content": text, "type": "json_text"})
            continue

        chunks.extend(_split_oversized_block(text, path, min_len, max_len, target_len))

    return chunks


def _split_oversized_block(
    text: str, path: str, min_len: int, max_len: int, target_len: int
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    buf: list[str] = []
    buf_len = 0
    part_idx = 0

    def _flush() -> None:
        nonlocal buf, buf_len, part_idx
        chunk_text = " ".join(buf)
        if chunk_text:
            chunks.append(
                {"path": f"{path}#part{part_idx}", "content": chunk_text, "type": "json_text"}
            )
            part_idx += 1
        buf = []
        buf_len = 0

    for sent in _split_to_sentences(text):
        s = sent.strip()
        if not s:
            continue
        slen = len(s) + 1

        if buf and buf_len + slen > max_len:
            _flush()
            buf, buf_len = [s], len(s)
            continue

        if buf and buf_len >= min_len and target_len < buf_len + slen <= max_len:
            _flush()
            buf, buf_len = [s], len(s)
            continue

        buf.append(s)
        buf_len += slen

    if buf:
        _flush()

    return chunks


def _split_to_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟])\s+", text)
    if len(parts) <= 1:
        return [ln for ln in text.splitlines() if ln.strip()]
    return parts


# --------------------------------------------------------------------------- #
# dict -> ParsedChunk                                                         #
# --------------------------------------------------------------------------- #
def _table_to_chunk(table: dict[str, Any], order: int) -> ParsedChunk:
    text = json.dumps({"headers": table["headers"], "rows": table["table"]}, ensure_ascii=False)
    metadata: Json = {
        "path": table["path"],
        "headers": table["headers"],
        "num_rows": table["num_rows"],
        "num_cols": table["num_cols"],
        "source_ext": ".json",
        **table["meta"],
    }
    return ParsedChunk(text=text, order=order, kind=ParsedChunkKind.TABLE, metadata=metadata)


def _text_block_to_chunk(block: dict[str, Any], order: int) -> ParsedChunk:
    metadata: Json = {
        "path": block["path"],
        "source_ext": ".json",
        "type": block.get("type", "json_text"),
    }
    return ParsedChunk(
        text=block["content"], order=order, kind=ParsedChunkKind.JSON, metadata=metadata
    )
