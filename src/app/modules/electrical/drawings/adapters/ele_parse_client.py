"""``EleParseClient``: the ``PdfInspection`` port over ele-parse's HTTP contract (DEC-04).

No httpx type leaves this module. The translation (LLD 3.3.7): ``422`` -> ``PdfRejected``
(the file is refused: not a PDF, corrupt, encrypted); ``504`` / ``507`` / ``500 parse_error`` ->
``PageRangeFailed`` (a range timed out, hit the memory limit, or failed to parse: the caller asks
page by page); ``503`` and every transport fault -> ``InspectionUnavailable`` (transient: the
engine redelivers). ``400`` / ``404`` / ``422 bad_range`` mean the two services disagree about the
stage volume or the contract: ``common.internal``, loud and not retried as if transient.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx

from app.framework.errors import AppError
from app.modules.electrical.drawings.domain.errors import (
    InspectionUnavailable,
    PageRangeFailed,
    PdfRejected,
)
from app.modules.electrical.drawings.domain.pages import (
    FontSignal,
    PageSignals,
    PdfSummary,
    StageReason,
)
from app.modules.electrical.drawings.ports.source import StagedSource

SCHEMA: Final = 1
# What the job's ``failure_detail`` says for a refusal: the CATALOGUE's text, never the service's
# (it ran the file's bytes: whatever it wrote could be a hostile file speaking to the client).
_REJECTIONS: Final = {
    "not_pdf": "the file is not a PDF",
    "corrupt": "the PDF is damaged and cannot be read",
    "encrypted": "the PDF is encrypted",
}
MAX_RESPONSE_BYTES: Final = 24 * 1024 * 1024  # ele-parse bounds its answer at 16 MiB
_MAX_FONTS: Final = 32
_MAX_TOKENS: Final = 1_000
_MAX_NAME_CHARS: Final = 96
_MAX_TYPE_CHARS: Final = 32
_MAX_TOKEN_CHARS: Final = 32


def summary_from_json(raw: Mapping[str, Any]) -> PdfSummary:
    return PdfSummary(
        page_count=int(raw["page_count"]),
        pdf_version=str(raw["pdf_version"]),
        encrypted=bool(raw["encrypted"]),
        creator=None if raw.get("creator") is None else str(raw["creator"]),
        producer=None if raw.get("producer") is None else str(raw["producer"]),
        outline_count=int(raw["outline_count"]),
    )


def signals_from_json(raw: Mapping[str, Any]) -> PageSignals:
    return PageSignals(
        page_no=int(raw["page_no"]),
        width_pt=float(raw["width_pt"]),
        height_pt=float(raw["height_pt"]),
        rotation=int(raw["rotation"]),
        char_count=int(raw["char_count"]),
        unmapped_char_count=int(raw["unmapped_char_count"]),
        pua_char_count=int(raw["pua_char_count"]),
        cjk_char_count=int(raw["cjk_char_count"]),
        latin_char_count=int(raw["latin_char_count"]),
        word_count=int(raw["word_count"]),
        vector_path_count=int(raw["vector_path_count"]),
        short_curve_path_count=int(raw["short_curve_path_count"]),
        image_count=int(raw["image_count"]),
        image_area_ratio=float(raw["image_area_ratio"]),
        link_count=int(raw["link_count"]),
        fonts=tuple(
            FontSignal(
                name=str(font["name"])[:_MAX_NAME_CHARS],
                font_type=str(font["font_type"])[:_MAX_TYPE_CHARS],
                cid=bool(font["cid"]),
                embedded=bool(font["embedded"]),
                has_tounicode=bool(font["has_tounicode"]),
            )
            for font in raw["fonts"][:_MAX_FONTS]
        ),
        tokens=tuple(str(token)[:_MAX_TOKEN_CHARS] for token in raw["tokens"][:_MAX_TOKENS]),
    )


class EleParseClient:
    def __init__(self, http: httpx.AsyncClient, *, timeout_s: float) -> None:
        self._http = http
        self._timeout_s = timeout_s

    async def summary(self, source: StagedSource) -> PdfSummary:
        body = await self._post("/v1/documents/summary", {"file": source.name})
        try:
            return summary_from_json(body)
        except (KeyError, TypeError, ValueError) as exc:
            raise _contract_error("summary") from exc

    async def pages(self, source: StagedSource, first: int, last: int) -> Sequence[PageSignals]:
        body = await self._post(
            "/v1/documents/pages", {"file": source.name, "first_page": first, "last_page": last}
        )
        try:
            return [signals_from_json(page) for page in body["pages"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise _contract_error("pages") from exc

    async def _post(self, path: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        try:
            async with self._http.stream(
                "POST", path, json=payload, timeout=self._timeout_s
            ) as response:
                status = response.status_code
                raw = await _read_bounded(response)
        except httpx.HTTPError as exc:  # transport, timeout, protocol: transient
            raise InspectionUnavailable(f"ele-parse is unreachable: {type(exc).__name__}") from exc
        if raw is None:
            # an answer past the bound is a fault of this range, never a reason to hold it in
            # memory: asked again page by page, the page that causes it alone is lost
            raise PageRangeFailed(StageReason.PARSE_ERROR)
        if status == httpx.codes.OK:
            return _json(raw)
        error = _error_body(raw)
        if status == httpx.codes.UNPROCESSABLE_ENTITY:
            code = error.get("code")
            if isinstance(code, str) and code in _REJECTIONS:
                raise PdfRejected(code, _REJECTIONS[code])
            raise AppError("ele-parse refused the request", code="common.internal")
        if status == httpx.codes.GATEWAY_TIMEOUT:
            raise PageRangeFailed(StageReason.PARSE_TIMEOUT)
        if status == httpx.codes.INSUFFICIENT_STORAGE:
            raise PageRangeFailed(StageReason.MEMORY_LIMIT)
        if status == httpx.codes.INTERNAL_SERVER_ERROR and error.get("code") == "parse_error":
            raise PageRangeFailed(StageReason.PARSE_ERROR)
        if status in (httpx.codes.BAD_REQUEST, httpx.codes.NOT_FOUND):
            raise AppError(
                f"ele-parse could not use the staged file ({status})", code="common.internal"
            )
        raise InspectionUnavailable(f"ele-parse answered {status}")


async def _read_bounded(response: httpx.Response) -> bytes | None:
    """The body, or ``None`` once it is past ``MAX_RESPONSE_BYTES`` (read no further)."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_RESPONSE_BYTES:
            return None
    return bytes(body)


def _json(raw: bytes) -> Mapping[str, Any]:
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise _contract_error("response") from exc
    if not isinstance(body, dict):
        raise _contract_error("response")
    return body


def _error_body(raw: bytes) -> Mapping[str, Any]:
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _contract_error(what: str) -> AppError:
    return AppError(f"ele-parse sent a malformed {what}", code="common.internal")
