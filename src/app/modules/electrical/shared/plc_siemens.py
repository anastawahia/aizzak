"""FR-ID-05: Siemens PLC addresses in every family at once (D-15).

The grammar is fixed in code, because S7 forms are fixed (LLD §3.3.5):

* ``S7_EN``  ``I0.0 Q4.7 M10.1 IB1 IW64 QD8 PIW256``
* ``S7_DE``  ``E0.0 A4.7 EW64 PEW256`` (``M`` is shared; it reads as English)
* ``TIA``    ``%I0.0 %Q4.7 %IW64 %IW64:P``
* ``S7_DB``  ``DB10.DBX2.3 DB10.DBW4``
* ``S7_200`` ``V100.0 VW100 AIW16 AQW16``

A bit address needs ``.bit`` (0..7); an address with a width letter has no bit. An
unwidthed, bitless token such as ``M1`` is NOT a PLC address (it is the NFPA letter
code). Forms that look like an address but break these rules are flagged invalid
(FR-ID-10). Other vendors' forms (FR-ID-14) are not claimed.

Search keys carry the same address in every S7 spelling (English, TIA, German), so a
query in one family finds a drawing written in another.
"""

from __future__ import annotations

import re

from app.modules.electrical.shared.kinds import (
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
    PlcAddress,
    PlcFamily,
)
from app.modules.electrical.shared.normalization import dedupe, strip_leading_zeros
from app.modules.electrical.shared.rules import IdentifierRules

MAX_BIT = 7
_WIDTHS = {"B": "byte", "W": "word", "D": "dword"}
_LETTER = {"byte": "B", "word": "W", "dword": "D"}
_TIA = re.compile(r"%(?P<area>[IQM])(?P<w>[BWD])?(?P<byte>\d{1,5})(?:\.(?P<bit>\d+))?(?P<per>:P)?")
_EN = re.compile(
    r"(?:P(?P<parea>[IQ])(?P<pw>[BWD])|(?P<area>[IQM])(?P<w>[BWD])?)"
    r"(?P<byte>\d{1,5})(?:\.(?P<bit>\d+))?"
)
_DE = re.compile(
    r"(?:P(?P<parea>[EA])(?P<pw>[BWD])|(?P<area>[EAM])(?P<w>[BWD])?)"
    r"(?P<byte>\d{1,5})(?:\.(?P<bit>\d+))?"
)
_DB = re.compile(r"DB(?P<db>\d{1,5})\.DB(?P<w>[XBWD])(?P<byte>\d{1,5})(?:\.(?P<bit>\d+))?")
_S7_200 = re.compile(
    r"(?:(?P<aarea>AI|AQ)W(?P<abyte>\d{1,5})|V(?P<w>[BWD])?(?P<byte>\d{1,5})(?:\.(?P<bit>\d+))?)"
)
_PERCENT_SHAPE = re.compile(r"%[A-Z][A-Z0-9.:]*")
_GERMAN = {"I": "E", "Q": "A", "PI": "PE", "PQ": "PA"}
_FROM_GERMAN = {"E": "I", "A": "Q", "PE": "PI", "PA": "PQ"}


def _render(style: str, area: str, width: str, byte: int, bit: int | None) -> str:
    """One spelling of an I/Q/M/PI/PQ address: ``en``, ``tia`` or ``de``."""
    peripheral = area.startswith("P")
    name = area
    if style == "de":
        name = _GERMAN.get(area, area)
    text = f"{name}{byte}.{bit}" if width == "bit" else f"{name}{_LETTER[width]}{byte}"
    if style == "tia":
        return f"%{area[-1]}{text[len(name) :]}" + (":P" if peripheral else "")
    return text


