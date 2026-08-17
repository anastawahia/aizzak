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

    ``space_id`` (spaces plan §3.4, step 8) is the space to search inside, and
    is keyword-only WITHOUT a default while ``file_ids`` keeps one. Forgetting
    a pin narrows nothing; forgetting a space widens the answer across the
    axis this plan draws — so the caller has to say, and the two callers that
    still cannot say it (``POST /knowledge/search`` and the RAG agent) are
    visible in the source as the ones passing ``None`` on purpose.

    ⚠️ **Step 12 did NOT close those two, contrary to what this note used to
    predict.** It put the space on the wire for the routes §3.7 names, and
    both of these reach retrieval from somewhere else: the search body is not
    in that table, and the RAG agent reads its space from ``AgentDeps``, which
    the orchestrator does not fill yet. Until that lands (recorded in the
    plan's §7), a thread inside a space still retrieves across every space —
    which is decision 1 unenforced on the read path, and the reason the entry
    is written down rather than left to be noticed.
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid | None,
    ) -> list[RetrievedChunk]: ...
