"""Knowledge inbound port (02-port-contracts §2).

``KnowledgeRetrieval`` is injected into the agent layer so it can retrieve
context chunks without importing the knowledge module directly (ARC-07/08) —
the ``files.FilesQuery`` precedent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.knowledge.ports.retrieval import RetrievedChunk


class KnowledgeRetrieval(Protocol):
    """Injected into agents; retrieves the top ``k`` relevant chunks for
    ``query`` within the caller's workspace (02 §2).

    ``file_ids`` (BE-RAG-005) narrows that workspace-wide search to the
    documents built from those files — the retrieval scope a conversation
    pins. It crosses as FILE ids, not document ids, so callers keep speaking
    about what they uploaded; the translation is the module's own
    (``KnowledgeRetrievalService``).

    Defaulted to ``None`` = unscoped, which keeps every existing caller —
    including ``POST /knowledge/search`` — exactly as it was.
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[Uuid] | None = None,
    ) -> list[RetrievedChunk]: ...
