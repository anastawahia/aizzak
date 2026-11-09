"""FR-ID-15: KKS plant designations (``20XYZ10AB123``), by STRUCTURE only (D-36).

The structure is the rules' (``[kks]`` in ``identifiers.toml``): position masks per part and the
forms (part sequences) in file order. No key catalogue and no meaning is embedded or looked
up (NFR-LIC-05). The patterns are built here from the validated masks and nothing else: no text
from a document, a profile or the rules file is ever compiled as a pattern (NFR-F2-3), and the
patterns are linear (fixed-length runs, no nested repetition).

KKS is additive: it is read only when ``Convention.KKS`` is enabled. A text that has the OUTER
shape of a tag but misses the structure is reported invalid (``kks.malformed``,
``kks.letter_in_digit_position``, ``kks.digit_in_letter_position``), never dropped and never
corrected (the correction is FR-ID-18, a separate step).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Final

from app.modules.electrical.shared.kinds import (
    Convention,
    IdentifierKind,
    KksPart,
    KksTag,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.normalization import with_keys

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules
    from app.modules.electrical.shared.rules.systems import KksRules

ISSUE_MALFORMED: Final = "kks.malformed"
ISSUE_LETTER_IN_DIGIT: Final = "kks.letter_in_digit_position"
ISSUE_DIGIT_IN_LETTER: Final = "kks.digit_in_letter_position"
ISSUE_EXCLUDED_LETTER: Final = "kks.excluded_letter"
_CHARSET: Final = re.compile(r"[=+]{0,2}[0-9A-Z]+", re.ASCII)
# The outer shape of a near-miss: digits, letters, digits, then letter/digit groups.
_OUTER: Final = re.compile(r"\d{1,3}[A-Z]{1,4}\d{1,3}(?:[A-Z]{1,3}\d{1,4}[A-Z]?){0,3}", re.ASCII)
_RUN: Final = re.compile(r"[0-9]+|[A-Z]+", re.ASCII)

# One form as a hashable spec: (form name, ((part name, mask), ...)).
_Spec = tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


def _digit_class(extra: str) -> str:
    return f"[0-9{extra}]"


def _letter_class(extra: str) -> str:
    return f"[A-Z{extra}]"


@lru_cache(maxsize=32)
def _patterns(spec: _Spec, digit_extra: str, letter_extra: str) -> tuple[re.Pattern[str], ...]:
    """One pattern per form, built from the masks alone. ``digit_extra`` / ``letter_extra`` are
    the confusable characters a digit / letter position also takes (the reason probe only)."""
    digit, letter = _digit_class(digit_extra), _letter_class(letter_extra)
    atoms = {"N": digit, "A": letter, "n": f"{digit}?", "a": f"{letter}?"}
    out: list[re.Pattern[str]] = []
    for _, parts in spec:
        groups = (
            f"(?P<p{i}>{''.join(atoms[c] for c in mask)})" for i, (_, mask) in enumerate(parts)
        )
        out.append(re.compile("".join(groups), re.ASCII))
    return tuple(out)


def _spec(kks: KksRules) -> _Spec:
    return tuple((f.name, tuple((p, kks.parts[p].mask) for p in f.parts)) for f in kks.forms)


def _shortest(kks: KksRules) -> int:
    """The fewest characters any form needs (mandatory positions only)."""
    return min(
        sum(sum(1 for c in kks.parts[p].mask if c in "NA") for p in f.parts) for f in kks.forms
    )


def _split_prefix(canonical: str, kks: KksRules) -> tuple[str | None, str | None, str]:
    for kind, symbol in sorted(kks.prefixes.items(), key=lambda item: (-len(item[1]), item[0])):
        if canonical.startswith(symbol):
            return symbol, kind, canonical[len(symbol) :]
    return None, None, canonical


def _tag(
    index: int, match: re.Match[str], kks: KksRules, prefix: str | None, prefix_kind: str | None
) -> KksTag:
    form = kks.forms[index]
    parts = tuple(
        KksPart(
            name=name,
            level=kks.parts[name].level,
            text=match[f"p{i}"],
            runs=tuple(_RUN.findall(match[f"p{i}"])),
        )
        for i, name in enumerate(form.parts)
    )
    return KksTag(form.name, form.kks_class, prefix, prefix_kind, parts)


def _near_miss_reasons(rest: str, rules: IdentifierRules) -> tuple[str, ...]:
    """Why a tag-shaped text misses the structure: a confusable letter where a digit belongs,
    a confusable digit where a letter belongs (both may hold), or neither (malformed)."""
    spec = _spec(rules.kks)
    digits = "".join(digit for digit, _ in rules.confusables)
    letters = "".join(letter for _, letter in rules.confusables)
    reasons: list[str] = []
    for issue, d_extra, l_extra in (
        (ISSUE_LETTER_IN_DIGIT, letters, ""),
        (ISSUE_DIGIT_IN_LETTER, "", digits),
    ):
        if (d_extra or l_extra) and any(
            p.fullmatch(rest) for p in _patterns(spec, d_extra, l_extra)
        ):
            reasons.append(issue)
    return tuple(reasons) or (ISSUE_MALFORMED,)


def parse_kks_tag(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    if Convention.KKS not in rules.conventions or _CHARSET.fullmatch(canonical) is None:
        return None
    kks = rules.kks
    prefix, prefix_kind, rest = _split_prefix(canonical, kks)
    if not rest[:1].isdigit():
        return None
    for index, pattern in enumerate(_patterns(_spec(kks), "", "")):
        match = pattern.fullmatch(rest)
        if match is not None:
            tag = _tag(index, match, kks, prefix, prefix_kind)
            excluded = any(
                ch in kks.excluded_letters for part in tag.parts for ch in part.text if ch.isalpha()
            )
            return ParsedIdentifier(
                kind=IdentifierKind.KKS_TAG,
                convention=Convention.KKS,
                forms=with_keys(forms, rest, only=True),
                valid=not excluded,
                issues=(ISSUE_EXCLUDED_LETTER,) if excluded else (),
                kks=tag,
            )
    if prefix is not None or _OUTER.fullmatch(rest) is None or len(rest) < _shortest(kks):
        return None
    return ParsedIdentifier(
        kind=IdentifierKind.KKS_TAG,
        convention=Convention.KKS,
        forms=with_keys(forms, rest, only=True),
        valid=False,
        issues=_near_miss_reasons(rest, rules),
    )
