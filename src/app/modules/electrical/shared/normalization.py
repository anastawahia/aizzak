"""FR-ID-09: the normalised forms of an identifier. Pure and deterministic.

``original`` is the text exactly as it appeared (DATA-06, NFR-I18N-03). ``canonical``
folds full-width characters (NFKC: a full-width ``KM1`` becomes ``KM1``, a full-width
colon ``:``), folds every Unicode decimal digit to ASCII (``KM`` + U+0661 -> ``KM1``),
unifies every dash to ``-``, drops whitespace and hidden characters (control, format and
default-ignorable code points), and upper-cases.

``search_keys`` are the forms a search may match on, most specific first: the canonical
form, without the leading ``-`` / ``%``, without leading zeros (``F0001`` -> ``F1``) and,
for a plain tag, without the separator between its letters and digits (T-21: ``F0001`` =
``F 0001`` = ``F-0001``). Each kind's parser adds only the keys that are safe for it (the
unified ``STRIP:POINT`` of a terminal, ``KM1`` / ``1KM`` of a letter code, the PLC
spellings). A separator is NEVER dropped from a key: ``X1:14`` and ``X11:4`` must not
meet, nor ``12.3`` and wire ``123``.

**Identity is (``kind``, ``canonical``).** ``search_keys`` are for retrieval only (S-6):
two different identifiers may legitimately share a key (``-K5`` and ``K5``), so a key is
never a join key or an equality test.
"""

from __future__ import annotations

import re
import unicodedata

from app.modules.electrical.shared.kinds import NormalizedForms

# hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar, minus sign,
# small em dash, small hyphen-minus, full-width hyphen-minus (code points, not literals)
_DASHES = dict.fromkeys(
    (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D), "-"
)
_LEADING_ZEROS = re.compile(r"(?<!\d)0+(?=\d)", re.ASCII)
# a plain tag: letters, ONE separator, digits (``F-0001``); T-21 says it equals ``F0001``
_TAG = re.compile(r"(?P<letters>[A-Z]+)[-._](?P<digits>[0-9]+)", re.ASCII)

# Bidirectional controls: LRM, RLM, ALM, LRE..RLO, LRI..PDI. Dropped from the canonical
# form, but their presence is reported (S-3), because they make the displayed order differ
# from the logical one.
_BIDI = frozenset((0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A)))
# Default_Ignorable_Code_Point outside the Cc/Cf categories, plus the blank-looking
# Braille pattern: invisible, so they must neither hide an identifier nor change it.
_IGNORABLE = frozenset(
    (
        0x034F,  # combining grapheme joiner
        0x115F, 0x1160,  # Hangul choseong / jungseong fillers
        0x17B4, 0x17B5,  # Khmer inherent vowels
        *range(0x180B, 0x1810),  # Mongolian free variation selectors, vowel separator
        0x2800,  # Braille pattern blank
        0x3164, 0xFFA0,  # Hangul / half-width Hangul filler
        *range(0xFE00, 0xFE10),  # variation selectors
        *range(0xFFF0, 0xFFF9),
        *range(0x1BCA0, 0x1BCA4),
        *range(0x1D173, 0x1D17B),
        *range(0xE0000, 0xE1000),  # tags and variation selectors supplement
    )
)  # fmt: skip

ISSUE_HIDDEN = "text.hidden_character"
ISSUE_BIDI = "text.bidi_control"
NON_INVALIDATING_ISSUES = frozenset({ISSUE_BIDI})  # flagged, but the identifier stays valid


def _is_hidden(ch: str) -> bool:
    """Invisible and not whitespace: control (Cc), format (Cf) or default-ignorable."""
    code = ord(ch)
    if ch.isspace():
        return False
    return unicodedata.category(ch) in ("Cc", "Cf") or code in _IGNORABLE


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return "".join(
        ch
        if ch.isascii()
        else (str(unicodedata.decimal(ch)) if unicodedata.category(ch) == "Nd" else ch)
        for ch in folded
    )


def canonical_form(text: str) -> str:
    kept = "".join(ch for ch in _fold(text) if not ch.isspace() and not _is_hidden(ch))
    return kept.upper()


def text_issues(text: str) -> tuple[str, ...]:
    """Reasons the TEXT (not the identifier) is suspect: hidden characters were dropped
    (invalidating, FR-ID-10: flagged, never silently dropped) or bidi controls were
    present (reported, not invalidating)."""
    issues: list[str] = []
    if any(_is_hidden(ch) and ord(ch) not in _BIDI for ch in _fold(text)):
        issues.append(ISSUE_HIDDEN)
    if any(ord(ch) in _BIDI for ch in text):
        issues.append(ISSUE_BIDI)
    return tuple(issues)


def strip_leading_zeros(text: str) -> str:
    """``F0001`` -> ``F1``, ``10.05`` -> ``10.5``; a lone ``0`` (as in ``0V``) stays."""
    return _LEADING_ZEROS.sub("", text)


def dedupe(keys: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Drop empty keys and repeats, keeping first-seen order."""
    seen: dict[str, None] = {}
    for key in keys:
        if key:
            seen.setdefault(key)
    return tuple(seen)


def normalize(text: str) -> NormalizedForms:
    canonical = canonical_form(text)
    stripped = canonical.lstrip("-%")
    no_zeros = strip_leading_zeros(stripped)
    tag = _TAG.fullmatch(stripped)
    tag_key = f"{tag['letters']}{strip_leading_zeros(tag['digits'])}" if tag else ""
    return NormalizedForms(
        original=text,
        canonical=canonical,
        search_keys=dedupe([canonical, stripped, no_zeros, tag_key]),
    )


def with_keys(forms: NormalizedForms, *extra: str, only: bool = False) -> NormalizedForms:
    """The same forms with more search keys (or exactly those keys when ``only``)."""
    base: tuple[str, ...] = (forms.canonical,) if only else forms.search_keys
    return NormalizedForms(forms.original, forms.canonical, dedupe([*base, *extra]))
