"""``services/ele_parse`` (BT26; INC1-AC-7, INC1-AC-9): the inspection over real PDFs generated
with PyMuPDF in ``tmp_path``, the contract's bounds, the sandbox (limits, time, memory, children)
and the CLI mode that works off the stack.

The child process is real wherever the question is about the process (limits, timeouts, signals);
the in-process ``child.handle`` answers the questions about what a PDF looks like, to keep the
suite fast.
"""

from __future__ import annotations

import http.client
import io
import json
import resource
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from services.ele_parse import app, child, contract, sandbox
from services.ele_parse.__main__ import inspect_file, main

from tests.unit.support_electrical import FakePdfInspection

WS_ID = "aaaaaaaa-1111-7111-8111-111111111111"
DOC_ID = "22222222-2222-7222-8222-222222222222"
ROOT = sandbox.ROOT


# ------------------------------------------------------------------------------ PDF factories
def _save(doc: Any, path: Path, **kwargs: Any) -> Path:
    doc.save(str(path), **kwargs)
    doc.close()
    return path


def vector_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"creator": "EPLAN Electric P8", "producer": "EPLAN"})
    for n in range(pages):
        page = doc.new_page(width=842, height=595)
        page.insert_text((40, 60), f"-K{n + 1} KM{n + 1} X1:14 /12.3 %I0.0 rated 24V", fontsize=11)
        page.draw_line((40, 80), (400, 80))
        page.draw_rect(pymupdf.Rect(40, 100, 200, 160))
    return _save(doc, path)


def image_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40))
    pix.set_rect(pix.irect, (200, 30, 30))
    page.insert_image(page.rect, pixmap=pix)
    return _save(doc, path)


def empty_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page()
    return _save(doc, path)


def encrypted_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "secret")
    return _save(
        doc, path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner"
    )


def corrupt_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8)
    return path


def curve_pdf(path: Path, *, extra_shapes: bool = False) -> Path:
    """One page with a real Bezier curve 6 pt across (stroke-sized) and one 200 pt across; with
    ``extra_shapes`` also a line, a rectangle and a quad (``l``, ``re``, ``qu`` items)."""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    if extra_shapes:
        page.draw_line((40, 80), (400, 80))
        page.draw_rect(pymupdf.Rect(40, 100, 200, 160))
        page.draw_quad(pymupdf.Quad((300, 100), (400, 100), (300, 160), (400, 160)))
    small = page.new_shape()
    small.draw_bezier((100, 300), (102, 296), (105, 296), (106, 300))
    small.finish()
    small.commit()
    if not extra_shapes:
        big = page.new_shape()
        big.draw_bezier((100, 400), (150, 300), (250, 500), (300, 400))
        big.finish()
        big.commit()
    return _save(doc, path)


def request(op: str, path: Path, **extra: Any) -> dict[str, Any]:
    return {"op": op, "path": str(path), "token_sample": 50, **extra}


# ----------------------------------------------------------------------- what a page looks like
def test_a_vector_text_page_reports_its_text_tokens_paths_and_fonts(tmp_path: Path) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf", pages=2)

    summary = child.handle(request("summary", pdf))
    pages = child.handle(request("pages", pdf, first=1, last=2))

    assert summary["ok"] and summary["result"]["page_count"] == 2
    assert summary["result"]["creator"] == "EPLAN Electric P8"
    assert summary["result"]["producer"] == "EPLAN" and summary["result"]["encrypted"] is False
    first = pages["result"]["pages"][0]
    assert first["page_no"] == 1 and (first["width_pt"], first["height_pt"]) == (842.0, 595.0)
    assert first["char_count"] > 20 and first["unmapped_char_count"] == 0
    assert first["pua_char_count"] == 0 and first["cjk_char_count"] == 0
    assert first["latin_char_count"] > 0 and first["word_count"] >= 6
    assert first["vector_path_count"] >= 2 and first["image_count"] == 0
    assert {"-K1", "KM1", "X1:14", "/12.3", "%I0.0"} <= set(first["tokens"])
    assert first["fonts"] and first["fonts"][0]["name"]
    assert set(first["fonts"][0]) == {"name", "font_type", "cid", "embedded", "has_tounicode"}
    assert pages["result"]["pages"][1]["page_no"] == 2


