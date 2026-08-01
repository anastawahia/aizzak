"""Knowledge persistence port (02-port-contracts §2).

Outbound repository contract for the ``Document`` aggregate + its ``Chunk``
rows. Every method takes ``ExecutionContext`` first so the SQL adapter can
apply the RLS guard (``SET LOCAL app.workspace_id``) and the ``WHERE
workspace_id`` filter (DD-04) — the files/memory port precedent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.knowledge.domain.entities import Chunk, Document


class DocumentRepository(Protocol):
    """Tenant-scoped persistence for the ``Document`` aggregate + its
    ``Chunk`` rows (02 §2, verbatim contract)."""

    async def get(self, ctx: ExecutionContext, doc_id: Uuid) -> Document | None: ...

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
    ) -> Page[Document]:
        """This workspace's registered documents, newest first — every
        lifecycle status included (6.1-و-3), cursor-paginated (6.3-ب).

        A document's whole point is that it has a status: ``pending`` while a
        worker has not reached it, ``failed`` with the reason it could not be
        indexed. Filtering to ``indexed`` would leave a client that just
        uploaded a file unable to see anything at all, and unable to explain
        why a search never finds it — the exact question this collection is
        asked.

        No ``Chunk`` rows are read: the chunk text belongs to retrieval
        (``RetrievedChunk``), and ``chunk_count`` on the row already answers
        "how much of it was indexed" without loading the corpus.

        Paginated, unlike the other collections 03 §2 grouped it with. That
        grouping held while every listing was small, and 6.3 tested the
        assumption per collection: ``credentials`` is one active key per
        provider over a closed vocabulary, ``connectors`` is a static
        catalog, ``mcp-servers``/``connections`` are administered by hand.
        A corpus is none of those — it grows by one row per completed upload,
        with no ceiling anywhere in the design — so this is the one listing
        whose unbounded response was a matter of time rather than of taste,
        and it takes the general 02 §2 signature the rest of the repositories
        already have.
        """
        ...

    async def add(self, ctx: ExecutionContext, doc: Document) -> None: ...

    async def set_status(
        self, ctx: ExecutionContext, doc_id: Uuid, status: str, error: str | None = None
    ) -> None:
        """Persist a ``Document`` status transition (+ optional ``error``),
        RLS-guarded and ``WHERE workspace_id``-filtered like every other
        method here.

        When ``status == 'indexed'`` the adapter ALSO refreshes the
        denormalized ``documents.chunk_count`` column by counting the
        ``knowledge.chunks`` rows already persisted (via ``add_chunks``) for
        this document, so the row store's count can never drift from what
        was actually persisted.
        """
        ...

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        """Idempotently persist ``chunks`` (INV-K1, DD-09): the SQL adapter
        upserts via ``ON CONFLICT (document_id, seq) DO NOTHING``, so
        re-delivering the same batch (a retry after a worker crash) leaves
        the first-written rows' ids untouched instead of duplicating or
        overwriting them.
        """
        ...
