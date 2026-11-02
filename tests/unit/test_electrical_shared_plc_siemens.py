"""FR-ID-05 / INC1-AC-4: Siemens PLC addresses, every S7 family at once."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.kinds import IdentifierKind, PlcFamily
from tests.unit.support_electrical_shared import PARSER, fw, parse

# text -> (family, area, width, byte, bit, db)
_VALID = {
    "I0.0": (PlcFamily.S7_EN, "I", "bit", 0, 0, None),
    "Q4.7": (PlcFamily.S7_EN, "Q", "bit", 4, 7, None),
    "M10.1": (PlcFamily.S7_EN, "M", "bit", 10, 1, None),
    "IW64": (PlcFamily.S7_EN, "I", "word", 64, None, None),
    "QW80": (PlcFamily.S7_EN, "Q", "word", 80, None, None),
    "PIW256": (PlcFamily.S7_EN, "PI", "word", 256, None, None),
    "IB1": (PlcFamily.S7_EN, "I", "byte", 1, None, None),
    "QD8": (PlcFamily.S7_EN, "Q", "dword", 8, None, None),
    "E0.0": (PlcFamily.S7_DE, "I", "bit", 0, 0, None),
    "A4.7": (PlcFamily.S7_DE, "Q", "bit", 4, 7, None),
    "EW64": (PlcFamily.S7_DE, "I", "word", 64, None, None),
    "PEW256": (PlcFamily.S7_DE, "PI", "word", 256, None, None),
    "PAW64": (PlcFamily.S7_DE, "PQ", "word", 64, None, None),
    "%I0.0": (PlcFamily.TIA, "I", "bit", 0, 0, None),
    "%Q4.7": (PlcFamily.TIA, "Q", "bit", 4, 7, None),
    "%IW64": (PlcFamily.TIA, "I", "word", 64, None, None),
    "%IW256:P": (PlcFamily.TIA, "PI", "word", 256, None, None),
    "DB10.DBX2.3": (PlcFamily.S7_DB, "DB", "bit", 2, 3, 10),
    "DB10.DBW4": (PlcFamily.S7_DB, "DB", "word", 4, None, 10),
    "DB1.DBD8": (PlcFamily.S7_DB, "DB", "dword", 8, None, 1),
    "V100.0": (PlcFamily.S7_200, "V", "bit", 100, 0, None),
    "VW100": (PlcFamily.S7_200, "V", "word", 100, None, None),
    "AIW16": (PlcFamily.S7_200, "AI", "word", 16, None, None),
    "AQW16": (PlcFamily.S7_200, "AQ", "word", 16, None, None),
}


@pytest.mark.parametrize(("text", "expected"), list(_VALID.items()))
def test_every_siemens_form_in_the_srs_appendix(text: str, expected: tuple[object, ...]) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.PLC_ADDRESS and found.valid
    assert found.plc is not None
    plc = found.plc
    assert (plc.family, plc.area, plc.width, plc.byte, plc.bit, plc.db_number) == expected


def test_full_width_tia_address() -> None:
    found = parse(fw("%I0.0"))
    assert found.plc is not None and found.plc.family is PlcFamily.TIA
    assert found.forms.original == fw("%I0.0") and found.forms.canonical == "%I0.0"


def test_the_same_address_is_findable_in_every_s7_spelling() -> None:
    keys = {text: set(parse(text).forms.search_keys) for text in ("I0.0", "E0.0", "%I0.0")}
    assert keys["I0.0"] == keys["E0.0"] == keys["%I0.0"] == {"I0.0", "E0.0", "%I0.0"}
    assert {"PIW256", "PEW256", "%IW256:P"} <= set(parse("PIW256").forms.search_keys)


def test_m_without_a_width_or_a_bit_is_not_an_address() -> None:
    assert parse("M1").kind is IdentifierKind.LETTER_CODE  # NFPA, not PLC
    assert parse("A1").kind is IdentifierKind.CONNECTION_POINT  # the coil label, not Q1


@pytest.mark.parametrize(
    ("text", "issue"),
    [
        ("I0.8", "plc.bit_out_of_range"),
        ("Q4.9", "plc.bit_out_of_range"),
        ("%I0.9", "plc.bit_out_of_range"),
        ("DB10.DBX2.8", "plc.bit_out_of_range"),
        ("IW64.1", "plc.bit_on_non_bit_width"),
        ("DB10.DBW4.1", "plc.bit_on_non_bit_width"),
        ("VW100.2", "plc.bit_on_non_bit_width"),
        ("%I0", "plc.bit_missing"),
        ("DB10.DBX2", "plc.bit_missing"),
        ("%IX0.0", "plc.unsupported_form"),
    ],
)
def test_a_broken_address_is_flagged_not_dropped(text: str, issue: str) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.PLC_ADDRESS
    assert found.valid is False and issue in found.issues


@pytest.mark.parametrize("text", ["Local:1:I.Data.0", "I:1/0", "X0", "Y0"])
def test_other_vendors_forms_are_not_claimed(text: str) -> None:
    assert PARSER.parse(text) is None  # FR-ID-14 is configuration, not built in