def test_an_image_page_has_no_text_and_covers_the_page(tmp_path: Path) -> None:
    pages = child.handle(request("pages", image_pdf(tmp_path / "i.pdf"), first=1, last=1))
    page = pages["result"]["pages"][0]
    assert page["char_count"] == 0 and page["tokens"] == [] and page["image_count"] == 1
    assert page["image_area_ratio"] >= 0.99


def test_an_empty_page_has_nothing_at_all(tmp_path: Path) -> None:
    page = child.handle(request("pages", empty_pdf(tmp_path / "e.pdf"), first=1, last=1))["result"][
        "pages"
    ][0]
    assert (page["char_count"], page["vector_path_count"], page["image_count"]) == (0, 0, 0)
    assert page["word_count"] == 0 and page["fonts"] == []


def test_an_encrypted_pdf_is_refused_with_its_own_code(tmp_path: Path) -> None:
    answer = child.handle(request("summary", encrypted_pdf(tmp_path / "x.pdf")))
    assert answer["ok"] is False and answer["code"] == "encrypted"


def test_damaged_bytes_and_a_non_pdf_are_refused_with_distinct_codes(tmp_path: Path) -> None:
    corrupt = child.handle(request("summary", corrupt_pdf(tmp_path / "c.pdf")))
    assert corrupt["ok"] is False and corrupt["code"] == "corrupt"
    text = tmp_path / "t.pdf"
    text.write_text("this is not a pdf at all")
    assert child.handle(request("summary", text))["code"] == "not_pdf"
    assert child.handle(request("summary", tmp_path / "missing.pdf"))["code"] == "corrupt"


def test_a_range_beyond_the_document_is_a_bad_range_and_one_overlapping_it_is_clamped(
    tmp_path: Path,
) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf", pages=2)
    assert child.handle(request("pages", pdf, first=3, last=4))["code"] == "bad_range"
    clamped = child.handle(request("pages", pdf, first=2, last=9))["result"]["pages"]
    assert [p["page_no"] for p in clamped] == [2]


def test_the_tokens_are_distinct_bounded_and_in_reading_order(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "A1 B2 A1 " + "X" * 40 + " C3 D4 E5 F6", fontsize=9)
    pdf = _save(doc, tmp_path / "t.pdf")
    tokens = child.handle(request("pages", pdf, first=1, last=1, token_sample=4))["result"][
        "pages"
    ][0]["tokens"]
    assert tokens == ["A1", "B2", "C3", "D4"]  # the repeat and the 40-character word are skipped


def test_two_inspections_of_one_file_are_the_same_bytes(tmp_path: Path) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf", pages=3)
    config = app.Config(stage_root=tmp_path)
    first = json.dumps(inspect_file(pdf, config), sort_keys=True)
    second = json.dumps(inspect_file(pdf, config), sort_keys=True)
    assert first == second


# ------------------------------------------------------------------------------- the contract
def test_the_file_name_must_be_two_uuids_and_a_pdf_extension() -> None:
    good = f"{WS_ID}/{DOC_ID}.pdf"
    assert contract.parse_file({"file": good}) == good
    for bad in (
        "../etc/passwd",
        f"{WS_ID}/../{DOC_ID}.pdf",
        f"/{WS_ID}/{DOC_ID}.pdf",
        f"{WS_ID}/{DOC_ID}.PDF",
        f"{WS_ID}/{DOC_ID}.pdf.part",
        f"{WS_ID}/{DOC_ID}.pdf\n",
        f"{WS_ID.upper()}/{DOC_ID}.pdf",
        "plain.pdf",
        "",
    ):
        with pytest.raises(contract.ContractError) as raised:
            contract.parse_file({"file": bad})
        assert (raised.value.status, raised.value.code) == (400, "bad_path"), bad
    for body in (None, [], "x", {"file": 7}, {}):
        with pytest.raises(contract.ContractError) as raised:
            contract.parse_file(body)
        assert raised.value.status == 400


