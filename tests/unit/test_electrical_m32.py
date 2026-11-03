"""BT39 / INC1-AC-13 (4): M-32 on synthetic inspection files and pinned labels.

The expected numbers are COUNTED BY HAND from ``tests/unit/fixtures/electrical/README.md``, not
read back from the code: five documents, the proposal right on both fields for three of them, on
the convention for four and on the symbol standard for four.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.ops.electrical_characterize import analyse_inspection, load_inspections
from tests.eval.electrical.m32_profile_detection import (
    LabelError,
    build_report,
    compute_m32,
    load_labels,
    main,
    measure,
)

FIXTURES = Path(__file__).parent / "fixtures" / "electrical"
RULES = PackagedRuleSource()


def test_m32_is_the_hand_counted_share_of_documents_right_on_both_fields() -> None:
    metric = measure(
        load_inspections(FIXTURES / "inspections"),
        load_labels(FIXTURES / "labels.json"),
        rules=RULES,
    )
    assert metric.metric_id == "M-32"
    assert metric.n == 5
    assert metric.value == pytest.approx(3 / 5)
    assert metric.target_r1 == 0.90
    assert metric.passed is False  # the labels are wrong on purpose: the metric can fail


def test_each_field_is_measured_alone_and_by_vendor_and_language() -> None:
    metric = measure(
        load_inspections(FIXTURES / "inspections"),
        load_labels(FIXTURES / "labels.json"),
        rules=RULES,
    )
    by_field = metric.breakdown["by_field"]
    assert by_field["both"] == pytest.approx(3 / 5)
    assert by_field["identifier_convention"] == pytest.approx(4 / 5)
    assert by_field["symbol_standard"] == pytest.approx(4 / 5)
    # the vendor is the PDF producer's family; no signature matches "WPS" or no producer: unknown
    assert metric.breakdown["by_vendor"] == {
        "autocad": 0.0,
        "eplan": 1.0,
        "unknown": pytest.approx(2 / 3),
    }
    assert metric.breakdown["by_language"] == {
        "bilingual": pytest.approx(1 / 2),
        "en": pytest.approx(2 / 3),
    }
    assert metric.breakdown["coverage"] == {"labelled": 5.0, "unlabelled": 0.0}


def test_an_unlabelled_inspection_is_left_out_and_counted() -> None:
    labels = load_labels(FIXTURES / "labels.json")
    kept = dict(list(labels.items())[:2])
    metric = measure(load_inspections(FIXTURES / "inspections"), kept, rules=RULES)
    assert metric.n == 2
    assert metric.breakdown["coverage"] == {"labelled": 2.0, "unlabelled": 3.0}


def test_a_label_without_an_inspection_is_an_error_not_a_smaller_sample() -> None:
    labels = dict(load_labels(FIXTURES / "labels.json"))
    labels["f" * 64] = {"symbol_standard": "iec_60617", "identifier_convention": "iec_81346"}
    with pytest.raises(LabelError, match="match no inspection"):
        measure(load_inspections(FIXTURES / "inspections"), labels, rules=RULES)


def test_nothing_to_measure_is_an_error() -> None:
    with pytest.raises(LabelError):
        compute_m32({}, {})


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"a" * 64: {"symbol_standard": "iec_60617"}},
        {"a" * 64: {"symbol_standard": "iec_60617", "identifier_convention": "nope"}},
        {"a" * 64: {"symbol_standard": "nope", "identifier_convention": "iec_81346"}},
        {"a" * 64: {"symbol_standard": "iec_60617", "identifier_convention": "iec_81346", "x": 1}},
    ],
)
def test_a_malformed_labels_file_is_refused(tmp_path: Path, body: object) -> None:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(LabelError):
        load_labels(path)


def test_the_measure_runs_the_workers_detection_not_a_copy() -> None:
    """The proposal M-32 judges is the one ``analyse_inspection`` returns -- the function the
    characterization tool (and so the worker's pure steps) shares."""
    inspections = load_inspections(FIXTURES / "inspections")
    analyses = {i.sha256: analyse_inspection(i, rules=RULES) for i in inspections}
    labels = load_labels(FIXTURES / "labels.json")
    assert compute_m32(analyses, labels).value == pytest.approx(3 / 5)


def test_the_report_carries_versions_commit_and_a_stable_fingerprint() -> None:
    inspections = load_inspections(FIXTURES / "inspections")
    labels = load_labels(FIXTURES / "labels.json")
    first = build_report(
        inspections, labels, commit="a" * 40, created_at="2026-11-02T00:00:00Z", run_id="r1"
    )
    second = build_report(
        inspections, labels, commit="b" * 40, created_at="2026-11-02T01:00:00Z", run_id="r2"
    )
    assert set(first.versions) == {"pipeline", "shared", "rules"}
    assert first.metrics[0].dataset_fingerprint == second.metrics[0].dataset_fingerprint
    changed = {k: dict(v) for k, v in labels.items()}
    changed[next(iter(changed))]["symbol_standard"] = "nfpa_jic"
    third = build_report(
        inspections, changed, commit="a" * 40, created_at="2026-11-02T00:00:00Z", run_id="r3"
    )
    assert third.metrics[0].dataset_fingerprint != first.metrics[0].dataset_fingerprint


def test_the_cli_writes_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "m32.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "m32",
            "--inspections",
            str(FIXTURES / "inspections"),
            "--labels",
            str(FIXTURES / "labels.json"),
            "--out",
            str(out),
        ],
    )
    main()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["metrics"][0]["metric_id"] == "M-32"
    assert written["metrics"][0]["n"] == 5
