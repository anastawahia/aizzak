"""BT28 / INC1-AC-13 (2): ``app.ops.electrical_characterize`` on pinned inspection files.

The claim worth testing is that the tool is not a second pipeline: for the same inspection the
record it computes IS the record the worker (``InspectDocument``) stores, down to the JSON bytes.
That is checked by running the worker itself over the in-memory stack with the same inspection
replayed as ele-parse, for every pinned file.
"""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.modules.electrical.drawings.domain.characterization import aggregate
from app.modules.electrical.drawings.domain.pages import StageReason
from app.ops.electrical_characterize import (
    InspectionFileError,
    build_report,
    characterize_inspection,
    load_inspection,
    load_inspections,
    main,
    render_markdown,
    to_json,
    to_plain,
)
from tests.unit.support_electrical import (
    FakePdfInspection,
    build_stack,
    seed_file,
    seed_project,
)

FIXTURES = Path(__file__).parent / "fixtures" / "electrical" / "inspections"
RULES = PackagedRuleSource()
PINNED = sorted(FIXTURES.glob("*.json"))


@pytest.mark.parametrize("path", PINNED, ids=lambda p: p.stem[:10])
async def test_the_tool_computes_the_record_the_worker_stores(path: Path, tmp_path: Path) -> None:
    body = b"%PDF-1.7 " + path.stem.encode()
    sha = hashlib.sha256(body).hexdigest()
    renamed = tmp_path / f"{sha}.json"
    renamed.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    stack = build_stack()
    project = await seed_project(stack)  # the DEFAULT profile, which is the tool's base
    await seed_file(stack, "f1", body=body)
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    pdf = FakePdfInspection.from_json(renamed.read_text(encoding="utf-8"))
    await stack.inspector(pdf).run(stack.ctx, job_id=registered.job.id, event_id="e1")

    stored = await stack.characterizations.get(stack.ctx, registered.document.id)
    assert stored is not None
    ours = characterize_inspection(load_inspection(renamed), rules=RULES)

    assert ours.document_id == sha  # the one field that differs by design: the worker's is a UUID
    assert json.dumps(to_plain(ours), sort_keys=True) == json.dumps(
        to_plain(replace(stored, document_id=sha)), sort_keys=True
    )


def _without_page_two(tmp_path: Path, source: Path, sha: str) -> tuple[Path, str]:
    """A pinned three-page inspection with page 2 left out, as ``inspect`` leaves out a page it
    could not read; returns the file in ``tmp_path`` and the text of the original."""
    original = source.read_text(encoding="utf-8")
    data = json.loads(original)
    assert [p["page_no"] for p in data["pages"]] == [1, 2, 3]
    data["pages"] = [p for p in data["pages"] if p["page_no"] != 2]
    target = tmp_path / f"{sha}.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    return target, original


@pytest.mark.parametrize("how", ["parse_error", "left_out"])
async def test_f0_ac_4_a_failed_or_left_out_page_is_counted_alike_by_the_tool_and_the_worker(
    how: str, tmp_path: Path
) -> None:
    three_pages = [p for p in PINNED if json.loads(p.read_text())["summary"]["page_count"] == 3]
    body = b"%PDF-1.7 f0 " + how.encode()
    sha = hashlib.sha256(body).hexdigest()
    tool_file, original = _without_page_two(tmp_path, three_pages[0], sha)
    assert len(PINNED) == 5  # the pinned set is untouched
    stack = build_stack()
    project = await seed_project(stack)
    await seed_file(stack, "f1", body=body)
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    failures = {2: StageReason.PARSE_ERROR} if how == "parse_error" else None
    pdf = FakePdfInspection.from_json(original, failures=failures)
    if how == "left_out":
        pdf = FakePdfInspection.from_json(tool_file.read_text(encoding="utf-8"))
    await stack.inspector(pdf).run(stack.ctx, job_id=registered.job.id, event_id="e1")

    stored = await stack.characterizations.get(stack.ctx, registered.document.id)
    assert stored is not None
    ours = characterize_inspection(load_inspection(tool_file), rules=RULES)

    assert json.dumps(to_plain(ours), sort_keys=True) == json.dumps(
        to_plain(replace(stored, document_id=sha)), sort_keys=True
    )
    assert ours.failed_pages == stored.failed_pages == 1
    assert ours.page_count == 3


