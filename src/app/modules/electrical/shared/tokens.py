"""F3 (gap 34): the identifier candidates a drawing's word holds. Stdlib only, a leaf module.

A word the text layer yields is not always one identifier: an ideographic full stop closes a
sentence, a semicolon (ASCII or full-width) separates two tags, brackets wrap a tag
(``(20XYZ)``), and a list holds its items (``X7:41,42,43``). ``split_candidates`` cuts a word at
whitespace and at the sentence breaks, then strips the opening brackets and quotes from the front
of each piece and the closing ones and the sentence marks from its end. A comma and the
ideographic comma stay inside a piece: they separate the items of ONE list, not words (FR-ID-19).

A prime or a double prime right after a digit stays (``1X-37'`` is a terminal whose point is
``37'``), unless the piece opened with the same quote (``'QS7'`` is a quoted tag). Linear.
"""

from __future__ import annotations

from typing import Final

# U+3002 ideographic full stop, U+FF61 half-width ideographic full stop, U+FF1B full-width
# semicolon, and the ASCII semicolon
SENTENCE_BREAKS: Final = frozenset("\u3002\uff61\uff1b;")
# opening: ( [ { U+FF08 U+3010 U+300C U+300E U+300A U+3008 " ' U+201C U+2018
OPENERS: Final = "([{\uff08\u3010\u300c\u300e\u300a\u3008\"'\u201c\u2018"
# closing: ) ] } U+FF09 U+3011 U+300D U+300F U+300B U+3009 " ' U+201D U+2019, then the sentence
# marks . , ; : U+FF0C U+3001 U+3002 U+FF1B U+FF1A U+FF61
CLOSERS: Final = (
    ")]}\uff09\u3011\u300d\u300f\u300b\u3009\"'\u201d\u2019.,;:\uff0c\u3001\u3002\uff1b\uff1a\uff61"
)
PRIMES: Final = frozenset("'\"\u2032\u2033")
_ASCII_QUOTES: Final = frozenset("'\"")


def _pieces(word: str) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    for ch in word:
        if ch.isspace() or ch in SENTENCE_BREAKS:
            if current:
                pieces.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        pieces.append("".join(current))
    return pieces


def _strip(piece: str) -> str:
    start = 0
    while start < len(piece) and piece[start] in OPENERS:
        start += 1
    opened = piece[:start]
    end = len(piece)
    while end > start and piece[end - 1] in CLOSERS:
        last = piece[end - 1]
        keeps_prime = (
            last in PRIMES
            and end - 1 > start
            and piece[end - 2].isdigit()
            and not (last in _ASCII_QUOTES and last in opened)
        )
        if keeps_prime:
            break
        end -= 1
    return piece[start:end]


def split_candidates(word: str) -> tuple[str, ...]:
    """The identifier candidates of ``word`` (see the module docstring); empty pieces are
    dropped."""
    return tuple(piece for piece in map(_strip, _pieces(word)) if piece)
