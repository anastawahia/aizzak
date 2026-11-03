"""M-32 (INC-1): how often STG-01's profile proposal is right (requirements.md, M-32).

**This is a tool, not a test** (no ``test_`` prefix). Two inputs, both given as arguments, so no
path is wired into the code and the owner's samples never need to enter the repository:

* ``--inspections <dir>``: a folder of ele-parse inspection files named ``<sha256 of the PDF>.json``
  (``python -m services.ele_parse inspect``; ``app.ops.electrical_characterize`` documents the
  naming);
* ``--labels <file.json>``: the owner's labels, ``{"<sha256>": {"symbol_standard": "iec_60617",
  "identifier_convention": "iec_81346"}, ...}``. The values are the profile's own vocabulary
  (``SymbolStandard``, ``Convention``); anything else is refused.

For every labelled document the tool runs ``detect_profile`` exactly as the worker does (through
``app.ops.electrical_characterize.analyse_inspection``, over the default profile and the packaged
rules) and compares the two proposed fields with the labels. The value is the share of documents
whose proposal is right on BOTH fields; the breakdown gives each field alone and the "both" figure
by producer family (the vendor, FR-EVAL-13) and by document language. An inspection without a label
is left out and counted under ``coverage``; a label without an inspection is an error (a typo in a
hash must not shrink the sample silently).

Run: ``.venv/bin/python -m tests.eval.electrical.m32_profile_detection --inspections <dir>
--labels <labels.json> --out report.json``. The first run on the owner's samples is outside INC-1
(LLD section 1.4); ``tests/unit/test_electrical_m32.py`` pins the arithmetic on synthetic
inspections.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.framework.observability.untrusted import escape_untrusted_text
from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import SymbolStandard
from app.modules.electrical.drawings.ports.rules import RuleSource
from app.modules.electrical.shared.kinds import Convention
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.version import SHARED_VERSION
from app.ops.electrical_characterize import (
    Analysis,
    Inspection,
    analyse_inspection,
    load_inspections,
)
from tests.eval.electrical.framework import (
    EvalReport,
    MetricResult,
    current_commit,
    fingerprint,
    sha256_text,
    write_report,
)

METRIC_ID = "M-32"
TARGET_R1 = 0.90  # requirements.md, M-32
UNKNOWN = "unknown"

Label = Mapping[str, str]


class LabelError(ValueError):
    """The labels file is unusable (the message never echoes a value it refused)."""


def load_labels(path: Path) -> dict[str, Label]:
    """``{sha256: {symbol_standard, identifier_convention}}``, validated against the profile's
    own vocabulary."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LabelError(f"the labels file cannot be read as JSON ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or not data:
        raise LabelError("the labels file must be a non-empty JSON object keyed by SHA-256")
    labels: dict[str, Label] = {}
    for sha256, entry in data.items():
        if not isinstance(entry, dict) or set(entry) != {
            "symbol_standard",
            "identifier_convention",
        }:
            raise LabelError(
                f"label {sha256[:16]}: need exactly symbol_standard and identifier_convention"
            )
        try:
            SymbolStandard(entry["symbol_standard"])
            Convention(entry["identifier_convention"])
        except ValueError as exc:
            raise LabelError(
                f"label {sha256[:16]}: not a known symbol standard or convention"
            ) from exc
        labels[sha256] = {
            "symbol_standard": entry["symbol_standard"],
            "identifier_convention": entry["identifier_convention"],
        }
    return labels


def _rate(flags: Sequence[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def compute_m32(
    analyses: Mapping[str, Analysis], labels: Mapping[str, Label], *, unlabelled: int = 0
) -> MetricResult:
    """The accuracy of the proposals in ``analyses`` (keyed by SHA-256) against ``labels``."""
    stray = sorted(set(labels) - set(analyses))
    if stray:
        raise LabelError(f"{len(stray)} label(s) match no inspection, first {stray[0][:16]}")
    if not analyses:
        raise LabelError("no labelled inspection to measure")
    symbol_flags: list[bool] = []
    convention_flags: list[bool] = []
    both_flags: list[bool] = []
    by_vendor: dict[str, list[bool]] = defaultdict(list)
    by_language: dict[str, list[bool]] = defaultdict(list)
    for sha256, analysis in sorted(analyses.items()):
        label = labels[sha256]
        profile = analysis.proposal.profile
        symbol_ok = profile.symbol_standard.value == label["symbol_standard"]
        convention_ok = profile.identifier_convention.value == label["identifier_convention"]
        symbol_flags.append(symbol_ok)
        convention_flags.append(convention_ok)
        both_flags.append(symbol_ok and convention_ok)
        vendor = escape_untrusted_text(analysis.record.producer_family or UNKNOWN, limit=60)
        by_vendor[vendor].append(symbol_ok and convention_ok)
        by_language[analysis.record.document_language].append(symbol_ok and convention_ok)
    value = _rate(both_flags)
    return MetricResult(
        metric_id=METRIC_ID,
        value=value,
        target_r1=TARGET_R1,
        passed=value >= TARGET_R1,
        n=len(both_flags),
        breakdown={
            "by_field": {
                "both": value,
                "identifier_convention": _rate(convention_flags),
                "symbol_standard": _rate(symbol_flags),
            },
            "by_language": {cell: _rate(f) for cell, f in sorted(by_language.items())},
            "by_vendor": {cell: _rate(f) for cell, f in sorted(by_vendor.items())},
            "coverage": {"labelled": float(len(both_flags)), "unlabelled": float(unlabelled)},
        },
        dataset_fingerprint=fingerprint(
            sha256_text(
                f"{sha}:{labels[sha]['symbol_standard']}:{labels[sha]['identifier_convention']}"
            )
            for sha in analyses
        ),
    )


def measure(
    inspections: Sequence[Inspection], labels: Mapping[str, Label], *, rules: RuleSource
) -> MetricResult:
    parser = IdentifierParser(rules.identifier_rules())
    labelled = {
        i.sha256: analyse_inspection(i, rules=rules, parser=parser)
        for i in inspections
        if i.sha256 in labels
    }
    return compute_m32(labelled, labels, unlabelled=len(inspections) - len(labelled))


def build_report(
    inspections: Sequence[Inspection],
    labels: Mapping[str, Label],
    *,
    commit: str,
    created_at: str,
    run_id: str,
    rules: RuleSource | None = None,
) -> EvalReport:
    rules = rules or PackagedRuleSource()
    return EvalReport(
        run_id=run_id,
        commit=commit,
        versions={
            "pipeline": PIPELINE_VERSION,
            "shared": SHARED_VERSION,
            "rules": rules.identifier_rules().version,
        },
        metrics=(measure(inspections, labels, rules=rules),),
        created_at=created_at,
    )


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Run M-32 on inspection files and labels")
    argument_parser.add_argument("--inspections", type=Path, required=True)
    argument_parser.add_argument("--labels", type=Path, required=True)
    argument_parser.add_argument("--out", type=Path, required=True)
    args = argument_parser.parse_args()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = current_commit()
    report = build_report(
        load_inspections(args.inspections),
        load_labels(args.labels),
        commit=commit,
        created_at=now,
        run_id=f"m32-{commit[:8]}-{now}",
    )
    write_report(report, args.out)
    metric = report.metrics[0]
    print(f"M-32 = {metric.value:.4f} over {metric.n} documents (target {metric.target_r1})")


if __name__ == "__main__":
    main()