def test_the_page_range_is_bounded_to_a_thousand_pages() -> None:
    ok = contract.parse_pages({"file": f"{WS_ID}/{DOC_ID}.pdf", "first_page": 1, "last_page": 1000})
    assert (ok.first_page, ok.last_page) == (1, 1000)
    for first, last in ((0, 1), (2, 1), (1, 1001), ("1", 2), (True, 2), (1.5, 2), (None, 2)):
        with pytest.raises(contract.ContractError) as raised:
            contract.parse_pages(
                {"file": f"{WS_ID}/{DOC_ID}.pdf", "first_page": first, "last_page": last}
            )
        assert (raised.value.status, raised.value.code) == (422, "bad_range")


# --------------------------------------------------------------------------- the path rules
def _staged(root: Path) -> Path:
    directory = root / WS_ID
    directory.mkdir()
    return vector_pdf(directory / f"{DOC_ID}.pdf")


def test_a_staged_file_resolves_inside_the_root(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    assert app.resolve(tmp_path, f"{WS_ID}/{DOC_ID}.pdf") == staged.resolve()


def test_a_missing_file_is_a_404(tmp_path: Path) -> None:
    with pytest.raises(contract.ContractError) as raised:
        app.resolve(tmp_path, f"{WS_ID}/{DOC_ID}.pdf")
    assert (raised.value.status, raised.value.code) == (404, "not_found")


def test_a_symbolic_link_is_refused_even_when_it_points_inside_the_root(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    link = tmp_path / WS_ID / "33333333-3333-7333-8333-333333333333.pdf"
    link.symlink_to(staged)
    with pytest.raises(contract.ContractError) as raised:
        app.resolve(tmp_path, f"{WS_ID}/{link.name}")
    assert (raised.value.status, raised.value.code) == (400, "bad_path")


def test_a_linked_directory_that_leaves_the_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    vector_pdf(outside / f"{DOC_ID}.pdf")
    root = tmp_path / "root"
    root.mkdir()
    (root / WS_ID).symlink_to(outside, target_is_directory=True)
    with pytest.raises(contract.ContractError) as raised:
        app.resolve(root, f"{WS_ID}/{DOC_ID}.pdf")
    assert raised.value.status == 400


def test_a_directory_named_like_a_file_is_not_a_file(tmp_path: Path) -> None:
    (tmp_path / WS_ID / f"{DOC_ID}.pdf").mkdir(parents=True)
    with pytest.raises(contract.ContractError) as raised:
        app.resolve(tmp_path, f"{WS_ID}/{DOC_ID}.pdf")
    assert raised.value.status == 404


# ----------------------------------------------------------------------------- the sandbox
def _stub(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_the_limits_are_applied_to_the_process_that_applies_them() -> None:
    code = (
        "import resource; from services.ele_parse import sandbox; "
        "sandbox.apply_limits(sandbox.Limits(300, 7, 33)); "
        "print(resource.getrlimit(resource.RLIMIT_AS), resource.getrlimit(resource.RLIMIT_CPU), "
        "resource.getrlimit(resource.RLIMIT_FSIZE), resource.getrlimit(resource.RLIMIT_NOFILE))"
    )
    out = subprocess.run(
        _stub(code),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT)},
        check=True,
    ).stdout.strip()
    mib = 1024 * 1024
    assert out == f"({300 * mib}, {300 * mib}) (7, 7) (0, 0) (33, 33)"


def test_the_child_sets_its_limits_before_it_touches_a_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def handle(_request: dict[str, Any]) -> dict[str, Any]:
        order.append("handle")
        return {"ok": True, "result": {}}

    class _Stdin:
        buffer = io.BytesIO(b"{}")

    monkeypatch.setattr(sandbox, "apply_limits", lambda limits: order.append(f"limits:{limits}"))
    monkeypatch.setattr(child, "handle", handle)
    monkeypatch.setattr(sys, "stdin", _Stdin())
    assert child.main(["--memory-mb", "256", "--cpu-seconds", "9"]) == 0
    assert order == [f"limits:{sandbox.Limits(256, 9, 64)}", "handle"]


def test_the_real_child_command_line_carries_the_limits() -> None:
    argv = sandbox.child_argv(sandbox.Limits(512, 20, 64))
    assert argv[1:3] == ["-m", "services.ele_parse.child"]
    assert argv[3:] == ["--memory-mb", "512", "--cpu-seconds", "20", "--nofile", "64"]


def test_a_request_through_the_real_child_returns_its_result(tmp_path: Path) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf")
    result = sandbox.run_child(
        request("summary", pdf), timeout_s=30, limits=sandbox.Limits(1024, 30)
    )
    assert result["page_count"] == 2


def test_a_rejection_from_the_real_child_is_a_422_with_its_code(tmp_path: Path) -> None:
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child(
            request("summary", corrupt_pdf(tmp_path / "c.pdf")),
            timeout_s=30,
            limits=sandbox.Limits(1024, 30),
        )
    assert (raised.value.status, raised.value.code) == (422, "corrupt")


def test_a_child_that_runs_too_long_is_killed_and_the_answer_is_504() -> None:
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child(
            {},
            timeout_s=0.5,
            limits=sandbox.Limits(256, 30),
            argv=_stub("import time; time.sleep(60)"),
        )
    assert (raised.value.status, raised.value.code) == (504, "timeout")


def test_a_child_that_runs_out_of_memory_is_a_507() -> None:
    code = (
        "import sys; from services.ele_parse import sandbox; "
        "sandbox.apply_limits(sandbox.Limits(200, 30))\n"
        "try:\n    bytearray(800 * 1024 * 1024)\nexcept MemoryError:\n    sys.exit(3)\n"
    )
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child({}, timeout_s=30, limits=sandbox.Limits(200, 30), argv=_stub(code))
    assert (raised.value.status, raised.value.code) == (507, "memory_limit")


def test_a_child_killed_by_a_signal_is_a_507_and_one_over_its_cpu_limit_a_504() -> None:
    with pytest.raises(contract.ContractError) as killed:
        sandbox.run_child(
            {},
            timeout_s=30,
            limits=sandbox.Limits(256, 30),
            argv=_stub("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),
        )
    assert killed.value.status == 507
    with pytest.raises(contract.ContractError) as cpu:
        sandbox.run_child(
            {},
            timeout_s=30,
            limits=sandbox.Limits(256, 30),
            argv=_stub("import os, signal; os.kill(os.getpid(), signal.SIGXCPU)"),
        )
    assert cpu.value.status == 504


def test_a_child_that_fails_for_another_reason_is_a_500_parse_error() -> None:
    for program in (
        "raise SystemExit(4)",
        "print('not json')",
        'print(\'{"ok": false, "code": "x"}\')',
    ):
        with pytest.raises(contract.ContractError) as raised:
            sandbox.run_child({}, timeout_s=30, limits=sandbox.Limits(256, 30), argv=_stub(program))
        assert (raised.value.status, raised.value.code) == (500, "parse_error"), program


def test_a_tiny_memory_limit_stops_the_real_child(tmp_path: Path) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf")
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child(request("summary", pdf), timeout_s=30, limits=sandbox.Limits(16, 30))
    assert raised.value.status in (500, 507)


def test_the_cpu_limit_follows_the_timeout() -> None:
    assert sandbox.cpu_seconds_for(0.2) == 1 and sandbox.cpu_seconds_for(10) == 10
    assert sandbox.cpu_seconds_for(10.2) == 11
    assert resource.RLIMIT_AS is not None


# ----------------------------------------------------------------------- the HTTP server
@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[app.EleParseServer, http.client.HTTPConnection]]:
    config = app.Config(stage_root=tmp_path, port=0, max_children=1, memory_mb=1024)
    srv = app.make_server(config, host="127.0.0.1")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=60)
    try:
        yield srv, conn
    finally:
        conn.close()
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _post(conn: http.client.HTTPConnection, path: str, body: Any) -> tuple[int, Any, Any]:
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()
    conn.request("POST", path, payload, {"Content-Type": "application/json"})
    response = conn.getresponse()
    return response.status, json.loads(response.read() or b"null"), response


