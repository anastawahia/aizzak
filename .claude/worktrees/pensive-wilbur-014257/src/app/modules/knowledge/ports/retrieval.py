"""Retrieval port DTOs + the embedding-resolution seam (03-api-spec §2
``RetrievedChunkOut``; 02-port-contracts §2 ``KnowledgeRetrieval``; 3.k3/3.k4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One retrieved chunk — 1:1 with the API's ``RetrievedChunkOut`` (03 §2)."""

    document_id: str
    chunk_id: str
    text: str
    score: float


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
