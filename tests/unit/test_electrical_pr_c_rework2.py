"""PR-C rework round 2 (code review #1..#10, security review C-1..C-11, QA-5).

Each test names the finding it closes and fails on the code it replaced. The inspection tests
drive the real use case over the in-memory stack of ``support_electrical``; the ele-parse ones
use the real child process on small PDFs written in ``tmp_path``.
"""

from __future__ import annotations

import html
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
from services.ele_parse import __main__ as ele_cli
from services.ele_parse import app, contract, inspect, sandbox
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

from app.framework.errors import NotFoundError, ValidationError
from app.framework.pagination import (
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
)
from app.modules.electrical.drawings.adapters import ele_parse_client, sql_repository
from app.modules.electrical.drawings.adapters.ele_parse_client import EleParseClient
from app.modules.electrical.drawings.adapters.source_stager import StreamingSourceStager
from app.modules.electrical.drawings.application import _common, purge
from app.modules.electrical.drawings.application.projects import parse_profile
from app.modules.electrical.drawings.domain.errors import InvalidProfile, PageRangeFailed
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pages import (
    FontSignal,
    StageReason,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.profile import DrawingProfile
from app.modules.electrical.drawings.ports.source import StagedSource
from app.ops import electrical_characterize
from tests.unit.support_electrical import (
    IEC_TOKENS,
    FakePdfInspection,
    Stack,
    build_stack,
    make_ctx,
    make_job,
    make_signals,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)
from tests.unit.test_api_electrical_router import Env, _make_env

BODY = b"%PDF-1.7 a drawing"


# --------------------------------------------------------------------------------- fixtures
@pytest.fixture
def stack() -> Stack:
    return build_stack()


@pytest.fixture
def env() -> Env:
    return _make_env()


def _pdf(pages: int = 4, **kwargs: Any) -> FakePdfInspection:
    return FakePdfInspection(
        make_summary(page_count=pages, creator="EPLAN Electric P8", producer="EPLAN"),
        [tokens_page(n, IEC_TOKENS) for n in range(1, pages + 1)],
        **kwargs,
    )


async def _registered(stack: Stack, *, profile: dict[str, Any] | None = None) -> tuple[str, str]:
    project = await seed_project(stack, **(profile or {"schema": 1, "auto_accept_confidence": 0.5}))
    await seed_file(stack, "f1", body=BODY)
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    return registered.document.id, registered.job.id


class _Hooked(FakePdfInspection):
    """A replayed ele-parse that runs ``hook(first, last)`` at the start of each ``pages`` call."""

    def __init__(self, *args: Any, hook: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._hook = hook

    async def pages(self, source: StagedSource, first: int, last: int) -> Any:
        await self._hook(first, last)
        return await super().pages(source, first, last)


def _hooked(pages: int, hook: Any, **kwargs: Any) -> _Hooked:
    return _Hooked(
        make_summary(page_count=pages),
        [tokens_page(n, IEC_TOKENS) for n in range(1, pages + 1)],
        hook=hook,
        **kwargs,
    )


# =================================================== #2 a cancellation in the last batch
async def test_a_cancel_asked_during_the_only_batch_ends_the_job_cancelled(stack: Stack) -> None:
    """INC1-AC-8. A document of up to a batch (200 pages) has no boundary after the request;
    the request used to be dropped and the job ended `succeeded` / `awaiting_confirmation`."""
    doc_id, job_id = await _registered(stack)

    async def cancel(first: int, last: int) -> None:
        await stack.job_service.cancel(stack.ctx, job_id)

    await stack.inspector(_hooked(2, cancel)).run(stack.ctx, job_id=job_id, event_id="e1")

    job = stack.jobs.rows[job_id]
    assert job.status is JobStatus.CANCELLED
    assert stack.ledger.claimed == ["e1"]
    assert stack.outbox.data("electrical.job.completed.v1")[-1]["status"] == "cancelled"
    assert await stack.characterizations.get(stack.ctx, doc_id) is None  # no result of a
    # cancelled run


async def test_a_second_cancel_of_a_job_that_awaits_confirmation_really_cancels_it(
    stack: Stack,
) -> None:
    """A request that outlived its run (`awaiting_confirmation` keeps the field in old rows)
    made the second `POST .../cancel` a 200 that wrote nothing: the job could not be cancelled."""
    _, job_id = await _registered(stack, profile={"schema": 1, "auto_accept_confidence": 1.0})
    await stack.inspector(_pdf()).run(stack.ctx, job_id=job_id, event_id="e1")
    stored = stack.jobs.rows[job_id]
    assert stored.status is JobStatus.AWAITING_CONFIRMATION
    stale_request = replace(stored, cancel_requested_at=stack.clock())  # a row from before
    stack.jobs.rows[job_id] = stale_request

    view = await stack.job_service.cancel(stack.ctx, job_id)

    assert view.job.status is JobStatus.CANCELLED
    assert stack.jobs.rows[job_id].status is JobStatus.CANCELLED


def test_the_end_of_a_run_clears_the_cancel_request() -> None:
    now = make_job().created_at
    for end in ("succeed", "fail", "await_confirmation"):
        job = make_job(status=JobStatus.RUNNING, lease_until=now, cancel_requested_at=now)
        if end == "fail":
            job.fail("electrical.pdf_rejected", "x", now)
        else:
            getattr(job, end)(now)
        assert job.cancel_requested_at is None, end


# =========================================================== #4 a summary that cannot be had
@pytest.mark.parametrize("reason", list(StageReason)[:3])
async def test_a_summary_that_times_out_or_hits_the_limit_fails_the_job_with_a_clear_text(
    stack: Stack, reason: StageReason
) -> None:
    _, job_id = await _registered(stack)

    class _NoSummary(FakePdfInspection):
        async def summary(self, source: StagedSource) -> Any:
            raise PageRangeFailed(reason)

    pdf = _NoSummary(make_summary(page_count=2), [])
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")

    job = stack.jobs.rows[job_id]
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.pdf_rejected"
    assert job.failure_detail is not None and f"({reason.value})" in job.failure_detail
    assert stack.ledger.claimed == ["e1"]


# ================================================ #6 the lease while the pages are retried
async def test_the_lease_is_extended_after_every_page_of_the_page_by_page_retry(
    stack: Stack,
) -> None:
    """Each page of a failed range may take its own time limit; the lease (600 s) used to be
    extended only when the whole batch was committed."""
    _, job_id = await _registered(stack)
    seen: list[tuple[Any, Any]] = []

    async def slow(first: int, last: int) -> None:
        seen.append((stack.jobs.rows[job_id].lease_until, stack.clock()))
        stack.clock.advance(400)

    pdf = _hooked(2, slow, failures={1: StageReason.PARSE_TIMEOUT})
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")

    assert len(seen) == 3  # the range, then the two pages alone
    assert all(lease is not None and lease > now for lease, now in seen), seen
    assert stack.jobs.rows[job_id].status is not JobStatus.RUNNING


async def test_a_run_that_lost_the_job_in_the_retry_writes_nothing_more(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)

    async def taken_over(first: int, last: int) -> None:
        if (first, last) == (1, 1):
            stack.jobs.rows[job_id].attempt += 1  # another run holds it now

    pdf = _hooked(2, taken_over, failures={1: StageReason.PARSE_TIMEOUT})
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")

    assert stack.jobs.rows[job_id].status is JobStatus.RUNNING  # not ours to end
    assert stack.ledger.claimed == []
    counts = await stack.pages.stage_counts(stack.ctx, doc_id, Stage.CLASSIFY)
    assert counts.get(StageStatus.SUCCEEDED, 0) == 0 and counts.get(StageStatus.FAILED, 0) == 0


# ============================================================= #3 seed under the driver's limit
class _RecordingSession:
    def __init__(self, sink: list[Any]) -> None:
        self._sink = sink

    async def execute(self, statement: Any) -> None:
        self._sink.append(statement)


def _parameters(statement: Any) -> int:
    return len(statement.compile(dialect=asyncpg_dialect.dialect()).params)


async def test_seed_of_5000_pages_stays_under_the_32767_parameter_limit() -> None:
    statements: list[Any] = []

    @asynccontextmanager
    async def tenant_session(_ctx: Any) -> AsyncIterator[_RecordingSession]:
        yield _RecordingSession(statements)

    repo = sql_repository.SqlPageRepository(tenant_session)
    pages = list(range(1, 5001))
    await repo.seed(make_ctx(), "doc", pages, Stage.CLASSIFY, "v1")

    assert len(statements) == 10  # a pages statement and a stages one per chunk of 1,000
    assert max(_parameters(s) for s in statements) < 32_767  # asyncpg's ceiling
    # nothing is lost to the chunking
    rows = sum(
        len(s.compile(dialect=asyncpg_dialect.dialect()).params) // 7 for s in statements[1::2]
    )
    assert rows == 5000


# ================================================================ #5 the profile at the boundary
@pytest.mark.parametrize(
    "labels",
    [["-K5"], ["KM1"], ["X1:14"], ["+1"], ["--"], ["-4\u200b8V"], ["+"], ["Q7Z", "+"]],
)
def test_a_voltage_label_the_core_would_refuse_is_a_422_at_save(
    env: Env, labels: list[str]
) -> None:
    project = env.project()
    response = env.put(
        f"/projects/{project['id']}/profile", {"body": {"schema": 1, "voltage_labels": labels}}
    )
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "electrical.invalid_profile"
    assert "voltage_labels" in problem["detail"]
    for label in labels:  # the client's text is never echoed (S-8)
        assert label.strip("+-") == "" or label not in response.text


def test_an_unknown_aspect_is_a_422_that_lists_the_known_ones_without_echo(env: Env) -> None:
    project = env.project()
    response = env.put(
        f"/projects/{project['id']}/profile", {"body": {"schema": 1, "extra_aspects": ["=+"]}}
    )
    assert response.status_code == 422, response.text
    assert "extra_aspects" in response.json()["detail"] and "=+" not in response.json()["detail"]


@pytest.mark.parametrize("label", ["+24V", "-48V"])
def test_a_rail_label_is_accepted_and_stored_canonical(env: Env, label: str) -> None:
    project = env.project()
    sent = label.lower()  # `-48v` -> stored `-48V`
    response = env.put(
        f"/projects/{project['id']}/profile", {"body": {"schema": 1, "voltage_labels": [sent]}}
    )
    assert response.status_code == 200, response.text
    stored = env.get(f"/projects/{project['id']}/profile").json()["body"]["voltage_labels"]
    assert stored == [label]


def test_parse_profile_message_never_carries_the_client_text(stack: Stack) -> None:
    with pytest.raises(ValidationError) as raised:
        parse_profile(stack.rules, {"schema": 1, "voltage_labels": ["-K5"]})
    assert "K5" not in str(raised.value)


# ================================================ C-5 / C-8 the profile's other fields
@pytest.mark.parametrize(
    "body", [{"languages": [[1]]}, {"plc_vendor": []}, {"plc_vendor": {}}, {"languages": [{}]}]
)
def test_an_unhashable_profile_value_is_an_invalid_profile_not_a_type_error(
    body: dict[str, Any],
) -> None:
    with pytest.raises(InvalidProfile):
        DrawingProfile.from_body({"schema": 1, **body}, known_patterns=frozenset())


@pytest.mark.parametrize(
    "name", ["lib" + chr(0x202E) + "v", "lib\u200bv", "lib\x07v", "lib" + chr(0xE000) + "v"]
)
def test_a_vendor_library_with_hidden_characters_is_refused_without_echo(name: str) -> None:
    with pytest.raises(InvalidProfile) as raised:
        DrawingProfile.from_body(
            {"schema": 1, "symbol_standard": "vendor", "vendor_library": name},
            known_patterns=frozenset(),
        )
    assert "lib" not in raised.value.message


# =========================================================================== QA-5 / the cursors
def test_a_seq_cursor_past_int32_is_an_invalid_cursor_when_the_column_is_int4() -> None:
    cursor = encode_seq_cursor(2**31)
    assert decode_seq_cursor(cursor) == 2**31  # a bigint column (messages) keeps its range
    with pytest.raises(ValidationError):
        decode_seq_cursor(cursor, maximum=2**31 - 1)
    assert decode_seq_cursor(encode_seq_cursor(2**31 - 1), maximum=2**31 - 1) == 2**31 - 1


def test_id_cursors_keep_accepting_every_canonical_uuid_text() -> None:
    for value in ("0190f1d2-0000-7000-8000-000000000000", "0190F1D2-0000-7000-8000-ABCDEF000000"):
        assert decode_id_cursor(encode_id_cursor(value)) == value


# ============================================================================ C-4 the stager
class _Overlong:
    """An object bigger than the file row says; counts what was served."""

    def __init__(self, size: int) -> None:
        self._size = size
        self.served = 0

    def read_chunks(self, key: str, *, chunk_bytes: int) -> AsyncIterator[bytes]:
        async def generate() -> AsyncIterator[bytes]:
            for _ in range(0, self._size, chunk_bytes):
                self.served += chunk_bytes
                yield b"x" * chunk_bytes

        return generate()


async def test_the_stager_stops_at_the_first_chunk_past_the_expected_size(tmp_path: Path) -> None:
    storage = _Overlong(10_000_000)
    stager = StreamingSourceStager(storage, tmp_path, chunk_bytes=1_000)
    with pytest.raises(ValidationError) as raised:
        await stager.stage(make_ctx(), document_id="d1", storage_key="k", expected_size=2_500)
    assert raised.value.code == "electrical.source_unreadable"
    assert storage.served == 3_000  # not the 10 MB
    assert list((tmp_path / make_ctx().workspace_id).iterdir()) == []


# ================================================================== C-3 the sandbox's bounds
def _stub(script: str) -> list[str]:
    return [sys.executable, "-c", script]


_LIMITS = sandbox.Limits(512, 20)


def test_a_rejection_reaches_the_caller_with_a_fixed_text_never_the_childs() -> None:
    script = (
        "import json,sys;sys.stdin.read();"
        "print(json.dumps({'ok':False,'code':'corrupt','detail':'HOSTILE TEXT'}))"
    )
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child({}, timeout_s=10, limits=_LIMITS, argv=_stub(script))
    assert raised.value.status == 422 and raised.value.code == "corrupt"
    assert "HOSTILE" not in raised.value.detail


def test_an_answer_past_the_bound_is_killed_and_is_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract, "MAX_ANSWER_BYTES", 100_000)
    script = "import sys;sys.stdin.read();sys.stdout.write('x'*50_000_000)"
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child({}, timeout_s=20, limits=_LIMITS, argv=_stub(script))
    assert (raised.value.status, raised.value.code) == (500, "parse_error")


def test_a_diagnostic_past_the_bound_is_a_parse_error() -> None:
    script = (
        "import sys;sys.stdin.read();sys.stderr.write('e'*1_000_000);sys.stderr.flush();"
        "sys.stdout.write('{}')"
    )
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child({}, timeout_s=20, limits=_LIMITS, argv=_stub(script))
    assert (raised.value.status, raised.value.code) == (500, "parse_error")


def _client(handler: Any) -> EleParseClient:
    transport = httpx.MockTransport(handler)
    return EleParseClient(
        httpx.AsyncClient(transport=transport, base_url="http://ele-parse"), timeout_s=5
    )


async def test_a_response_past_the_bound_is_a_failed_range_not_a_batch_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ele_parse_client, "MAX_RESPONSE_BYTES", 1_000)
    client = _client(lambda r: httpx.Response(200, content=b"{" + b" " * 5_000 + b"}"))
    with pytest.raises(PageRangeFailed) as raised:
        await client.pages(StagedSource("w/d.pdf", "0" * 64, 1), 1, 200)
    assert raised.value.reason is StageReason.PARSE_ERROR


