"""M-04 (INC-1): the identifier parser's exact-match rate on the core case set.

**This is a tool, not a test** (no ``test_`` prefix). ``cases/identifiers.toml`` holds
every format of the SRS Appendix A, each with the exact shape the parser must return:
kind, convention, aspects, connection point, device class, pole role, PLC address,
validity and issues, and the three forms (original, canonical, search keys). A case
matches only when EVERY field matches. ``tests/unit/test_electrical_shared_parser.py``
reads the same cases, so a failing case fails CI and the value here is 1.0 by build.

Run: ``.venv/bin/python -m tests.eval.electrical.m04_identifiers --out report.json``
"""

from __future__ import annotations

import argparse
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import IdentifierRules, RuleSelection
from app.modules.electrical.shared.version import SHARED_VERSION
from tests.eval.electrical.framework import (
    EvalReport,
    MetricResult,
    current_commit,
    fingerprint,
    sha256_text,
    write_report,
)

METRIC_ID = "M-04"
TARGET_R1 = 0.995  # requirements.md §M-04
CASES_FILE = Path(__file__).parent / "cases" / "identifiers.toml"

Case = Mapping[str, Any]


def load_rules() -> IdentifierRules:
    """The packaged rules, read the way a section adapter reads them (INC1-D-19)."""
    text = (
        resources.files("app.modules.electrical.shared.rules")
        .joinpath("identifiers.toml")
        .read_text(encoding="utf-8")
    )
    return IdentifierRules.from_mapping(tomllib.loads(text))


