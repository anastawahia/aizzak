"""BT12 / INC1-AC-13 (framework share): report shape, comparison of two runs, M-04."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app.modules.electrical.shared.parser import IdentifierParser
from tests.eval.electrical.framework import (
    EvalReport,
    MetricResult,
    compare,
    fingerprint,
    sha256_text,
    to_json,
    write_report,
)
from tests.eval.electrical.m04_identifiers import (
    TARGET_R1,
    build_report,
    compute_m04,
    load_cases,
    load_rules,
)


def _metric(value: float, **cells: float) -> MetricResult:
    return MetricResult("M-04", value, 0.995, value >= 0.995, 10, {"by_kind": cells}, "f" * 64)


def _report(*metrics: MetricResult, run_id: str = "r1") -> EvalReport:
    return EvalReport(run_id, "a" * 40, {"shared": "1.0.0"}, tuple(metrics), "2026-11-02T00:00:00Z")


def test_the_report_is_written_as_sorted_json_with_commit_versions_and_fingerprint(
    tmp_path: Path,
) -> None:
    out = tmp_path / "deep" / "report.json"
    report = _report(_metric(1.0, terminal=1.0))
    write_report(report, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["commit"] == "a" * 40
    assert loaded["versions"] == {"shared": "1.0.0"}
    assert loaded["metrics"][0]["dataset_fingerprint"] == "f" * 64
    assert out.read_text(encoding="utf-8") == to_json(report)
    assert list(loaded) == sorted(loaded)  # keys sorted: the same report is the same bytes


def test_two_runs_are_compared_by_delta() -> None:
    before = _report(_metric(0.90, terminal=0.8, plc=1.0))
    after = _report(_metric(0.95, terminal=0.9, plc=1.0), run_id="r2")
    deltas = compare(before, after)
    assert deltas["M-04"] == 0.95 - 0.90
    assert deltas["M-04/by_kind/terminal"] == 0.9 - 0.8
    assert deltas["M-04/by_kind/plc"] == 0.0


def test_a_metric_only_in_one_run_is_not_compared() -> None:
    other = replace(_metric(0.5), metric_id="M-32")
    assert compare(_report(_metric(1.0)), _report(other)) == {}


def test_the_fingerprint_ignores_input_order_and_changes_with_content() -> None:
    a, b = sha256_text("a"), sha256_text("b")
    assert fingerprint([a, b]) == fingerprint([b, a])
    assert fingerprint([a, b]) != fingerprint([a])


def test_m04_is_the_exact_match_rate_with_a_breakdown() -> None:
    parser = IdentifierParser(load_rules())
    cases = load_cases()
    assert compute_m04(parser, cases).value == 1.0
    # corrupt one expectation: exactly one case in n fails
    broken = [dict(c) for c in cases]
    broken[0]["canonical"] = "WRONG"
    metric = compute_m04(parser, broken)
    assert metric.n == len(cases)
    assert metric.value == (len(cases) - 1) / len(cases)
    assert metric.breakdown["by_kind"]["reference_designation"] < 1.0
    assert metric.target_r1 == TARGET_R1
    assert metric.passed is (metric.value >= TARGET_R1)


def test_m04_breakdowns_cover_standard_script_and_source() -> None:
    metric = compute_m04(IdentifierParser(load_rules()), load_cases())
    assert {"by_kind", "by_convention", "by_script", "by_source"} <= set(metric.breakdown)
    assert "fullwidth" in metric.breakdown["by_script"]
    assert all(v == 1.0 for cells in metric.breakdown.values() for v in cells.values())


def test_the_dataset_fingerprint_changes_when_a_case_changes() -> None:
    parser = IdentifierParser(load_rules())
    cases = load_cases()
    changed = [dict(c) for c in cases]
    changed[1]["canonical"] = "OTHER"
    assert (
        compute_m04(parser, cases).dataset_fingerprint
        != compute_m04(parser, changed).dataset_fingerprint
    )


def test_build_report_records_the_versions_of_what_was_measured() -> None:
    report = build_report(commit="c" * 40, created_at="2026-11-02T00:00:00Z", run_id="x")
    assert dict(report.versions) == {"shared": "1.2.0", "rules": "1.0.0"}
    assert report.metrics[0].metric_id == "M-04" and report.metrics[0].value == 1.0