def _build(
    forms: NormalizedForms,
    family: PlcFamily,
    area: str,
    width: str,
    byte: int,
    bit: int | None,
    db_number: int | None = None,
    extra_issues: tuple[str, ...] = (),
    spellings: bool = True,
) -> ParsedIdentifier:
    issues = list(extra_issues)
    if bit is not None and not 0 <= bit <= MAX_BIT:
        issues.append("plc.bit_out_of_range")
    if width == "bit" and bit is None:
        issues.append("plc.bit_missing")
    if width != "bit" and bit is not None:
        issues.append("plc.bit_on_non_bit_width")
    if spellings and not issues:
        keys = [
            forms.canonical,
            _render("en", area, width, byte, bit),
            _render("tia", area, width, byte, bit),
            _render("de", area, width, byte, bit),
        ]
    else:
        keys = [forms.canonical, strip_leading_zeros(forms.canonical)]
    return ParsedIdentifier(
        kind=IdentifierKind.PLC_ADDRESS,
        convention=None,
        forms=NormalizedForms(forms.original, forms.canonical, dedupe(keys)),
        plc=PlcAddress(family, area, width, byte, bit, db_number),
        valid=not issues,
        issues=tuple(issues),
    )


def _width_and_bit(letter: str | None, bit: str | None) -> tuple[str, int | None]:
    return (_WIDTHS[letter] if letter else "bit"), (int(bit) if bit is not None else None)


def _tia(forms: NormalizedForms) -> ParsedIdentifier | None:
    match = _TIA.fullmatch(forms.canonical)
    if match is None:
        return _unsupported(forms) if _PERCENT_SHAPE.fullmatch(forms.canonical) else None
    width, bit = _width_and_bit(match["w"], match["bit"])
    area = match["area"]
    extra: tuple[str, ...] = ()
    if match["per"]:
        if area == "M":
            extra = ("plc.peripheral_marker",)
        else:
            area = f"P{area}"
    return _build(forms, PlcFamily.TIA, area, width, int(match["byte"]), bit, None, extra)


def _data_block(match: re.Match[str], forms: NormalizedForms) -> ParsedIdentifier:
    letter = match["w"]
    width = "bit" if letter == "X" else _WIDTHS[letter]
    bit = int(match["bit"]) if match["bit"] is not None else None
    return _build(
        forms,
        PlcFamily.S7_DB,
        "DB",
        width,
        int(match["byte"]),
        bit,
        int(match["db"]),
        spellings=False,
    )


def _s7_200(match: re.Match[str], forms: NormalizedForms) -> ParsedIdentifier | None:
    if match["aarea"]:
        return _build(
            forms,
            PlcFamily.S7_200,
            match["aarea"],
            "word",
            int(match["abyte"]),
            None,
            spellings=False,
        )
    width, bit = _width_and_bit(match["w"], match["bit"])
    if width == "bit" and bit is None:
        return None  # "V100" alone is not an address
    return _build(forms, PlcFamily.S7_200, "V", width, int(match["byte"]), bit, spellings=False)


def parse_plc_address(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    families = rules.plc_families  # the families the profile enables
    if canonical.startswith("%"):
        return _tia(forms) if PlcFamily.TIA in families else None
    if PlcFamily.S7_DB in families and (match := _DB.fullmatch(canonical)):
        return _data_block(match, forms)
    if PlcFamily.S7_200 in families and (match := _S7_200.fullmatch(canonical)):
        return _s7_200(match, forms)
    if PlcFamily.S7_EN in families and (match := _EN.fullmatch(canonical)):
        return _classic(forms, match, PlcFamily.S7_EN, {})
    if PlcFamily.S7_DE in families and (match := _DE.fullmatch(canonical)):
        return _classic(forms, match, PlcFamily.S7_DE, _FROM_GERMAN)
    return None


def _classic(
    forms: NormalizedForms, match: re.Match[str], family: PlcFamily, to_english: dict[str, str]
) -> ParsedIdentifier | None:
    if match["parea"]:
        area = to_english.get(f"P{match['parea']}", f"P{match['parea']}")
        width, bit = _WIDTHS[match["pw"]], (int(match["bit"]) if match["bit"] else None)
    else:
        area = to_english.get(match["area"], match["area"])
        width, bit = _width_and_bit(match["w"], match["bit"])
        if width == "bit" and bit is None:
            return None  # "M1", "A1", "E5": no width and no bit is not an address
    return _build(forms, family, area, width, int(match["byte"]), bit)


def _unsupported(forms: NormalizedForms) -> ParsedIdentifier:
    return ParsedIdentifier(
        kind=IdentifierKind.PLC_ADDRESS,
        convention=None,
        forms=forms,
        plc=None,
        valid=False,
        issues=("plc.unsupported_form",),
    )
