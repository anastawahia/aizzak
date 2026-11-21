"""FR-ID-16: ANSI/IEEE C37.2 device function numbers (``87T``, ``50BF``, ``27-1``, ``50/51``).

Numbers, suffix letters and OUR class names only, from the rules (NFR-LIC-05): no standard text
is embedded. A suffix is compared by membership in the rules' set (the packaged ones plus the
profile's); it is never compiled into a pattern (NFR-F2-3).

ANSI is additive: it is read only when ``Convention.ANSI`` is enabled. A BARE number (``52``)
names a function, not a device, so without context it stays what it was (a wire number); a
caller that has the context (a symbol bubble, a stacked label: STG-05) passes ``hint`` and gets
the ``*_in_context`` readings. ``needs_context`` on the result tells the caller the full
identity still has to come from context.

F3 (FR-ID-16): without ``hint`` a number with a suffix or an instance is read as a device only
when its number has a class in ``[ansi.functions]`` (a compound: every number has one). Wire
numbers, ratings, ranges and poles (``38Y``, ``8P``, ``23-4``) have numbers outside the table, so
they are no device without context. A wire-number pattern the profile named, and that matches
the text, wins over the ANSI reading (the reading stays an alternative, as for a pinned
cross-reference format). A suffix followed by ONE digit is an instance without the dash (``59G2``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from app.modules.electrical.shared.cross_references import parse_cross_reference
from app.modules.electrical.shared.kinds import (
    AnsiDevice,
    AnsiFunction,
    Convention,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
)
from app.modules.electrical.shared.normalization import with_keys
from app.modules.electrical.shared.patterns import compiled

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

ISSUE_UNKNOWN_SUFFIX: Final = "ansi.unknown_suffix"
COMPOUND_SEPARATOR: Final = "/"
MIN_COMPOUND: Final = 2
_FUNCTION: Final = re.compile(r"(?P<num>[1-9][0-9]?)(?P<suffix>[A-Z]{0,3})", re.ASCII)
_SINGLE: Final = re.compile(
    r"(?P<num>[1-9][0-9]?)(?:(?P<suffix>[A-Z]{1,3})(?P<ord>[1-9])?)?(?:-(?P<inst>[1-9][0-9]?))?",
    re.ASCII,
)
_BARE: Final = re.compile(r"[1-9][0-9]?", re.ASCII)


def is_bare_ansi_number(canonical: str) -> bool:
    """``1``..``99`` without a leading zero: a function number with nothing else."""
    return _BARE.fullmatch(canonical) is not None


def is_ansi_evidence(found: ParsedIdentifier, rules: IdentifierRules) -> bool:
    """D-36: a reading that proves a document uses ANSI device numbers. VALID, one function
    and no other reading (a bare number or a compound proves nothing), a number with a class
    in [ansi.functions], and a suffix outside [ansi] non_evidence_suffixes (a rating letter,
    as in a breaker's 32A) or else an instance (27-1). The tally counts these, and a voltage
    label may not hide one."""
    if found.ansi is None or not found.valid or found.alternatives:
        return False
    if len(found.ansi.functions) != 1:
        return False
    function = found.ansi.functions[0]
    if function.function_class is None:
        return False
    if function.suffix:
        return function.suffix not in rules.ansi.non_evidence_suffixes
    return found.ansi.instance is not None


def _function(number: str, suffix: str, rules: IdentifierRules) -> AnsiFunction:
    value = int(number)
    return AnsiFunction(value, suffix, rules.ansi.functions.get(value))


def read_compound(canonical: str, rules: IdentifierRules) -> AnsiDevice | None:
    """Two to ``max_compound`` functions joined by ``/`` (``50/51``), each with an optional
    KNOWN suffix and no instance; None otherwise (or when ANSI is not enabled)."""
    if Convention.ANSI not in rules.conventions or COMPOUND_SEPARATOR not in canonical:
        return None
    texts = canonical.split(COMPOUND_SEPARATOR)
    if not MIN_COMPOUND <= len(texts) <= rules.ansi.max_compound:
        return None
    functions: list[AnsiFunction] = []
    for text in texts:
        match = _FUNCTION.fullmatch(text)
        if match is None or (match["suffix"] and match["suffix"] not in rules.ansi.suffixes):
            return None
        functions.append(_function(match["num"], match["suffix"], rules))
    return AnsiDevice(tuple(functions), None)


def _every_function_has_a_class(device: AnsiDevice) -> bool:
    return all(function.function_class is not None for function in device.functions)


def _compound_result(
    device: AnsiDevice, canonical: str, forms: NormalizedForms
) -> ParsedIdentifier:
    keys = canonical.split(COMPOUND_SEPARATOR)
    return ParsedIdentifier(
        kind=IdentifierKind.ANSI_DEVICE,
        convention=Convention.ANSI,
        forms=with_keys(forms, *keys, only=True),
        ansi=device,
    )


def parse_ansi_compound(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    device = read_compound(canonical, rules)
    if device is None or not _every_function_has_a_class(device):
        return None  # F3: without context a number outside the table is no device
    if rules.cross_reference_pinned and parse_cross_reference(canonical, forms, rules) is not None:
        return None  # the profile chose a format that reads this text: it is a cross-reference
    return _compound_result(device, canonical, forms)


def parse_ansi_compound_in_context(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    """A caller with the context (a stacked label in an ANSI bubble) reads the compound even
    when the profile pinned a cross-reference format."""
    device = read_compound(canonical, rules)
    return None if device is None else _compound_result(device, canonical, forms)


def _named_wire_reads(canonical: str, rules: IdentifierRules) -> bool:
    """F3: the profile named its wire-number patterns and one of them matches the text."""
    return rules.wire_number_pinned and any(
        compiled(pattern).fullmatch(canonical) for pattern in rules.wire_number_patterns.values()
    )


def _single(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules, *, bare: bool
) -> ParsedIdentifier | None:
    if Convention.ANSI not in rules.conventions:
        return None
    match = _SINGLE.fullmatch(canonical)
    if match is None:
        return None
    suffix, instance = match["suffix"] or "", match["inst"]
    if match["ord"] is not None:
        if instance is not None:
            return None  # 59G2-1: two instances
        instance = match["ord"]  # F3: a suffix then ONE digit is the instance (59G2)
    if not suffix and instance is None and not bare:
        return None  # a bare number is no device without context (FR-ID-16)
    function = _function(match["num"], suffix, rules)
    if not bare and (function.function_class is None or _named_wire_reads(canonical, rules)):
        return None  # F3: no class, or a wire pattern the profile named: no device without context
    unknown = bool(suffix) and suffix not in rules.ansi.suffixes
    return ParsedIdentifier(
        kind=IdentifierKind.ANSI_DEVICE,
        convention=Convention.ANSI,
        forms=with_keys(forms, only=True),
        device_class=function.function_class,
        valid=not unknown,
        issues=(ISSUE_UNKNOWN_SUFFIX,) if unknown else (),
        ansi=AnsiDevice((function,), None if instance is None else int(instance)),
    )


def parse_ansi_device(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    return _single(canonical, forms, rules, bare=False)


def parse_ansi_device_in_context(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None:
    return _single(canonical, forms, rules, bare=True)
