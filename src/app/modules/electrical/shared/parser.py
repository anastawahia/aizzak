"""The ONE identifier parser (FR-ID-12), shared by extraction, indexing, report
understanding, output checking and integration.

Each kind has a pure function ``(canonical, forms, rules) -> ParsedIdentifier | None``;
the parser tries them in a FIXED order, so the result is deterministic. The order puts
the unambiguous shapes first. Tokens that are legitimately two things are resolved by
that order, and a caller who knows better passes ``hint``:

* ``12.3`` reads as a cross-reference (hint ``WIRE_NUMBER`` for the page/column wire);
* ``13`` and ``95`` read as wire numbers (hint ``CONNECTION_POINT`` for the pole label);
* ``M10.1`` is a PLC address, ``M1`` an NFPA letter code, ``M`` a voltage label.

``None`` means "no identifier at all"; a malformed identifier comes back with
``valid=False`` and its issues (FR-ID-10), never dropped. Hidden characters in the text
(control, format, default-ignorable) are dropped from the canonical form but flagged
``text.hidden_character`` (invalid); bidi controls are flagged ``text.bidi_control`` and
the identifier stays valid. Identity is (``kind``, ``canonical``); ``search_keys`` are
for retrieval only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.modules.electrical.shared.connection_points import parse_connection_point
from app.modules.electrical.shared.cross_references import parse_cross_reference
from app.modules.electrical.shared.kinds import (
    Convention,
    ConventionTally,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
    PlcFamily,
)
from app.modules.electrical.shared.letter_codes import parse_letter_code
from app.modules.electrical.shared.normalization import (
    NON_INVALIDATING_ISSUES,
    normalize,
    text_issues,
)
from app.modules.electrical.shared.plc_siemens import parse_plc_address
from app.modules.electrical.shared.reference_designation import parse_reference_designation
from app.modules.electrical.shared.terminals import parse_bridge, parse_terminal
from app.modules.electrical.shared.voltages import parse_voltage
from app.modules.electrical.shared.wire_numbers import parse_wire_number

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

# Longer text is prose. The bound applies to the raw text AND to the canonical form (NFKC
# can expand one character into many), so it bounds every regular expression's input.
MAX_TEXT_CHARS = 200

_Parse = Callable[[str, NormalizedForms, "IdentifierRules"], ParsedIdentifier | None]

_ORDER: tuple[tuple[IdentifierKind, _Parse], ...] = (
    (IdentifierKind.PLC_ADDRESS, parse_plc_address),
    (IdentifierKind.CROSS_REFERENCE, parse_cross_reference),
    (IdentifierKind.VOLTAGE, parse_voltage),
    (IdentifierKind.BRIDGE, parse_bridge),
    (IdentifierKind.TERMINAL, parse_terminal),
    (IdentifierKind.REFERENCE_DESIGNATION, parse_reference_designation),
    (IdentifierKind.LETTER_CODE, parse_letter_code),
    (IdentifierKind.WIRE_NUMBER, parse_wire_number),
    (IdentifierKind.CONNECTION_POINT, parse_connection_point),
)


def _flag_text(found: ParsedIdentifier, issues: tuple[str, ...]) -> ParsedIdentifier:
    """FR-ID-10: hidden characters and bidi controls are reported, never silent."""
    if not issues:
        return found
    all_issues = tuple(dict.fromkeys((*found.issues, *issues)))
    invalidating = any(i not in NON_INVALIDATING_ISSUES for i in issues)
    return replace(found, valid=found.valid and not invalidating, issues=all_issues)


class IdentifierParser:
    def __init__(self, rules: IdentifierRules) -> None:
        self._rules = rules

    @property
    def rules(self) -> IdentifierRules:
        return self._rules

    def parse(self, text: str, *, hint: IdentifierKind | None = None) -> ParsedIdentifier | None:
        """None when the text is no identifier at all; a ParsedIdentifier with
        valid=False when it is one, malformed (FR-ID-10)."""
        if not text.strip() or len(text) > MAX_TEXT_CHARS:
            return None
        forms = normalize(text)
        if len(forms.canonical) > MAX_TEXT_CHARS or not any(ch.isalnum() for ch in forms.canonical):
            return None
        for kind, parse in _ORDER:
            if hint is not None and kind is not hint:
                continue
            found = parse(forms.canonical, forms, self._rules)
            if found is not None:
                return _flag_text(found, text_issues(text))
        return None

    def tally(self, tokens: Iterable[str]) -> ConventionTally:
        conventions: Counter[Convention] = Counter()
        families: Counter[PlcFamily] = Counter()
        formats: Counter[str] = Counter()
        seen = parsed = 0
        for token in tokens:
            seen += 1
            found = self.parse(token)
            if found is None:
                continue
            parsed += 1
            if found.convention is not None:
                conventions[found.convention] += 1
            if found.plc is not None:
                families[found.plc.family] += 1
            if found.kind is IdentifierKind.CROSS_REFERENCE and found.variant is not None:
                formats[found.variant] += 1
        return ConventionTally(
            conventions=MappingProxyType(dict(sorted(conventions.items()))),
            plc_families=MappingProxyType(dict(sorted(families.items()))),
            cross_reference_formats=MappingProxyType(dict(sorted(formats.items()))),
            identifiers=parsed,
            tokens=seen,
        )