def test_f0_ac_3_the_json_and_the_markdown_show_the_failed_pages() -> None:
    report = build_report(load_inspections(FIXTURES), rules=RULES)
    assert report["report"]["failed_pages"] == 0
    assert all(d["failed_pages"] == 0 for d in report["documents"])
    assert all("failed_pages" in d for d in report["documents"])
    text = render_markdown(report)
    pages = report["report"]["pages"]
    assert f"- failed pages: 0 of {pages}\n- OCR needed ratio" in text
    assert "| sha256 | pages | failed |" in text


def _row_cells(text: str, label: str) -> list[str]:
    """The cells of the Documents table row whose first cell is ``label``."""
    rows = [ln for ln in text.splitlines() if ln.startswith(f"| {label} |")]
    assert len(rows) == 1
    return [c.strip() for c in rows[0].strip("|").split("|")]


def test_f0_ac_3_a_dropped_page_reaches_the_json_and_the_markdown(tmp_path: Path) -> None:
    three_pages = [p for p in PINNED if json.loads(p.read_text())["summary"]["page_count"] == 3]
    _without_page_two(tmp_path, three_pages[0], "d" * 64)
    report = build_report(load_inspections(tmp_path), rules=RULES)
    assert report["report"]["failed_pages"] == 1 and report["report"]["pages"] == 3
    assert report["documents"][0]["failed_pages"] == 1
    text = render_markdown(report)
    assert "- failed pages: 1 of 3" in text
    cells = _row_cells(text, "d" * 16)
    assert cells[:3] == ["d" * 16, "3", "1"]  # sha, pages, failed


def test_f0_ac_6_the_markdown_claims_no_symbol_standard_without_evidence(
    tmp_path: Path,
) -> None:
    _inspection(tmp_path, "e" * 64 + ".json", page_count=0)
    report = build_report(load_inspections(tmp_path), rules=RULES)
    assert report["documents"][0]["symbol_standard_guess"] is None
    assert report["report"]["symbol_standards"] == {"unknown": 1}
    text = render_markdown(report)
    assert "iec_60617" not in text
    assert "symbol standards: unknown 1" in text
    cells = _row_cells(text, "e" * 16)
    assert cells[1] == "0" and cells[2] == "0"
    assert cells[-1] == "unknown"  # never the string "None"


def test_the_project_report_is_the_aggregate_of_the_documents_it_lists() -> None:
    items = load_inspections(FIXTURES)
    report = build_report(items, rules=RULES)
    records = [characterize_inspection(i, rules=RULES) for i in items]
    assert report["report"] == to_plain(aggregate(records))
    assert report["report"]["documents"] == len(items) == len(report["documents"]) == 5
    assert report["report"]["pages"] == sum(d["page_count"] for d in report["documents"])
    assert report["inspections"] == 5


def test_every_fr_eval_14_field_is_in_each_document_record() -> None:
    report = build_report(load_inspections(FIXTURES), rules=RULES)
    wanted = {
        "producer_family", "creator", "producer", "text_kinds", "page_languages",
        "document_language", "conventions", "symbol_standard_guess", "symbol_standard_basis",
        "plc_families", "ocr_needed_pages", "pipeline_version", "shared_version", "rules_version",
    }  # fmt: skip
    for document in report["documents"]:
        assert wanted <= set(document)
    eplan = next(d for d in report["documents"] if d["producer_family"] == "eplan")
    assert eplan["conventions"] == {"iec_81346": 120}
    assert eplan["symbol_standard_guess"] == "iec_60617"


def test_the_same_inputs_are_the_same_bytes_and_names_only_on_request() -> None:
    items = load_inspections(FIXTURES)
    first = to_json(build_report(items, rules=RULES))
    assert first == to_json(build_report(items, rules=RULES))
    assert '"file"' not in first  # SHA-256 only: no names unless asked
    named = build_report(items, rules=RULES, with_names=True)
    assert {d["file"] for d in named["documents"]} == {p.name for p in PINNED}


def _inspection(tmp_path: Path, name: str, **summary: Any) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "summary": {
                    "page_count": 0,
                    "pdf_version": "1.7",
                    "encrypted": False,
                    "creator": None,
                    "producer": None,
                    "outline_count": 0,
                    **summary,
                },
                "pages": [],
            }
        ),
        encoding="utf-8",
    )
    return path


