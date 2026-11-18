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
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from app.modules.electrical.shared.kinds import (
    DEVICE_CONVENTIONS,
    Convention,
    IdentifierKind,
    KksClass,
)
from app.modules.electrical.shared.normalization import canonical_form, text_issues
from app.modules.electrical.shared.rules.errors import RuleError

MAX_KKS_PARTS: Final = 8
MAX_KKS_FORMS: Final = 16
MAX_KKS_LEVEL: Final = 3
MAX_PARTS_PER_FORM: Final = 4
MAX_MASK_CHARS: Final = 8
MAX_OPTIONAL_PER_PART: Final = 2  # S-1: optional (n / a) positions in one mask
MAX_OPTIONAL_PER_FORM: Final = 4  # ... and across the parts of one form: at most 2**4 patterns
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
_KKS_KEYS: Final = frozenset(
    {
        "excluded_letters",
        "evidence_min_level",
        "prefixes",
        "parts",
        "forms",
        "part_separators",
        "near_miss_min_length",
    }
)
MAX_RATING_UNITS: Final = 32
MAX_RATING_UNIT_CHARS: Final = 5
MAX_GROUP_MEMBERS: Final = 64
GROUP_SEPARATORS: Final = frozenset({"/", "\\"})
KKS_PART_SEPARATOR_CHARS: Final = frozenset({"-", "."})
MAX_NEAR_MISS_LENGTH: Final = 32
_GROUP_KEYS: Final = frozenset({"max_members", "suffix_lists"})
_RATING_KEYS: Final = frozenset({"units"})
_ANSI_KEYS: Final = frozenset({"max_compound", "suffixes", "non_evidence_suffixes", "functions"})
_OPTIONAL: Final = frozenset("na")
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


# One form as a hashable spec: (form name, ((part name, mask), ...)).
KksSpec = tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


@dataclass(frozen=True, slots=True)
class KksRules:
    prefixes: Mapping[str, str]  # kind name -> symbol, e.g. "process" -> "="
    parts: Mapping[str, KksPartRule]
    forms: tuple[KksForm, ...]  # file order: the first full match wins
    excluded_letters: frozenset[str]
    evidence_min_level: int = 2  # D-36: the shallowest level the tally counts as KKS evidence
    # F3 (gap 33): the one character a tag may carry between two parts (20XYZ10AB123-CD45)
    part_separators: frozenset[str] = frozenset()
    # F3: the shortest text a near miss is reported for (kks.malformed); replaces ``shortest``
    near_miss_min_length: int = 7
    # derived once from ``parts`` and ``forms``, so the parser never rebuilds them per token
    spec: KksSpec = field(init=False, repr=False, compare=False)
    shortest: int = field(init=False, repr=False, compare=False)  # fewest characters of a form

    def __post_init__(self) -> None:
        spec = tuple((f.name, tuple((p, self.parts[p].mask) for p in f.parts)) for f in self.forms)
        shortest = min(
            sum(sum(1 for c in self.parts[p].mask if c in "NA") for p in f.parts)
            for f in self.forms
        )
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "shortest", shortest)


@dataclass(frozen=True, slots=True)
class AnsiRules:
    suffixes: frozenset[str]  # the packaged ones, plus the profile's after narrowing
    functions: Mapping[int, str]  # number -> our class name
    max_compound: int  # 2..3
    non_evidence_suffixes: frozenset[str] = frozenset()  # D-36: a rating's letter (32A)


@dataclass(frozen=True, slots=True)
class RatingRules:
    """F3 (FR-ID-20): the units that make a number a rating, never an identifier."""

    units: tuple[str, ...]  # as written in the file (SI case)
    folded_units: frozenset[str] = field(init=False, repr=False, compare=False)  # upper-cased

    def __post_init__(self) -> None:
        object.__setattr__(self, "folded_units", frozenset(u.upper() for u in self.units))


@dataclass(frozen=True, slots=True)
class GroupRules:
    """F3 (FR-ID-19): the bound of a list or range and the kinds a suffix list may follow."""

    max_members: int  # 2..64
    suffix_lists: Mapping[str, frozenset[IdentifierKind]]  # "/" -> {KKS_TAG}


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
        if sum(1 for c in mask if c in _OPTIONAL) > MAX_OPTIONAL_PER_PART:
            raise RuleError(
                f"[kks.parts.{name}] at most {MAX_OPTIONAL_PER_PART} optional positions"
            )
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
        if (
            sum(1 for n in names for c in parts[str(n)].mask if c in _OPTIONAL)
            > MAX_OPTIONAL_PER_FORM
        ):
            raise RuleError(
                f"[[kks.forms]] {name!r}: at most {MAX_OPTIONAL_PER_FORM} optional positions"
            )
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
        evidence_min_level=_int(
            data.get("evidence_min_level"), "kks.evidence_min_level", 1, MAX_KKS_LEVEL
        ),
        part_separators=_kks_part_separators(data),
        near_miss_min_length=_int(
            data.get("near_miss_min_length"), "kks.near_miss_min_length", 1, MAX_NEAR_MISS_LENGTH
        ),
    )


