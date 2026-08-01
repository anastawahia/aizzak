"""Content-extraction driven port (docs/migration/refs/parsers.md;
06-domain-models §7; 01-data-model §2.7).

Ports alpha's two `rag/parsers/*` output shapes — the "RAGFlow dict"
(`{file_type, texts, tables}`, used by JSON/Excel) and the "Document stream"
(`llama_index.core.Document(text, metadata)`, used by PDF-layout/OCR) — into a
**single neutral output type**. No adapter leaks a technical type
(`llama_index.Document`, `fitz.Page`, a pandas `DataFrame`...) across the
module boundary; only stdlib + `Json` cross into `app.modules.knowledge.ports`.

A `ParsedChunk` is a **coarse**, structurally-ordered slice of a source file
(a PDF page, an Excel table segment, a JSON block...) — matching alpha's own
split between "parsers do coarse slicing" and "`node_builder.py` does the real
chunking" (parsers.md §3). Real semantic chunking (sentence/parent-chunk/
table-row explosion) is a later concern (the `node_builder.py` equivalent) that
will consume this port's output, not reproduce it — out of scope here (3.k1).

`ParsedChunk.order` is a **deterministic STRUCTURAL ordinal** (e.g. a PDF page
index, or `sheet_index * STRIDE + segment_index` for Excel) — NOT the order in
which chunks happened to be produced/appended during processing. This is a
deliberate fix of the risk flagged in parsers.md §6 risk #5: rebuilding from
the same bytes must always yield the same order, which the future
`knowledge.chunks` idempotency invariant (INV-K1: `UNIQUE(document_id, seq)`)
depends on.

`ParsedChunk.metadata` / `ParsedDocument.metadata` are intentionally
schema-free (`Json`): the future Qdrant point-payload schema is **not decided
here** (parsers.md §6 risk #4) — adapters attach whatever rich diagnostics
alpha carried (`page_number`, `sheet_name`, `headers`, `table_name`,
`source_ext`, `sha1`...) and a later step (3.k4+) decides what survives into
storage.

`ContentExtractor` is **synchronous** by design: PyMuPDF/pandas/pytesseract
are CPU-bound/blocking C-extension calls with no native asyncio integration.
Callers on the async path (the future `knowledge` worker) MUST offload via
``asyncio.to_thread`` — this port does not do so internally, which keeps every
adapter simple and unit-testable without an event loop. Extraction runs on
already-fetched bytes (files live in MinIO, fetched by the caller) — never a
filesystem path, so this port stays infrastructure-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.framework.types import Json


class ParsedChunkKind(StrEnum):
    """Coarse shape of a parsed chunk's content.

    Alpha's many `chunk_type`/`type` subtypes (`excel_table`, `json_table`,
    `docx_text`, `page_merged_images`...) collapse to four neutral kinds; the
    specific alpha subtype/pattern (e.g. "kv_table", "dict_rows") lives in
    ``ParsedChunk.metadata`` instead of widening this enum.
    """

    TEXT = "text"
    TABLE = "table"
    OCR = "ocr"
    JSON = "json"


# Deferred (3.k1): deep chunking (alpha `node_builder.py` — semantic/
# sentence-window/parent-chunk splitting, table-row-explosion) is a later
# stage that CONSUMES this coarse `ParsedChunk` output; not reproduced here.
@dataclass(frozen=True, slots=True)
class ParsedChunk:
    """One coarse, structurally-ordered slice of a parsed source file."""

    text: str
    order: int
    kind: ParsedChunkKind
    metadata: Json


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The full neutral extraction result for one source file."""

    source_ext: str
    content_type: str
    chunks: tuple[ParsedChunk, ...]
    metadata: Json


class ContentExtractor(Protocol):
    """Driven port: turn already-fetched file bytes into a `ParsedDocument`.

    Synchronous — see the module docstring. ``filename`` is used only to
    resolve the source extension for routing; the bytes are never read from
    disk by an implementation of this port.
    """

    def supports(self, *, content_type: str, filename: str) -> bool:
        """Whether this extractor can route ``filename``/``content_type``."""
        ...

    def extract(self, *, data: bytes, filename: str, content_type: str) -> ParsedDocument:
        """Parse ``data`` into a `ParsedDocument`.

        Raises ``app.framework.errors.UnsupportedTypeError`` when no parser
        routes this file, and ``app.framework.errors.ValidationError`` when
        the file is empty or a genuine parse failure occurs.
        """
        ...