async def test_the_client_clips_what_the_service_sent_beyond_the_contract() -> None:
    page = {
        k: (0 if k not in ("image_area_ratio", "width_pt", "height_pt") else 0.0)
        for k in [
            "page_no",
            "width_pt",
            "height_pt",
            "rotation",
            "char_count",
            "unmapped_char_count",
            "pua_char_count",
            "cjk_char_count",
            "latin_char_count",
            "word_count",
            "vector_path_count",
            "short_curve_path_count",
            "image_count",
            "image_area_ratio",
            "link_count",
        ]
    }
    page["page_no"] = 1
    fonts = [
        {"name": "n" * 500, "font_type": "t" * 5000, "cid": 0, "embedded": 1, "has_tounicode": 0}
    ] * 40
    body = {"pages": [{**page, "fonts": fonts, "tokens": ["k" * 400] * 3}]}
    client = _client(lambda r: httpx.Response(200, json=body))
    (signals,) = await client.pages(StagedSource("w/d.pdf", "0" * 64, 1), 1, 1)
    assert len(signals.fonts) == 32
    assert len(signals.fonts[0].name) == 96 and len(signals.fonts[0].font_type) == 32
    assert all(len(t) == 32 for t in signals.tokens)


def test_a_stored_font_type_is_clipped() -> None:
    signals = replace(make_signals(1), fonts=(FontSignal("f", "T" * 4000, False, True, False),))
    stored = sql_repository._abridged_signals(signals)
    assert len(stored["fonts"][0]["font_type"]) == 32


