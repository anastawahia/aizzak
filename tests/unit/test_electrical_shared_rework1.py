"""QA re-check of PR-B after rework round 1: closed bugs, search-key collisions over a
grammar, and the digit fold for every Unicode decimal digit."""

from __future__ import annotations

import itertools
import re
import sys
import unicodedata

import pytest

from app.modules.electrical.shared.kinds import IdentifierKind
from app.modules.electrical.shared.normalization import canonical_form
from tests.unit.support_electrical_shared import PARSER, parse

K = IdentifierKind


def test_qa_b1_b3_b4_repros_are_closed() -> None:
    # B-1: Arabic-Indic digits no longer split the kinds
    assert parse("X\u0661:\u0661\u0664").forms.canonical == "X1:14"
    assert parse("KM\u0661").forms.search_keys == parse("KM1").forms.search_keys
    assert parse("I\u0660.\u0660").forms.search_keys == parse("I0.0").forms.search_keys
    # B-3: the voltage key keeps its sign, and is not the bare letter
    assert parse("L-").forms.search_keys == ("L-",)
    assert "L" not in parse("L-").forms.search_keys
    # B-4: a control character flags the identifier instead of dropping it
    found = parse("-K5\x00")
    assert found.valid is False and "text.hidden_character" in found.issues


def _nd_points() -> list[str]:
    return [
        chr(cp)
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) == "Nd" and not chr(cp).isascii()
    ]


