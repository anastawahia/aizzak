"""FR-ID-20 (F3): a number with a unit of ``[ratings]`` is a rating, never an identifier.

``37kV``, ``47Hz``, ``17VA``, ``7-29kV``, ``0.7kV``: a value and its unit. The units are the
rules' (data) and are compared by MEMBERSHIP in a set, never compiled into a pattern (NFR-F3-4);
the one pattern here is fixed and linear. A unit whose upper-case form is a letter code an
enabled table has (``kV`` and ``KV``, ``kA`` and ``KA``) matches only in the rules' own spelling:
``37kV`` is a rating, ``37KV`` is a voltage relay and ``3KA`` a relay. Any other unit matches in
any case (``7KW`` is a rating).

A rating is a value of its own (``Rating``), outside ``IdentifierKind``: the parser uses it as a
barrier, and understanding a report's numbers (FR-NLU-02, DATA-05) reads it later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.modules.electrical.shared.kinds import Convention

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

_NUMBER: Final = r"\d{1,6}(?:\.\d{1,3})?"
_RATING: Final = re.compile(
    rf"(?P<value>{_NUMBER}(?:[-~]{_NUMBER})?)(?P<unit>[A-Za-z]{{1,5}})", re.ASCII
)


@dataclass(frozen=True, slots=True)
class Rating:
    value: str  # "37", "0.7", "7-29" as written (DATA-05 converts it, later)
    unit: str  # the rules' spelling: "kV"


def _is_enabled_letter_code(code: str, rules: IdentifierRules) -> bool:
    return (Convention.CN_LETTER_CODES in rules.conventions and code in rules.cn_letter_codes) or (
        Convention.NFPA_JIC in rules.conventions and code in rules.nfpa_codes
    )


def read_rating(folded: str, rules: IdentifierRules) -> Rating | None:
    """A number, or two joined by ``-`` or ``~``, then a unit of ``[ratings]``. ``folded`` is the
    text NFKC-folded WITHOUT upper-casing (``normalization.folded``). Linear: one bounded
    pattern, then set lookups."""
    match = _RATING.fullmatch(folded)
    if match is None:
        return None
    written, upper = match["unit"], match["unit"].upper()
    if upper not in rules.ratings.folded_units:
        return None
    if written in rules.ratings.units:
        return Rating(match["value"], written)
    if _is_enabled_letter_code(upper, rules):
        return None  # 3KA, 37KV: the shape of a letter code, not written as the rules' unit
    unit = next(u for u in rules.ratings.units if u.upper() == upper)
    return Rating(match["value"], unit)