def test_health_reports_the_versions_and_the_schema(server: Any) -> None:
    _, conn = server
    conn.request("GET", "/health")
    response = conn.getresponse()
    body = json.loads(response.read())
    assert response.status == 200 and body["status"] == "ok" and body["schema"] == 1
    assert body["pymupdf_version"] == pymupdf.__version__ or body["pymupdf_version"]
    assert body["service_version"]


def test_the_server_answers_summary_and_pages_for_a_staged_file(
    server: Any, tmp_path: Path
) -> None:
    _staged(tmp_path)
    _, conn = server
    name = f"{WS_ID}/{DOC_ID}.pdf"

    status, summary, _ = _post(conn, "/v1/documents/summary", {"file": name})
    assert status == 200 and summary["page_count"] == 2
    status, pages, _ = _post(
        conn, "/v1/documents/pages", {"file": name, "first_page": 1, "last_page": 2}
    )
    assert status == 200 and [p["page_no"] for p in pages["pages"]] == [1, 2]
    # what the server says is what the replayed double understands (the contract, both sides)
    replay = FakePdfInspection.from_json(
        json.dumps({"schema": 1, "summary": summary, "pages": pages["pages"]})
    )
    assert replay is not None


def test_the_server_refuses_what_the_contract_refuses(server: Any, tmp_path: Path) -> None:
    _, conn = server
    cases: list[tuple[str, Any, int, str]] = [
        ("/v1/documents/summary", {"file": "../../etc/passwd"}, 400, "bad_path"),
        ("/v1/documents/summary", {"file": f"{WS_ID}/{DOC_ID}.pdf"}, 404, "not_found"),
        ("/v1/documents/summary", b"not json", 400, "bad_request"),
        (
            "/v1/documents/pages",
            {"file": f"{WS_ID}/{DOC_ID}.pdf", "first_page": 2, "last_page": 1},
            422,
            "bad_range",
        ),
        ("/v1/nothing", {}, 404, "not_found"),
    ]
    for path, body, status, code in cases:
        got, answer, _ = _post(conn, path, body)
        assert (got, answer["code"]) == (status, code), (path, body)


