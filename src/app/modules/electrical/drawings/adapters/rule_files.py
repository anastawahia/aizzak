"""``PackagedRuleSource``: the packaged rule files, read ONCE (LLD 3.3.7).

``shared`` never opens a file, so this adapter does: ``shared/rules/identifiers.toml`` through
``importlib.resources`` into ``IdentifierRules.from_mapping`` and ``adapters/data/producers.toml``
into ``ProducerSignature`` values. Both are read, parsed and validated at construction and
answered from memory afterwards; a file that does not validate fails the boot (``RuleError``),
not the first document.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Final

from app.modules.electrical.drawings.domain.profile import SymbolStandard
from app.modules.electrical.drawings.domain.profile_detection import ProducerSignature
from app.modules.electrical.shared.rules import IdentifierRules, RuleError

_IDENTIFIERS_PACKAGE: Final = "app.modules.electrical.shared.rules"
_PRODUCERS_PACKAGE: Final = "app.modules.electrical.drawings.adapters.data"
_MAX_PATTERN_CHARS: Final = 128


def _parse(text: str, what: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RuleError(f"{what} is not valid TOML: {exc}") from exc


def parse_producers(data: Mapping[str, object]) -> tuple[ProducerSignature, ...]:
    """Validate ``producers.toml``'s parsed form; ``RuleError`` names the offending entry."""
    entries = data.get("producer")
    if not isinstance(entries, list) or not entries:
        raise RuleError("producers.toml needs at least one [[producer]]")
    signatures: list[ProducerSignature] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"producer #{index + 1}"
        if not isinstance(entry, dict):
            raise RuleError(f"{where} must be a table")
        unknown = sorted(set(entry) - {"pattern", "family", "symbol_hint", "weight"})
        if unknown:
            raise RuleError(f"{where} has unknown keys {unknown}")
        pattern, family = entry.get("pattern"), entry.get("family")
        if not isinstance(pattern, str) or not 0 < len(pattern.strip()) <= _MAX_PATTERN_CHARS:
            raise RuleError(f"{where}: pattern must be a non-empty string")
        if not isinstance(family, str) or not family.strip():
            raise RuleError(f"{where}: family must be a non-empty string")
        weight = entry.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, int | float) or not 0 < weight <= 1:
            raise RuleError(f"{where}: weight must be a number in (0, 1]")
        hint_raw = entry.get("symbol_hint")
        hint: SymbolStandard | None = None
        if hint_raw is not None:
            try:
                hint = SymbolStandard(hint_raw)
            except ValueError as exc:
                raise RuleError(f"{where}: unknown symbol_hint {hint_raw!r}") from exc
        key = (pattern.lower(), family)
        if key in seen:
            raise RuleError(f"{where}: duplicate pattern/family {key}")
        seen.add(key)
        signatures.append(
            ProducerSignature(
                pattern=pattern, family=family, symbol_hint=hint, weight=float(weight)
            )
        )
    return tuple(signatures)


class PackagedRuleSource:
    """``RuleSource`` over the packaged files. ``from_texts`` builds one from given text, for
    tests and for tools that bring their own rules."""

    def __init__(self, *, identifiers_toml: str | None = None, producers_toml: str | None = None):
        if identifiers_toml is None:
            identifiers_toml = (
                resources.files(_IDENTIFIERS_PACKAGE)
                .joinpath("identifiers.toml")
                .read_text(encoding="utf-8")
            )
        if producers_toml is None:
            producers_toml = (
                resources.files(_PRODUCERS_PACKAGE)
                .joinpath("producers.toml")
                .read_text(encoding="utf-8")
            )
        self._rules = IdentifierRules.from_mapping(_parse(identifiers_toml, "identifiers.toml"))
        self._producers = parse_producers(_parse(producers_toml, "producers.toml"))

    @classmethod
    def from_texts(cls, identifiers_toml: str, producers_toml: str) -> PackagedRuleSource:
        return cls(identifiers_toml=identifiers_toml, producers_toml=producers_toml)

    def identifier_rules(self) -> IdentifierRules:
        return self._rules

    def producer_signatures(self) -> Sequence[ProducerSignature]:
        return self._producers
