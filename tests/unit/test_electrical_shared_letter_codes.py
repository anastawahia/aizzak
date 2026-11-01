"""FR-ID-13 / INC1-AC-4: Chinese letter codes and NFPA/JIC codes without an aspect prefix."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.kinds import Convention, IdentifierKind
from tests.unit.support_electrical_shared import PARSER, fw, parse

_CN = Convention.CN_LETTER_CODES


@pytest.mark.parametrize(
    ("text", "device_class"),
    [
        ("KM1", "contactor"),
        ("KA3", "relay"),
        ("KT1", "time_relay"),
        ("QF2", "circuit_breaker"),
        ("FU1", "fuse"),
        ("FR1", "thermal_relay"),
        ("SB1", "pushbutton"),
        ("SA2", "selector_switch"),
        ("SQ3", "limit_switch"),
        ("HL2", "indicator_lamp"),
        ("XT1", "terminal_strip"),
    ],
)
def test_chinese_letter_codes_suggest_the_device_class(text: str, device_class: str) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.LETTER_CODE
    assert found.convention is _CN
    assert found.device_class == device_class


@pytest.mark.parametrize(
    ("text", "device_class"), [("1KM", "contactor"), ("2QF", "circuit_breaker")]
)
def test_the_digits_first_form_is_a_chinese_code_form(text: str, device_class: str) -> None:
    found = parse(text)
    assert (found.kind, found.convention) == (IdentifierKind.LETTER_CODE, _CN)
    assert found.device_class == device_class
    # both spellings are search keys
    assert {text, f"{text[1:]}{text[0]}"} <= set(found.forms.search_keys)


@pytest.mark.parametrize(
    ("text", "device_class"),
    [
        ("CR1", "control_relay"),
        ("M1", "motor_starter"),
        ("PB1", "pushbutton"),
        ("LT1", "indicator_lamp"),
        ("TB1", "terminal_block"),
    ],
)
def test_nfpa_jic_codes(text: str, device_class: str) -> None:
    found = parse(text)
    assert found.convention is Convention.NFPA_JIC
    assert found.device_class == device_class


def test_a_full_width_code_parses_and_keeps_its_original() -> None:
    found = parse(fw("KM1"))
    assert found.convention is _CN and found.device_class == "contactor"
    assert found.forms.original == fw("KM1")
    assert found.forms.canonical == "KM1"


def test_a_code_may_carry_a_connection_point() -> None:
    found = parse("KM1:A1")
    assert found.kind is IdentifierKind.LETTER_CODE
    assert (found.connection_point, found.pole_role) == ("A1", "coil")


def test_digits_first_is_not_an_nfpa_form() -> None:
    assert PARSER.parse("1CR") is None


def test_an_unknown_code_is_no_identifier() -> None:
    assert PARSER.parse("ZZ1") is None
    assert PARSER.parse("ABCD1234") is None


def test_a_letter_code_with_a_bad_point_is_flagged() -> None:
    found = parse("KM1:")
    assert found.valid is False and "connection_point.empty" in found.issues


def test_m1_is_a_letter_code_but_m10_1_is_a_plc_address() -> None:
    assert parse("M1").kind is IdentifierKind.LETTER_CODE
    assert parse("M10.1").kind is IdentifierKind.PLC_ADDRESS