def load_cases(path: Path = CASES_FILE) -> tuple[Case, ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(data["case"])


def hint_of(case: Case) -> IdentifierKind | None:
    return IdentifierKind(case["hint"]) if "hint" in case else None


def selection_of(case: Case) -> RuleSelection | None:
    """The case's ``select`` table (D-36): RuleSelection field names, by NAME only."""
    chosen = case.get("select")
    if chosen is None:
        return None
    return RuleSelection(
        conventions=frozenset(Convention(name) for name in chosen.get("conventions", ())),
        plc_families=frozenset(),
        extra_aspects=(),
        wire_number_patterns=(),
        cross_reference_format=chosen.get("cross_reference_format"),
        extra_voltage_labels=(),
        extra_ansi_suffixes=tuple(chosen.get("extra_ansi_suffixes", ())),
    )


_NARROWED: dict[tuple[int, str], tuple[IdentifierRules, IdentifierParser]] = {}


def parse_case(parser: IdentifierParser, case: Case) -> ParsedIdentifier | None:
    """Parse the case's text the way the case says: with the rules narrowed by its ``select``
    (one parser per rules object and selection, cached), or with ``parser`` itself."""
    selection = selection_of(case)
    if selection is not None:
        key = (id(parser.rules), repr(selection))
        cached = _NARROWED.get(key)
        if cached is None or cached[0] is not parser.rules:
            cached = (parser.rules, IdentifierParser(parser.rules.narrowed(selection)))
            _NARROWED[key] = cached
        parser = cached[1]
    return parser.parse(case["text"], hint=hint_of(case))


def observed(parsed: ParsedIdentifier | None) -> dict[str, Any]:
    """The comparable shape of a parse result, in the case file's own vocabulary."""
    if parsed is None:
        return {"kind": "none"}
    shape: dict[str, Any] = {
        "kind": parsed.kind.value,
        "valid": parsed.valid,
        "canonical": parsed.forms.canonical,
        "search_keys": list(parsed.forms.search_keys),
        "aspects": [a.prefix + a.code for a in parsed.aspects],
    }
    kks, ansi = parsed.kks, parsed.ansi
    optional: dict[str, Any] = {
        "convention": parsed.convention.value if parsed.convention else None,
        "connection_point": parsed.connection_point,
        "device_class": parsed.device_class,
        "pole_role": parsed.pole_role,
        "variant": parsed.variant,
        "issues": list(parsed.issues) or None,
        "kks": (
            {
                "form": kks.form,
                "class": kks.kks_class.value,
                **({"prefix": kks.prefix} if kks.prefix is not None else {}),
                "parts": [
                    {
                        "name": p.name,
                        **({"level": p.level} if p.level is not None else {}),
                        "text": p.text,
                        "runs": list(p.runs),
                    }
                    for p in kks.parts
                ],
            }
            if kks
            else None
        ),
        "ansi": (
            {
                "functions": [
                    {
                        "number": f.number,
                        "suffix": f.suffix,
                        **({"class": f.function_class} if f.function_class is not None else {}),
                    }
                    for f in ansi.functions
                ],
                **({"instance": ansi.instance} if ansi.instance is not None else {}),
            }
            if ansi
            else None
        ),
        "alternatives": [k.value for k in parsed.alternatives] or None,
        "plc": (
            {
                "family": parsed.plc.family.value,
                "area": parsed.plc.area,
                "width": parsed.plc.width,
                "byte": parsed.plc.byte,
                **({"bit": parsed.plc.bit} if parsed.plc.bit is not None else {}),
                **({"db_number": parsed.plc.db_number} if parsed.plc.db_number is not None else {}),
            }
            if parsed.plc
            else None
        ),
    }
    shape.update({k: v for k, v in optional.items() if v is not None})
    return shape


EXPECTED_KEYS = (
    "kind", "valid", "canonical", "search_keys", "aspects", "convention", "connection_point",
    "device_class", "pole_role", "variant", "issues", "plc", "kks", "ansi", "alternatives",
)  # fmt: skip


def expected_shape(case: Case) -> dict[str, Any]:
    """What the case file says the parser must return (absent optional field = None)."""
    if case["kind"] == "none":
        return {"kind": "none"}
    shape = {key: case[key] for key in EXPECTED_KEYS if key in case}
    shape.setdefault("valid", True)
    shape.setdefault("aspects", [])
    return shape


def matches(parser: IdentifierParser, case: Case) -> bool:
    return observed(parse_case(parser, case)) == expected_shape(case)


def _case_hash(case: Case) -> str:
    return sha256_text(repr(sorted((k, repr(v)) for k, v in case.items())))


def _rate(flags: Sequence[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def compute_m04(parser: IdentifierParser, cases: Sequence[Case]) -> MetricResult:
    flags = [matches(parser, case) for case in cases]
    cells: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for case, flag in zip(cases, flags, strict=True):
        cells["by_kind"][case["kind"]].append(flag)
        cells["by_convention"][case.get("convention", "none")].append(flag)
        cells["by_script"][case.get("script", "ascii")].append(flag)
        cells["by_source"][case.get("source", "appendix-a")].append(flag)
    value = _rate(flags)
    return MetricResult(
        metric_id=METRIC_ID,
        value=value,
        target_r1=TARGET_R1,
        passed=value >= TARGET_R1,
        n=len(cases),
        breakdown={
            dimension: {cell: _rate(f) for cell, f in sorted(by_cell.items())}
            for dimension, by_cell in sorted(cells.items())
        },
        dataset_fingerprint=fingerprint(_case_hash(case) for case in cases),
    )


def build_report(
    *, commit: str, created_at: str, run_id: str, rules: IdentifierRules | None = None
) -> EvalReport:
    rules = rules or load_rules()
    metric = compute_m04(IdentifierParser(rules), load_cases())
    return EvalReport(
        run_id=run_id,
        commit=commit,
        versions={"shared": SHARED_VERSION, "rules": rules.version},
        metrics=(metric,),
        created_at=created_at,
    )


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Run M-04 on the core identifier cases")
    argument_parser.add_argument("--out", type=Path, required=True)
    args = argument_parser.parse_args()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = current_commit()
    report = build_report(commit=commit, created_at=now, run_id=f"m04-{commit[:8]}-{now}")
    write_report(report, args.out)
    print(f"M-04 = {report.metrics[0].value:.4f} over {report.metrics[0].n} cases")


if __name__ == "__main__":
    main()
