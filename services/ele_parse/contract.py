"""The ele-parse JSON contract (schema 1): names, bounds and validators. Pure: no PyMuPDF, no I/O.

The client (``drawings/adapters/ele_parse_client.py``) reads these shapes and the test double
(``tests/unit/support_electrical.py``) replays them; this module is the service's side of the
same agreement. Everything is bounded and ordered, and no field carries a time, so the same file
gives the same bytes on every run (FR-ING-05).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

SCHEMA: Final = 1
MAX_BODY_BYTES: Final = 4_096
MAX_RANGE_PAGES: Final = 1_000
MAX_FONTS: Final = 32
MAX_TOKEN_CHARS: Final = 32
MAX_FONT_NAME_CHARS: Final = 96
MAX_METADATA_CHARS: Final = 512
MAX_FONT_TYPE_CHARS: Final = 32
# One page's signals as JSON: 200 tokens of 32 characters, all non-ASCII (6 bytes each when
# escaped), plus 32 fonts, come to about 40 KiB, so a page past this is not a drawing page.
MAX_PAGE_JSON_BYTES: Final = 64 * 1024
# The child's whole answer (a range of up to 200 pages in the worker): bigger is a fault.
MAX_ANSWER_BYTES: Final = 16 * 1024 * 1024
MAX_STDERR_BYTES: Final = 4 * 1024

_UUID: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
FILE_PATTERN: Final = re.compile(rf"{_UUID}/{_UUID}\.pdf", re.ASCII)

# error codes of a 422: the file is refused
NOT_PDF: Final = "not_pdf"
CORRUPT: Final = "corrupt"
ENCRYPTED: Final = "encrypted"
BAD_RANGE: Final = "bad_range"


class ContractError(Exception):
    """A request the service refuses; ``status`` and ``code`` are what it answers."""

    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(f"{status} {code}: {detail}" if detail else f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail or code


@dataclass(frozen=True, slots=True)
class PagesRequest:
    file: str
    first_page: int
    last_page: int


def parse_file(body: object) -> str:
    """The ``file`` of a request: ``<workspace>/<document>.pdf``, nothing else (400)."""
    if not isinstance(body, dict):
        raise ContractError(400, "bad_request", "the body must be a JSON object")
    name = body.get("file")
    if not isinstance(name, str) or FILE_PATTERN.fullmatch(name) is None:
        raise ContractError(400, "bad_path", "file must be <workspace-uuid>/<document-uuid>.pdf")
    return name


def parse_pages(body: object) -> PagesRequest:
    name = parse_file(body)
    assert isinstance(body, dict)
    first, last = body.get("first_page"), body.get("last_page")
    if (
        isinstance(first, bool)
        or isinstance(last, bool)
        or not isinstance(first, int)
        or not isinstance(last, int)
    ):
        raise ContractError(422, BAD_RANGE, "first_page and last_page must be integers")
    if first < 1 or last < first or last - first + 1 > MAX_RANGE_PAGES:
        raise ContractError(
            422, BAD_RANGE, f"the range must be 1 <= first <= last, at most {MAX_RANGE_PAGES} pages"
        )
    return PagesRequest(file=name, first_page=first, last_page=last)


def clip(text: str | None, limit: int) -> str | None:
    """A metadata string cut to ``limit`` characters; empty is ``None``."""
    if text is None:
        return None
    text = text.strip()
    return text[:limit] if text else None


def clean(text: object, limit: int) -> str:
    """A string from the PDF as the contract carries it: control and format characters (Cc, Cf:
    line breaks, bidi marks, zero-width characters) dropped, then cut to ``limit``. The cut comes
    last, so the result never holds more than ``limit`` characters."""
    kept = "".join(c for c in str(text) if unicodedata.category(c) not in ("Cc", "Cf"))
    return kept[:limit]