def test_a_damaged_staged_file_is_a_422_with_its_code(server: Any, tmp_path: Path) -> None:
    (tmp_path / WS_ID).mkdir()
    corrupt_pdf(tmp_path / WS_ID / f"{DOC_ID}.pdf")
    _, conn = server
    status, answer, _ = _post(conn, "/v1/documents/summary", {"file": f"{WS_ID}/{DOC_ID}.pdf"})
    assert (status, answer["code"]) == (422, "corrupt")


def test_a_body_over_four_kib_is_refused_unread(server: Any) -> None:
    _, conn = server
    status, answer, _ = _post(conn, "/v1/documents/summary", b"{" + b" " * 5000 + b"}")
    assert (status, answer["code"]) == (400, "bad_request")


def test_when_every_slot_is_taken_the_next_request_is_a_503_with_retry_after(
    server: Any, tmp_path: Path
) -> None:
    _staged(tmp_path)
    srv, conn = server
    assert srv.children.acquire(blocking=False)  # max_children is 1: the one slot is busy
    try:
        status, answer, response = _post(
            conn, "/v1/documents/summary", {"file": f"{WS_ID}/{DOC_ID}.pdf"}
        )
    finally:
        srv.children.release()
    assert (status, answer["code"]) == (503, "busy")
    assert response.getheader("Retry-After") == "5"
    # the slot comes back: the same request now works
    status, _, _ = _post(conn, "/v1/documents/summary", {"file": f"{WS_ID}/{DOC_ID}.pdf"})
    assert status == 200


