"""Knowledge Qdrant collection naming + deterministic point ids (06-domain-
models §7, DD-04; 3.k3).

Mirrors ``memory``'s ``mem-<workspace_id>`` per-workspace collection scheme
(``memory/application/use_cases.py::RecallRelevant``) with a ``kn-`` prefix
instead — one Qdrant collection per workspace, shared by every document in
it; per-document/per-chunk isolation is a payload filter (``workspace_id``,
``document_id``), not a collection-per-document scheme.

``chunk_point_id`` derives a **deterministic**, idempotent Qdrant point id
from ``(document_id, seq)`` via ``uuid5`` over a fixed namespace: re-indexing
the same document/seq (a retry, a worker crash-restart, at-least-once event
redelivery) always upserts the *same* point instead of leaking duplicates —
this is what keeps ``IndexDocument`` naturally idempotent without a separate
dedup step, mirroring INV-K1 (``UNIQUE(document_id, seq)``, `01-data-model
§2.7`) projected onto the vector store.
"""

from __future__ import annotations

import uuid

# Fixed, frozen namespace UUID for chunk_point_id's uuid5 derivation --
# generated once (uuid.uuid4()) and never regenerated. Changing it would
# silently re-point every previously-indexed chunk at a brand-new Qdrant
# point id on the next index run.
_KNOWLEDGE_NS = uuid.UUID("5561cc57-1a7e-43a9-81de-1c93c6192ccf")


def knowledge_collection(workspace_id: str) -> str:
    """The single per-workspace Qdrant collection name (DD-04 tenant
    isolation) — mirrors ``memory``'s ``mem-<workspace_id>``."""
    return f"kn-{workspace_id}"


def chunk_point_id(document_id: str, seq: int) -> str:
    """Deterministic Qdrant point id for one chunk, derived from
    ``document_id`` plus its 0-based ``seq`` (INV-K1) via ``uuid5`` —
    re-indexing always upserts the same point, never a duplicate.

    Distinct from the future ``Chunk.id`` (a fresh UUIDv7 minted per DD-02):
    this is the vector-store identity, not the row identity.
    """
    return str(uuid.uuid5(_KNOWLEDGE_NS, f"{document_id}:{seq}"))
