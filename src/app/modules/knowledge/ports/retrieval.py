"""Retrieval port DTOs + the embedding-resolution seam (03-api-spec §2
``RetrievedChunkOut``; 02-port-contracts §2 ``KnowledgeRetrieval``; 3.k3/3.k4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One retrieved chunk — 1:1 with the API's ``RetrievedChunkOut`` (03 §2).

    ``file_name``/``page_number``/``section`` (retrieval plan §3.1/§3.9, س-19,
    ``P-18``) are the citation fields the indexing side's ``_CITATION_KEYS``
    (``application/indexing.py``) already writes onto every Qdrant point's
    payload. Explicit fields, NOT a ``metadata: Mapping`` — the project is on
    ``mypy --strict`` and this port must not guess keys; the declared price is
    that each future citation key needs a contract edit (retrieval plan
    §3.1). All three are ``| None`` because an OLDER point, indexed before
    this field existed, or a point whose parser never emitted a value (e.g.
    no ``page_number`` off a DOCX), carries no such payload key at all — a
    missing citation degrades to "unknown", never a crash.
    """

    document_id: str
    chunk_id: str
    text: str
    score: float
    file_name: str | None = None
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEmbedding:
    """The embedding model + resolved API key a retrieval call needs."""

    model: str
    api_key: str


class EmbeddingResolver(Protocol):
    """Resolves which embedding model/key to use for a retrieval call.

    A temporary, module-local seam standing in for the framework
    ``ProviderResolver`` (02-port-contracts §3.5), which is not built yet
    (it lands in Phase 2.9). The inbound ``KnowledgeRetrieval.retrieve(ctx,
    query, k)`` signature is fixed by contract and carries no ``model``/
    ``api_key`` of its own, so something has to resolve them on the inbound
    service's behalf; once ``ProviderResolver`` exists, the Composition Root
    can adapt it to this same seam (or retire the seam entirely) without
    touching the inbound port's signature.
    """

    async def resolve_embedding(self, ctx: ExecutionContext) -> ResolvedEmbedding: ...