def test_the_timeout_for_a_range_is_per_page_and_capped() -> None:
    config = app.Config(stage_root=Path("/x"), page_timeout_s=10, range_timeout_s=300)
    assert config.timeout_for(3) == 30 and config.timeout_for(1000) == 300


def test_the_config_reads_the_five_variables_and_nothing_else() -> None:
    config = app.Config.from_env(
        {
            "ELE_PARSE_STAGE_ROOT": "/stage",
            "ELE_PARSE_PORT": "9000",
            "ELE_PARSE_PAGE_TIMEOUT_S": "7",
            "ELE_PARSE_RANGE_TIMEOUT_S": "70",
            "ELE_PARSE_MEMORY_MB": "512",
            "ELE_PARSE_MAX_CHILDREN": "3",
            "ELE_PARSE_TOKEN_SAMPLE": "99",
            "DATABASE_URL": "never read",
        }
    )
    assert (config.stage_root, config.port, config.page_timeout_s) == (Path("/stage"), 9000, 7.0)
    assert (config.range_timeout_s, config.memory_mb, config.max_children) == (70.0, 512, 3)
    assert config.token_sample == 99
    assert app.Config.from_env({}).max_children == 2  # the shipped defaults


# ------------------------------------------------------------------ curves in _paths (F0-AC-1/2)
def test_f0_ac_1_a_real_bezier_page_is_read_and_only_the_small_curve_is_counted(
    tmp_path: Path,
) -> None:
    pdf = curve_pdf(tmp_path / "c.pdf")

    answer = child.handle(request("pages", pdf, first=1, last=1))
    assert answer["ok"], answer
    page = answer["result"]["pages"][0]
    assert page["vector_path_count"] == 2
    assert page["short_curve_path_count"] == 1  # the 6 pt curve, not the 200 pt one

    report = inspect_file(pdf, app.Config(stage_root=tmp_path))  # a real child per range
    assert "failed_pages" not in report
    pages = json.loads(json.dumps(report["pages"]))
    assert pages[0]["short_curve_path_count"] == 1


def test_f0_ac_2_lines_rects_and_quads_do_not_break_the_page_or_count_as_curves(
    tmp_path: Path,
) -> None:
    pdf = curve_pdf(tmp_path / "m.pdf", extra_shapes=True)

    answer = child.handle(request("pages", pdf, first=1, last=1))
    assert answer["ok"], answer
    page = answer["result"]["pages"][0]
    assert page["vector_path_count"] >= 4
    assert page["short_curve_path_count"] == 1  # only the bezier


# ----------------------------------------------------------------------------------- the CLI
def test_the_cli_prints_the_inspection_json_and_the_double_replays_it(tmp_path: Path) -> None:
    pdf = vector_pdf(tmp_path / "v.pdf", pages=2)
    out = subprocess.run(
        [sys.executable, "-m", "services.ele_parse", "inspect", str(pdf)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        check=True,
    ).stdout
    report = json.loads(out)
    assert report["schema"] == 1 and report["summary"]["page_count"] == 2
    assert [p["page_no"] for p in report["pages"]] == [1, 2]
    assert report == inspect_file(pdf, app.Config(stage_root=tmp_path))  # one code path
    replay = FakePdfInspection.from_json(out)
    assert replay is not None  # the pinned-file format the unit tests replay


def test_the_cli_exits_2_on_a_pdf_it_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(corrupt_pdf(tmp_path / "c.pdf"))]) == 2
    assert "corrupt" in capsys.readouterr().err


def test_the_cli_writes_the_report_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(vector_pdf(tmp_path / "v.pdf", pages=1))]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["page_count"] == 1