# ========================================================= C-2 a crafted PDF with huge font tables
def _fonts_pdf(path: Path, pages: int, fonts: int = 32) -> Path:
    """Fonts whose /Subtype and /BaseFont are as long as MuPDF allows and full of control
    characters (`#01`), shared by every page: 32 of them on each of `pages` pages."""
    first, resources = 3, 3 + fonts
    content_obj, page_first = resources + 1, resources + 2
    objs: dict[int, bytes] = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    kids = " ".join(f"{page_first + i} 0 R" for i in range(pages))
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()
    for i in range(fonts):
        objs[first + i] = (
            f"<< /Type /Font /Subtype /S{i:02d}{'#01' * 4200} /BaseFont /B{i:02d}{'#01' * 200} >>"
        ).encode()
    refs = " ".join(f"/F{i} {first + i} 0 R" for i in range(fonts))
    objs[resources] = f"<< /Font << {refs} >> >>".encode()
    text = b"BT /F0 12 Tf 72 720 Td (KM1) Tj ET"
    objs[content_obj] = b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream"
    for i in range(pages):
        objs[page_first + i] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources {resources} 0 R /Contents {content_obj} 0 R >>"
        ).encode()
    data, offsets = b"%PDF-1.7\n", {}
    for number in sorted(objs):
        offsets[number] = len(data)
        data += b"%d 0 obj\n" % number + objs[number] + b"\nendobj\n"
    size = max(objs) + 1
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        data += b"%010d 00000 n \n" % offsets[number]
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    path.write_bytes(data)
    return path


