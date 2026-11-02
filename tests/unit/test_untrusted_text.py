"""R1-4 (PR-B security review): text out of a document is escaped before it reaches a log or a
report. ``escape_untrusted_text`` keeps what Python calls printable and escapes the rest.

The hostile characters are built with ``chr`` so that no invisible character sits in this source.
"""

from __future__ import annotations

import pytest

from app.framework.observability.untrusted import escape_untrusted_text

NEL, NBSP, LS, PS = chr(0x85), chr(0xA0), chr(0x2028), chr(0x2029)
RLO, ZWSP, BOM, IDEOGRAPHIC_SPACE = chr(0x202E), chr(0x200B), chr(0xFEFF), chr(0x3000)
TAG_A, LONE_SURROGATE, GRIN = chr(0xE0041), chr(0xDC80), chr(0x1F600)
ARABIC = "".join(chr(code) for code in (0x643, 0x647, 0x631, 0x628, 0x627, 0x621))
CONTACTOR_ZH = "".join(chr(code) for code in (0x63A5, 0x89E6, 0x5668))


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("KM1", "KM1"),
        ("-K5:A1", "-K5:A1"),
        (ARABIC, ARABIC),
        (CONTACTOR_ZH + "KM1", CONTACTOR_ZH + "KM1"),
        ("a\nb", "a\\x0ab"),
        ("a\rb", "a\\x0db"),
        (f"a{NEL}b", "a\\x85b"),  # NEL: a line break to many log readers
        (f"a{LS}b", "a\\u2028b"),
        (f"a{PS}b", "a\\u2029b"),
        (f"K{RLO}1M", "K\\u202e1M"),  # right-to-left override
        (f"K{ZWSP}1", "K\\u200b1"),  # zero-width space
        (f"K{BOM}1", "K\\ufeff1"),  # BOM
        (f"K{NBSP}1", "K\\xa01"),  # no-break space looks like a space and is not one
        (f"K{IDEOGRAPHIC_SPACE}1", "K\\u30001"),
        ("a\\b", "a\\\\b"),  # a backslash is doubled so the escapes stay unambiguous
        (GRIN, GRIN),  # a printable astral character stays
        (TAG_A, "\\U000e0041"),  # a tag character is escaped
        (LONE_SURROGATE, "\\udc80"),  # a lone surrogate is escaped, not an encoding error
        ("\x00\x1b[31m", "\\x00\\x1b[31m"),
    ],
)
def test_unsafe_characters_are_escaped_and_the_rest_is_kept(raw: str, escaped: str) -> None:
    assert escape_untrusted_text(raw) == escaped


def test_the_result_has_no_line_break_or_control_character() -> None:
    hostile = "".join(chr(code) for code in range(0x110))
    result = escape_untrusted_text(hostile, limit=0x110)
    assert "\n" not in result and "\r" not in result
    assert all(ch.isprintable() for ch in result)


def test_a_long_text_is_cut_and_says_how_much_was_dropped() -> None:
    result = escape_untrusted_text("x" * 250)
    assert result == "x" * 200 + "...[+50 chars]"
    assert escape_untrusted_text("x" * 200) == "x" * 200
    assert escape_untrusted_text("abcdef", limit=3) == "abc...[+3 chars]"
    assert escape_untrusted_text("") == ""
