"""FR-ID-12 / INC1-AC-4: the ONE parser, over the whole case set and the whole SRS appendix."""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, PlcFamily
from tests.eval.electrical.m04_identifiers import (
    compute_m04,
    expected_shape,
    hint_of,
    load_cases,
    observed,
)
from tests.unit.support_electrical_shared import PARSER, fw, parse

_CASES = load_cases()

# Every example of SRS Appendix A (requirements.md) for the kinds this kernel parses.
_APPENDIX_A = {
    IdentifierKind.REFERENCE_DESIGNATION: ["-K5", "=A1-K5", "=A1+P2-K5", "==PLANT=A1++SITE+P2-K5"],
    IdentifierKind.LETTER_CODE: [
        "KM1", "KA3", "KT1", "QF2", "FU1", "FR1", "SB1", "SA2", "SQ3", "HL2", "1KM", "2QF",
        fw("KM1"), "CR1", "M1", "PB1", "LT1", "TB1",
    ],
    IdentifierKind.TERMINAL: [
        "-X1:14", "X1.14", "X1-14", "=A1+P2-X1:14", "XT1:5", "1XT-5", "XT1" + chr(0xFF1A) + "5",
    ],
    IdentifierKind.WIRE_NUMBER: ["123", "W123", "12.3-1", "L1-5", "U11", "V11", "W11"],
    IdentifierKind.PLC_ADDRESS: [
        "I0.0", "Q4.7", "M10.1", "IW64", "PIW256", "E0.0", "A4.7", "PEW256", "%I0.0", "%Q4.7",
        "%IW64", "DB10.DBX2.3", "DB10.DBW4", "V100.0", "VW100", "AIW16", "AQW16",
    ],
    IdentifierKind.CROSS_REFERENCE: ["/12.3", "12.3", "=A1/12.3", "12/3"],
    IdentifierKind.VOLTAGE: ["L1", "L2", "L3", "N", "PE", "L+", "M", "+24V", "0V", "24VDC"],
}  # fmt: skip
_CONNECTION_POINT_EXAMPLES = ["-K5:A1", "-K5:13", "-Q1:2", "KM1:A1"]


@pytest.mark.parametrize(
    ("kind", "text"), [(k, t) for k, texts in _APPENDIX_A.items() for t in texts]
)
def test_every_appendix_a_example_parses_to_its_kind(kind: IdentifierKind, text: str) -> None:
    found = parse(text)
    assert found.kind is kind and found.valid and not found.issues
    assert found.forms.original == text
    assert found.forms.canonical and found.forms.search_keys


@pytest.mark.parametrize("text", _CONNECTION_POINT_EXAMPLES)
def test_appendix_a_connection_points_carry_label_and_role(text: str) -> None:
    found = parse(text)
    assert found.connection_point is not None and found.pole_role is not None
    assert found.valid


def test_every_appendix_a_example_is_in_the_case_set() -> None:
    texts = {c["text"] for c in _CASES}
    needed = {t for group in _APPENDIX_A.values() for t in group} | set(_CONNECTION_POINT_EXAMPLES)
    assert needed <= texts


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_the_parser_returns_exactly_what_each_case_says(case: dict[str, object]) -> None:
    got = observed(PARSER.parse(str(case["text"]), hint=hint_of(case)))
    assert got == expected_shape(case)


def test_m04_on_the_case_set_is_one() -> None:
    metric = compute_m04(PARSER, _CASES)
    assert metric.value == 1.0 and metric.passed and metric.n == len(_CASES)


def test_the_case_ids_are_unique() -> None:
    ids = [c["id"] for c in _CASES]
    assert len(set(ids)) == len(ids)


def test_invalid_input_is_flagged_never_dropped() -> None:
    flagged = [c for c in _CASES if c["source"] == "invalid"]
    assert len(flagged) >= 15
    for case in flagged:
        found = PARSER.parse(case["text"])
        assert found is not None and found.valid is False and found.issues


def test_the_parse_is_deterministic() -> None:
    for case in _CASES:
        assert PARSER.parse(case["text"]) == PARSER.parse(case["text"])


def test_nothing_that_is_empty_or_prose_is_an_identifier() -> None:
    for text in ("", "   ", "-", "--", "hello", "price: $5", "x" * 300):
        assert PARSER.parse(text) is None


def test_the_tally_reports_conventions_and_plc_families() -> None:
    tally = PARSER.tally(["KM1", "QF2", "CR1", "-K5", "I0.0", "E0.0", "%IW64", "", "zzz"])
    assert tally.tokens == 9 and tally.identifiers == 7
    assert tally.conventions == {
        Convention.CN_LETTER_CODES: 2,
        Convention.NFPA_JIC: 1,
        Convention.IEC_81346: 1,
    }
    assert tally.plc_families == {PlcFamily.S7_EN: 1, PlcFamily.S7_DE: 1, PlcFamily.TIA: 1}


def test_the_kernel_imports_only_the_standard_library_and_itself() -> None:
    root = pathlib.Path(__file__).resolve().parents[2] / "src/app/modules/electrical/shared"
    stdlib = set(sys.stdlib_module_names)
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                own = name.startswith("app.modules.electrical.shared")
                assert top in stdlib or own, f"{path.name} imports {name}"
            # no file I/O in the kernel: the rules arrive as a mapping
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", f"{path.name} opens a file"
