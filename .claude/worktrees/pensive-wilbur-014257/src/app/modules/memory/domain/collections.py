"""Memory Qdrant collection naming + deterministic point ids (06-domain-models
§5, DD-04; 5.1-ج).

Mirrors ``knowledge``'s own ``chunk_point_id``/``knowledge_collection`` scheme
(``knowledge/domain/collections.py``) applied to ``memory``: one Qdrant
collection per workspace -- ``RecallRelevant``'s pre-existing inline
``f"mem-{ctx.workspace_id}"`` (``application/use_cases.py``), extracted here
so ``RecallRelevant`` and the new worker-facing ``IndexMemoryItem`` can never
silently drift onto two different collection names for the same workspace
(``memory_collection`` below is a byte-for-byte non-behavioural refactor of
that pre-existing expression, not a new naming scheme).

``memory_point_id`` derives a **deterministic**, idempotent Qdrant point id
from ``memory_id`` alone via ``uuid5`` over a fixed namespace -- unlike a
``knowledge.Chunk`` (many chunks per document, disambiguated by ``seq``), one
``MemoryItem`` is always exactly one point, so ``memory_id`` alone is already
a unique key. Determinism matters for the SAME reason ``chunk_point_id``'s
docstring gives: a retry after a partial failure (``IndexMemoryItem`` upserts
the point, then ``MemoryRepository.save`` fails before ``vector_ref`` is
persisted) must UPSERT the same point on the next attempt, never leak an
orphaned duplicate that a later ``RecallRelevant.execute`` could then return
twice for the same ``memory_id``.
"""

from __future__ import annotations

import uuid

# Fixed, frozen namespace UUID for memory_point_id's uuid5 derivation --
# generated once (uuid.uuid4()) and never regenerated: changing it would
# silently re-point every previously-indexed memory item at a brand-new
# Qdrant point id on the next index run. Distinct from knowledge's own
# `_KNOWLEDGE_NS` (module independence -- 12-module-authoring-guide §3).
_MEMORY_NS = uuid.UUID("76b836ab-7135-40f0-8f55-bab063f42a86")


def memory_collection(workspace_id: str) -> str:
    """The single per-workspace Qdrant collection name (DD-04 tenant
    isolation) -- mirrors ``knowledge``'s ``kn-<workspace_id>``."""
    return f"mem-{workspace_id}"


def memory_point_id(memory_id: str) -> str:
    """Deterministic Qdrant point id for one memory item, derived from
    ``memory_id`` via ``uuid5`` -- re-indexing (a retry, at-least-once event
    redelivery) always upserts the same point, never a duplicate.

    Distinct from ``MemoryItem.id`` itself (a fresh UUIDv7 minted per DD-02):
    this is the vector-store identity, not the row identity -- the
    ``knowledge.chunk_point_id`` precedent, applied 1:1 instead of per-chunk.
    """
    return str(uuid.uuid5(_MEMORY_NS, memory_id))
