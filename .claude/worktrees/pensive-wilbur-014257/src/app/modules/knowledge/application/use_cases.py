"""Knowledge use-cases (06-domain-models §7).

Thin application services that coordinate the pure domain over injected
ports. They own identity/time (framework ``new_uuid7`` / ``utc_now``) and
translate domain-rule violations into the shared framework error hierarchy at
this boundary — the domain itself stays framework-free (files/memory
precedent).

``RegisterDocumentFromFile`` only mints a ``pending`` ``Document`` row (06 §7
notes ingestion is a heavy, event-driven operation -- the actual work is
``IndexRegisteredDocument``, run by a worker). Duplicate registrations for the
same ``file_id`` are allowed by design: a re-upload becomes a brand-new
``Document``, never an update to a prior one (INV-K3).

``IndexRegisteredDocument`` is the worker-facing lifecycle wrapper around the
3.k3 ``IndexDocument`` pipeline (06 §7 "IndexDocument (worker)"): the future
Phase-5 ``knowledge_worker`` becomes a thin adapter over THIS use-case, not
over the pipeline directly, so status transitions, chunk persistence, event
emission, and idempotent-redelivery guards (DD-09) all live in exactly one
place.

``KnowledgeRetrievalService`` implements the ``KnowledgeRetrieval`` inbound
port (02 §2) over the 3.k3 ``RetrieveContext`` use-case, resolving the
embedding model/key through the temporary ``EmbeddingResolver`` seam
(``ports/retrieval.py``) — the ``FilesQueryService`` precedent for an
inbound-port implementation living alongside the other use-cases.

``ListDocuments``/``GetDocument`` + ``KnowledgeUseCases`` (6.1-و-3) are the
API-facing surface. The bundle carries the two document reads and the
retrieval port; it carries NEITHER ingestion face, because ingestion is a
worker's job that a request must not be able to start.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.knowledge.application.indexing import IndexDocument, IndexOutcome
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.domain.entities import Chunk, Document
from app.modules.knowledge.domain.errors import DocumentStateError
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
    KnowledgeEvent,
)
from app.modules.knowledge.domain.value_objects import IndexStatus, VectorRef
from app.modules.knowledge.ports.content_extractor import ParsedDocument
from app.modules.knowledge.ports.inbound import KnowledgeRetrieval
from app.modules.knowledge.ports.repository import DocumentRepository
from app.modules.knowledge.ports.retrieval import EmbeddingResolver, RetrievedChunk

# Idempotent-redelivery guard (DD-09): a terminal document is a silent no-op
# for IndexRegisteredDocument -- the pipeline never re-runs against it.
# `indexed` already finished; `failed` is terminal too (INV-K3: reprocessing
# means registering a NEW document, never resurrecting this one).
_TERMINAL = (IndexStatus.INDEXED, IndexStatus.FAILED)


class RegisterDocumentFromFile:
    """Mint a ``pending`` ``Document`` for a file (06 §7
    ``RegisterDocumentFromFile``).

    Typically invoked by the (future) subscriber to the global
    ``files.FileUploaded`` event; duplicate registrations for the same
    ``file_id`` are allowed by design (INV-K3) -- a re-upload becomes a new
    ``Document``, never an update to a prior one.
    """

    def __init__(self, documents: DocumentRepository) -> None:
        self._documents = documents

    async def execute(
        self, ctx: ExecutionContext, *, file_id: Uuid
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        if not file_id.strip():
            raise ValidationError("file_id must not be empty")

        now = utc_now()
        doc = Document(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            file_id=file_id,
            status=IndexStatus.PENDING,
            chunk_count=0,
            error=None,
            created_at=now,
            updated_at=now,
            version=1,
        )
        await self._documents.add(ctx, doc)
        event = DocumentRegistered(doc.id, ctx.workspace_id, doc.file_id, now)
        return doc, (event,)


@dataclass(frozen=True, slots=True)
class IndexAttempt:
    """The I/O phase's outcome (``IndexRegisteredDocument.run``), awaiting
    its terminal transaction (``finalize``).

    Exactly one of ``outcome``/``error`` is set when the pipeline actually
    ran; both ``None`` means the document was already terminal — the DD-09
    idempotent-redelivery no-op, for which ``finalize`` is a pure pass-through
    (so ``execute = run + finalize`` stays total and callers that only want
    the split can skip the terminal transaction entirely via
    ``is_redelivery_noop``).
    """

    document: Document
    outcome: IndexOutcome | None
    error: str | None

    @property
    def is_redelivery_noop(self) -> bool:
        return self.outcome is None and self.error is None


class IndexRegisteredDocument:
    """Run the 3.k3 ``IndexDocument`` pipeline against a registered
    ``Document`` and persist the outcome (06 §7 "IndexDocument (worker)").

    The Phase-5 ``knowledge_worker`` is a thin adapter over this use-case
    rather than over ``IndexDocument`` directly, so all lifecycle bookkeeping
    lives here, not in the worker.

    **Split in 5.2-أ into ``run`` (I/O phase) + ``finalize`` (terminal
    phase), closing 5.1's documented D5 terminal window.** ``run`` claims the
    document (``pending → indexing``, its own short transaction) and executes
    the pipeline — embedding-provider and vector-store round trips that must
    never run under an open DB transaction (R2). ``finalize`` persists the
    terminal outcome: chunks + status + the follow-on event. The worker
    handler wraps ``finalize`` (plus the outbox append and the DD-09
    ``processed_events`` claim) in ONE ``uow.begin`` block, so «terminal
    state without its event» can no longer be produced by a crash between
    two transactions. ``execute`` composes the two halves with per-call
    transactions — byte-for-byte the pre-split behaviour — for callers that
    have no unit of work to offer.
    """

    def __init__(self, documents: DocumentRepository, pipeline: IndexDocument) -> None:
        self._documents = documents
        self._pipeline = pipeline

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        parsed: ParsedDocument,
        model: str,
        api_key: str,
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        attempt = await self.run(
            ctx, document_id=document_id, parsed=parsed, model=model, api_key=api_key
        )
        return await self.finalize(ctx, attempt)

    async def run(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        parsed: ParsedDocument,
        model: str,
        api_key: str,
    ) -> IndexAttempt:
        doc = await self._documents.get(ctx, document_id)
        if doc is None:
            raise NotFoundError("document not found")

        if doc.status in _TERMINAL:
            return IndexAttempt(document=doc, outcome=None, error=None)

        now = utc_now()
        try:
            doc.start_indexing(now)
        except DocumentStateError as exc:
            raise ConflictError(str(exc)) from exc
        await self._documents.set_status(ctx, doc.id, IndexStatus.INDEXING.value)

        try:
            outcome = await self._pipeline.execute(
                ctx, document_id=document_id, parsed=parsed, model=model, api_key=api_key
            )
        except Exception as exc:
            # Broad catch is deliberate: ANY pipeline failure (embedding
            # provider outage, vector-store error, an edge case that slipped
            # past the parser) must land the document in `failed` with its
            # reason recorded, not crash the caller. Retry/DLQ mechanics
            # belong to the worker's redelivery handling; the failure is
            # carried as data to `finalize` and never re-raised.
            return IndexAttempt(document=doc, outcome=None, error=str(exc))

        return IndexAttempt(document=doc, outcome=outcome, error=None)

    async def finalize(
        self, ctx: ExecutionContext, attempt: IndexAttempt
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        doc = attempt.document

        if attempt.outcome is not None:
            chunks = [
                Chunk(
                    id=new_uuid7(),
                    document_id=doc.id,
                    workspace_id=ctx.workspace_id,
                    seq=indexed_chunk.seq,
                    text=indexed_chunk.text,
                    token_count=indexed_chunk.token_count,
                    vector_ref=VectorRef(attempt.outcome.collection, indexed_chunk.chunk_id),
                )
                for indexed_chunk in attempt.outcome.chunks
            ]
            await self._documents.add_chunks(ctx, chunks)

            now = utc_now()
            doc.complete_indexing(len(chunks), now)
            await self._documents.set_status(ctx, doc.id, IndexStatus.INDEXED.value)

            indexed_event = DocumentIndexed(
                doc.id, ctx.workspace_id, doc.file_id, len(chunks), attempt.outcome.collection, now
            )
            return doc, (indexed_event,)

        if attempt.error is not None:
            now = utc_now()
            doc.fail_indexing(attempt.error, now)
            await self._documents.set_status(
                ctx, doc.id, IndexStatus.FAILED.value, error=attempt.error
            )
            failed_event = DocumentIndexingFailed(doc.id, ctx.workspace_id, attempt.error, now)
            return doc, (failed_event,)

        # Redelivery no-op (both fields None): nothing ran, nothing to persist.
        return doc, ()


class ListDocuments:
    """This workspace's registered documents, newest first, one page at a
    time (6.1-و-3 · paginated in 6.3-ب).

    Every lifecycle status is returned — see ``DocumentRepository.list``: a
    ``pending`` row is the answer to "did my upload get picked up?" and a
    ``failed`` row carries the reason indexing could not finish. Both are
    questions this collection exists to answer.

    Hands the repository's ``Page`` straight back rather than re-wrapping it:
    the use-case adds no rule of its own here, and a second envelope would
    only be a place for the cursor to get lost.
    """

    def __init__(self, documents: DocumentRepository) -> None:
        self._documents = documents

    async def execute(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Document]:
        return await self._documents.list(ctx, limit=limit, cursor=cursor)


class GetDocument:
    """One document's ingestion state, or ``NotFoundError`` (6.1-و-3).

    Another tenant's document is indistinguishable from a non-existent one:
    the repository's ``WHERE workspace_id`` filter returns ``None`` either
    way, and 404 is the answer to both — a 403 would confirm the id exists.
    """

    def __init__(self, documents: DocumentRepository) -> None:
        self._documents = documents

    async def execute(self, ctx: ExecutionContext, *, document_id: Uuid) -> Document:
        document = await self._documents.get(ctx, document_id)
        if document is None:
            raise NotFoundError("document not found")
        return document


class KnowledgeRetrievalService:
    """Implements the ``KnowledgeRetrieval`` inbound port (02 §2) over the
    3.k3 ``RetrieveContext`` use-case (the ``FilesQueryService`` precedent)."""

    def __init__(self, retrieval: RetrieveContext, resolver: EmbeddingResolver) -> None:
        self._retrieval = retrieval
        self._resolver = resolver

    async def retrieve(self, ctx: ExecutionContext, query: str, k: int) -> list[RetrievedChunk]:
        resolved = await self._resolver.resolve_embedding(ctx)
        return await self._retrieval.execute(
            ctx, query=query, model=resolved.model, api_key=resolved.api_key, k=k
        )


@dataclass(frozen=True, slots=True)
class KnowledgeUseCases:
    """The module's API-facing bundle (the ``CredentialUseCases`` precedent):
    ONE field on ``ApiServices`` per module, matching 03 §1's ``POST /search ·
    GET /documents · GET /documents/{id}``.

    ``RegisterDocumentFromFile``/``IndexRegisteredDocument`` are pointedly
    absent. Ingestion is event-driven (06 §7): a file's upload completing is
    what registers a document, and a worker is what indexes it. There is no
    v1 route for either, and a bundle the API layer holds must not be able to
    mint a document out of band or re-drive a worker's pipeline from a
    request.

    ``search`` is typed ``KnowledgeRetrieval | None`` — the module's INBOUND
    port, not the concrete service — and is genuinely ``None`` in the
    Composition Root today: ``KnowledgeRetrievalService`` composes over
    ``RetrieveContext``, which needs an ``EmbeddingProvider``, and no such
    adapter exists yet (a Phase-2 scheduling gap, see the root's module
    docstring). Optionality here is what makes that gap a VISIBLE, typed fact
    that the router must answer for, instead of an unregistered route the
    OpenAPI schema would silently omit. When 2.10 lands, this field is filled
    and nothing else about the route changes.
    """

    list_documents: ListDocuments
    get_document: GetDocument
    search: KnowledgeRetrieval | None