def test_every_unicode_decimal_digit_folds_to_its_ascii_value() -> None:
    points = _nd_points()
    assert len(points) > 400  # Devanagari, Bengali, full-width, mathematical, ...
    for ch in points:
        d = str(unicodedata.decimal(ch))
        assert canonical_form("KM" + ch) == "KM" + d, hex(ord(ch))
        assert parse("X1:" + ch).forms.canonical == "X1:" + d, hex(ord(ch))
        assert parse("12." + ch).forms.canonical == "12." + d, hex(ord(ch))
    for ch in points[:: max(1, len(points) // 60)]:  # a spread of scripts for PLC kinds
        d = str(unicodedata.decimal(ch))
        found = PARSER.parse("I0." + ch)
        assert found is not None and found.forms.canonical == "I0." + d


def test_other_digit_like_characters_are_not_folded() -> None:
    # No (digits that are not decimal), e.g. superscript and circled numbers, go through
    # NFKC only: they either become ASCII or stay non-ASCII and are not an identifier
    for text in ("KM\u00b2", "KM\u2460", "KM\u2155", "KM\u4e00"):
        found = PARSER.parse(text)
        assert found is None or not found.forms.canonical.isascii() or found.kind is not None


# --- search keys over a grammar: nothing but the documented merges ----------------------

_BASE = ("X", "XT", "K", "KM", "L", "W", "U", "M", "I", "Q", "F")
_NUM = ("0", "1", "5", "10", "01", "001", "11", "14", "123", "114")
_SEP = ("", "-", ":", ".", "/", "~", "_")


def _tokens() -> set[str]:
    out: set[str] = set()
    for a, b in itertools.product(_NUM, _NUM):
        for s in (".", ",", "/", ":", "-", "_", ""):
            out.add(a + s + b)
            out.add(a + s + b + "-" + a)
    for letter, a, b in itertools.product(_BASE, _NUM, _NUM):
        for s in _SEP:
            out.add(f"{letter}{a}{s}{b}")
            out.add(f"-{letter}{a}{s}{b}")
            out.add(f"{letter}{a}:{b}~{letter}{a}:{a}")
            out.add(f"={letter}{a}-{letter}{b}")
            out.add(f"{letter}{a}-{b}")
    for lab in ("L1", "L2", "L3", "N", "PE", "M", "L+", "L-", "+24V", "-24V", "24V", "0V", "L1-5"):
        out.add(lab)
    return out


def _semantic(kind: K, canonical: str) -> str:
    c = canonical.lstrip("-%")
    c = re.sub(r"(?<![0-9])0+(?=[0-9])", "", c)  # leading zeros are merged on purpose
    if kind in (K.TERMINAL, K.BRIDGE):
        c = re.sub(r"[-.]", ":", c)  # `X1-5` / `X1.5` / `X1:5` are one terminal
    return c


_STRICT = {K.TERMINAL, K.BRIDGE, K.CROSS_REFERENCE, K.WIRE_NUMBER, K.VOLTAGE}


def test_no_search_key_merges_two_different_identifiers_over_a_grammar() -> None:
    owners: dict[str, set[tuple[K, str]]] = {}
    for text in sorted(_tokens()):
        found = PARSER.parse(text)
        if found is None or found.kind not in _STRICT:
            continue
        for key in found.forms.search_keys:
            owners.setdefault(key, set()).add(
                (found.kind, _semantic(found.kind, found.forms.canonical))
            )
    assert len(owners) > 1000
    bad = {k: sorted(v) for k, v in owners.items() if len(v) > 1}
    assert not bad, list(bad.items())[:10]


def test_a_strict_kind_never_shares_a_key_with_another_kind() -> None:
    owners: dict[str, set[K]] = {}
    for text in sorted(_tokens()):
        found = PARSER.parse(text)
        if found is None or found.kind is K.PLC_ADDRESS:
            continue
        if sum(a.prefix == "-" for a in found.aspects) > 1:
            continue  # QA-N1 (below): a chain of several `-` aspects
        for key in found.forms.search_keys:
            owners.setdefault(key, set()).add(found.kind)
    # the documented merges: a `-K5` designation, its `K5` tag and the NFPA/GB letter
    # code (reversed `5K`); a wire number or a cross-reference stays alone
    allowed = {K.REFERENCE_DESIGNATION, K.LETTER_CODE, K.CONNECTION_POINT, K.VOLTAGE}
    for key, kinds in owners.items():
        if len(kinds) > 1:
            if re.fullmatch(r"[A-Z]+[0-9]+", key):  # `-L10` / `L10`: the dash is not part of a tag
                allowed_here = allowed | {K.WIRE_NUMBER}
            elif re.fullmatch(r"[A-Z]+[0-9]+\.[0-9]+", key):  # `-X0.0` / `X0.0`: QA-N2
                allowed_here = allowed | {K.TERMINAL}
            else:
                allowed_here = allowed
            assert kinds <= allowed_here, (key, kinds)


def test_canonical_form_is_idempotent_and_ascii_digits_only() -> None:
    for text in (
        "\uff38\uff11\uff1a\uff11\uff14",
        "X\u0661\u0662",
        "\u2212K5",
        "KM\u202e1",
        "\uff2b\uff2d\u0e51",
    ):
        once = canonical_form(text)
        assert canonical_form(once) == once
        assert not any(unicodedata.category(c) == "Nd" and not c.isascii() for c in once)


@pytest.mark.parametrize("text", ["KM1\u0301", "K\u0301M1"])
def test_a_combining_mark_is_flagged_not_dropped(text: str) -> None:
    # INC1-Q-6: combining marks are not stripped; the identifier is reported invalid
    found = PARSER.parse(text)
    assert found is not None and not found.valid
    assert found.issues == ("text.non_ascii",)
    assert not found.forms.canonical.isascii()  # it never collides with the plain KM1


@pytest.mark.parametrize(
    ("device", "wire", "code"),
    [("-K5-13", "13", "K5"), ("-X1-5", "5", "X1"), ("-Q1-3", "3", "Q1"), ("=A1-K5-3", "3", "K5")],
)
def test_qa_n1_a_hyphen_chain_does_not_leak_a_bare_number_key(
    device: str, wire: str, code: str
) -> None:
    keys = set(parse(device).forms.search_keys)
    assert code in keys  # the device is still found by its product code
    assert keys.isdisjoint(parse(wire).forms.search_keys)
