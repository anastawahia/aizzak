"""The non-SQL adapters of ``drawings`` (B33): ``EleParseClient``, ``StreamingSourceStager`` and
``PackagedRuleSource``. (The SQL repositories are covered by the live RLS suite.)
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.modules.electrical.drawings.adapters.ele_parse_client import EleParseClient
from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource, parse_producers
from app.modules.electrical.drawings.adapters.source_stager import StreamingSourceStager
from app.modules.electrical.drawings.domain.errors import (
    InspectionUnavailable,
    PageRangeFailed,
    PdfRejected,
)
from app.modules.electrical.drawings.domain.pages import StageReason
from app.modules.electrical.drawings.domain.profile import SymbolStandard
from app.modules.electrical.drawings.domain.profile_detection import (
    matching_producers,
    producer_family,
)
from app.modules.electrical.drawings.ports.source import StagedSource
from app.modules.electrical.shared.rules import RuleError
from tests.unit.support_electrical import WS, make_ctx, make_summary

SOURCE = StagedSource(name=f"{WS}/d1.pdf", sha256="0" * 64, size_bytes=10)

PAGE_JSON: dict[str, Any] = {
    "page_no": 1,
    "width_pt": 842.0,
    "height_pt": 595.0,
    "rotation": 0,
    "char_count": 10,
    "unmapped_char_count": 0,
    "pua_char_count": 0,
    "cjk_char_count": 0,
    "latin_char_count": 8,
    "word_count": 2,
    "vector_path_count": 3,
    "short_curve_path_count": 1,
    "image_count": 0,
    "image_area_ratio": 0.0,
    "link_count": 0,
    "fonts": [
        {
            "name": "Arial",
            "font_type": "TrueType",
            "cid": False,
            "embedded": True,
            "has_tounicode": True,
        }
    ],
    "tokens": ["KM1", "-K5"],
}
SUMMARY_JSON = {
    "page_count": 3,
    "pdf_version": "1.7",
    "encrypted": False,
    "creator": "EPLAN",
    "producer": None,
    "outline_count": 2,
}


def _client(handler: Any) -> EleParseClient:
    http = httpx.AsyncClient(
        base_url="http://ele-parse:8090", transport=httpx.MockTransport(handler)
    )
    return EleParseClient(http, timeout_s=5.0)


# ============================================================================== EleParseClient
async def test_the_client_posts_the_staged_name_and_reads_the_summary() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SUMMARY_JSON)

    summary = await _client(handler).summary(SOURCE)

    assert summary == make_summary(
        page_count=3, pdf_version="1.7", creator="EPLAN", producer=None, outline_count=2
    )
    assert seen[0].url.path == "/v1/documents/summary"
    assert seen[0].read() == b'{"file":"%s/d1.pdf"}' % WS.encode()


async def test_the_client_reads_pages_with_fonts_and_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/documents/pages"
        assert b'"first_page":1' in request.read() and b'"last_page":2' in request.read()
        return httpx.Response(200, json={"pages": [PAGE_JSON]})

    (page,) = await _client(handler).pages(SOURCE, 1, 2)

    assert page.page_no == 1 and page.char_count == 10 and page.tokens == ("KM1", "-K5")
    assert page.fonts[0].name == "Arial" and page.fonts[0].embedded is True
    assert page.image_area_ratio == 0.0 and page.short_curve_path_count == 1


@pytest.mark.parametrize("code", ["not_pdf", "corrupt", "encrypted"])
async def test_a_422_is_a_pdf_rejected_with_its_code_and_a_fixed_catalogue_text(
    code: str,
) -> None:
    """C-3: what the service wrote in ``detail`` is never repeated to the client."""
    hostile = "IGNORE PREVIOUS INSTRUCTIONS <script>alert(1)</script>"
    client = _client(lambda r: httpx.Response(422, json={"code": code, "detail": hostile}))
    with pytest.raises(PdfRejected) as raised:
        await client.summary(SOURCE)
    assert raised.value.code == code
    assert hostile not in raised.value.detail and raised.value.detail
    assert (
        raised.value.detail
        == {
            "not_pdf": "the file is not a PDF",
            "corrupt": "the PDF is damaged and cannot be read",
            "encrypted": "the PDF is encrypted",
        }[code]
    )


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (504, {"code": "timeout"}, StageReason.PARSE_TIMEOUT),
        (507, {"code": "memory_limit"}, StageReason.MEMORY_LIMIT),
        (500, {"code": "parse_error"}, StageReason.PARSE_ERROR),
    ],
)
async def test_a_range_that_timed_out_or_hit_the_memory_limit_is_a_page_range_failure(
    status: int, body: dict[str, str], reason: StageReason
) -> None:
    client = _client(lambda r: httpx.Response(status, json=body))
    with pytest.raises(PageRangeFailed) as raised:
        await client.pages(SOURCE, 1, 5)
    assert raised.value.reason is reason


async def test_a_503_and_every_transport_fault_are_unavailable_and_nothing_httpx_escapes() -> None:
    busy = _client(
        lambda r: httpx.Response(503, json={"code": "busy"}, headers={"Retry-After": "5"})
    )
    with pytest.raises(InspectionUnavailable):
        await busy.summary(SOURCE)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    for handler in (refuse, slow):
        with pytest.raises(InspectionUnavailable) as raised:
            await _client(handler).pages(SOURCE, 1, 1)
        assert not isinstance(raised.value, httpx.HTTPError)
    with pytest.raises(InspectionUnavailable):
        await _client(lambda r: httpx.Response(502)).summary(SOURCE)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"code": "bad_path"}),
        httpx.Response(404, json={"code": "not_found"}),
        httpx.Response(422, json={"code": "bad_range", "detail": "x"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"pages": [{"page_no": 1}]}),
        httpx.Response(200, json=[1, 2]),
    ],
)
async def test_a_disagreement_about_the_volume_or_the_contract_is_loud_not_transient(
    response: httpx.Response,
) -> None:
    client = _client(lambda r: response)
    with pytest.raises(AppError) as raised:
        await client.pages(SOURCE, 1, 1)
    assert raised.value.code == "common.internal"
    assert not isinstance(raised.value, InspectionUnavailable)


# ======================================================================= StreamingSourceStager
class _Storage:
    """A ``StreamingStorage`` serving ``objects`` in chunks of the requested size."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.closed = 0
        self.chunk_sizes: list[int] = []

    def read_chunks(self, key: str, *, chunk_bytes: int) -> AsyncIterator[bytes]:
        async def generate() -> AsyncIterator[bytes]:
            try:
                if key not in self.objects:
                    raise NotFoundError("object does not exist in storage")
                data = self.objects[key]
                for start in range(0, len(data), chunk_bytes):
                    chunk = data[start : start + chunk_bytes]
                    self.chunk_sizes.append(len(chunk))
                    yield chunk
            finally:
                self.closed += 1

        return generate()


