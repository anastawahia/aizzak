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

``ReindexDocuments``/``GetReindexJob``/``CancelReindexJob`` (BE-RAG-007/008)
are the manual-rebuild trio, and they respect that same line: re-indexing
registers new documents and publishes the ordinary ``DocumentRegistered``
event, then waits for a worker exactly like an upload does. It destroys the
documents it supersedes first (INV-K4) — see ``ReindexDocuments`` for why
that is the operation and not its cleanup — and reports progress that is
derived from the corpus rather than counted anywhere (INV-K5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.ports.vector_store import HybridVectorStore
from app.framework.types import Uuid
from app.modules.knowledge.application.event_mapping import to_outbox_record
from app.modules.knowledge.application.indexing import IndexDocument, IndexOutcome
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.application.summarization import (
    SummarizeDocument,
    SummaryBuildCancelled,
    SummaryDraft,
)
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ReindexItem,
    ReindexJob,
    Summary,
    SummaryJob,
)
from app.modules.knowledge.domain.errors import DocumentStateError, SummaryJobStateError
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
    KnowledgeEvent,
    SummaryBuildFailed,
    SummaryBuilt,
    SummaryRequested,
)
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    ReindexJobStatus,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)
from app.modules.knowledge.ports.content_extractor import ParsedDocument
from app.modules.knowledge.ports.export import ExportFormat, RenderedSummary, SummaryRenderer
from app.modules.knowledge.ports.inbound import KnowledgeRetrieval
from app.modules.knowledge.ports.repository import (
    DocumentRepository,
    ReindexJobRepository,
    SummaryJobRepository,
    SummaryRepository,
)
from app.modules.knowledge.ports.retrieval import EmbeddingResolver, RetrievedChunk
from app.modules.knowledge.ports.summarization import ResolvedSummarizer, SummarizerResolver

# Idempotent-redelivery guard (DD-09): a terminal document is a silent no-op
# for IndexRegisteredDocument -- the pipeline never re-runs against it.
# `indexed` already finished; `failed` is terminal too (INV-K3: reprocessing
# means registering a NEW document, never resurrecting this one).
#
# The same set gates BE-RAG-007: only a terminal document may be re-indexed,
# because destroying a row a worker is mid-pipeline on breaks its chunk write.
_TERMINAL = (IndexStatus.INDEXED, IndexStatus.FAILED)

# One request may rebuild this many documents. A bound, not a guess at what
# anyone needs: each target costs a vector-store delete, a purge and an
# outbox row, and an unbounded body would let one call delete an unbounded
# slice of the corpus. A bigger corpus is re-indexed in batches, which is
# also the only way its progress stays readable.
_MAX_REINDEX = 50

# Written into the `error` column of every document a cancellation claims,
# and carried on its `DocumentIndexingFailed` event. Phrased for the person
# who will read it in the document list weeks later, not for a log grep.
CANCELLED_REASON = "re-indexing was cancelled before this document was processed"

# The same idea for a summary job, and phrased for the same reader. It rides
# on the job's `error` column and on its `SummaryBuildFailed` event, because a
# cancelled build really did end without a summary and a client watching the
# stream needs to stop waiting for one.
SUMMARY_CANCELLED_REASON = "the summary build was cancelled"


