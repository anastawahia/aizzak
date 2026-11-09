"""Shared helpers for the `electrical.shared` unit tests (BT10)."""

from __future__ import annotations

from typing import Any

from app.modules.electrical.shared.kinds import IdentifierKind, ParsedIdentifier
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.rules import RuleSelection
from tests.eval.electrical.m04_identifiers import load_rules

PARSER = IdentifierParser(load_rules())


def parse(text: str, hint: IdentifierKind | None = None) -> ParsedIdentifier:
    found = PARSER.parse(text, hint=hint)
    assert found is not None, f"{text!r} did not parse"
    return found


def aspects(found: ParsedIdentifier) -> list[str]:
    return [a.prefix + a.code for a in found.aspects]


def fw(text: str) -> str:
    """The full-width spelling of ASCII text (as in Chinese-language drawings)."""
    return "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in text)


def _selection(**changes: Any) -> RuleSelection:
    base: dict[str, Any] = {
        "conventions": frozenset(),
        "plc_families": frozenset(),
        "extra_aspects": (),
        "wire_number_patterns": (),
        "cross_reference_format": None,
        "extra_voltage_labels": (),
        "extra_ansi_suffixes": (),
    }
    return RuleSelection(**{**base, **changes})