_RLO, _ZWSP, _NEL = chr(0x202E), chr(0x200B), chr(0x85)
HOSTILE = f"EPLAN{_RLO}evil\r\n[forged log line]{_ZWSP}{_NEL} \\ end"


def test_what_a_pdf_says_about_itself_is_written_escaped(tmp_path: Path) -> None:
    """R1-4: line breaks, bidi controls and zero-width characters out of a PDF never reach the
    report, the Markdown or the terminal as themselves."""
    sha = "a" * 64
    _inspection(tmp_path, f"{sha}.json", creator=HOSTILE, producer=HOSTILE)
    out, md = tmp_path / "out" / "r.json", tmp_path / "out" / "r.md"

    assert main(["--inspections", str(tmp_path), "--out", str(out), "--markdown", str(md)]) == 0

    written = out.read_text(encoding="utf-8")
    for text in (written, md.read_text(encoding="utf-8")):
        for raw in (_RLO, _ZWSP, _NEL, "\r"):
            assert raw not in text
    document = json.loads(written)["documents"][0]
    assert document["creator"] == document["producer"]
    assert document["creator"] == (
        "EPLAN\\u202eevil\\x0d\\x0a[forged log line]\\u200b\\x85 \\\\ end"
    )
    assert len(written.splitlines()) == len(json.dumps(json.loads(written), indent=2).splitlines())


def test_the_cli_writes_the_json_and_the_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, md = tmp_path / "r.json", tmp_path / "r.md"
    code = main(["--inspections", str(FIXTURES), "--out", str(out), "--markdown", str(md)])
    assert code == 0
    assert "characterized 5 inspection file(s)" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == to_json(
        build_report(load_inspections(FIXTURES), rules=RULES)
    )
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# WireTwin characterization")
    assert "| sha256 | pages | failed |" in text


def test_the_tool_needs_no_network_and_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no(*args: object, **kwargs: object) -> None:
        raise AssertionError("the tool opened a socket")

    monkeypatch.setattr(socket.socket, "connect", _no)
    monkeypatch.setattr(socket, "create_connection", _no)
    assert build_report(load_inspections(FIXTURES), rules=RULES)["inspections"] == 5


@pytest.mark.parametrize(
    "name",
    ["plant.json", ("A" * 64) + ".json", ("a" * 63) + ".json"],
)
def test_a_file_that_cannot_say_which_pdf_it_describes_is_refused(
    tmp_path: Path, name: str
) -> None:
    path = _inspection(tmp_path, name)
    with pytest.raises(InspectionFileError, match="sha256"):
        load_inspection(path)


def test_a_sha256_key_inside_the_file_names_it(tmp_path: Path) -> None:
    path = _inspection(tmp_path, "plant.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sha256"] = "b" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_inspection(path).sha256 == "b" * 64


def test_a_wrong_schema_a_broken_file_and_a_repeated_pdf_are_refused(tmp_path: Path) -> None:
    (tmp_path / ("c" * 64 + ".json")).write_text('{"schema": 2}', encoding="utf-8")
    with pytest.raises(InspectionFileError, match="not an ele-parse inspection"):
        load_inspection(tmp_path / ("c" * 64 + ".json"))
    (tmp_path / ("d" * 64 + ".json")).write_text("not json", encoding="utf-8")
    with pytest.raises(InspectionFileError, match="unreadable"):
        load_inspection(tmp_path / ("d" * 64 + ".json"))
    twin = tmp_path / "twin"
    twin.mkdir()
    first = _inspection(twin, "first.json")
    data = json.loads(first.read_text(encoding="utf-8"))
    data["sha256"] = "e" * 64
    for name in ("first.json", "second.json"):
        (twin / name).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InspectionFileError, match="two inspection files"):
        load_inspections(twin)


def test_the_cli_exits_2_on_bad_input_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "r.json"
    assert main(["--inspections", str(tmp_path / "missing"), "--out", str(out)]) == 2
    _inspection(tmp_path, "plant.json")
    assert main(["--inspections", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists()
    assert "sha256" in capsys.readouterr().err
