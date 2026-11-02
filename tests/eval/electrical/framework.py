"""FR-EVAL-01: the evaluation framework's report shape, writer and comparison.

**This is a tool, not a test.** The file has no ``test_`` prefix, so pytest collects
nothing from it; its pure functions are unit-tested in
``tests/unit/test_electrical_eval_framework.py``.

A run produces an ``EvalReport``: the commit (FR-EVAL-08), the versions of what was
measured, and one ``MetricResult`` per metric with a breakdown (FR-EVAL-13) and a
fingerprint of the inputs, so two runs can be compared only when they measured the same
data.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetricResult:
    metric_id: str  # "M-04", "M-32"
    value: float
    target_r1: float
    passed: bool
    n: int
    breakdown: Mapping[str, Mapping[str, float]]  # FR-EVAL-13: by standard, language, kind, ...
    dataset_fingerprint: str  # sha256 over the inputs' own hashes, sorted


@dataclass(frozen=True)
class EvalReport:
    run_id: str
    commit: str  # `git rev-parse HEAD`, FR-EVAL-08
    versions: Mapping[str, str]  # pipeline, shared, rules, pymupdf
    metrics: tuple[MetricResult, ...]
    created_at: str


def fingerprint(input_hashes: Iterable[str]) -> str:
    """sha256 over the inputs' own hashes, sorted, so input order never matters."""
    digest = hashlib.sha256()
    for item in sorted(input_hashes):
        digest.update(item.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_commit(repo: Path | None = None) -> str:
    """``git rev-parse HEAD`` of the repository the tool runs in."""
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def to_json(report: EvalReport) -> str:
    """The report as JSON with sorted keys: the same report is the same bytes."""
    return json.dumps(asdict(report), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_report(report: EvalReport, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_json(report), encoding="utf-8")


def compare(previous: EvalReport, current: EvalReport) -> Mapping[str, float]:
    """Deltas (current minus previous) of every metric present in both runs, keyed by
    ``M-04`` and, for each breakdown cell present in both, ``M-04/<dimension>/<cell>``."""
    before = {m.metric_id: m for m in previous.metrics}
    deltas: dict[str, float] = {}
    for metric in current.metrics:
        old = before.get(metric.metric_id)
        if old is None:
            continue
        deltas[metric.metric_id] = metric.value - old.value
        for dimension, cells in metric.breakdown.items():
            old_cells = old.breakdown.get(dimension, {})
            for cell, value in cells.items():
                if cell in old_cells:
                    deltas[f"{metric.metric_id}/{dimension}/{cell}"] = value - old_cells[cell]
    return dict(sorted(deltas.items()))
