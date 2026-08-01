"""Knowledge inbound port (02-port-contracts §2).

``KnowledgeRetrieval`` is injected into the agent layer so it can retrieve
context chunks without importing the knowledge module directly (ARC-07/08) —
the ``files.FilesQuery`` precedent.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.modules.knowledge.ports.retrieval import RetrievedChunk


class KnowledgeRetrieval(Protocol):
    """Injected into agents; retrieves the top ``k`` relevant chunks for
    ``query`` within the caller's workspace (02 §2, verbatim contract)."""

    async def retrieve(self, ctx: ExecutionContext, query: str, k: int) -> list[RetrievedChunk]: ...
