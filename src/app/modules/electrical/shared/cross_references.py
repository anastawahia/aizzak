"""FR-ID-06: cross-reference formats (``/12.3``, ``12.3``, ``=A1/12.3``, ``12/3``) and the
dominant format of a bag of tokens."""

from __future__ import annotations

import re

from app.modules.electrical.shared.kinds import (
    ConventionTally,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.rules import IdentifierRules


def parse_cross_reference(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    for name, pattern in rules.cross_reference_formats.items():
        if re.fullmatch(pattern, canonical):
            return ParsedIdentifier(
                kind=IdentifierKind.CROSS_REFERENCE,
                convention=None,
                forms=forms,
                variant=name,
            )
    return None


def dominant_format(tally: ConventionTally) -> str | None:
    """The most frequent format; a tie goes to the alphabetically first name; None when
    no token was a cross-reference."""
    counts = tally.cross_reference_formats
    if not counts:
        return None
    return min(counts, key=lambda name: (-counts[name], name))