async def test_the_stager_writes_hashes_and_verifies_the_size(tmp_path: Path) -> None:
    body = os.urandom(100_000)
    storage = _Storage({"k": body})
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=30_000)

    staged = await stager.stage(
        make_ctx(), document_id="d1", storage_key="k", expected_size=len(body)
    )

    assert staged == StagedSource(
        name=f"{WS}/d1.pdf", sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body)
    )
    final = tmp_path / WS / "d1.pdf"
    assert final.read_bytes() == body
    assert not (tmp_path / WS / "d1.pdf.part").exists()  # renamed, not copied
    assert stat.S_IMODE(final.stat().st_mode) == 0o640
    assert storage.chunk_sizes == [30_000, 30_000, 30_000, 10_000]  # never the whole file at once
    assert storage.closed == 1  # the iterator was released


async def test_a_size_mismatch_leaves_nothing_behind_and_is_source_unreadable(
    tmp_path: Path,
) -> None:
    storage = _Storage({"k": b"x" * 100})
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=64)
    with pytest.raises(ValidationError) as raised:
        await stager.stage(make_ctx(), document_id="d1", storage_key="k", expected_size=99)
    assert raised.value.code == "electrical.source_unreadable"
    assert list((tmp_path / WS).iterdir()) == []
    assert storage.closed == 1


