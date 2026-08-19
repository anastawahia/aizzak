"""Plain text (.txt/.md/.csv) parsing — stdlib only.

Alpha has no hand-rolled plain-text parser: `.txt`/`.md`/`.csv` fell through
`scanner.py`'s "OTHER FILES" branch straight into llama_index's
`SimpleDirectoryReader` (a whole separate library, replaced entirely by this
port), which handled encoding/splitting internally. Two pieces of genuinely
alpha-authored logic ARE ported faithfully here, because they are
format-agnostic utilities rather than part of that reader:

- `clean_text` — alpha `rag/utils/text.py::_clean_text` (page-number/noise
  stripping, ASCII+Arabic-only filter, whitespace normalization).
- `split_long_text` — alpha `rag/parsers/text_parser.py::_split_long_text`,
  alpha's generic newline/sentence-aware splitter (written there for DOCX). It
  is Arabic-punctuation-aware (splits on ``، ؛`` too, not just ``. ! ?``), so
  it is reused here verbatim for plain text, keyed off the same
  `DOCX_MAX_CHUNK_CHARS` value (2000). Public since plan step 4 (`P-08`): the
  DOCX parser alpha wrote it for now needs it as well, and it stays in the
  module that has always owned it rather than moving under a new caller.

Adapted: the encoding-fallback candidate chain is the same policy applied in
`json_doc.py` (utf-8-sig/utf-8/utf-16/cp1256/latin-1, ending in a lossless
last resort) — a new, but consistent, choice, since alpha had no hand-rolled
routine here to port (see above).
"""

from __future__ import annotations

import re

from app.modules.knowledge.ports.content_extractor import ParsedChunk, ParsedChunkKind

# alpha `DOCX_MAX_CHUNK_CHARS` default, reused for plain text per the task's
# explicit analogy (coarse, paragraph/size-aware splitting).
_MAX_CHUNK_CHARS = 2000

_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "utf-16", "cp1256", "latin-1")


def parse_text(data: bytes, ext: str) -> list[ParsedChunk]:
    """Decode + clean + coarse-split a plain-text file into one chunk per
    split segment. `order` is the deterministic split index."""
    cleaned = clean_text(_decode_bytes(data))
    if not cleaned:
        return []

    return [
        ParsedChunk(
            text=piece,
            order=index,
            kind=ParsedChunkKind.TEXT,
            metadata={"source_ext": ext, "split_index": index},
        )
        for index, piece in enumerate(split_long_text(cleaned, _MAX_CHUNK_CHARS))
    ]


def clean_text(text: str) -> str:
    """Strip page numbers/noise sequences, keep ASCII-printable + Arabic
    characters only, normalize whitespace (alpha `utils/text.py::_clean_text`)."""
    text = re.sub(r"\bPage\s*\d+\b", " ", text, flags=re.I)
    text = re.sub(r"[~_*={}\[\]\-]{3,}", " ", text)
    text = re.sub(r"[^\x09\x0A\x0D\x20-\x7E؀-ۿ]", " ", text)
    text = re.sub(r"\s{3,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _decode_bytes(data: bytes) -> str:
    if not data:
        return ""
    for encoding in _ENCODING_CANDIDATES:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Split at natural boundaries (newlines, then Arabic-aware sentence
    punctuation ``. ! ? ، ؛``) when text exceeds `max_chars` (alpha
    `text_parser.py::_split_long_text`, ported verbatim)."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for line in text.split("\n"):
        if buf_len + len(line) + 1 <= max_chars:
            buf.append(line)
            buf_len += len(line) + 1
            continue

        if buf:
            chunks.append("\n".join(buf).strip())
            buf, buf_len = [], 0

        if len(line) <= max_chars:
            buf.append(line)
            buf_len = len(line) + 1
        else:
            for sent in re.split(r"(?<=[.!?،؛])\s+", line):
                if buf_len + len(sent) + 1 <= max_chars:
                    buf.append(sent)
                    buf_len += len(sent) + 1
                else:
                    if buf:
                        chunks.append(" ".join(buf).strip())
                        buf, buf_len = [], 0
                    for k in range(0, len(sent), max_chars):
                        chunks.append(sent[k : k + max_chars])

    if buf:
        chunks.append("\n".join(buf).strip())

    return [c for c in chunks if c]
