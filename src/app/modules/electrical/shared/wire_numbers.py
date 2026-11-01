"""FR-ID-04: wire numbers by configurable patterns (sequential, page/column, voltage-based).

The patterns are the rules' (selected by name in the profile); the first match in file
order wins, and its name is the identifier's ``variant``.
"""

from __future__ import annotations

import re

from app.modules.electrical.shared.kinds import IdentifierKind, NormalizedForms, ParsedIdentifier
from app.modules.electrical.shared.rules import IdentifierRules


def parse_wire_number(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    for name, pattern in rules.wire_number_patterns.items():
        if re.fullmatch(pattern, canonical):
            return ParsedIdentifier(
                kind=IdentifierKind.WIRE_NUMBER,
                convention=None,
                forms=forms,
                variant=name,
            )
    return None
