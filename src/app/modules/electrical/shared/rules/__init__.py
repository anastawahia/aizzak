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

from app.modules.electrical.shared.kinds import Convention, PlcFamily

_ASPECT_CHARS = frozenset("=+-&#")


class RuleError(ValueError):
    """The rules mapping has the wrong shape, or a rule does not compile."""


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
    # Additive to the LLD shape: what `narrowed` restricts, and the TOML's separator.
    conventions: frozenset[Convention] = field(default_factory=lambda: frozenset(Convention))
    plc_families: frozenset[PlcFamily] = field(default_factory=lambda: frozenset(PlcFamily))
    connection_point_separator: str = ":"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> IdentifierRules:
        """Validates shape and compiles every regex once; RuleError otherwise."""
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
                re.compile(pattern)
            except re.error as exc:
                raise RuleError(f"rule {name!r} is not a valid regular expression: {exc}") from exc

        return cls(
            version=_text(data, "version"),
            aspects=aspects,
            extra_aspects=extras,
            cn_letter_codes=_codes(_table(data, "cn_letter_codes"), "cn_letter_codes"),
            nfpa_codes=_codes(_table(data, "nfpa_codes"), "nfpa_codes"),
            terminal_prefixes=_strings(terminals, "prefixes", "terminals"),
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
                label.upper() for label in _strings(_table(data, "voltages"), "labels", "voltages")
            ),
            connection_point_separator=separator,
        )

    def narrowed(self, profile: RuleSelection) -> IdentifierRules:
        """The subset a document's profile selects (conventions, PLC families,
        patterns by NAME, extra voltage labels). A profile never carries a regex."""
        conventions = (
            self.conventions & profile.conventions if profile.conventions else self.conventions
        )
        plc_families = (
            self.plc_families & profile.plc_families if profile.plc_families else self.plc_families
        )
        extra_aspects = (
            tuple(a for a in self.extra_aspects if a in profile.extra_aspects)
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
        voltages = tuple(
            dict.fromkeys(
                (*self.voltage_labels, *(v.upper() for v in profile.extra_voltage_labels))
            )
        )
        return replace(
            self,
            conventions=conventions,
            plc_families=plc_families,
            extra_aspects=extra_aspects,
            wire_number_patterns=wire,
            cross_reference_formats=xref,
            voltage_labels=voltages,
        )

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
