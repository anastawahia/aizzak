"""Where the rules come from. ``shared`` never opens a file: an adapter reads the packaged
``identifiers.toml`` and drawings' ``producers.toml`` once at build time (failing the boot if
either is invalid) and answers from memory."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.modules.electrical.drawings.domain.profile_detection import ProducerSignature
from app.modules.electrical.shared.rules import IdentifierRules


class RuleSource(Protocol):
    def identifier_rules(self) -> IdentifierRules: ...

    def producer_signatures(self) -> Sequence[ProducerSignature]: ...
