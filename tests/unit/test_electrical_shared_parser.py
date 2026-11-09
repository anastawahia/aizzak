"""FR-ID-12 / INC1-AC-4: the ONE parser, over the whole case set and the whole SRS appendix."""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind, PlcFamily
from app.modules.electrical.shared.parser import MAX_TEXT_CHARS, IdentifierParser, _may_be_additive
from tests.eval.electrical.m04_identifiers import (
    compute_m04,
    expected_shape,
    load_cases,
    observed,
    parse_case,
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
        "-X1:14", "X1.14", "X1-14", "=A1+P2-X1:14", "XT1:5", "1XT-5", "1X-25",
        "XT1" + chr(0xFF1A) + "5",
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
# The F2 examples (FR-ID-15, FR-ID-16, D-36), invented; read with KKS and ANSI enabled.
_APPENDIX_A_F2 = {
    IdentifierKind.KKS_TAG: [
        "20XYZ10", "20XYZ10AB123", "20XYZ10AB123CD45", "20XYZ10AB1234567", "20XYZ104567",
        "=20XYZ10AB123",
    ],
    IdentifierKind.ANSI_DEVICE: ["87T", "50BF", "51N", "27-1", "50/51"],
}  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "text"), [(k, t) for k, texts in _APPENDIX_A.items() for t in texts]
)
def test_every_appendix_a_example_parses_to_its_kind(kind: IdentifierKind, text: str) -> None:
    found = parse(text)
    assert found.kind is kind and found.valid and not found.issues
    assert found.forms.original == text
    assert found.forms.canonical and found.forms.search_keys


@pytest.mark.parametrize(
    ("kind", "text"), [(k, t) for k, texts in _APPENDIX_A_F2.items() for t in texts]
)
def test_every_f2_appendix_a_example_parses_to_its_kind(kind: IdentifierKind, text: str) -> None:
    found = _with(Convention.KKS, Convention.ANSI).parse(text)
    assert found is not None and found.kind is kind and found.valid and not found.issues
    assert found.forms.original == text


@pytest.mark.parametrize("text", _CONNECTION_POINT_EXAMPLES)
def test_appendix_a_connection_points_carry_label_and_role(text: str) -> None:
    found = parse(text)
    assert found.connection_point is not None and found.pole_role is not None
    assert found.valid


def test_every_appendix_a_example_is_in_the_case_set() -> None:
    texts = {c["text"] for c in _CASES}
    needed = {t for group in (*_APPENDIX_A.values(), *_APPENDIX_A_F2.values()) for t in group}
    needed |= set(_CONNECTION_POINT_EXAMPLES)
    assert needed <= texts


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_the_parser_returns_exactly_what_each_case_says(case: dict[str, object]) -> None:
    got = observed(parse_case(PARSER, case))
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
        found = parse_case(PARSER, case)
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


_ALLOWED_IMPORTS = frozenset(
    {
        "__future__", "re", "unicodedata", "dataclasses", "enum", "functools", "typing",
        "collections", "itertools", "types",
    }
)  # fmt: skip
_FORBIDDEN_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__", "input"})
_FORBIDDEN_ATTRIBUTES = frozenset({"read_text", "read_bytes", "write_text", "write_bytes", "open"})


def test_the_kernel_imports_only_an_allowlist_of_pure_modules_and_itself() -> None:
    """INC1-AC-4 "no I/O": the allowlist forbids os, pathlib, io, subprocess, socket,
    importlib, shutil, tempfile... by default, not by a blocklist that can be outgrown."""
    root = pathlib.Path(__file__).resolve().parents[2] / "src/app/modules/electrical/shared"
    own = "app.modules.electrical.shared"
    files = sorted(root.rglob("*.py"))
    assert len(files) >= 14
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top in _ALLOWED_IMPORTS or name.startswith(own), (
                    f"{path.name} imports {name}"
                )
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in _FORBIDDEN_CALLS, f"{path.name} calls {func.id}"
                if isinstance(func, ast.Attribute):
                    assert func.attr not in _FORBIDDEN_ATTRIBUTES, f"{path.name} calls .{func.attr}"


# --- F2-AC-7 (D-36): KKS and ANSI are additive, and change no existing case -------------

_PRE_F2 = [c for c in _CASES if int(str(c["id"])[1:]) <= 192 and "select" not in c]


def _with(*extra: Convention) -> IdentifierParser:
    rules = PARSER.rules
    return IdentifierParser(replace(rules, conventions=rules.conventions | frozenset(extra)))


def test_f2_ac_7_kks_on_or_off_changes_no_existing_case() -> None:
    assert len(_PRE_F2) == 192
    kks_on = _with(Convention.KKS)
    for case in _PRE_F2:
        assert observed(parse_case(kks_on, case)) == observed(parse_case(PARSER, case)), case["id"]
    # the invented tags are read only when KKS is on
    for text in ("20XYZ10", "20XYZ10AB123", "20XYZ10AB123CD45"):
        assert PARSER.parse(text) is None
        found = kks_on.parse(text)
        assert found is not None and found.kind is IdentifierKind.KKS_TAG


def test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities() -> None:
    ansi_on = _with(Convention.ANSI)
    changed: dict[str, str] = {}
    for case in _PRE_F2:
        before, after = observed(parse_case(PARSER, case)), observed(parse_case(ansi_on, case))
        if before == after:
            continue
        changed[str(case["text"])] = str(after["kind"])
        if after["kind"] == before["kind"]:  # a bare number: only the alternative is added
            assert {k: v for k, v in after.items() if k != "alternatives"} == before
            assert after["alternatives"] == ["ansi_device"]
    # the compound against the cross-reference, and nothing else; 1KM and 2QF stay letter codes
    assert {text for text, kind in changed.items() if kind == "ansi_device"} == {"12/3"}
    for text in ("1KM", "2QF"):
        found = ansi_on.parse(text)
        assert found is not None and found.kind is IdentifierKind.LETTER_CODE
    compound = ansi_on.parse("12/3")
    assert compound is not None and compound.alternatives == (IdentifierKind.CROSS_REFERENCE,)


def test_the_probe_filter_checks_the_length_first() -> None:
    """S-4: parse refuses long text before normalising, so does the probe's filter."""
    assert _may_be_additive("1" * MAX_TEXT_CHARS)
    assert not _may_be_additive("1" * (MAX_TEXT_CHARS + 1))
    assert not _may_be_additive("\ufdfa" * 1000)
