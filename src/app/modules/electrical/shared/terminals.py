"""FR-ID-03: terminals and the bridges between them.

Terminal forms: ``-X1:14`` / ``=A1+P2-X1:14`` (aspect chain whose product is a terminal
strip, then the terminal), ``X1.14``, ``X1-14``, ``XT1:5`` (strip, separator, terminal)
and ``1XT-5`` (digits-first Chinese strip code). Whatever the form, the search keys
include the unified ``STRIP:TERMINAL`` key, so ``X1.14`` and ``-X1:14`` meet.

Bridges are not in the SRS appendix. This kernel reads ``<terminal>~<terminal>`` (the
right side may be a bare terminal of the same strip): ``X1:1~X1:2``, ``X1:1~2``. Any
other bridge notation is a rule extension, not a silent guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.modules.electrical.shared.connection_points import check_label, is_valid_label
from app.modules.electrical.shared.kinds import (
    Aspect,
    Convention,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.letter_codes import pick_convention
from app.modules.electrical.shared.normalization import strip_leading_zeros, with_keys
from app.modules.electrical.shared.reference_designation import split_chain
from app.modules.electrical.shared.rules import IdentifierRules

BRIDGE_SEPARATOR = "~"
_CHARSET = re.compile(r"[A-Z0-9_=+\-&#.:/~]+")


@dataclass(frozen=True, slots=True)
class _Terminal:
    aspects: tuple[Aspect, ...]
    strip: str
    point: str | None
    convention: Convention
    device_class: str
    issues: tuple[str, ...]

    def unified(self, separator: str) -> str:
        return f"{self.strip}{separator}{self.point}" if self.point else self.strip


@lru_cache(maxsize=32)
def _patterns(prefixes: tuple[str, ...], separator: str) -> tuple[re.Pattern[str], ...]:
    alt = "|".join(re.escape(p) for p in sorted(prefixes, key=lambda p: (-len(p), p)))
    glue = rf"[.\-{re.escape(separator)}]"
    return (
        re.compile(rf"(?P<strip>(?:{alt})\d{{1,4}}){glue}(?P<point>.*)"),
        re.compile(rf"(?P<strip>\d{{1,4}}(?:{alt})){glue}(?P<point>.*)"),
        re.compile(rf"(?:{alt})\d{{1,4}}"),
        re.compile(rf"(?P<prefix>{alt})(?P<num>\d{{1,4}})"),
    )


def _device_class(prefix: str) -> str:
    return "terminal_block" if prefix == "TB" else "terminal_strip"


def _split_chain(canonical: str, rules: IdentifierRules) -> _Terminal | None:
    """``-X1:14`` / ``=A1+P2-X1:14``; the caller has checked the text starts with an aspect."""
    chain = split_chain(canonical, rules)
    iec = pick_convention(rules, Convention.IEC_81346, Convention.GBT_5094)
    if chain is None or chain.connection_point is None or chain.issues or not chain.aspects:
        return None
    last = chain.aspects[-1]
    whole = _patterns(rules.terminal_prefixes, rules.connection_point_separator)[2]
    if last.prefix != "-" or whole.fullmatch(last.code) is None or iec is None:
        return None
    prefix = _prefix_of(last.code, rules)
    return _Terminal(
        chain.aspects, last.code, chain.connection_point, iec, _device_class(prefix), ()
    )


def _split(canonical: str, rules: IdentifierRules) -> _Terminal | None:
    if not rules.terminal_prefixes:
        return None
    if canonical.startswith(rules.all_aspects):
        return _split_chain(canonical, rules)
    bare, digits_first, _, _ = _patterns(rules.terminal_prefixes, rules.connection_point_separator)
    number_first = False
    match = bare.fullmatch(canonical)
    if match is None:
        match = digits_first.fullmatch(canonical)
        number_first = match is not None
    if match is None:
        return None
    strip = match["strip"]
    prefix = _prefix_of(strip, rules)
    if number_first or prefix == "XT":
        convention = pick_convention(rules, Convention.CN_LETTER_CODES)
    elif prefix == "TB":
        convention = pick_convention(rules, Convention.NFPA_JIC)
    else:
        convention = pick_convention(rules, Convention.IEC_81346, Convention.GBT_5094)
    if convention is None:
        return None
    point, issues = check_label(rules, match["point"])
    return _Terminal((), strip, point, convention, _device_class(prefix), issues)


def _prefix_of(strip: str, rules: IdentifierRules) -> str:
    letters = "".join(ch for ch in strip if ch.isalpha())
    return letters if letters in rules.terminal_prefixes else ""


def _keys(unified: str) -> tuple[str, ...]:
    return (unified, strip_leading_zeros(unified))


def parse_terminal(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    if _CHARSET.fullmatch(canonical) is None or BRIDGE_SEPARATOR in canonical:
        return None
    found = _split(canonical, rules)
    if found is None or (found.point is None and not found.issues):
        return None
    separator = rules.connection_point_separator
    return ParsedIdentifier(
        kind=IdentifierKind.TERMINAL,
        convention=found.convention,
        forms=with_keys(forms, *_keys(found.unified(separator))),
        aspects=found.aspects,
        connection_point=found.point,
        device_class=found.device_class,
        valid=not found.issues,
        issues=found.issues,
    )


def parse_bridge(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    if canonical.count(BRIDGE_SEPARATOR) != 1 or _CHARSET.fullmatch(canonical) is None:
        return None
    left_text, right_text = canonical.split(BRIDGE_SEPARATOR)
    left = _split(left_text, rules)
    if left is None or (left.point is None and not left.issues):
        return None
    separator = rules.connection_point_separator
    issues = list(left.issues)
    right = _split(right_text, rules)
    right_point: str | None
    right_unified: str
    if right is not None and (right.point is not None or right.issues):
        issues.extend(right.issues)
        right_point = right.point
        right_unified = right.unified(separator)
    elif is_valid_label(right_text):
        right_point = right_text
        right_unified = f"{left.strip}{separator}{right_text}"
    else:
        issues.append("bridge.right_not_terminal")
        right_point = None
        right_unified = right_text
    point = f"{left.point}{BRIDGE_SEPARATOR}{right_point}" if left.point and right_point else None
    unified = f"{left.unified(separator)}{BRIDGE_SEPARATOR}{right_unified}"
    return ParsedIdentifier(
        kind=IdentifierKind.BRIDGE,
        convention=left.convention,
        forms=with_keys(forms, *_keys(unified)),
        aspects=left.aspects,
        connection_point=point,
        device_class=left.device_class,
        valid=not issues,
        issues=tuple(issues),
    )
