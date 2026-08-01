"""Memory value objects (pure — 06-domain-models §5).

Frozen, self-validating primitives. ``AgentKey`` is a module-local copy of the
agent slug VO — memory is self-contained and does not import another module's
value objects (module independence, 12-module-authoring-guide §3). ``VectorRef``
points at an indexed Qdrant point (collection + point id); a ``MemoryItem``
without one is simply pending indexing. Identifiers stay plain UUIDv7 text; the
domain imports no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.memory.domain.errors import InvalidMemoryInput

_MAX_AGENT_KEY_LEN = 64
_AGENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class MemoryKind(StrEnum):
    """What kind of memory an item represents."""

    SEMANTIC = "semantic"
    EPISODIC = "episodic"


@dataclass(frozen=True, slots=True)
class AgentKey:
    """The owning agent's slug (from its manifest). A trimmed, lower-cased
    1..64 character slug starting with a letter/digit."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not normalized or len(normalized) > _MAX_AGENT_KEY_LEN:
            raise InvalidMemoryInput(f"agent key must be 1..{_MAX_AGENT_KEY_LEN} characters")
        if not _AGENT_KEY_RE.match(normalized):
            raise InvalidMemoryInput(f"invalid agent key format: {self.value!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class VectorRef:
    """A pointer to an indexed Qdrant point: the owning collection + point id."""

    collection: str
    point_id: str

    def __post_init__(self) -> None:
        if not self.collection:
            raise InvalidMemoryInput("vector ref collection must not be empty")
        if not self.point_id:
            raise InvalidMemoryInput("vector ref point id must not be empty")
