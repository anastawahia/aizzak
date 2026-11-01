"""FR-ID-09: the normalised forms of an identifier. Pure and deterministic.

``original`` is the text exactly as it appeared (DATA-06). ``canonical`` folds
full-width characters (NFKC: a full-width ``KM1`` becomes ``KM1``, a full-width colon
``:``), unifies every dash to ``-``, drops whitespace and invisible format characters,
and upper-cases.
``search_keys`` are the forms a search may match on, most specific first: the
canonical form, without the leading ``-`` / ``%``, without leading zeros
(``F0001`` -> ``F1``), and without separators (``X1:14`` -> ``X114``).
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
_LEADING_ZEROS = re.compile(r"(?<!\d)0+(?=\d)")
_SEPARATORS = re.compile(r"[-.:/_]")


def canonical_form(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_DASHES)
    kept = "".join(ch for ch in folded if not ch.isspace() and unicodedata.category(ch) != "Cf")
    return kept.upper()


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
    compact = _SEPARATORS.sub("", no_zeros)
    return NormalizedForms(
        original=text,
        canonical=canonical,
        search_keys=dedupe([canonical, stripped, no_zeros, compact]),
    )


def with_keys(forms: NormalizedForms, *extra: str, only: bool = False) -> NormalizedForms:
    """The same forms with more search keys (or exactly those keys when ``only``)."""
    base: tuple[str, ...] = (forms.canonical,) if only else forms.search_keys
    return NormalizedForms(forms.original, forms.canonical, dedupe([*base, *extra]))
