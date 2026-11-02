"""FR-ID-13: letter codes without an aspect prefix (``KM1``, ``1KM``, ``CR1``).

The code picks the convention (Chinese letter codes before NFPA/JIC when a code is in
both tables) and suggests the device class. The digits-first form (``1KM``, ``2QF``) is
a Chinese-code form only.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from app.modules.electrical.shared.connection_points import check_label, role_of
from app.modules.electrical.shared.kinds import (
    Convention,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.normalization import strip_leading_zeros, with_keys

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules


def pick_convention(rules: IdentifierRules, *candidates: Convention) -> Convention | None:
    """The first candidate the rules (as narrowed by the profile) have enabled."""
    for candidate in candidates:
        if candidate in rules.conventions:
            return candidate
    return None


def classify_code(
    rules: IdentifierRules, code: str, *, number_first: bool = False
) -> tuple[Convention, str] | None:
    """(convention, device class) of a letter code, or None when no enabled table has it."""
    if Convention.CN_LETTER_CODES in rules.conventions and code in rules.cn_letter_codes:
        return Convention.CN_LETTER_CODES, rules.cn_letter_codes[code]
    if not number_first and Convention.NFPA_JIC in rules.conventions and code in rules.nfpa_codes:
        return Convention.NFPA_JIC, rules.nfpa_codes[code]
    return None


def device_class_of(rules: IdentifierRules, code: str) -> str | None:
    """Device class for a code under any enabled convention (used by prefixed forms)."""
    found = classify_code(rules, code)
    return found[1] if found else None


@lru_cache(maxsize=16)
def _patterns(separator: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    tail = rf"(?:{re.escape(separator)}(?P<cp>.*))?"
    return (
        re.compile(rf"(?P<code>[A-Z]{{1,3}})(?P<num>\d{{1,4}}){tail}", re.DOTALL | re.ASCII),
        re.compile(rf"(?P<num>\d{{1,4}})(?P<code>[A-Z]{{1,3}}){tail}", re.DOTALL | re.ASCII),
    )


def parse_letter_code(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    letters_first, digits_first = _patterns(rules.connection_point_separator)
    number_first = False
    match = letters_first.fullmatch(canonical)
    if match is None:
        match = digits_first.fullmatch(canonical)
        number_first = match is not None
    if match is None:
        return None
    code, number, tail = match["code"], match["num"], match["cp"]
    found = classify_code(rules, code, number_first=number_first)
    if found is None:
        return None
    convention, device_class = found
    cp: str | None = None
    issues: tuple[str, ...] = ()
    if tail is not None:
        cp, issues = check_label(rules, tail)
    plain = strip_leading_zeros(number)
    keys = [f"{code}{plain}", f"{plain}{code}"]
    return ParsedIdentifier(
        kind=IdentifierKind.LETTER_CODE,
        convention=convention,
        forms=with_keys(forms, *keys),
        connection_point=cp,
        device_class=device_class,
        pole_role=role_of(rules, cp),
        valid=not issues,
        issues=issues,
    )
