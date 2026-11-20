"""The ONE identifier parser (FR-ID-12), shared by extraction, indexing, report
understanding, output checking and integration.

Each kind has a pure function ``(canonical, forms, rules) -> ParsedIdentifier | None``;
the parser tries them in a FIXED order, so the result is deterministic. The order puts
the unambiguous shapes first. Tokens that are legitimately two things are resolved by
that order, and a caller who knows better passes ``hint``:

* ``12.3`` reads as a cross-reference (hint ``WIRE_NUMBER`` for the page/column wire);
* ``13`` and ``95`` read as wire numbers (hint ``CONNECTION_POINT`` for the pole label);
* ``M10.1`` is a PLC address, ``M1`` an NFPA letter code, ``M`` a voltage label.

``None`` means "no identifier at all"; a malformed identifier comes back with
``valid=False`` and its issues (FR-ID-10), never dropped. Hidden characters in the text
(control, format, default-ignorable) are dropped from the canonical form but flagged
``text.hidden_character`` (invalid); bidi controls are flagged ``text.bidi_control`` and
the identifier stays valid. Combining marks that stay after normalisation (``KM1`` + U+0301,
``Ké``) are never silently dropped (INC1-Q-6): when the text without them is an identifier it
comes back ``valid=False`` with ``text.non_ascii``, its canonical form keeping the marks;
text in another script (Chinese prose, Cyrillic) that no mark-free ASCII form explains is
no identifier. Identity is (``kind``, ``canonical``); ``search_keys`` are
for retrieval only.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.modules.electrical.shared.ansi import (
    is_ansi_evidence,
    parse_ansi_compound,
    parse_ansi_compound_in_context,
    parse_ansi_device,
    parse_ansi_device_in_context,
    read_compound,
)
from app.modules.electrical.shared.connection_points import is_valid_label, parse_connection_point
from app.modules.electrical.shared.cross_references import parse_cross_reference
from app.modules.electrical.shared.groups import (
    ISSUE_EMPTY_MEMBER,
    ISSUE_MEMBER_INVALID,
    ISSUE_MEMBER_UNREADABLE,
    ISSUE_TOO_MANY,
    LIST_SEPARATOR,
    RANGE_SEPARATOR,
    inherit,
    list_items,
    range_members,
    suffix_members,
)
from app.modules.electrical.shared.kinds import (
    ADDITIVE_CONVENTIONS,
    Convention,
    ConventionTally,
    GroupForm,
    GroupMember,
    IdentifierGroup,
    IdentifierKind,
    NormalizedForms,
    ParsedIdentifier,
    PlcFamily,
)
from app.modules.electrical.shared.kks import is_kks_evidence, parse_kks_tag
from app.modules.electrical.shared.letter_codes import parse_letter_code
from app.modules.electrical.shared.normalization import (
    ISSUE_NON_ASCII,
    NON_INVALIDATING_ISSUES,
    canonical_form,
    dedupe,
    folded,
    has_enclosed_number,
    normalize,
    text_issues,
)
from app.modules.electrical.shared.plc_siemens import parse_plc_address
from app.modules.electrical.shared.ratings import read_rating
from app.modules.electrical.shared.reference_designation import parse_reference_designation
from app.modules.electrical.shared.terminals import parse_bridge, parse_terminal
from app.modules.electrical.shared.tokens import split_candidates
from app.modules.electrical.shared.voltages import parse_voltage
from app.modules.electrical.shared.wire_numbers import parse_wire_number

if TYPE_CHECKING:
    from app.modules.electrical.shared.rules import IdentifierRules

# Longer text is prose. The bound applies to the raw text AND to the canonical form (NFKC
# can expand one character into many), so it bounds every regular expression's input.
MAX_TEXT_CHARS = 200
MAX_GROUP_KEYS = 64  # F3: a group's search keys (its own form, then each member's)

_Parse = Callable[[str, NormalizedForms, "IdentifierRules"], ParsedIdentifier | None]

_KKS_LEADS = ("=", "+")
_BARE_KINDS = frozenset({IdentifierKind.WIRE_NUMBER, IdentifierKind.CONNECTION_POINT})

_ORDER: tuple[tuple[IdentifierKind, _Parse], ...] = (
    (IdentifierKind.PLC_ADDRESS, parse_plc_address),
    (IdentifierKind.ANSI_DEVICE, parse_ansi_compound),  # 50/51 before 12/3 (D-36)
    (IdentifierKind.CROSS_REFERENCE, parse_cross_reference),
    (IdentifierKind.VOLTAGE, parse_voltage),
    (IdentifierKind.BRIDGE, parse_bridge),
    (IdentifierKind.TERMINAL, parse_terminal),
    (IdentifierKind.KKS_TAG, parse_kks_tag),  # before IEC, for =20XYZ10AB123 (D-36)
    (IdentifierKind.REFERENCE_DESIGNATION, parse_reference_designation),
    (IdentifierKind.LETTER_CODE, parse_letter_code),
    (IdentifierKind.ANSI_DEVICE, parse_ansi_device),  # 87T 50BF 27-1, never a bare number
    (IdentifierKind.WIRE_NUMBER, parse_wire_number),
    (IdentifierKind.CONNECTION_POINT, parse_connection_point),
)

# With a hint the caller has the context (STG-05: a bubble, a stacked label, a device symbol).
_HINTED: Mapping[IdentifierKind, tuple[_Parse, ...]] = MappingProxyType(
    {IdentifierKind.ANSI_DEVICE: (parse_ansi_compound_in_context, parse_ansi_device_in_context)}
)


def _alternatives(
    found: ParsedIdentifier, forms: NormalizedForms, rules: IdentifierRules
) -> tuple[IdentifierKind, ...]:
    """The other kinds the SAME text reads as under the enabled rules, for the F2 pairs only
    (D-36): a bare number against an ANSI function number, an ANSI compound against a
    cross-reference (either way round), a KKS tag with a prefix against an IEC designation."""
    canonical = forms.canonical
    if Convention.ANSI in rules.conventions:
        if (
            found.kind in _BARE_KINDS
            and parse_ansi_device_in_context(canonical, forms, rules) is not None
        ):
            return (IdentifierKind.ANSI_DEVICE,)  # F3: a bare number, or a wire a pattern read
        if found.kind is IdentifierKind.ANSI_DEVICE and found.ansi is not None:
            if (
                len(found.ansi.functions) > 1
                and parse_cross_reference(canonical, forms, rules) is not None
            ):
                return (IdentifierKind.CROSS_REFERENCE,)
        elif found.kind is IdentifierKind.CROSS_REFERENCE and read_compound(canonical, rules):
            return (IdentifierKind.ANSI_DEVICE,)
    if (
        found.kind is IdentifierKind.KKS_TAG
        and found.kks is not None
        and found.kks.prefix
        and parse_reference_designation(forms.canonical, forms, rules) is not None
    ):
        return (IdentifierKind.REFERENCE_DESIGNATION,)
    return ()


def _flag_text(found: ParsedIdentifier, issues: tuple[str, ...]) -> ParsedIdentifier:
    """FR-ID-10: hidden characters and bidi controls are reported, never silent."""
    if not issues:
        return found
    all_issues = tuple(dict.fromkeys((*found.issues, *issues)))
    invalidating = any(i not in NON_INVALIDATING_ISSUES for i in issues)
    return replace(found, valid=found.valid and not invalidating, issues=all_issues)


class IdentifierParser:
    def __init__(self, rules: IdentifierRules) -> None:
        self._rules = rules

    @property
    def rules(self) -> IdentifierRules:
        return self._rules

    def parse(self, text: str, *, hint: IdentifierKind | None = None) -> ParsedIdentifier | None:
        """None when the text is no identifier at all; a ParsedIdentifier with
        valid=False when it is one, malformed (FR-ID-10)."""
        if not text.strip() or len(text) > MAX_TEXT_CHARS:
            return None
        if hint is None and has_enclosed_number(text):
            return None  # F3 (gap 14): ⑰ is a marker, not wire 17 (NFKC would fold it to 17)
        forms = normalize(text)
        if len(forms.canonical) > MAX_TEXT_CHARS or not any(ch.isalnum() for ch in forms.canonical):
            return None
        if not forms.canonical.isascii():
            return self._parse_non_ascii(text, forms, hint)
        return self._first(forms, hint, text)

    def _first(
        self,
        forms: NormalizedForms,
        hint: IdentifierKind | None,
        text: str,
        *,
        grouped: bool = True,
    ) -> ParsedIdentifier | None:
        if hint is None:
            if self._is_rating(forms, text):
                return None  # F3 (FR-ID-20): a value with a unit is a rating, never an identifier
            if grouped and (group := self._group(forms)) is not None:
                return _flag_text(group, text_issues(text))
        candidates: Iterable[_Parse] = (
            _HINTED[hint]
            if hint is not None and hint in _HINTED
            else (p for k, p in _ORDER if hint is None or k is hint)
        )
        for parse in candidates:
            found = parse(forms.canonical, forms, self._rules)
            if found is not None:
                if hint is None and (alternatives := _alternatives(found, forms, self._rules)):
                    found = replace(found, alternatives=alternatives)
                return _flag_text(found, text_issues(text))
        return None

    def _member(self, text: str) -> ParsedIdentifier | None:
        """One member of a group, read alone and never as a group (the depth is 1)."""
        return self._first(normalize(text), None, text, grouped=False)

    def _group(self, forms: NormalizedForms) -> ParsedIdentifier | None:
        """F3 (FR-ID-19): the text as a range, a list or a suffix list, or None. A range is
        tried first (before the terminals: ``X23-X37``), then a list, then a suffix list."""
        canonical = forms.canonical
        limit = self._rules.groups.max_members
        if RANGE_SEPARATOR in canonical and (ranged := range_members(canonical, limit)):
            members, too_many = ranged
            return self._group_result(
                forms, GroupForm.RANGE, RANGE_SEPARATOR, [self._plain(t) for t in members], too_many
            )
        if (items := list_items(canonical)) is not None:
            return self._list_group(forms, items, limit)
        return self._suffix_group(forms, limit)

    def _plain(self, text: str) -> tuple[str, ParsedIdentifier | None]:
        return text, self._member(text)

    def _list_group(
        self, forms: NormalizedForms, items: tuple[str, ...], limit: int
    ) -> ParsedIdentifier | None:
        if not items[0]:
            return None
        first = self._member(items[0])
        if first is None:
            return None
        if len(items) > limit:
            return self._group_result(forms, GroupForm.LIST, LIST_SEPARATOR, [], True)
        members: list[tuple[str, ParsedIdentifier | None]] = [(items[0], first)]
        for item in items[1:]:
            alone = self._member(item) if item else None
            inherited = (
                first.connection_point is not None
                and bool(item)
                and is_valid_label(item)
                and (alone is None or alone.kind in _BARE_KINDS)
            )
            if inherited:
                assert first.connection_point is not None
                text = inherit(items[0], first.connection_point, item)
                members.append((text, self._member(text)))
            else:
                members.append((item, alone))
        return self._group_result(forms, GroupForm.LIST, LIST_SEPARATOR, members, False)

    def _suffix_group(self, forms: NormalizedForms, limit: int) -> ParsedIdentifier | None:
        canonical = forms.canonical
        for separator, kinds in self._rules.groups.suffix_lists.items():
            if separator not in canonical:
                continue
            head, *tails = canonical.split(separator)
            first = self._member(head) if head else None
            if first is None or first.kind not in kinds:
                continue
            if len(tails) + 1 > limit:
                return self._group_result(forms, GroupForm.SUFFIX_LIST, separator, [], True)
            texts = suffix_members(head, tuple(tails))
            if texts is None:
                continue
            members = [(head, first), *(self._plain(t) for t in texts[1:])]
            return self._group_result(forms, GroupForm.SUFFIX_LIST, separator, members, False)
        return None

    def _group_result(
        self,
        forms: NormalizedForms,
        form: GroupForm,
        separator: str,
        read: list[tuple[str, ParsedIdentifier | None]],
        too_many: bool,
    ) -> ParsedIdentifier:
        issues: list[str] = [ISSUE_TOO_MANY] if too_many else []
        for text, identifier in read:
            if not text:
                issues.append(ISSUE_EMPTY_MEMBER)
            elif identifier is None:
                issues.append(ISSUE_MEMBER_UNREADABLE)
            elif not identifier.valid:
                issues.append(ISSUE_MEMBER_INVALID)
        members = tuple(GroupMember(text, identifier) for text, identifier in read)
        conventions = {m.identifier.convention for m in members if m.identifier is not None}
        keys = dedupe(
            [forms.canonical, *(m.identifier.forms.canonical for m in members if m.identifier)]
        )[:MAX_GROUP_KEYS]
        return ParsedIdentifier(
            kind=IdentifierKind.IDENTIFIER_GROUP,
            convention=conventions.pop() if len(conventions) == 1 else None,
            forms=NormalizedForms(forms.original, forms.canonical, keys),
            valid=not issues,
            issues=tuple(dict.fromkeys(issues)),
            group=IdentifierGroup(form, separator, members),
        )

    def _is_rating(self, forms: NormalizedForms, text: str) -> bool:
        """A supply voltage label (``24VDC``, ``+24V``, ``0V``, or the profile's) is a voltage
        (FR-ID-08), whatever its shape; any other value with a unit of ``[ratings]`` is a rating."""
        if forms.canonical in self._rules.voltage_labels:
            return False
        if text.isascii() and not text.rstrip()[-1:].isalpha():
            return False  # a rating ends in its unit: skip the fold for the common text
        return read_rating(folded(text), self._rules) is not None

    def _parse_non_ascii(
        self, text: str, forms: NormalizedForms, hint: IdentifierKind | None
    ) -> ParsedIdentifier | None:
        """INC1-Q-6: the identifier hiding behind non-ASCII characters is reported invalid,
        under its own canonical form (so it never collides with the plain one)."""
        skeleton = "".join(
            ch
            for ch in unicodedata.normalize("NFD", forms.canonical)
            if unicodedata.category(ch) not in ("Mn", "Me")
        )
        if not skeleton.isascii() or not any(ch.isalnum() for ch in skeleton):
            return None  # other scripts (Chinese prose, Cyrillic) are no identifier here
        found = self._first(normalize(skeleton), hint, text)
        if found is None:
            return None
        return replace(
            found,
            forms=NormalizedForms(forms.original, forms.canonical, (forms.canonical,)),
            valid=False,
            issues=tuple(dict.fromkeys((*found.issues, ISSUE_NON_ASCII))),
        )

    def tally(self, tokens: Iterable[str]) -> ConventionTally:
        """What a bag of tokens says about the conventions (FR-ING-12). The device conventions,
        the PLC families and the formats come from this parser's own rules, as always. KKS and
        ANSI (D-36) are seen even when these rules did not enable them: a probe parser with
        both enabled reads the same tokens, so the profile proposal can suggest them."""
        conventions: Counter[Convention] = Counter()
        families: Counter[PlcFamily] = Counter()
        formats: Counter[str] = Counter()
        enabled = self._rules.conventions
        probe = (
            None
            if enabled >= ADDITIVE_CONVENTIONS
            else IdentifierParser(self._rules.with_additive())
        )
        seen = parsed = 0
        for token in tokens:
            seen += 1
            for candidate in split_candidates(token):  # F3 (gap 34): a word may hold several
                found = self.parse(candidate)
                if found is not None:
                    for reading in _readings(found):  # F3: each member of a group is one
                        parsed += 1
                        if ISSUE_NON_ASCII not in reading.issues:
                            self._count(reading, conventions, families, formats)
                if probe is not None and _may_be_additive(candidate):
                    extra = probe.parse(candidate)
                    for reading in () if extra is None else _readings(extra):
                        if (
                            reading.convention in ADDITIVE_CONVENTIONS
                            and reading.convention not in enabled
                            and _is_evidence(reading, self._rules)
                        ):
                            conventions[reading.convention] += 1
        return ConventionTally(
            conventions=MappingProxyType(dict(sorted(conventions.items()))),
            plc_families=MappingProxyType(dict(sorted(families.items()))),
            cross_reference_formats=MappingProxyType(dict(sorted(formats.items()))),
            identifiers=parsed,
            tokens=seen,
        )

    def _count(
        self,
        found: ParsedIdentifier,
        conventions: Counter[Convention],
        families: Counter[PlcFamily],
        formats: Counter[str],
    ) -> None:
        # lookalike / mixed-script text (text.non_ascii) is no evidence: the caller skips it
        if found.convention is not None and (
            found.convention not in ADDITIVE_CONVENTIONS or _is_evidence(found, self._rules)
        ):
            conventions[found.convention] += 1
        if found.plc is not None:
            families[found.plc.family] += 1
        if found.kind is IdentifierKind.CROSS_REFERENCE and found.variant is not None:
            formats[found.variant] += 1


def _readings(found: ParsedIdentifier) -> tuple[ParsedIdentifier, ...]:
    """What a parse counts: the identifier itself, or for a group each member that read (F3)."""
    if found.group is None:
        return (found,)
    return tuple(m.identifier for m in found.group.members if m.identifier is not None)


def _may_be_additive(token: str) -> bool:
    """KKS and ANSI start with a digit, or with a KKS prefix (``=`` ``+``): the probe skips the
    rest, which keeps its cost to the tokens that can matter (NFR-F2-4). The length is checked
    first: parse refuses it before normalising, so does the probe (S-4)."""
    if len(token) > MAX_TEXT_CHARS:
        return False
    lead = canonical_form(token)[:1]
    return lead.isdigit() or lead in _KKS_LEADS


def _is_evidence(found: ParsedIdentifier, rules: IdentifierRules) -> bool:
    """Does a KKS or ANSI reading count as evidence of that convention (D-36)? A VALID reading
    without ``text.non_ascii``, then the convention's own rule: ``is_ansi_evidence`` (a function
    with a class, not a rating) or ``is_kks_evidence`` (an equipment tag or deeper)."""
    if not found.valid or ISSUE_NON_ASCII in found.issues:
        return False
    if found.convention is Convention.ANSI:
        return is_ansi_evidence(found, rules)
    if found.convention is Convention.KKS:
        return is_kks_evidence(found, rules)
    return True