def _kks_part_separators(data: Mapping[str, object]) -> frozenset[str]:
    raw = _list(data, "part_separators", "kks")
    if not all(isinstance(x, str) and x in KKS_PART_SEPARATOR_CHARS for x in raw):
        raise RuleError("[kks] part_separators must be single characters from - .")
    if len(set(raw)) != len(raw):
        raise RuleError("[kks] part_separators must be unique")
    return frozenset(str(x) for x in raw)


def ratings_from_mapping(data: Mapping[str, object]) -> RatingRules:
    _only_keys(data, _RATING_KEYS, "ratings")
    units = _list(data, "units", "ratings")
    if not 1 <= len(units) <= MAX_RATING_UNITS:
        raise RuleError(f"[ratings] units needs 1..{MAX_RATING_UNITS} entries")
    for unit in units:
        if (
            not isinstance(unit, str)
            or not 1 <= len(unit) <= MAX_RATING_UNIT_CHARS
            or not unit.isascii()
            or not unit.isalpha()
        ):
            raise RuleError(f"[ratings] each unit is 1..{MAX_RATING_UNIT_CHARS} ASCII letters")
    if len({str(u).upper() for u in units}) != len(units):
        raise RuleError("[ratings] units must be unique, ignoring case")
    return RatingRules(tuple(str(u) for u in units))


def groups_from_mapping(data: Mapping[str, object]) -> GroupRules:
    _only_keys(data, _GROUP_KEYS, "groups")
    max_members = _int(data.get("max_members"), "groups.max_members", 2, MAX_GROUP_MEMBERS)
    raw = data.get("suffix_lists")
    if not isinstance(raw, Mapping):
        raise RuleError("[groups] suffix_lists must be a table")
    out: dict[str, frozenset[IdentifierKind]] = {}
    for separator, names in raw.items():
        if separator not in GROUP_SEPARATORS:
            raise RuleError("[groups] suffix_lists keys must be / or a backslash")
        if not isinstance(names, list | tuple) or not names:
            raise RuleError("[groups] suffix_lists values must be non-empty lists of kinds")
        kinds: set[IdentifierKind] = set()
        for name in names:
            try:
                kind = IdentifierKind(str(name))
            except ValueError:
                raise RuleError("[groups] suffix_lists names an unknown kind") from None
            if kind is IdentifierKind.IDENTIFIER_GROUP:
                raise RuleError("[groups] a suffix list cannot hold groups")
            kinds.add(kind)
        out[separator] = frozenset(kinds)
    return GroupRules(max_members, MappingProxyType(out))


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
    non_evidence = _list(data, "non_evidence_suffixes", "ansi")
    if not all(isinstance(x, str) and x in suffixes for x in non_evidence):
        raise RuleError("[ansi] non_evidence_suffixes must be listed in suffixes")
    if len(set(non_evidence)) != len(non_evidence):
        raise RuleError("[ansi] non_evidence_suffixes must be unique")
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
        non_evidence_suffixes=frozenset(str(x) for x in non_evidence),
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
    terminals: Mapping[str, object], prefixes: tuple[str, ...], key: str = "digits_first"
) -> Mapping[str, tuple[Convention, ...]]:
    """The per-prefix device conventions of a terminal form (``[terminals.digits_first]``, and
    F3's ``[terminals.letters_first]``, which names the same kind of table)."""
    raw = terminals.get(key, {})
    where = f"[terminals.{key}]"
    if not isinstance(raw, Mapping):
        raise RuleError(f"{where} must be a table")
    out: dict[str, tuple[Convention, ...]] = {}
    for prefix, names in raw.items():
        if prefix not in prefixes:
            raise RuleError(f"{where} keys must be terminal prefixes")
        if not isinstance(names, list | tuple) or not names or len(set(names)) != len(names):
            raise RuleError(f"{where} {prefix!r} must be a non-empty unique list")
        conventions: list[Convention] = []
        for name in names:
            try:
                convention = Convention(str(name))
            except ValueError:
                raise RuleError(f"{where} {prefix!r} names an unknown convention") from None
            if convention not in DEVICE_CONVENTIONS:
                raise RuleError(f"{where} {prefix!r} must name device conventions")
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
