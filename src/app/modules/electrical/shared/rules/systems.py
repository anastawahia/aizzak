"""KKS and ANSI rule tables (FR-ID-15, FR-ID-16, D-36): the STRUCTURE of a plant designation
and the numbers, suffixes and OUR class names of device functions, as VALUES.

Nothing here is a key catalogue or the standards' text (NFR-LIC-05). Nothing a profile or the
rules file says ever becomes a regular expression (NFR-F2-3): a KKS mask is validated against
``[NA]+[na]*`` and only then turned into a pattern by ``kks.py``, and an ANSI suffix is compared
by membership in a set.

Imports ``kinds``, ``normalization`` and ``rules.errors`` only, so the package can import it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.modules.electrical.shared.kinds import DEVICE_CONVENTIONS, Convention, KksClass
from app.modules.electrical.shared.normalization import canonical_form, text_issues
from app.modules.electrical.shared.rules.errors import RuleError

MAX_KKS_PARTS: Final = 8
MAX_KKS_FORMS: Final = 16
MAX_PARTS_PER_FORM: Final = 4
MAX_MASK_CHARS: Final = 8
MAX_ANSI_SUFFIX_CHARS: Final = 3
MAX_ANSI_COMPOUND: Final = 3
MAX_EXTRA_ANSI_SUFFIXES: Final = 64
MAX_RAW_ANSI_SUFFIX_CHARS: Final = 64  # checked before normalisation (NFKC can expand)
MAX_ANSI_FUNCTIONS: Final = 99
MAX_KKS_PREFIXES: Final = 4
MAX_CONFUSABLE_PAIRS: Final = 16
MAX_PREFIX_CHARS: Final = 2
PAIR_CHARS: Final = 2
CLASS_NAME: Final = re.compile(r"[a-z][a-z_]{0,39}", re.ASCII)  # NFR-F2-6
_NAME: Final = re.compile(r"[a-z][a-z_]{0,31}", re.ASCII)
_MASK: Final = re.compile(r"[NA]+[na]*", re.ASCII)
_SUFFIX: Final = re.compile(r"[A-Z]{1,3}", re.ASCII)
_PREFIX_CHARS: Final = frozenset("=+")
_KKS_KEYS: Final = frozenset({"excluded_letters", "prefixes", "parts", "forms"})
_ANSI_KEYS: Final = frozenset({"max_compound", "suffixes", "functions"})
_PART_KEYS: Final = frozenset({"level", "mask"})
_FORM_KEYS: Final = frozenset({"name", "parts", "class"})


@dataclass(frozen=True, slots=True)
class KksPartRule:
    name: str
    level: int | None  # 1..3, or None (a cable number)
    mask: str  # "NNAAANN"


@dataclass(frozen=True, slots=True)
class KksForm:
    name: str
    parts: tuple[str, ...]  # part names, in order
    kks_class: KksClass


@dataclass(frozen=True, slots=True)
class KksRules:
    prefixes: Mapping[str, str]  # kind name -> symbol, e.g. "process" -> "="
    parts: Mapping[str, KksPartRule]
    forms: tuple[KksForm, ...]  # file order: the first full match wins
    excluded_letters: frozenset[str]


@dataclass(frozen=True, slots=True)
class AnsiRules:
    suffixes: frozenset[str]  # the packaged ones, plus the profile's after narrowing
    functions: Mapping[int, str]  # number -> our class name
    max_compound: int  # 2..3


def _table(data: Mapping[str, object], key: str, where: str) -> Mapping[str, object]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise RuleError(f"[{where}] table [{key}] is missing")
    return value


def _only_keys(data: Mapping[str, object], allowed: frozenset[str], where: str) -> None:
    for key in data:
        if key not in allowed:
            raise RuleError(f"[{where}] unknown key {str(key)[:40]!r}")


def _name(value: object, where: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise RuleError(f"[{where}] name must match {_NAME.pattern}")
    return value


def _list(data: Mapping[str, object], key: str, where: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list | tuple):
        raise RuleError(f"[{where}] {key!r} must be a list")
    return list(value)


def _int(value: object, where: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RuleError(f"[{where}] must be an integer from {low} to {high}")
    return value


def _kks_parts(data: Mapping[str, object]) -> Mapping[str, KksPartRule]:
    raw = _table(data, "parts", "kks")
    if not raw or len(raw) > MAX_KKS_PARTS:
        raise RuleError(f"[kks.parts] needs 1..{MAX_KKS_PARTS} parts")
    parts: dict[str, KksPartRule] = {}
    for name, entry in raw.items():
        _name(name, "kks.parts")
        if not isinstance(entry, Mapping):
            raise RuleError(f"[kks.parts] {name!r} must be a table")
        _only_keys(entry, _PART_KEYS, f"kks.parts.{name}")
        mask = entry.get("mask")
        if (
            not isinstance(mask, str)
            or not 1 <= len(mask) <= MAX_MASK_CHARS
            or _MASK.fullmatch(mask) is None
        ):
            raise RuleError(f"[kks.parts.{name}] mask must match {_MASK.pattern}, 1..8 characters")
        level = entry.get("level")
        parts[name] = KksPartRule(
            name, None if level is None else _int(level, f"kks.parts.{name}.level", 1, 3), mask
        )
    return MappingProxyType(parts)


def _kks_forms(data: Mapping[str, object], parts: Mapping[str, KksPartRule]) -> tuple[KksForm, ...]:
    raw = _list(data, "forms", "kks")
    if not raw or len(raw) > MAX_KKS_FORMS:
        raise RuleError(f"[kks.forms] needs 1..{MAX_KKS_FORMS} forms")
    forms: list[KksForm] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise RuleError("[kks.forms] each form must be a table")
        _only_keys(entry, _FORM_KEYS, "kks.forms")
        name = _name(entry.get("name"), "kks.forms")
        names = _list(entry, "parts", f"kks.forms.{name}")
        if not 1 <= len(names) <= MAX_PARTS_PER_FORM:
            raise RuleError(f"[kks.forms.{name}] needs 1..{MAX_PARTS_PER_FORM} parts")
        if not all(isinstance(n, str) and n in parts for n in names) or len(set(names)) != len(
            names
        ):
            raise RuleError(f"[kks.forms.{name}] parts must be known and not repeated")
        try:
            kks_class = KksClass(str(entry.get("class")))
        except ValueError:
            raise RuleError(f"[kks.forms.{name}] class must be a KksClass") from None
        forms.append(KksForm(name, tuple(str(n) for n in names), kks_class))
    if len({f.name for f in forms}) != len(forms):
        raise RuleError("[kks.forms] form names must be unique")
    return tuple(forms)


def kks_from_mapping(data: Mapping[str, object]) -> KksRules:
    _only_keys(data, _KKS_KEYS, "kks")
    prefixes_raw = _table(data, "prefixes", "kks")
    if len(prefixes_raw) > MAX_KKS_PREFIXES:
        raise RuleError(f"[kks.prefixes] at most {MAX_KKS_PREFIXES}")
    prefixes: dict[str, str] = {}
    for kind, symbol in prefixes_raw.items():
        _name(kind, "kks.prefixes")
        if (
            not isinstance(symbol, str)
            or not 1 <= len(symbol) <= MAX_PREFIX_CHARS
            or not set(symbol) <= _PREFIX_CHARS
        ):
            raise RuleError(f"[kks.prefixes] {kind!r} must be one or two of = +")
        prefixes[kind] = symbol
    if len(set(prefixes.values())) != len(prefixes):
        raise RuleError("[kks.prefixes] symbols must be unique")
    excluded_raw = _list(data, "excluded_letters", "kks")
    for letter in excluded_raw:
        if not isinstance(letter, str) or len(letter) != 1 or not ("A" <= letter <= "Z"):
            raise RuleError("[kks] excluded_letters must be single letters A-Z")
    parts = _kks_parts(data)
    return KksRules(
        prefixes=MappingProxyType(prefixes),
        parts=parts,
        forms=_kks_forms(data, parts),
        excluded_letters=frozenset(str(x) for x in excluded_raw),
    )


def ansi_from_mapping(data: Mapping[str, object]) -> AnsiRules:
    _only_keys(data, _ANSI_KEYS, "ansi")
    suffixes_raw = _list(data, "suffixes", "ansi")
    suffixes: list[str] = []
    for suffix in suffixes_raw:
        if not isinstance(suffix, str) or _SUFFIX.fullmatch(suffix) is None:
            raise RuleError("[ansi] suffixes must be 1..3 letters A-Z")
        suffixes.append(suffix)
    if len(set(suffixes)) != len(suffixes):
        raise RuleError("[ansi] suffixes must be unique")
    functions: dict[int, str] = {}
    for key, name in _table(data, "functions", "ansi").items():
        if not isinstance(key, str) or not key.isascii() or not key.isdigit():
            raise RuleError("[ansi.functions] keys must be numbers")
        number = _int(int(key), "ansi.functions", 1, MAX_ANSI_FUNCTIONS)
        if not isinstance(name, str) or CLASS_NAME.fullmatch(name) is None:
            raise RuleError(f"[ansi.functions] {number} must be a class name")
        functions[number] = name
    return AnsiRules(
        suffixes=frozenset(suffixes),
        functions=MappingProxyType(functions),
        max_compound=_int(data.get("max_compound"), "ansi.max_compound", 2, MAX_ANSI_COMPOUND),
    )


def confusables_from_mapping(data: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    pairs = _list(data, "pairs", "confusables")
    if len(pairs) > MAX_CONFUSABLE_PAIRS:
        raise RuleError(f"[confusables] at most {MAX_CONFUSABLE_PAIRS} pairs")
    out: list[tuple[str, str]] = []
    for pair in pairs:
        if (
            not isinstance(pair, str)
            or len(pair) != PAIR_CHARS
            or not ("0" <= pair[0] <= "9")
            or not ("A" <= pair[1] <= "Z")
        ):
            raise RuleError("[confusables] each pair is a digit then a letter A-Z")
        out.append((pair[0], pair[1]))
    return tuple(dict.fromkeys(out))


def digits_first_from_mapping(
    terminals: Mapping[str, object], prefixes: tuple[str, ...]
) -> Mapping[str, tuple[Convention, ...]]:
    raw = terminals.get("digits_first", {})
    if not isinstance(raw, Mapping):
        raise RuleError("[terminals.digits_first] must be a table")
    out: dict[str, tuple[Convention, ...]] = {}
    for prefix, names in raw.items():
        if prefix not in prefixes:
            raise RuleError("[terminals.digits_first] keys must be terminal prefixes")
        if not isinstance(names, list | tuple) or not names or len(set(names)) != len(names):
            raise RuleError(f"[terminals.digits_first] {prefix!r} must be a non-empty unique list")
        conventions: list[Convention] = []
        for name in names:
            try:
                convention = Convention(str(name))
            except ValueError:
                raise RuleError(
                    f"[terminals.digits_first] {prefix!r} names an unknown convention"
                ) from None
            if convention not in DEVICE_CONVENTIONS:
                raise RuleError(f"[terminals.digits_first] {prefix!r} must name device conventions")
            conventions.append(convention)
        out[prefix] = tuple(conventions)
    return MappingProxyType(out)


def checked_ansi_suffixes(raw: tuple[str, ...]) -> tuple[str, ...]:
    """The profile's extra ANSI suffixes, normalised, unique and sorted. A suffix is compared
    by membership in a set and never compiled (NFR-F2-3). RuleError never echoes a value."""
    if len(raw) > MAX_EXTRA_ANSI_SUFFIXES:
        raise RuleError(f"at most {MAX_EXTRA_ANSI_SUFFIXES} extra ANSI suffixes")
    checked: set[str] = set()
    for item in raw:
        if len(item) > MAX_RAW_ANSI_SUFFIX_CHARS:
            raise RuleError("ANSI suffix is too long")
        if text_issues(item):
            raise RuleError("ANSI suffix must not contain a hidden character")
        suffix = canonical_form(item)
        if _SUFFIX.fullmatch(suffix) is None:
            raise RuleError("ANSI suffix must be 1..3 letters A-Z")
        checked.add(suffix)
    return tuple(sorted(checked))
