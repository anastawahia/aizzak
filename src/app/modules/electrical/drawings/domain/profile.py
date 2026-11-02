"""The drawing profile (FR-ING-01, FR-ADM-04, NFR-CFG-01/02). Stdlib and ``shared`` only.

One shape for a project's profile and a document's override. A profile NEVER carries a regular
expression: it selects the wire-number patterns by NAME (``INC1-D-19``), and ``rule_selection``
turns it into what ``shared`` narrows its packaged rules with.

``from_body`` is strict: ``schema`` must be 1, an unknown key, a wrong type, a bad enum value
or an out-of-range number raises ``InvalidProfile`` naming the field path. An ABSENT key takes
its default, so a body is a replacement that need only state what differs.

``rule_selection`` restricts the identifier conventions to the profile's own one. The worker
that PROPOSES a profile must therefore tally a document's tokens with the unnarrowed packaged
rules, not with the project's (default) selection, or a Chinese drawing would show no
convention at all.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from app.modules.electrical.drawings.domain.errors import InvalidProfile
from app.modules.electrical.drawings.domain.pages import ClassificationThresholds
from app.modules.electrical.shared.kinds import Convention, PlcFamily
from app.modules.electrical.shared.rules import RuleSelection

PROFILE_SCHEMA: Final = 1
MAX_EXTRA_ASPECTS: Final = 8
MAX_ASPECT_CHARS: Final = 4
MAX_VOLTAGE_LABELS: Final = 64  # the same bounds `shared` enforces when it narrows
MAX_VOLTAGE_LABEL_CHARS: Final = 16
MAX_VENDOR_LIBRARY_CHARS: Final = 100
PLC_VENDORS: Final = frozenset({"siemens"})  # FR-ID-14 is C: one vendor in R1
LANGUAGES: Final = frozenset({"en", "zh"})
_ASPECT_CHARS: Final = frozenset("=+-&#")
MIN_AUTO_ACCEPT: Final = 0.5
MAX_AUTO_ACCEPT: Final = 1.0
DEFAULT_AUTO_ACCEPT: Final = 0.90  # M-32's R1 target


class SymbolStandard(StrEnum):
    IEC_60617 = "iec_60617"
    GBT_4728 = "gbt_4728"
    NFPA_JIC = "nfpa_jic"
    VENDOR = "vendor"


class CrossReferenceFormat(StrEnum):
    AUTO = "auto"
    SLASH_PAGE_DOT_COLUMN = "slash_page_dot_column"  # /12.3
    PAGE_DOT_COLUMN = "page_dot_column"  # 12.3
    PREFIXED_SLASH = "prefixed_slash"  # =A1/12.3
    PAGE_SLASH_COLUMN = "page_slash_column"  # 12/3


class JunctionConvention(StrEnum):
    AUTO = "auto"
    DOT_MARKS_JUNCTION = "dot_marks_junction"
    NO_DOT = "no_dot"


class ProfileSource(StrEnum):
    DEFAULT = "default"
    USER = "user"
    DETECTED = "detected"
    CONFIRMED = "confirmed"


# name -> (type, low, high) of the classification thresholds, in field order
_THRESHOLD_RANGES: Final[Mapping[str, tuple[type, float, float]]] = {
    "min_text_chars": (int, 0, 10_000),
    "garbled_ratio": (float, 0.0, 1.0),
    "raster_area_ratio": (float, 0.0, 1.0),
    "outlined_curves_min": (int, 1, 1_000_000),
    "ambiguity_margin": (float, 0.0, 0.5),
    "cjk_share": (float, 0.0, 1.0),
    "script_mix_share": (float, 0.0, 1.0),
}
_TOP_KEYS: Final = (
    "schema", "symbol_standard", "vendor_library", "identifier_convention", "extra_aspects",
    "cross_reference_format", "junction_convention", "plc_vendor", "plc_families", "languages",
    "wire_number_patterns", "voltage_labels", "auto_accept_confidence", "classification",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class DrawingProfile:
    """FR-ING-01: one shape for the project profile and a document's override."""

    symbol_standard: SymbolStandard
    vendor_library: str | None  # only with VENDOR
    identifier_convention: Convention
    extra_aspects: tuple[str, ...]
    cross_reference_format: CrossReferenceFormat
    junction_convention: JunctionConvention
    plc_vendor: str  # "siemens" in R1 (FR-ID-14 is C)
    plc_families: frozenset[PlcFamily]
    languages: frozenset[str]  # subset of {"en", "zh"}, 1..2
    wire_number_patterns: tuple[str, ...]  # names from identifiers.toml
    voltage_labels: tuple[str, ...]  # extra labels, <= 64, each <= 16 chars
    auto_accept_confidence: float  # 0.5..1.0; default 0.90, M-32's R1 target
    classification: ClassificationThresholds

    @classmethod
    def default(cls) -> DrawingProfile:
        return cls(
            symbol_standard=SymbolStandard.IEC_60617,
            vendor_library=None,
            identifier_convention=Convention.IEC_81346,
            extra_aspects=(),
            cross_reference_format=CrossReferenceFormat.AUTO,
            junction_convention=JunctionConvention.AUTO,
            plc_vendor="siemens",
            plc_families=frozenset(PlcFamily),
            languages=frozenset(LANGUAGES),
            wire_number_patterns=(),
            voltage_labels=(),
            auto_accept_confidence=DEFAULT_AUTO_ACCEPT,
            classification=ClassificationThresholds(),
        )

    @classmethod
    def from_body(
        cls, body: Mapping[str, object], *, known_patterns: frozenset[str]
    ) -> DrawingProfile:
        """`schema` must be 1; unknown keys, bad enum values and out-of-range
        numbers raise InvalidProfile naming the field path (NFR-CFG-02)."""
        if not isinstance(body, Mapping):
            raise InvalidProfile("body", "must be an object")
        for key in body:
            if key not in _TOP_KEYS:
                raise InvalidProfile(str(key), "unknown field")
        schema = body.get("schema")
        if isinstance(schema, bool) or schema != PROFILE_SCHEMA:
            raise InvalidProfile("schema", f"must be {PROFILE_SCHEMA}")
        base = cls.default()

        symbol = _enum(body, "symbol_standard", SymbolStandard, base.symbol_standard)
        vendor = _vendor_library(body, symbol)
        convention = _enum(body, "identifier_convention", Convention, base.identifier_convention)
        xref = _enum(
            body, "cross_reference_format", CrossReferenceFormat, base.cross_reference_format
        )
        junction = _enum(body, "junction_convention", JunctionConvention, base.junction_convention)
        plc_vendor = _plc_vendor(body)
        families = frozenset(
            _enum_set(body, "plc_families", PlcFamily, minimum=1, default=base.plc_families)
        )
        languages = _languages(body, base.languages)
        patterns = _names(body, "wire_number_patterns", known_patterns)
        aspects = _aspects(body)
        labels = _voltage_labels(body)
        accept = _number(
            body.get("auto_accept_confidence", base.auto_accept_confidence),
            "auto_accept_confidence",
            float,
            MIN_AUTO_ACCEPT,
            MAX_AUTO_ACCEPT,
        )
        return cls(
            symbol_standard=symbol,
            vendor_library=vendor,
            identifier_convention=convention,
            extra_aspects=aspects,
            cross_reference_format=xref,
            junction_convention=junction,
            plc_vendor=plc_vendor,
            plc_families=families,
            languages=languages,
            wire_number_patterns=patterns,
            voltage_labels=labels,
            auto_accept_confidence=float(accept),
            classification=_thresholds(body.get("classification"), base.classification),
        )

    def to_body(self) -> dict[str, object]:
        """The canonical body: every field, sets sorted, with ``"schema": 1``."""
        t = self.classification
        return {
            "schema": PROFILE_SCHEMA,
            "symbol_standard": self.symbol_standard.value,
            "vendor_library": self.vendor_library,
            "identifier_convention": self.identifier_convention.value,
            "extra_aspects": list(self.extra_aspects),
            "cross_reference_format": self.cross_reference_format.value,
            "junction_convention": self.junction_convention.value,
            "plc_vendor": self.plc_vendor,
            "plc_families": sorted(f.value for f in self.plc_families),
            "languages": sorted(self.languages),
            "wire_number_patterns": list(self.wire_number_patterns),
            "voltage_labels": list(self.voltage_labels),
            "auto_accept_confidence": self.auto_accept_confidence,
            "classification": {
                "min_text_chars": t.min_text_chars,
                "garbled_ratio": t.garbled_ratio,
                "raster_area_ratio": t.raster_area_ratio,
                "outlined_curves_min": t.outlined_curves_min,
                "ambiguity_margin": t.ambiguity_margin,
                "cjk_share": t.cjk_share,
                "script_mix_share": t.script_mix_share,
            },
        }

    def fingerprint(self) -> str:
        """sha256 of the canonical JSON (sorted keys): equal profiles, equal fingerprints."""
        canonical = json.dumps(self.to_body(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def rule_selection(self) -> RuleSelection:
        return RuleSelection(
            conventions=frozenset({self.identifier_convention}),
            plc_families=self.plc_families,
            extra_aspects=self.extra_aspects,
            wire_number_patterns=self.wire_number_patterns,
            cross_reference_format=(
                None
                if self.cross_reference_format is CrossReferenceFormat.AUTO
                else self.cross_reference_format.value
            ),
            extra_voltage_labels=self.voltage_labels,
        )

    def with_detected(
        self,
        *,
        symbol_standard: SymbolStandard,
        identifier_convention: Convention,
        cross_reference_format: CrossReferenceFormat,
        plc_families: frozenset[PlcFamily],
        languages: frozenset[str],
    ) -> DrawingProfile:
        """The same profile with the fields STG-01 can detect replaced. ``vendor_library``
        is dropped unless the standard is still ``VENDOR`` (it is valid only with it)."""
        return replace(
            self,
            symbol_standard=symbol_standard,
            vendor_library=self.vendor_library
            if symbol_standard is SymbolStandard.VENDOR
            else None,
            identifier_convention=identifier_convention,
            cross_reference_format=cross_reference_format,
            plc_families=plc_families,
            languages=languages,
        )


@dataclass(frozen=True, slots=True)
class ProfileVersion:
    id: str
    workspace_id: str
    project_id: str
    document_id: str | None  # None = the project's own profile
    version: int  # 1, 2, ... per owner; append-only (FR-ADM-04)
    source: ProfileSource
    profile: DrawingProfile
    fingerprint: str
    confidence: float | None  # DETECTED only
    mixed_standards: bool
    created_by: str | None
    created_at: datetime
    # DETECTED only: {"field_confidence": {...}, "basis": {...}} as plain JSON (LLD 4.3).
    detection: Mapping[str, Any] | None = None


# --------------------------------------------------------------------------------------
# field readers: each raises InvalidProfile naming the path
# --------------------------------------------------------------------------------------
def _enum[E: StrEnum](body: Mapping[str, Any], key: str, kind: type[E], default: E) -> E:
    if key not in body:
        return default
    return _to_enum(body[key], key, kind)


def _to_enum[E: StrEnum](value: object, path: str, kind: type[E]) -> E:
    try:
        if not isinstance(value, str):
            raise ValueError(value)
        return kind(value)
    except ValueError:
        allowed = ", ".join(m.value for m in kind)
        raise InvalidProfile(path, f"must be one of: {allowed}") from None


def _list(body: Mapping[str, Any], key: str) -> list[object] | None:
    if key not in body:
        return None
    value = body[key]
    if not isinstance(value, list | tuple):
        raise InvalidProfile(key, "must be a list")
    return list(value)


def _enum_set[E: StrEnum](
    body: Mapping[str, Any], key: str, kind: type[E], *, minimum: int, default: frozenset[E]
) -> frozenset[E]:
    items = _list(body, key)
    if items is None:
        return default
    found = frozenset(_to_enum(item, f"{key}[{i}]", kind) for i, item in enumerate(items))
    if len(found) < minimum:
        raise InvalidProfile(key, f"must list at least {minimum}")
    return found


def _languages(body: Mapping[str, Any], default: frozenset[str]) -> frozenset[str]:
    items = _list(body, "languages")
    if items is None:
        return default
    for i, item in enumerate(items):
        if item not in LANGUAGES:
            raise InvalidProfile(
                f"languages[{i}]", f"must be one of: {', '.join(sorted(LANGUAGES))}"
            )
    if not items:
        raise InvalidProfile("languages", "must list at least 1")
    return frozenset(str(item) for item in items)


def _strings(items: list[object], key: str) -> list[str]:
    out: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise InvalidProfile(f"{key}[{i}]", "must be a string")
        out.append(item)
    return out


def _names(body: Mapping[str, Any], key: str, known: frozenset[str]) -> tuple[str, ...]:
    items = _list(body, key)
    if items is None:
        return ()
    names = _strings(items, key)
    for i, name in enumerate(names):
        if name not in known:
            raise InvalidProfile(f"{key}[{i}]", f"unknown pattern {name!r}")
    return tuple(sorted(set(names)))


def _aspects(body: Mapping[str, Any]) -> tuple[str, ...]:
    items = _list(body, "extra_aspects")
    if items is None:
        return ()
    aspects = _strings(items, "extra_aspects")
    if len(aspects) > MAX_EXTRA_ASPECTS:
        raise InvalidProfile("extra_aspects", f"at most {MAX_EXTRA_ASPECTS}")
    for i, aspect in enumerate(aspects):
        if not aspect or len(aspect) > MAX_ASPECT_CHARS or not set(aspect) <= _ASPECT_CHARS:
            raise InvalidProfile(
                f"extra_aspects[{i}]", f"must be 1..{MAX_ASPECT_CHARS} characters of = + - & #"
            )
    return tuple(sorted(set(aspects)))


def _voltage_labels(body: Mapping[str, Any]) -> tuple[str, ...]:
    items = _list(body, "voltage_labels")
    if items is None:
        return ()
    labels = _strings(items, "voltage_labels")
    if len(labels) > MAX_VOLTAGE_LABELS:
        raise InvalidProfile("voltage_labels", f"at most {MAX_VOLTAGE_LABELS}")
    for i, label in enumerate(labels):
        if not label.strip() or len(label) > MAX_VOLTAGE_LABEL_CHARS:
            raise InvalidProfile(
                f"voltage_labels[{i}]", f"must be 1..{MAX_VOLTAGE_LABEL_CHARS} characters"
            )
    return tuple(sorted(set(labels)))


def _vendor_library(body: Mapping[str, Any], symbol: SymbolStandard) -> str | None:
    value = body.get("vendor_library")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidProfile("vendor_library", "must be a non-empty string or null")
    if len(value) > MAX_VENDOR_LIBRARY_CHARS:
        raise InvalidProfile("vendor_library", f"at most {MAX_VENDOR_LIBRARY_CHARS} characters")
    if symbol is not SymbolStandard.VENDOR:
        raise InvalidProfile("vendor_library", "allowed only with symbol_standard 'vendor'")
    return value.strip()


def _plc_vendor(body: Mapping[str, Any]) -> str:
    value = body.get("plc_vendor", "siemens")
    if value not in PLC_VENDORS:
        raise InvalidProfile("plc_vendor", f"must be one of: {', '.join(sorted(PLC_VENDORS))}")
    return str(value)


def _number(value: object, path: str, kind: type, low: float, high: float) -> float:
    """``int`` fields reject a float and a bool; ``float`` fields take an int or a float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidProfile(path, "must be a number")
    if kind is int and not isinstance(value, int):
        raise InvalidProfile(path, "must be an integer")
    if not low <= value <= high:
        raise InvalidProfile(path, f"must be between {low} and {high}")
    return float(value) if kind is float else value


def _thresholds(raw: object, default: ClassificationThresholds) -> ClassificationThresholds:
    if raw is None:
        return default
    if not isinstance(raw, Mapping):
        raise InvalidProfile("classification", "must be an object")
    for key in raw:
        if key not in _THRESHOLD_RANGES:
            raise InvalidProfile(f"classification.{key}", "unknown field")
    values: dict[str, Any] = {}
    for name, (kind, low, high) in _THRESHOLD_RANGES.items():
        if name in raw:
            values[name] = _number(raw[name], f"classification.{name}", kind, low, high)
    return replace(default, **values)
