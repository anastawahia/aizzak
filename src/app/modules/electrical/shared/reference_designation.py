"""FR-ID-01: IEC 81346 / GB/T 5094 reference designations with their aspects.

``=A1+P2-K5``, ``==PLANT=A1++SITE+P2-K5``, ``-K5:A1``: a chain of aspect elements (a
prefix from the rules and a code), then an optional connection point after ``:``.
The grammar is identical for IEC and GB/T, so the convention is the first of the two
the rules enable. Text that starts like a designation but is malformed is flagged
(FR-ID-10), never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from app.modules.electrical.shared.connection_points import check_label, role_of
from app.modules.electrical.shared.kinds import (
    Aspect,
    Convention,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.letter_codes import device_class_of, pick_convention
from app.modules.electrical.shared.normalization import with_keys

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

_PROSE = re.compile(r"[A-Z0-9_=+\-&#.:/]+", re.ASCII)
_LEADING_LETTERS = re.compile(r"[A-Z]+", re.ASCII)


@dataclass(frozen=True, slots=True)
class Chain:
    aspects: tuple[Aspect, ...]
    connection_point: str | None
    issues: tuple[str, ...]


@lru_cache(maxsize=32)
def _element(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(p) for p in sorted(prefixes, key=lambda p: (-len(p), p)))
    return re.compile(rf"({alternatives})([A-Z0-9_]+)", re.ASCII)


def split_chain(canonical: str, rules: IdentifierRules) -> Chain | None:
    """The aspect chain and connection point of ``canonical``; None when the text does not
    start with an aspect prefix at all."""
    head, has_separator, tail = canonical.partition(rules.connection_point_separator)
    prefixes = rules.all_aspects
    if not head.startswith(prefixes):
        return None
    issues: list[str] = []
    connection_point: str | None = None
    if has_separator:
        connection_point, label_issues = check_label(rules, tail)
        issues.extend(label_issues)
    pattern = _element(prefixes)
    aspects: list[Aspect] = []
    position = 0
    while position < len(head):
        match = pattern.match(head, position)
        if match is None:
            issues.append("aspect.malformed")
            break
        aspects.append(Aspect(match[1], match[2]))
        position = match.end()
    return Chain(tuple(aspects), connection_point, tuple(issues))


def parse_reference_designation(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    if _PROSE.fullmatch(canonical) is None:
        return None
    chain = split_chain(canonical, rules)
    if chain is None:
        return None
    convention: Convention | None = pick_convention(
        rules, Convention.IEC_81346, Convention.GBT_5094
    )
    if convention is None:
        return None
    # the product aspect: the last ``-`` aspect whose code is a product code (starts with a
    # letter); a trailing number (``-K5-13``) is a sub-element and never a device key
    product = next(
        (a for a in reversed(chain.aspects) if a.prefix == "-" and a.code[:1].isalpha()), None
    )
    device_class: str | None = None
    keys: list[str] = []
    if product is not None:
        letters = _LEADING_LETTERS.match(product.code)
        device_class = device_class_of(rules, letters[0]) if letters else None
        keys.append(product.code)
        if chain.connection_point:
            keys.append(f"{product.code}{rules.connection_point_separator}{chain.connection_point}")
    return ParsedIdentifier(
        kind=IdentifierKind.REFERENCE_DESIGNATION,
        convention=convention,
        forms=with_keys(forms, *keys),
        aspects=chain.aspects,
        connection_point=chain.connection_point,
        device_class=device_class,
        pole_role=role_of(rules, chain.connection_point),
        valid=not chain.issues,
        issues=chain.issues,
    )
