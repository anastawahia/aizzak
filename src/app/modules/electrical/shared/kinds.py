"""Value types of the identifier kernel (FR-ID-01..13). Stdlib only, immutable."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class IdentifierKind(StrEnum):
    REFERENCE_DESIGNATION = "reference_designation"  # IEC 81346 / GB/T 5094 (FR-ID-01)
    LETTER_CODE = "letter_code"  # KM1, 1KM, CR1 (FR-ID-13)
    TERMINAL = "terminal"  # -X1:14, X1.14, XT1:5, 1XT-5 (FR-ID-03)
    BRIDGE = "bridge"  # FR-ID-03
    WIRE_NUMBER = "wire_number"  # FR-ID-04
    PLC_ADDRESS = "plc_address"  # FR-ID-05
    CROSS_REFERENCE = "cross_reference"  # FR-ID-06
    CONNECTION_POINT = "connection_point"  # A1, 13, 95 (FR-ID-07)
    VOLTAGE = "voltage"  # L1, PE, +24V (FR-ID-08)
    KKS_TAG = "kks_tag"  # 20XYZ10AB123, =20XYZ10AB123 (FR-ID-15, D-36)
    ANSI_DEVICE = "ansi_device"  # 87T, 50BF, 27-1, 50/51 (FR-ID-16, D-36)


class Convention(StrEnum):  # FR-ID-13
    IEC_81346 = "iec_81346"
    GBT_5094 = "gbt_5094"
    CN_LETTER_CODES = "cn_letter_codes"
    NFPA_JIC = "nfpa_jic"
    # Additive: they add to the document's ONE device convention and never compete with it,
    # so they are never "mixed" (FR-ING-12) and never imply a symbol standard. Appended LAST:
    # ties go to the earlier member, and the four above keep their order.
    KKS = "kks"  # plant designation (FR-ID-15)
    ANSI = "ansi"  # ANSI/IEEE C37.2 device function numbers (FR-ID-16)


DEVICE_CONVENTIONS: Final = frozenset(
    {Convention.IEC_81346, Convention.GBT_5094, Convention.CN_LETTER_CODES, Convention.NFPA_JIC}
)
ADDITIVE_CONVENTIONS: Final = frozenset({Convention.KKS, Convention.ANSI})


class PlcFamily(StrEnum):  # FR-ID-05, every form at once (D-15)
    S7_EN = "s7_en"  # I0.0 Q0.0 M10.1 IW64 PIW256
    S7_DE = "s7_de"  # E0.0 A0.0 EW64 PEW256
    TIA = "tia"  # %I0.0 %IW64
    S7_DB = "s7_db"  # DB10.DBX2.3 DB10.DBW4
    S7_200 = "s7_200"  # V100.0 VW100 AIW16 AQW16


class KksClass(StrEnum):  # FR-ID-15
    SYSTEM = "system"
    EQUIPMENT = "equipment"
    COMPONENT = "component"
    CABLE = "cable"


@dataclass(frozen=True, slots=True)
class KksPart:
    name: str  # the rules' part name: "system" | "equipment" | "component" | "cable"
    level: int | None  # 1..3 (FR-ID-15); None for a cable number
    text: str  # "20XYZ10"
    runs: tuple[str, ...]  # ("20", "XYZ", "10"): the maximal runs of one position type


@dataclass(frozen=True, slots=True)
class KksTag:
    form: str  # the rules' form name ("equipment", "equipment_cable", ...)
    kks_class: KksClass
    prefix: str | None  # "=" | "+" | "++" as written; None when absent
    prefix_kind: str | None  # the rules' name for it: "process" | "installation" | "location"
    parts: tuple[KksPart, ...]

    @property
    def level(self) -> int:  # the deepest level among the parts (1..3)
        return max((p.level for p in self.parts if p.level is not None), default=0)


@dataclass(frozen=True, slots=True)
class AnsiFunction:
    number: int  # 1..99
    suffix: str  # "" | "T" | "BF" ...
    function_class: str | None  # OUR class name from the rules (NFR-LIC-05); None if unmapped


@dataclass(frozen=True, slots=True)
class AnsiDevice:
    functions: tuple[AnsiFunction, ...]  # one; two or three for a compound (50/51)
    instance: int | None  # 27-1 -> 1


@dataclass(frozen=True, slots=True)
class Aspect:
    prefix: str  # "=" "+" "-" and the configured extras "==" "++" "&" "#"
    code: str  # "A1"


@dataclass(frozen=True, slots=True)
class PlcAddress:
    family: PlcFamily
    area: str  # canonical English: I Q M PI PQ V AI AQ DB
    width: str  # bit | byte | word | dword
    byte: int
    bit: int | None
    db_number: int | None


@dataclass(frozen=True, slots=True)
class NormalizedForms:  # FR-ID-09
    original: str  # exactly as it appeared (DATA-06, NFR-I18N-03)
    canonical: str  # full-width and digit folded, upper-cased, unified separators
    # Retrieval only (S-6): without leading zeros, without the leading "-", plus the keys
    # each kind adds. Two identifiers may share a key; identity is (kind, canonical).
    search_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedIdentifier:
    """One parsed identifier. Its identity is (``kind``, ``forms.canonical``); the
    ``forms.search_keys`` are for retrieval and never an equality test (S-6).

    ``valid`` is False when any issue invalidates it; the only non-invalidating issue is
    ``text.bidi_control`` (FR-ID-10: flagged, never dropped)."""

    kind: IdentifierKind
    convention: Convention | None
    forms: NormalizedForms
    aspects: tuple[Aspect, ...] = ()
    connection_point: str | None = None
    device_class: str | None = None  # suggested from the letter code (FR-ID-13)
    pole_role: str | None = None  # FR-ID-07
    plc: PlcAddress | None = None
    valid: bool = True  # FR-ID-10: flagged, never dropped
    issues: tuple[str, ...] = ()
    # Name of the rule that matched (cross-reference format, wire-number pattern);
    # additive to the LLD shape so `tally` can count cross-reference formats.
    variant: str | None = None
    # Additive (D-36), last and defaulted, so every existing construction stays valid:
    kks: KksTag | None = None
    ansi: AnsiDevice | None = None
    # The other kinds the SAME text reads as under the enabled rules, for the F2 pairs only
    # (bare number / ANSI, compound / cross-reference, prefixed KKS / IEC). Set only without
    # a hint: a caller with a hint has resolved it. Empty for every other kind (no change).
    alternatives: tuple[IdentifierKind, ...] = ()

    @property
    def needs_context(self) -> bool:
        """Q2 (a): an ANSI number names a function, not a device; its full identity comes
        from context (a KKS tag, a stacked second part, the inherited structure)."""
        return (
            self.kind is IdentifierKind.ANSI_DEVICE
            or IdentifierKind.ANSI_DEVICE in self.alternatives
        )


@dataclass(frozen=True, slots=True)
class ConventionTally:
    """What a bag of tokens says about the drawing's conventions (FR-ING-12)."""

    conventions: Mapping[Convention, int]
    plc_families: Mapping[PlcFamily, int]
    cross_reference_formats: Mapping[str, int]
    identifiers: int  # tokens that parsed as anything
    tokens: int  # tokens seen