def test_a_pdf_of_huge_font_tables_gives_a_small_answer_of_clean_fields(tmp_path: Path) -> None:
    path = _fonts_pdf(tmp_path / "fonts.pdf", pages=10)
    result = sandbox.run_child(
        {"op": "pages", "path": str(path), "first": 1, "last": 10, "token_sample": 200},
        timeout_s=60,
        limits=sandbox.Limits(1024, 60),
    )
    pages = result["pages"]
    assert len(pages) == 10
    for page in pages:
        assert len(page["fonts"]) == 32
        for font in page["fonts"]:
            assert len(font["font_type"]) <= contract.MAX_FONT_TYPE_CHARS
            assert len(font["name"]) <= contract.MAX_FONT_NAME_CHARS
            assert all(ch >= " " for ch in font["name"] + font["font_type"])  # no control chars
    assert len(repr(result)) < 200_000  # it was ~25 MB for these ten pages


def test_a_page_whose_signals_do_not_fit_the_bound_is_left_out_and_the_rest_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _fonts_pdf(tmp_path / "fonts.pdf", pages=3, fonts=2)
    monkeypatch.setattr(contract, "MAX_PAGE_JSON_BYTES", 10)
    with inspect.open_document(path) as document:
        assert inspect.inspect_pages(document, 1, 3, 200) == []  # the caller marks them failed
    monkeypatch.undo()
    with inspect.open_document(path) as document:
        assert len(inspect.inspect_pages(document, 1, 3, 200)) == 3


