"""Identifier rules as VALUES (HLD §5.4, INC1-D-19), plus the packaged `identifiers.toml`.

``shared`` never opens a file: a section adapter reads ``identifiers.toml`` (with
``importlib.resources``) and hands the parsed mapping to ``IdentifierRules.from_mapping``.
Regular expressions are written by the repository and reviewed with it. A document's
profile selects them by NAME through ``RuleSelection`` and never carries one, so no
user input is ever compiled (no ReDoS).

``rules`` is a package, not a module, so the data file ships beside the code that
interprets it (LLD §2.1 rows B15/B16).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from app.modules.electrical.shared.kinds import (
    ADDITIVE_CONVENTIONS,
    DEVICE_CONVENTIONS,
    Convention,
    IdentifierKind,
    PlcFamily,
)
from app.modules.electrical.shared.normalization import canonical_form, text_issues
from app.modules.electrical.shared.parser import IdentifierParser
from app.modules.electrical.shared.patterns import compiled
from app.modules.electrical.shared.rules.errors import RuleError
from app.modules.electrical.shared.rules.systems import (
    AnsiRules,
    KksRules,
    ansi_from_mapping,
    checked_ansi_suffixes,
    confusables_from_mapping,
    digits_first_from_mapping,
    kks_from_mapping,
)

__all__ = ["IdentifierRules", "RuleError", "RuleSelection"]

_ASPECT_CHARS = frozenset("=+-&#")
MAX_EXTRA_VOLTAGE_LABELS = 64  # LLD 3.3.6: DrawingProfile.voltage_labels <= 64
MAX_VOLTAGE_LABEL_CHARS = 16  # ... each <= 16 characters
MAX_RAW_VOLTAGE_LABEL_CHARS = 64  # checked before normalisation (NFKC can expand a character)
_VOLTAGE_LABEL = re.compile(r"[A-Z0-9+\-]+", re.ASCII)
# a rail written as a designation. ``-`` keeps ``-48V`` and ``-12``; ``+`` REQUIRES the ``V``
# (INC1-Q-5): ``+24V`` is a rail, ``+1`` is a location aspect of IEC 81346.
_RAIL_CODE = {
    "-": re.compile(r"\d{1,4}V?", re.ASCII),
    "+": re.compile(r"\d{1,4}V", re.ASCII),
}


@dataclass(frozen=True, slots=True)
class RuleSelection:
    """What a document's profile chooses, by NAME only (drawings builds it).

    An EMPTY field means "no restriction": the document inherits the packaged rules
    for it. ``extra_voltage_labels`` are added to the packaged labels.
    """

    conventions: frozenset[Convention]
    plc_families: frozenset[PlcFamily]
    extra_aspects: tuple[str, ...]
    wire_number_patterns: tuple[str, ...]
    cross_reference_format: str | None  # None = detect the dominant one
    extra_voltage_labels: tuple[str, ...]
    extra_ansi_suffixes: tuple[str, ...] = ()  # D-36: added to the packaged suffixes


@dataclass(frozen=True, slots=True)
class IdentifierRules:
    """Rules as VALUES: built from a mapping a section adapter read from the TOML file."""

    version: str
    aspects: tuple[str, ...]
    extra_aspects: tuple[str, ...]
    cn_letter_codes: Mapping[str, str]
    nfpa_codes: Mapping[str, str]
    terminal_prefixes: tuple[str, ...]
    wire_number_patterns: Mapping[str, str]  # name -> regex
    cross_reference_formats: Mapping[str, str]  # name -> regex
    connection_point_roles: Mapping[str, str]
    voltage_labels: tuple[str, ...]
    kks: KksRules  # FR-ID-15, D-36
    ansi: AnsiRules  # FR-ID-16, D-36
    confusables: tuple[tuple[str, str], ...]  # (("0", "O"),): FR-ID-18
    terminal_digits_first: Mapping[str, tuple[Convention, ...]]  # prefix -> conventions (A-5)
    # Additive to the LLD shape: what `narrowed` restricts, and the TOML's separator. The
    # default is the DEVICE conventions: KKS and ANSI are read only when named (D-36).
    conventions: frozenset[Convention] = field(default_factory=lambda: DEVICE_CONVENTIONS)
    plc_families: frozenset[PlcFamily] = field(default_factory=lambda: frozenset(PlcFamily))
    connection_point_separator: str = ":"
    cross_reference_pinned: bool = False  # the profile chose ONE cross-reference format

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> IdentifierRules:
        """Validates shape and that every regex compiles (``compiled`` caches the compiled
        form, so a match never recompiles); RuleError otherwise."""
        reference = _table(data, "reference_designation")
        aspects = _strings(reference, "aspects", "reference_designation")
        extras = _strings(reference, "extra_aspects", "reference_designation")
        for token in (*aspects, *extras):
            if not token or not set(token) <= _ASPECT_CHARS:
                raise RuleError(f"aspect prefix {token!r} must be made of = + - & #")
        if not aspects or len(set(aspects) | set(extras)) != len(aspects) + len(extras):
            raise RuleError("aspects must be non-empty and unique across aspects and extras")
        separator = reference.get("connection_point_separator", ":")
        if not isinstance(separator, str) or len(separator) != 1:
            raise RuleError("connection_point_separator must be one character")

        terminals = _table(data, "terminals")
        wire = _string_map(_table(data, "wire_numbers"), "wire_numbers")
        xref = _string_map(_table(data, "cross_references"), "cross_references")
        for name, pattern in (*wire.items(), *xref.items()):
            try:
                compiled(pattern)
            except re.error as exc:
                raise RuleError(f"rule {name!r} is not a valid regular expression: {exc}") from exc

        terminal_prefixes = _strings(terminals, "prefixes", "terminals")
        return cls(
            version=_text(data, "version"),
            aspects=aspects,
            extra_aspects=extras,
            cn_letter_codes=_codes(_table(data, "cn_letter_codes"), "cn_letter_codes"),
            nfpa_codes=_codes(_table(data, "nfpa_codes"), "nfpa_codes"),
            terminal_prefixes=terminal_prefixes,
            wire_number_patterns=MappingProxyType(wire),
            cross_reference_formats=MappingProxyType(xref),
            connection_point_roles=MappingProxyType(
                {
                    k.upper(): v
                    for k, v in _string_map(
                        _table(data, "connection_points"), "connection_points"
                    ).items()
                }
            ),
            voltage_labels=tuple(
                canonical_form(label)
                for label in _strings(_table(data, "voltages"), "labels", "voltages")
            ),
            kks=kks_from_mapping(_table(data, "kks")),
            ansi=ansi_from_mapping(_table(data, "ansi")),
            confusables=confusables_from_mapping(_table(data, "confusables")),
            terminal_digits_first=digits_first_from_mapping(terminals, terminal_prefixes),
            connection_point_separator=separator,
        )

    def narrowed(self, profile: RuleSelection) -> IdentifierRules:
        """The subset a document's profile selects (conventions, PLC families,
        patterns by NAME, extra voltage labels). A profile never carries a regex."""
        conventions = (
            (self.conventions | ADDITIVE_CONVENTIONS) & profile.conventions
            if profile.conventions
            else self.conventions
        )
        plc_families = (
            self.plc_families & profile.plc_families if profile.plc_families else self.plc_families
        )
        extra_aspects = (
            tuple(
                _pick(
                    dict.fromkeys(self.extra_aspects, ""),
                    profile.extra_aspects,
                    "extra aspect",
                )
            )
            if profile.extra_aspects
            else self.extra_aspects
        )
        wire = (
            _pick(self.wire_number_patterns, profile.wire_number_patterns, "wire-number pattern")
            if profile.wire_number_patterns
            else self.wire_number_patterns
        )
        xref = (
            _pick(
                self.cross_reference_formats,
                (profile.cross_reference_format,),
                "cross-reference format",
            )
            if profile.cross_reference_format is not None
            else self.cross_reference_formats
        )
        narrowed = replace(
            self,
            conventions=conventions,
            plc_families=plc_families,
            extra_aspects=extra_aspects,
            wire_number_patterns=wire,
            cross_reference_formats=xref,
            ansi=replace(
                self.ansi,
                suffixes=self.ansi.suffixes
                | frozenset(checked_ansi_suffixes(profile.extra_ansi_suffixes)),
            ),
            cross_reference_pinned=(
                self.cross_reference_pinned or profile.cross_reference_format is not None
            ),
        )
        extras = narrowed._checked_voltage_labels(profile.extra_voltage_labels)
        return replace(
            narrowed, voltage_labels=tuple(dict.fromkeys((*self.voltage_labels, *extras)))
        )

    def with_additive(self) -> IdentifierRules:
        """The same rules with KKS and ANSI enabled too (the tally's probe, FR-ING-12)."""
        return replace(self, conventions=self.conventions | ADDITIVE_CONVENTIONS)

    def _checked_voltage_labels(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        """Normalise the profile's extra voltage labels and reject any that is empty, too
        long, outside ``[A-Z0-9+-]``, too many, or that the parser would read as another
        kind of identifier (a label must not hide ``-K5``, ``KM1`` or ``X1:14``). A rail
        such as ``-48V`` is allowed: only a VALID single-aspect designation with prefix ``-`` and
        a code of 1..4 digits and an optional ``V`` (so a bare ``-12`` too), or prefix ``+`` and
        1..4 digits and a ``V`` (INC1-Q-5). A label that is made of ``+`` / ``-`` only, or holds
        a hidden character, is rejected (R2-2)."""
        if len(labels) > MAX_EXTRA_VOLTAGE_LABELS:
            raise RuleError(f"at most {MAX_EXTRA_VOLTAGE_LABELS} extra voltage labels")
        checked: list[str] = []
        for raw in labels:
            if len(raw) > MAX_RAW_VOLTAGE_LABEL_CHARS:
                raise RuleError(
                    f"voltage label must be at most {MAX_RAW_VOLTAGE_LABEL_CHARS} characters"
                )
            if text_issues(raw):  # R2-2: reported, never silently dropped
                raise RuleError("voltage label must not contain a hidden character")
            label = canonical_form(raw)
            if (
                not label
                or len(label) > MAX_VOLTAGE_LABEL_CHARS
                or _VOLTAGE_LABEL.fullmatch(label) is None
                or not any(ch.isalnum() for ch in label)  # R2-2: ``+`` / ``--`` say nothing
            ):
                raise RuleError(
                    f"voltage label must be 1..{MAX_VOLTAGE_LABEL_CHARS} characters of A-Z 0-9 + -"
                )
            other = self._other_kind(label)
            if other is not None:
                raise RuleError(f"voltage label would be read as a {other.value}")
            checked.append(label)
        return tuple(dict.fromkeys(checked))

    def _other_kind(self, label: str) -> IdentifierKind | None:
        """The kind a label would be read as if it were not a voltage, or None. A rail such
        as ``-48V`` or ``+24V`` is allowed: a VALID single-aspect designation whose prefix is
        ``-`` and whose code matches ``\\d{1,4}V?``, or ``+`` and ``\\d{1,4}V``."""
        found = IdentifierParser(self).parse(label)
        if found is None or found.kind is IdentifierKind.VOLTAGE:
            return None
        if (
            found.kind is IdentifierKind.REFERENCE_DESIGNATION
            and found.valid
            and len(found.aspects) == 1
            and (rail := _RAIL_CODE.get(found.aspects[0].prefix)) is not None
            and rail.fullmatch(found.aspects[0].code)
        ):
            return None
        return found.kind

    @property
    def all_aspects(self) -> tuple[str, ...]:
        return (*self.aspects, *self.extra_aspects)


def _pick(available: Mapping[str, str], names: tuple[str, ...], what: str) -> Mapping[str, str]:
    unknown = [n for n in names if n not in available]
    if unknown:
        raise RuleError(f"unknown {what} {unknown[0]!r}")
    return MappingProxyType({n: p for n, p in available.items() if n in names})


def _table(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise RuleError(f"rules table [{key}] is missing")
    return value


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuleError(f"rules key {key!r} must be a non-empty string")
    return value


def _strings(data: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        raise RuleError(f"[{where}] {key!r} must be a list of strings")
    return tuple(value)


def _string_map(data: Mapping[str, object], where: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str) or not value:
            raise RuleError(f"[{where}] {key!r} must be a non-empty string")
        out[key] = value
    return out


def _codes(data: Mapping[str, object], where: str) -> Mapping[str, str]:
    codes = _string_map(data, where)
    for code in codes:
        if not code.isascii() or not code.isalpha() or code != code.upper():
            raise RuleError(f"[{where}] letter code {code!r} must be upper-case ASCII letters")
    return MappingProxyType(codes)
