"""Knowledge DTOs — 03-api-spec §2 (``KnowledgeSearchIn`` ·
``RetrievedChunkOut`` · ``DocumentOut``), Phase 6.1-و-3.

``k`` keeps the spec's ``le=50`` ceiling and gains a ``ge=1`` floor the spec
leaves implicit: ``k=0`` would be a well-formed request for nothing, and
``k=-1`` a request the vector store would have to interpret. Both are
argument errors, and 422 says so before a single embedding is computed.

``DocumentOut`` deliberately carries no ``error`` field even though the
aggregate has one (06 §7). An indexing failure's reason is an internal
diagnostic — a parser stack trace, a provider's message — and 03 §2's shape
stops at ``status``. A client learns THAT indexing failed, which is what
changes its behaviour; WHY belongs in logs and events, not in a tenant-facing
payload that would leak whatever the pipeline happened to say.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeSearchIn(BaseModel):
    """A retrieval request over this workspace's indexed corpus."""

    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)


class RetrievedChunkOut(BaseModel):
    """One retrieved chunk — 1:1 with the module's ``RetrievedChunk``."""

    document_id: str
    chunk_id: str
    text: str
    score: float


class DocumentOut(BaseModel):
    """A document's ingestion state. No chunk text, no failure reason."""

    id: str
    file_id: str
    status: str
    chunk_count: int
    created_at: datetime