def test_font_names_and_types_lose_control_and_format_characters_then_are_cut() -> None:
    assert contract.clean("a\x01b\u200bc" + chr(0x202E) + "d", 10) == "abcd"
    assert contract.clean("x" * 100, 32) == "x" * 32


# ============================================================ C-11 opening without following links
def test_a_symlinked_pdf_is_not_opened(tmp_path: Path) -> None:
    real = tmp_path / "real.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(real))
    doc.close()
    link = tmp_path / "link.pdf"
    link.symlink_to(real)
    with inspect.open_document(real) as document:
        assert document.page_count == 1
    with pytest.raises(inspect.Rejected), inspect.open_document(link):
        pass


def test_a_directory_is_not_a_pdf(tmp_path: Path) -> None:
    with pytest.raises(inspect.Rejected), inspect.open_document(tmp_path):
        pass


# ============================================================================ C-7 the access log
def test_the_access_log_does_not_raise_when_the_request_line_never_parsed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    handler = app._Handler.__new__(app._Handler)  # no `command`, no `path`: as after a timeout
    handler.log_message("%s", "timed out")
    handler.log_message("code %d, message %s", 400, "Bad request")
    assert capsys.readouterr().out.startswith("ele-parse - - ")


# ================================================================ #7 the CLI retries by page
def test_the_cli_inspection_keeps_the_good_pages_of_a_range_with_a_failing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-")
    calls: list[tuple[int, int]] = []

    def run_child(request: dict[str, Any], **_: Any) -> dict[str, Any]:
        if request["op"] == "summary":
            return {"page_count": 4}
        first, last = request["first"], request["last"]
        calls.append((first, last))
        if first <= 3 <= last:
            raise contract.ContractError(507, "memory_limit", "bomb")
        return {"pages": [{"page_no": n} for n in range(first, last + 1)]}

    monkeypatch.setattr(sandbox, "run_child", run_child)
    report = ele_cli.inspect_file(pdf, app.Config(stage_root=tmp_path))

    assert [p["page_no"] for p in report["pages"]] == [1, 2, 4]  # type: ignore[index]
    assert report["failed_pages"] == [{"page_no": 3, "reason": "memory_limit"}]
    assert calls == [(1, 4), (1, 1), (2, 2), (3, 3), (4, 4)]


def test_the_cli_still_stops_on_a_refusal_that_is_not_about_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_child(request: dict[str, Any], **_: Any) -> dict[str, Any]:
        raise contract.ContractError(422, "encrypted", "x")

    monkeypatch.setattr(sandbox, "run_child", run_child)
    with pytest.raises(contract.ContractError):
        ele_cli.inspect_file(tmp_path / "x.pdf", app.Config(stage_root=tmp_path))


# ================================================================= C-9 the Markdown report
def test_the_markdown_report_escapes_html_and_links_from_the_drawing() -> None:
    hostile = "<img src=x onerror=alert(1)>[x](javascript:alert(1))|"
    cell = electrical_characterize._cell(hostile)
    assert "<" not in cell and html.unescape(cell.replace("\\", "")) == hostile
    assert "[" not in cell.replace("\\[", "") and "|" not in cell.replace("\\|", "")


# ====================================================================== single constants, guards
def test_document_not_live_is_one_constant() -> None:
    assert purge.DOCUMENT_NOT_LIVE is _common.DOCUMENT_NOT_LIVE
    assert _common.DOCUMENT_NOT_LIVE == "electrical.document_not_live"


_ = (Sequence, NotFoundError)  # imported for the type checkers of the fixtures above
