"""FR-ID-07: connection-point labels (A1/A2, 13/14, 95/96, ...) and their pole roles."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.modules.electrical.shared.kinds import IdentifierKind, NormalizedForms, ParsedIdentifier

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

_LABEL = re.compile(r"[A-Z0-9]{1,6}", re.ASCII)


def is_valid_label(label: str) -> bool:
    return _LABEL.fullmatch(label) is not None


def role_of(rules: IdentifierRules, label: str | None) -> str | None:
    """The pole role a standard label names (``A1`` -> ``coil``), else None."""
    if label is None:
        return None
    return rules.connection_point_roles.get(label.upper())


def check_label(rules: IdentifierRules, tail: str) -> tuple[str | None, tuple[str, ...]]:
    """The label after the separator: (label, issues). A bad label is flagged, not lost."""
    sep = rules.connection_point_separator
    if not tail:
        return None, ("connection_point.empty",)
    if sep in tail:
        return None, ("connection_point.multiple",)
    if not is_valid_label(tail):
        return None, ("connection_point.malformed",)
    return tail, ()


def parse_connection_point(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    """A bare standard label. Labels that are also plain numbers (``13``, ``95``) read as
    wire numbers unless the caller hints ``CONNECTION_POINT``."""
    role = role_of(rules, canonical)
    if role is None:
        return None
    return ParsedIdentifier(
        kind=IdentifierKind.CONNECTION_POINT,
        convention=None,
        forms=forms,
        connection_point=canonical,
        pole_role=role,
    )