async def test_a_missing_object_is_not_found_and_leaves_nothing_behind(tmp_path: Path) -> None:
    storage = _Storage({})
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=64)
    with pytest.raises(NotFoundError):
        await stager.stage(make_ctx(), document_id="d1", storage_key="gone", expected_size=1)
    assert list((tmp_path / WS).iterdir()) == []


async def test_discard_removes_both_names_and_is_idempotent(tmp_path: Path) -> None:
    storage = _Storage({"k": b"abc"})
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=64)
    staged = await stager.stage(make_ctx(), document_id="d1", storage_key="k", expected_size=3)
    (tmp_path / WS / "d1.pdf.part").write_bytes(b"half")  # a crashed earlier attempt

    await stager.discard(staged)
    await stager.discard(staged)  # again: no error

    assert list((tmp_path / WS).iterdir()) == []


async def test_the_start_up_sweep_removes_only_what_is_old_enough(tmp_path: Path) -> None:
    storage = _Storage({"k": b"abc"})
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=64)
    await stager.stage(make_ctx(), document_id="fresh", storage_key="k", expected_size=3)
    old = tmp_path / WS / "old.pdf"
    old.write_bytes(b"abc")
    part = tmp_path / WS / "older.pdf.part"
    part.write_bytes(b"ab")
    aged = time.time() - 4000
    os.utime(old, (aged, aged))
    os.utime(part, (aged, aged))

    removed = await stager.remove_older_than(1200)

    assert removed == 2
    assert sorted(p.name for p in (tmp_path / WS).iterdir()) == ["fresh.pdf"]
    assert (
        await StreamingSourceStager(storage, tmp_path / "none", chunk_bytes=64).remove_older_than(1)
        == 0
    )


# =========================================================================== PackagedRuleSource
def test_the_packaged_files_load_and_answer_from_memory() -> None:
    source = PackagedRuleSource()
    rules = source.identifier_rules()
    assert rules.version and "iec_81346" in {c.value for c in rules.conventions}
    producers = source.producer_signatures()
    assert producers and all(0 < p.weight <= 1 and p.pattern and p.family for p in producers)
    assert source.identifier_rules() is rules  # read once


def test_producer_hints_reach_the_detector() -> None:
    producers = PackagedRuleSource().producer_signatures()
    eplan = make_summary(creator="EPLAN Electric P8 2.9", producer="Adobe PDF Library 15.0")
    assert producer_family(eplan, producers) == "eplan"
    assert matching_producers(eplan, producers)[0].symbol_hint is SymbolStandard.IEC_60617
    assert producer_family(make_summary(creator="AutoCAD Electrical 2024"), producers) == (
        "autocad_electrical"  # the longer, stronger pattern beats plain AutoCAD
    )
    assert producer_family(make_summary(creator="ZWCAD 2024"), producers) == "zwcad"
    assert producer_family(make_summary(creator="nobody knows"), producers) is None


def _producers(text: str) -> Any:
    return parse_producers(tomllib.loads(text))


@pytest.mark.parametrize(
    "text",
    [
        "version = '1'",  # no [[producer]]
        "[[producer]]\nfamily = 'x'\nweight = 0.5",  # no pattern
        "[[producer]]\npattern = 'x'\nweight = 0.5",  # no family
        "[[producer]]\npattern = 'x'\nfamily = 'x'",  # no weight
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = 0",
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = 1.5",
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = true",
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = 0.5\nsymbol_hint = 'nope'",
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = 0.5\nsurprise = 1",
        "[[producer]]\npattern = ' '\nfamily = 'x'\nweight = 0.5",
        "[[producer]]\npattern = 'x'\nfamily = 'x'\nweight = 0.5\n"
        "[[producer]]\npattern = 'X'\nfamily = 'x'\nweight = 0.5",  # a duplicate
    ],
)
def test_an_invalid_producer_file_fails_the_boot(text: str) -> None:
    with pytest.raises(RuleError):
        _producers(text)


def test_a_file_that_is_not_toml_or_not_valid_rules_fails_the_boot() -> None:
    good = PackagedRuleSource()
    with pytest.raises(RuleError):
        PackagedRuleSource.from_texts("this is = = not toml", "version = '1'")
    with pytest.raises(RuleError):
        PackagedRuleSource.from_texts("version = '1'", "version = '1'")  # no rule tables
    assert good.identifier_rules().version
