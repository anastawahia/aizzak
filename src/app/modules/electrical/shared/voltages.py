"""FR-ID-08: voltage and net labels from the configurable list (L1, PE, +24V, 0V, ...)."""

from __future__ import annotations

from app.modules.electrical.shared.kinds import IdentifierKind, NormalizedForms, ParsedIdentifier
from app.modules.electrical.shared.rules import IdentifierRules


def parse_voltage(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    if canonical not in rules.voltage_labels:
        return None
    return ParsedIdentifier(
        kind=IdentifierKind.VOLTAGE,
        convention=None,
        forms=forms,
    )
