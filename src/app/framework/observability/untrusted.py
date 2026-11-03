"""Text that came out of a document is untrusted when it reaches a log line or a report.

An identifier's ``original`` text (or a PDF's Creator / Producer) is whatever the file said. It can
hold a line break a log reader takes for a new record (``\\r``, ``\\x85``, U+2028), a bidi
override that reorders what a person reads (U+202E), a zero-width character that makes two
different strings look alike, or simply a million characters. ``escape_untrusted_text`` is what
every place that prints such text to a log, a Markdown report or an operator's terminal calls
(electrical-drawings PR-B security review, R1-4).

The rule is one line: a character is kept only when Python calls it printable (``str.isprintable``:
letters of any script, digits, punctuation, symbols and the ordinary space); everything else -- the
categories Cc, Cf, Cs, Co, Cn, Zl, Zp and every space other than U+0020 -- becomes ``\\xNN``,
``\\uNNNN`` or ``\\UNNNNNNNN``, and a backslash is doubled so the escapes stay unambiguous. The
result is capped, with the number of characters dropped stated.
"""

from __future__ import annotations

from typing import Final

DEFAULT_LIMIT: Final = 200
_BACKSLASH: Final = "\\"
_LATIN1: Final = 0xFF
_BMP: Final = 0xFFFF


def _escape(char: str) -> str:
    if char == _BACKSLASH:
        return _BACKSLASH * 2
    if char.isprintable():
        return char
    code = ord(char)
    if code <= _LATIN1:
        return f"\\x{code:02x}"
    if code <= _BMP:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def escape_untrusted_text(text: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """``text`` made safe to print on one line: escaped as above and cut to ``limit`` characters
    of INPUT (the output may be longer where a character became an escape sequence)."""
    shown = "".join(_escape(char) for char in text[:limit])
    dropped = len(text) - limit
    return shown if dropped <= 0 else f"{shown}...[+{dropped} chars]"


__all__ = ["DEFAULT_LIMIT", "escape_untrusted_text"]
