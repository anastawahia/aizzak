"""Knowledge value objects (pure — 06-domain-models §7).

Frozen, self-validating primitives. ``VectorRef`` is a module-local copy of
the same-shaped value object in ``memory`` (module independence,
12-module-authoring-guide §3) — knowledge does not import another module's
domain, even one that happens to look identical. Identifiers stay plain
UUIDv7 text; the domain imports no framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.knowledge.domain.errors import InvalidKnowledgeInput


class IndexStatus(StrEnum):
    """A ``Document``'s ingestion lifecycle (06 §7, INV-K2): one-way
    ``pending -> indexing -> (indexed | failed)``."""

    PENDING = "pending"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VectorRef:
    """A pointer to an indexed Qdrant point: the owning collection + point id."""

    collection: str
    point_id: str

    def __post_init__(self) -> None:
        if not self.collection:
            raise InvalidKnowledgeInput("vector ref collection must not be empty")
        if not self.point_id:
            raise InvalidKnowledgeInput("vector ref point id must not be empty")