class RegisterDocumentFromFile:
    """Mint a ``pending`` ``Document`` for a file (06 §7
    ``RegisterDocumentFromFile``).

    Typically invoked by the (future) subscriber to the global
    ``files.FileUploaded`` event; duplicate registrations for the same
    ``file_id`` are allowed by design (INV-K3) -- a re-upload becomes a new
    ``Document``, never an update to a prior one.

    **``space_id`` is the FILE's space, carried on that same event** (spaces
    plan, step 8). This use-case does not choose it and does not verify it:
    the space was proven to exist when the file was registered
    (``files/ports/spaces.py``), and re-asking here would be a second
    authority on a question ``files`` has already answered — the wrong kind of
    second answer, because the two could disagree. There is no
    ``spaces``-facing port in this module for exactly that reason.
    """

    def __init__(self, documents: DocumentRepository) -> None:
        self._documents = documents

    async def execute(
        self, ctx: ExecutionContext, *, file_id: Uuid, space_id: Uuid | None
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        if not file_id.strip():
            raise ValidationError("file_id must not be empty")

        now = utc_now()
        doc = Document(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            space_id=space_id,
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

    **``fail`` (step 16) is a THIRD entry point into the same ``finalize``**,
    for a failure that happens before ``run`` can be called at all (the
    file's bytes could not be fetched or parsed, so there is nothing to
    embed). See its own docstring.
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
                ctx,
                document_id=document_id,
                # The space comes off the ROW, not off the event that woke
                # this worker (step 8): `knowledge.document.registered.v1`
                # carries no space, and the aggregate this method just loaded
                # is the only thing that knows which one the document was
                # filed under. It becomes the point payload's `space` key.
                space_id=doc.space_id,
                parsed=parsed,
                model=model,
                api_key=api_key,
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

    async def fail(self, ctx: ExecutionContext, *, document_id: Uuid, reason: str) -> IndexAttempt:
        """An ``IndexAttempt`` carrying a failure that happened BEFORE the
        pipeline could run at all — the content could not be fetched or
        parsed, so there is nothing to embed (deferred-adapters-plan.md step
        16, §1-ج).

        Without this, such a failure had no terminal path: it was raised out
        of the worker's ``content.resolve`` call, escaped the handler
        entirely, and was redelivered until the DLQ swallowed it — leaving
        the document ``pending`` FOREVER, with not one failure event to tell
        the user why their upload never became searchable. Routing it through
        the SAME ``IndexAttempt``/``finalize`` pair the pipeline's own
        failures already use means one terminal-state path, not two.

        The same DD-09 redelivery guard as ``run``: an already-terminal
        document is a no-op, never re-failed.

        Unlike ``run``, this does NOT persist the intermediate ``indexing``
        status. ``run`` writes it because the pipeline's external I/O then
        happens outside any transaction, and a crash mid-pipeline must leave
        a claimed document behind; here there is no I/O left to do — the
        caller's ``finalize`` transaction records ``failed`` immediately — so
        the transition is taken in memory ONLY, to satisfy INV-K2's
        ``indexing -> failed`` edge, and a second write is spent on nothing.
        """
        doc = await self._documents.get(ctx, document_id)
        if doc is None:
            raise NotFoundError("document not found")

        if doc.status in _TERMINAL:
            return IndexAttempt(document=doc, outcome=None, error=None)

        # Not terminal, so the status is `pending` or `indexing` — exactly
        # `entities._STARTABLE`, so this transition cannot raise.
        doc.start_indexing(utc_now())
        return IndexAttempt(document=doc, outcome=None, error=reason)

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


@dataclass(frozen=True, slots=True)
class ReindexTarget:
    """One document ``ReindexDocuments.prepare`` resolved and emptied out of
    the vector store, waiting for its terminal transaction (``commit``).

    ``replacement`` is the ``Document`` that will take its place — minted in
    ``prepare`` rather than ``commit`` so the plan is fully decided before any
    row is touched, and so nothing inside the transaction has to reach for the
    clock or an id generator.
    """

    source: Document
    replacement: Document


@dataclass(frozen=True, slots=True)
class ReindexPlan:
    """``ReindexDocuments.prepare``'s outcome: the whole job, decided.

    Carrying the job's id and instant here (rather than minting them in
    ``commit``) is what lets ``commit`` be a pure sequence of writes — the
    ``IndexAttempt`` precedent, for the same reason: a transaction that also
    makes decisions is a transaction whose retry means something different
    the second time.
    """

    job_id: Uuid
    at: datetime
    targets: tuple[ReindexTarget, ...]


class ReindexDocuments:
    """Rebuild the index for one or more documents (06 §7 ``ReindexDocuments``
    · BE-RAG-007).

    **Re-indexing obeys INV-K3 rather than bending it.** No document is ever
    reset: each target is superseded by a brand-new ``pending`` ``Document``
    over the same ``file_id``, exactly as a re-upload would be, and a worker
    indexes it through the ordinary ``DocumentRegistered`` path. Nothing in
    the worker knows this feature exists.

    **The old document is DESTROYED, up front** (INV-K4). A Qdrant point id is
    derived from its document id (``indexing.chunk_point_id``), so a second
    live document over the same file does not overwrite the first — it doubles
    it, and every search answers twice, forever. Deleting the points first and
    the rows second is deliberate: if the vector delete fails, nothing has
    changed and the request simply errors; the reverse order would leave
    points behind for a document that no longer exists, and those points ARE
    reachable (retrieval filters on the payload, and never joins Postgres).

    The cost of doing it up front is stated in the contract rather than hidden:
    **the file is not searchable until the job finishes**, and a cancelled or
    failed job leaves it that way. The alternative — index the new document
    first, purge the old one on success — buys a seamless window at the price
    of a crash between two stores leaving permanent duplicates, and of the
    purge having to run inside the worker's terminal transaction, where
    external I/O may not go (R2).

    **Only TERMINAL documents may be re-indexed.** A ``pending``/``indexing``
    one is a 409: the rebuild being asked for is already under way, and
    deleting a row a worker is mid-pipeline on would make its ``add_chunks``
    fail against ``fk_chunk_doc`` and its event redeliver until the DLQ ate it.

    Split into ``prepare`` (reads + the vector deletes) and ``commit`` (the
    row writes) for the reason ``IndexRegisteredDocument`` is: the vector
    store is a network round trip and must not run under an open database
    transaction (R2). ``ReindexService`` is what pairs the two with the
    Outbox append.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        jobs: ReindexJobRepository,
        vectors: HybridVectorStore,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._vectors = vectors

    async def prepare(self, ctx: ExecutionContext, *, document_ids: Sequence[Uuid]) -> ReindexPlan:
        ids = _unique_ids(document_ids)
        if not ids:
            raise ValidationError("document_ids must not be empty")
        if len(ids) > _MAX_REINDEX:
            raise ValidationError(f"at most {_MAX_REINDEX} documents can be re-indexed at once")

        now = utc_now()
        targets: list[ReindexTarget] = []
        for document_id in ids:
            source = await self._documents.get(ctx, document_id)
            if source is None:
                raise NotFoundError("document not found")
            if source.status not in _TERMINAL:
                raise ConflictError(
                    f"document {source.id} is already being indexed"
                    f" (status {source.status.value!r})"
                )
            targets.append(
                ReindexTarget(
                    source=source,
                    replacement=Document(
                        id=new_uuid7(),
                        workspace_id=ctx.workspace_id,
                        # The superseded document's space, not a fresh
                        # decision (step 8): re-indexing rebuilds the SAME
                        # file's content, and a replacement that landed in
                        # another space -- or in none -- would move content
                        # between spaces through the back door decision 3
                        # closes at the front.
                        space_id=source.space_id,
                        file_id=source.file_id,
                        status=IndexStatus.PENDING,
                        chunk_count=0,
                        error=None,
                        created_at=now,
                        updated_at=now,
                        version=1,
                    ),
                )
            )

        # Every target is validated BEFORE the first point is deleted: a
        # request that names one bad id destroys nothing at all, rather than
        # half-rebuilding the corpus and then reporting a 404.
        for target in targets:
            await self._purge_vectors(ctx, target.source.id)

        return ReindexPlan(job_id=new_uuid7(), at=now, targets=tuple(targets))

    async def commit(
        self, ctx: ExecutionContext, plan: ReindexPlan
    ) -> tuple[ReindexJob, tuple[KnowledgeEvent, ...]]:
        events: list[KnowledgeEvent] = []
        items: list[ReindexItem] = []
        for target in plan.targets:
            await self._documents.purge(ctx, target.source.id)
            await self._documents.add(ctx, target.replacement)
            events.append(
                DocumentRegistered(
                    target.replacement.id, ctx.workspace_id, target.replacement.file_id, plan.at
                )
            )
            items.append(
                ReindexItem(
                    document_id=target.replacement.id,
                    file_id=target.replacement.file_id,
                    source_document_id=target.source.id,
                    status=target.replacement.status,
                )
            )

        job = ReindexJob(
            id=plan.job_id,
            workspace_id=ctx.workspace_id,
            items=tuple(items),
            cancelled_at=None,
            created_at=plan.at,
        )
        await self._jobs.add(ctx, job)
        return job, tuple(events)

    async def _purge_vectors(self, ctx: ExecutionContext, document_id: Uuid) -> None:
        """Delete a document's points, grouped by collection.

        Grouping is defensive rather than expected: every chunk of one
        document is written to the one per-workspace collection today
        (``knowledge_collection``), but the ``VectorRef`` on each chunk names
        its own, and honouring that costs one dict.
        """
        by_collection: dict[str, list[Uuid]] = {}
        for ref in await self._documents.vector_refs(ctx, document_id):
            by_collection.setdefault(ref.collection, []).append(ref.point_id)
        for collection, point_ids in by_collection.items():
            await self._vectors.delete(collection, point_ids)


class GetReindexJob:
    """One re-index job's live progress, or ``NotFoundError`` (BE-RAG-008).

    Reads nothing but the job: every number on it is derived from the
    documents its items point at (INV-K5), which the repository joins in.
    Another tenant's job is a 404 like every other read here.
    """

    def __init__(self, jobs: ReindexJobRepository) -> None:
        self._jobs = jobs

    async def execute(self, ctx: ExecutionContext, *, job_id: Uuid) -> ReindexJob:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("re-index job not found")
        return job


class CancelReindexJob:
    """Stop a running re-index (BE-RAG-008).

    The aggregate decides WHICH documents can still be claimed (``pending``
    ones only — see ``ReindexJob.cancel``); this use-case persists that
    decision: one ``failed`` status per claimed document, then the job's
    ``cancelled_at``.

    **Each claimed document also gets its ``DocumentIndexingFailed`` event**,
    the same one the pipeline's own failures emit. That is not ceremony: those
    documents really will never be indexed, the reason is recorded, and the
    WebSocket notification the event already drives is how a client watching
    a different tab learns its file went unindexed. Minting a new event type
    for "cancelled" would have been a promise with no consumer — the
    ``FileRenamed`` mistake in another costume.

    A job with nothing left to stop is a ``ConflictError``: the caller asked
    to prevent work that has already happened, and answering 200 would suggest
    it was prevented. Cancelling an already-cancelled job is a no-op that
    writes nothing and returns the job unchanged — the instant someone stopped
    it is not re-datable.
    """

    def __init__(self, documents: DocumentRepository, jobs: ReindexJobRepository) -> None:
        self._documents = documents
        self._jobs = jobs

    async def execute(
        self, ctx: ExecutionContext, *, job_id: Uuid
    ) -> tuple[ReindexJob, tuple[KnowledgeEvent, ...]]:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("re-index job not found")
        if job.status is ReindexJobStatus.CANCELLED:
            return job, ()
        if job.status is ReindexJobStatus.COMPLETED:
            raise ConflictError("re-index job has already finished")

        now = utc_now()
        claimed = job.cancel(now)
        events: list[KnowledgeEvent] = []
        for document_id in claimed:
            await self._documents.set_status(
                ctx, document_id, IndexStatus.FAILED.value, error=CANCELLED_REASON
            )
            events.append(
                DocumentIndexingFailed(document_id, ctx.workspace_id, CANCELLED_REASON, now)
            )
        await self._jobs.mark_cancelled(ctx, job.id, now)
        return job, tuple(events)


class ReindexService:
    """Wraps ``ReindexDocuments`` with the Outbox append inside ONE
    request-scoped unit of work — the ``CompleteUploadService`` precedent, and
    the first one this module has needed.

    ``prepare`` runs OUTSIDE the transaction, deliberately: it resolves every
    target and empties the vector store, both of which are round trips that
    must not hold a database transaction open (R2). Only the row writes and
    their events are inside, and they are inside TOGETHER — a new ``pending``
    document without its ``DocumentRegistered`` event would be a file that is
    unsearchable forever, with no worker ever told to index it. That is
    exactly the failure atomicity exists to prevent, so the append is not
    swallowed.
    """

    def __init__(self, reindex: ReindexDocuments, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._reindex = reindex
        self._outbox = outbox
        self._uow = uow

    async def start(self, ctx: ExecutionContext, *, document_ids: Sequence[Uuid]) -> ReindexJob:
        plan = await self._reindex.prepare(ctx, document_ids=document_ids)
        async with self._uow.begin(ctx):
            job, events = await self._reindex.commit(ctx, plan)
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return job


class CancelReindexJobService:
    """Wraps ``CancelReindexJob`` with the Outbox append inside ONE unit of
    work (the ``ReindexService`` shape).

    Atomicity earns its keep here even though nothing external is called: a
    partial cancellation — some documents failed, the job never stamped — is
    worse than none, because the job would keep reporting itself ``running``
    while the documents it was waiting on had already been claimed.
    """

    def __init__(self, cancel: CancelReindexJob, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._cancel = cancel
        self._outbox = outbox
        self._uow = uow

    async def cancel(self, ctx: ExecutionContext, *, job_id: Uuid) -> ReindexJob:
        async with self._uow.begin(ctx):
            job, events = await self._cancel.execute(ctx, job_id=job_id)
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return job


class RequestSummary:
    """Queue a build of one document's summary (06 §7 · BE-RAG-009).

    **There is no ``force`` flag, and its absence is the contract's shape
    rather than an omission.** The plan proposed one; building it would have
    meant a POST that sometimes queues work and sometimes returns a stored
    artefact, under two status codes and two response bodies on one route. The
    two things that flag distinguished are already two different HTTP verbs:
    reading the stored summary is ``GET``, and building one is ``POST``. So
    ``POST`` always builds, and "summarise" and "rebuild" stop being two
    operations — the second was only ever the first, aimed at a key that was
    already occupied.

    That makes the route expensive by design, which is why the guards are
    here rather than in the client: the document must be ``indexed`` (there is
    nothing to read otherwise), and a build already queued or running for the
    same key is a **409** rather than a second one. Without that check two
    impatient clicks pay for the same document twice and then race to write
    one ``uq_summary_key`` row — so the loser pays in full and fails at its
    last statement. ``uq_summary_job_active`` catches the pair that checked in
    the same instant; this check catches every other pair, before a token is
    spent.

    Nothing here reads or destroys the summary that may already be stored
    under the key. A build that fails or is cancelled must leave the previous
    summary exactly where it was — a rebuild that ends by deleting what it
    could not replace is worse than no rebuild.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        jobs: SummaryJobRepository,
    ) -> None:
        self._documents = documents
        self._jobs = jobs

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> tuple[SummaryJob, tuple[KnowledgeEvent, ...]]:
        document = await self._documents.get(ctx, document_id)
        if document is None:
            raise NotFoundError("document not found")
        if document.status is not IndexStatus.INDEXED:
            raise ConflictError(
                f"document {document.id} is not indexed (status {document.status.value!r})"
                " and has no text to summarise"
            )

        active = await self._jobs.active_for(ctx, document_id, kind, lang)
        if active is not None:
            raise ConflictError(
                f"a {kind.value} summary of this document in {lang.value}"
                f" is already being built (job {active.id})"
            )

        now = utc_now()
        job = SummaryJob(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            kind=kind,
            lang=lang,
            status=SummaryJobStatus.QUEUED,
            total_chunks=0,
            done_chunks=0,
            error=None,
            cancelled_at=None,
            finished_at=None,
            created_at=now,
        )
        await self._jobs.add(ctx, job)
        event = SummaryRequested(job.id, ctx.workspace_id, document_id, kind.value, lang.value, now)
        return job, (event,)


class RequestSummaryService:
    """Wraps ``RequestSummary`` with the Outbox append inside ONE unit of work
    (the ``ReindexService`` shape).

    Atomicity is the whole feature here: a queued job whose
    ``SummaryRequested`` event was lost is a job no worker will ever be told
    about, sitting at 0% forever while occupying ``uq_summary_job_active`` and
    blocking every retry of the same build. That is worse than the request
    having failed outright, which is what rolling both back produces.
    """

    def __init__(self, request: RequestSummary, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._request = request
        self._outbox = outbox
        self._uow = uow

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob:
        async with self._uow.begin(ctx):
            job, events = await self._request.execute(
                ctx, document_id=document_id, kind=kind, lang=lang
            )
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return job


class GetSummary:
    """One stored summary, or ``NotFoundError`` (BE-RAG-010).

    **404 rather than 200 with an existence flag.** The Alpha contract
    answered ``{has_summary: false}`` with a 200, and mirroring that would
    have made every typed client destructure a body that may describe nothing.
    A summary under a key either exists or it does not, which is what 404
    means; the client that wants "show me the saved one" reads it and treats
    absence as absence, exactly as it does for ``GET /documents/{id}``.

    Another tenant's summary is indistinguishable from a missing one.
    """

    def __init__(self, summaries: SummaryRepository) -> None:
        self._summaries = summaries

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary:
        summary = await self._summaries.get(ctx, document_id, kind, lang)
        if summary is None:
            raise NotFoundError("summary not found")
        return summary


class DeleteSummary:
    """Delete one stored summary (BE-RAG-011). Idempotent.

    Returns whether a row was there, and answers 200 either way rather than
    404 on the second call: the caller asked for a state, and after the first
    call that state holds. The flag is what lets the UI say "deleted" once and
    "there was nothing to delete" the second time without either being an
    error.

    Deleting a summary never touches the job that built it. The job is the
    record of an operation that really happened, and forgetting it because its
    output was discarded would erase the only evidence of what the workspace
    was charged for.
    """

    def __init__(self, summaries: SummaryRepository) -> None:
        self._summaries = summaries

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> bool:
        return await self._summaries.delete(ctx, document_id, kind, lang)


class ExportSummary:
    """Render a stored summary as a downloadable document (BE-RAG-012).

    **Synchronous, not a job — and that is a departure from the plan's own
    proposal.** BE-RAG-012 asked for "a pollable, downloadable export job,
    with an optional synchronous path for small summaries". Every summary is
    small: the pipeline caps its reduce step at ``_REDUCE_MAX_TOKENS``, so
    the largest body this can ever be handed is a few tens of kilobytes of
    Markdown, and rendering that takes a fraction of a second. The job
    machinery the plan imagined — a row, an event, a worker, an object in
    MinIO, a presigned URL, a polling loop and an expiry policy — would all
    exist to manage work that finishes before the client's first poll. Alpha
    needed it because Alpha's export ran over unbounded text; ours is bounded
    by contract, so the "optional synchronous path" is the only path.

    That is also why the route is a **GET**: it returns another
    representation of a resource that already exists, changes nothing, and
    can be retried freely.

    **The render runs in a worker thread.** It is CPU work with no ``await``
    in it (``SummaryRenderer.render`` is deliberately sync), and running it
    inline would block the event loop for every other request this process is
    serving. ``asyncio.to_thread`` is what keeps one export from becoming
    everyone's latency.

    A missing summary is the ``GetSummary`` 404, reached through the same
    use-case: there is one definition of "this summary exists", not two.
    """

    def __init__(self, summaries: SummaryRepository, renderer: SummaryRenderer) -> None:
        self._get = GetSummary(summaries)
        self._renderer = renderer

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
        fmt: ExportFormat,
        title: str,
    ) -> RenderedSummary:
        summary = await self._get.execute(ctx, document_id=document_id, kind=kind, lang=lang)
        return await asyncio.to_thread(
            self._renderer.render,
            summary.text,
            fmt,
            title=title,
            rtl=_is_rtl(summary),
        )


def _is_rtl(summary: Summary) -> bool:
    """Whether this summary should be laid out right to left.

    ``ar`` is unambiguous and ``en`` is too. ``auto`` is the interesting one:
    the request never said, so the only honest source is the text itself, and
    the test is whether Arabic script is the majority of its LETTERS — not
    whether any appears. A summary in English quoting one Arabic term is an
    English document, and laying it out right to left because of that quote
    would be worse than the mistake it was trying to avoid.
    """
    if summary.lang is SummaryLanguage.AR:
        return True
    if summary.lang is SummaryLanguage.EN:
        return False
    letters = [char for char in summary.text if char.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for char in letters if "؀" <= char <= "ۿ")
    return arabic * 2 > len(letters)


class GetSummaryJob:
    """One summary build's progress, or ``NotFoundError`` (BE-RAG-009/011)."""

    def __init__(self, jobs: SummaryJobRepository) -> None:
        self._jobs = jobs

    async def execute(self, ctx: ExecutionContext, *, job_id: Uuid) -> SummaryJob:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("summary job not found")
        return job


class CancelSummaryJob:
    """Stop a summary build (BE-RAG-011).

    **Unlike ``CancelReindexJob``, this needs the worker's cooperation**, and
    the difference is worth stating because it is the reason the two look
    different. Cancelling a re-index claims documents the worker has not
    started, landing them in the terminal state its own redelivery guard
    already declines — so nothing in the worker changed. A summary build is a
    chain of provider calls inside one handler, and no lifecycle it consults
    will stop it: this stamps the row, and the worker re-reads it between map
    steps.

    Two honest consequences. A job still ``queued`` stops for free — the
    worker's claim sees a terminal status and declines, exactly the re-index
    mechanism. A ``running`` one stops at the next step boundary, so the
    request already in flight is paid for; the response says ``cancelled``
    because that is what was decided, not because every provider call has
    already returned.

    The ``SummaryBuildFailed`` event is emitted HERE, at the cancellation,
    rather than left for the worker to emit when it notices. A worker that
    already died will never notice, and the client waiting on the stream would
    wait forever for a build nobody is running.
    """

    def __init__(self, jobs: SummaryJobRepository) -> None:
        self._jobs = jobs

    async def execute(
        self, ctx: ExecutionContext, *, job_id: Uuid
    ) -> tuple[SummaryJob, tuple[KnowledgeEvent, ...]]:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("summary job not found")
        if job.status is SummaryJobStatus.CANCELLED:
            return job, ()
        if job.is_terminal:
            raise ConflictError(f"summary job has already finished ({job.status.value})")

        now = utc_now()
        job.cancel(SUMMARY_CANCELLED_REASON, now)
        await self._jobs.save(ctx, job)
        event = SummaryBuildFailed(
            job.id, ctx.workspace_id, job.document_id, SUMMARY_CANCELLED_REASON, now
        )
        return job, (event,)


class CancelSummaryJobService:
    """Wraps ``CancelSummaryJob`` with the Outbox append inside ONE unit of
    work (the ``CancelReindexJobService`` shape)."""

    def __init__(self, cancel: CancelSummaryJob, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._cancel = cancel
        self._outbox = outbox
        self._uow = uow

    async def cancel(self, ctx: ExecutionContext, *, job_id: Uuid) -> SummaryJob:
        async with self._uow.begin(ctx):
            job, events = await self._cancel.execute(ctx, job_id=job_id)
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return job


@dataclass(frozen=True, slots=True)
class SummaryBuildPlan:
    """``BuildSummary.claim``'s outcome: a claimed job with everything its
    build needs, decided before any provider is called.

    The ``ReindexPlan`` precedent — the plan is fully resolved outside the
    work so the work itself makes no decisions, and so a retry of the work
    means the same thing the second time.
    """

    job: SummaryJob
    chunks: tuple[str, ...]
    summarizer: ResolvedSummarizer


@dataclass(frozen=True, slots=True)
class SummaryAttempt:
    """The I/O phase's outcome (``BuildSummary.run``), awaiting its terminal
    transaction (``finalize``) — the ``IndexAttempt`` shape.

    Exactly one of ``draft``/``error`` is set unless ``cancelled``, in which
    case neither is: the build stopped on request, the row that says so was
    already written by whoever asked, and ``finalize`` has nothing left to do.
    """

    job: SummaryJob
    draft: SummaryDraft | None
    error: str | None
    cancelled: bool


class BuildSummary:
    """Run the summarisation pipeline against a queued ``SummaryJob`` and
    persist the outcome (06 §7 · BE-RAG-009).

    The worker-facing lifecycle wrapper, and it is the ``IndexRegisteredDocument``
    design applied to a second kind of work: ``claim`` takes the job and reads
    everything the build needs in one short transaction, ``run`` performs the
    provider round trips with NO transaction open (R2), and ``finalize``
    writes the terminal state and its event together so a crash can never
    produce «a stored summary with no event» or «a succeeded job with no
    summary».

    The same DD-09 redelivery guard: a terminal job is a silent no-op. That is
    also what makes cancelling a ``queued`` build free — the worker's claim
    finds a ``cancelled`` job and declines, with nothing in the worker needing
    to know cancellation exists.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        summaries: SummaryRepository,
        jobs: SummaryJobRepository,
        pipeline: SummarizeDocument,
        resolver: SummarizerResolver,
    ) -> None:
        self._documents = documents
        self._summaries = summaries
        self._jobs = jobs
        self._pipeline = pipeline
        self._resolver = resolver

    async def claim(self, ctx: ExecutionContext, *, job_id: Uuid) -> SummaryBuildPlan | None:
        """Claim a queued job, or ``None`` if there is nothing to do.

        ``None`` is the DD-09 no-op: the job is already terminal — finished,
        failed, or cancelled before a worker reached it — or its row is gone.
        Neither is an error, and neither should open a transaction to record
        nothing.

        **A document with no indexed text is NOT handled here**, deliberately.
        The pipeline already refuses an empty corpus with the sentence a
        reader should see (``SummarizeDocument.execute``), and routing it
        through ``run``
        means the failure takes the one path every other failure takes, with
        its event minted by ``finalize`` inside the terminal transaction. A
        check here would have had to write the failure itself, outside that
        transaction — and the event it minted would have had nowhere to go.

        What CAN fail here is resolution: a deployment whose ``summarize``
        route names no reachable provider, or a workspace with no key for it.
        That raises out of this method and the handler answers it with
        ``fail``, exactly as the indexing handler answers an unparseable file.
        """
        job = await self._jobs.get(ctx, job_id)
        if job is None or job.is_terminal:
            return None

        resolved = await self._resolver.resolve_summarizer(ctx)
        chunks = tuple(await self._documents.chunk_texts(ctx, job.document_id))
        job.start(len(chunks))
        await self._jobs.save(ctx, job)
        return SummaryBuildPlan(job=job, chunks=chunks, summarizer=resolved)

    async def fail(
        self, ctx: ExecutionContext, *, job_id: Uuid, reason: str
    ) -> SummaryAttempt | None:
        """A ``SummaryAttempt`` carrying a failure that happened BEFORE the
        pipeline could run — the ``IndexRegisteredDocument.fail`` precedent,
        for the same class of problem.

        Its case is a job that cannot be claimed at all: no ``summarize``
        route resolves a key for this workspace, so there is no model to call
        and no amount of redelivery will produce one. Without this entry
        point that failure escapes the handler, is redelivered until the DLQ
        swallows it, and leaves the job ``queued`` forever — holding
        ``uq_summary_job_active`` so the user cannot even ask again.

        ``None`` on a job that is already terminal or gone: the same
        redelivery guard as ``claim``, so a second delivery of a failure
        already recorded writes nothing.

        Unlike ``claim``, this persists no intermediate ``running`` status.
        There is no external I/O left to survive, and the caller's
        ``finalize`` transaction records ``failed`` immediately — the
        transition is taken in memory only, so a second write is not spent on
        a state nobody will observe.
        """
        job = await self._jobs.get(ctx, job_id)
        if job is None or job.is_terminal:
            return None
        return SummaryAttempt(job=job, draft=None, error=reason, cancelled=False)

    async def run(self, ctx: ExecutionContext, plan: SummaryBuildPlan) -> SummaryAttempt:
        """The provider round trips. Never raises for a build failure — the
        reason is carried as data to ``finalize``, the ``IndexAttempt`` rule.

        Progress is written through ``record_progress`` and never through
        ``save``: see that port method for why a whole-row write here would
        let a running build overwrite a cancellation it had not noticed yet.
        """
        job = plan.job

        async def _progress(done: int) -> None:
            job.advance(done)
            await self._jobs.record_progress(ctx, job.id, job.done_chunks)

        async def _should_cancel() -> bool:
            fresh = await self._jobs.get(ctx, job.id)
            return fresh is None or fresh.is_terminal

        try:
            draft = await self._pipeline.execute(
                ctx,
                chunks=plan.chunks,
                kind=job.kind,
                lang=job.lang,
                summarizer=plan.summarizer,
                on_progress=_progress,
                should_cancel=_should_cancel,
            )
        except SummaryBuildCancelled:
            return SummaryAttempt(job=job, draft=None, error=None, cancelled=True)
        except Exception as exc:
            # Broad catch, deliberate — the `IndexRegisteredDocument.run`
            # reasoning verbatim: ANY provider failure must land the job in
            # `failed` with its reason recorded, not crash the handler and
            # leave the row `running` until a redelivery guard that only
            # declines TERMINAL jobs lets it run all over again.
            return SummaryAttempt(job=job, draft=None, error=str(exc), cancelled=False)

        return SummaryAttempt(job=job, draft=draft, error=None, cancelled=False)

    async def finalize(
        self, ctx: ExecutionContext, attempt: SummaryAttempt
    ) -> tuple[SummaryJob, tuple[KnowledgeEvent, ...]]:
        job = attempt.job

        if attempt.cancelled:
            # Whoever cancelled already wrote the row and published the
            # event. Writing again here would re-date `cancelled_at` to the
            # moment the worker noticed rather than the moment someone asked.
            return job, ()

        now = utc_now()
        if attempt.draft is not None:
            summary = Summary(
                id=new_uuid7(),
                workspace_id=ctx.workspace_id,
                document_id=job.document_id,
                kind=job.kind,
                lang=job.lang,
                text=attempt.draft.text,
                model=attempt.draft.model,
                source_chunks=attempt.draft.source_chunks,
                truncated=attempt.draft.truncated,
                built_at=now,
            )
            # The summary is stored BEFORE the job is marked succeeded, in one
            # transaction, so the two can only be observed together. If a
            # cancellation landed while the last provider call was returning,
            # this still stores what the workspace already paid for: the build
            # completed, and discarding a finished summary to honour a stop
            # that arrived too late helps nobody.
            await self._summaries.upsert(ctx, summary)
            job.succeed(now)
            await self._jobs.save(ctx, job)
            event = SummaryBuilt(
                job.id, ctx.workspace_id, job.document_id, job.kind.value, job.lang.value, now
            )
            return job, (event,)

        reason = attempt.error or "the summary build produced no text"
        return job, await self._settle_failure(ctx, job, reason, now=now)

    async def _settle_failure(
        self,
        ctx: ExecutionContext,
        job: SummaryJob,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> tuple[KnowledgeEvent, ...]:
        """Land a job in ``failed`` with its reason, and mint the event.

        Shared by ``claim`` (nothing to summarise) and ``finalize`` (the
        pipeline failed) so there is exactly ONE path to a failed summary job,
        the same rule ``IndexRegisteredDocument.finalize`` keeps for documents.

        A job that raced into a terminal state between the two reads is left
        alone rather than re-failed — ``SummaryJobStateError`` is the
        aggregate refusing to overwrite an outcome that already happened.
        """
        at = now or utc_now()
        try:
            job.fail(reason, at)
        except SummaryJobStateError:
            return ()
        await self._jobs.save(ctx, job)
        return (SummaryBuildFailed(job.id, ctx.workspace_id, job.document_id, reason, at),)


def _unique_ids(document_ids: Sequence[Uuid]) -> list[Uuid]:
    """De-duplicate while preserving order, dropping blanks.

    Naming the same document twice is a client slip, not a request to rebuild
    it twice — and rebuilding it twice would mean the second pass destroying
    the document the first pass had just registered.
    """
    seen: dict[Uuid, None] = {}
    for document_id in document_ids:
        if document_id.strip():
            seen.setdefault(document_id, None)
    return list(seen)


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

    ``space_id=None`` means EVERY space, never "the documents that have no
    space" (step 8, the ``files``/``conversations`` rule): the condition is
    ADDED to the workspace filter, never swapped for a comparison against
    ``NULL``.
    """

    def __init__(self, documents: DocumentRepository) -> None:
        self._documents = documents

    async def execute(
        self, ctx: ExecutionContext, *, space_id: Uuid | None, limit: int, cursor: str | None = None
    ) -> Page[Document]:
        return await self._documents.list(ctx, space_id=space_id, limit=limit, cursor=cursor)


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

    def __init__(
        self,
        retrieval: RetrieveContext,
        resolver: EmbeddingResolver,
        documents: DocumentRepository,
    ) -> None:
        self._retrieval = retrieval
        self._resolver = resolver
        self._documents = documents

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid | None,
    ) -> list[RetrievedChunk]:
        """Retrieve the top ``k`` chunks, optionally scoped to ``file_ids``.

        **The file ⇒ document translation happens HERE, not in the caller.**
        A conversation pins files (BE-RAG-005) because a file is what its user
        uploaded; the vector payload keys on ``document_id``
        (``indexing._payload``). Doing the translation inside the module is
        what lets the seam stay in the caller's vocabulary — the agents layer
        never has to learn that documents exist.

        ``None`` (the default, and what every pre-BE-RAG-005 caller passes by
        omission) leaves retrieval unscoped over the workspace corpus. A
        NON-empty ``file_ids`` that resolves to no documents stays a scope of
        zero documents rather than collapsing back to ``None``: see
        ``RetrieveContext`` for why widening there would be the wrong answer.

        ``space_id`` is the SECOND, independent narrowing (step 8), and it is
        keyword-only with no default while ``file_ids`` keeps one. The
        asymmetry is deliberate: forgetting a pinned scope narrows nothing and
        answers from more of the caller's OWN workspace, while forgetting a
        space answers from other spaces — a widening across the very axis this
        plan exists to draw. The one mistake has to be impossible to make
        silently, so it is impossible to make at all.

        The two are ANDed one layer down (``RetrieveContext``), not merged
        here: a pin from another space is already refused at pin time (§3.5),
        so a scope that survives both conditions is the only honest one.
        """
        resolved = await self._resolver.resolve_embedding(ctx)
        document_ids = (
            None if file_ids is None else await self._documents.ids_for_files(ctx, file_ids)
        )
        return await self._retrieval.execute(
            ctx,
            query=query,
            model=resolved.model,
            api_key=resolved.api_key,
            k=k,
            document_ids=document_ids,
            space_id=space_id,
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

    **``reindex`` is not a hole in that rule** (BE-RAG-007/008). It cannot
    index anything: it registers documents and publishes the same event an
    upload does, then a worker decides when to run. What a request gains is
    the ability to ASK for ingestion, which is the one thing the absent pair
    would have let it do INSTEAD of asking. The three fields are required
    rather than ``| None`` like ``search``: they need the vector store, which
    every composed deployment has, and no embedding adapter at all.

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
    reindex: ReindexService
    get_job: GetReindexJob
    cancel_job: CancelReindexJobService
    request_summary: RequestSummaryService
    get_summary: GetSummary
    export_summary: ExportSummary
    delete_summary: DeleteSummary
    get_summary_job: GetSummaryJob
    cancel_summary_job: CancelSummaryJobService
