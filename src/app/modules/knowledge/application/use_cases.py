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

``IndexFile``/``IndexFileService`` are the one thing allowed to CALL that
mint. Completing an upload no longer registers anything (the knowledge worker
stopped subscribing to ``files.file.uploaded.v1``), so a file sits in storage,
indexed by nothing, until somebody asks -- and this pair is the asking. It is
not a hole in "a request may not index a document" for the same reason
``reindex`` is not: all it produces is a ``pending`` document and the ordinary
``DocumentRegistered`` event, and a worker still decides when the pipeline
runs.

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
inbound-port implementation living alongside the other use-cases. It also
composes ``ListDocumentNames`` (retrieval plan §3.6/§4 row 6, ``P-36``), the
port's second face, over the ``files`` seam ``IndexFile`` already uses.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.quota_lock import QuotaLock
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.ports.vector_store import HybridVectorStore, VectorStore
from app.framework.types import Uuid
from app.modules.knowledge.application.event_mapping import to_outbox_record
from app.modules.knowledge.application.indexing import IndexDocument, IndexOutcome
from app.modules.knowledge.application.retrieval import RetrieveContext, require_space_scope
from app.modules.knowledge.application.routing import (
    RouteQuestion,
    SummaryBuildInProgress,
    SummaryStarting,
    SummaryTargetNotIndexed,
    SummaryWorkspaceBusy,
)
from app.modules.knowledge.application.summarization import (
    SUMMARY_NO_INDEXED_TEXT_REASON,
    SummarizeDocument,
    SummaryBuildCancelled,
    SummaryDraft,
)
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ParentChunk,
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
from app.modules.knowledge.domain.file_resolution import FileCandidate
from app.modules.knowledge.domain.pipeline import PIPELINE_VERSION, content_pipeline_unchanged
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
from app.modules.knowledge.ports.files import ReadableFiles
from app.modules.knowledge.ports.inbound import DocumentNames, KnowledgeRetrieval, RoutedAnswer
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

# ب-9 (scenarios plan section 6, gap ف-6) — written into a job no worker is
# coming back to, and carried on the `SummaryBuildFailed` it provokes. The
# reader is the person who pressed the button, got a 409 for a build that
# had already died, and is now being told in one sentence both that it
# ended and that they may ask again — which is more than the fifteen-minute
# ghost sweep ever told them.
SUMMARY_ABANDONED_REASON = (
    "the summary build stopped before it finished and was released so it can be asked for again"
)

# `finalize`'s answer to a pipeline that returned nothing and raised nothing.
# A constant since ب-11ب, for `SUMMARY_NO_INDEXED_TEXT_REASON`'s reason: the
# set below can only hold sentences that have a name.
SUMMARY_EMPTY_BUILD_REASON = "the summary build produced no text"

# ب-11ب (خطة السيناريوهات §7، ف-3) — the reasons a THREAD may be shown
# verbatim, and the reason there has to be a set at all.
#
# The item's own decision reads: `reason` is displayed and not translated,
# because the three possible reasons are all sentences this module wrote for
# this reader. The first half is kept and the second is not true. `reason`
# reaches this event from six places, and three of them carry
# `str(exc)`: `run`'s broad `except Exception` around the whole provider
# pipeline, `claim`'s `(AppError, ValueError)` in the handler, and the
# `ConflictError` branch after it. What those produce is an httpx connect
# error naming an internal host, a JSON decode error, or whatever a provider
# SDK puts in `str()` — text written for a log, about to be appended to a
# user's conversation as an assistant message.
#
# So the set is the whitelist, and everything outside it gets
# `SUMMARY_FAILURE_RETRY_*` instead: one sentence that is true of every
# failure. Nothing is lost by that, because the reasons it hides all mean the
# same actionable thing — it failed, ask again — and the one that means
# something else is in the set.
#
# Two members can no longer reach a thread at all (a cancellation and ب-9's
# release both publish `conversation_id=None`), and they are in it anyway:
# this is "sentences this module wrote for this reader", which is a property
# of the sentence, not of today's routing.
SUMMARY_DELIVERABLE_REASONS: frozenset[str] = frozenset(
    {
        SUMMARY_NO_INDEXED_TEXT_REASON,
        SUMMARY_CANCELLED_REASON,
        SUMMARY_ABANDONED_REASON,
        SUMMARY_EMPTY_BUILD_REASON,
    }
)

# ب-11ب — what a thread is told when the build it was promised will not
# arrive, on `SUMMARY_DELIVERY_HEADER_*`'s shape: a named form and an unnamed
# one, in each language, differing in the NAME and nothing else.
#
# ق-ز's wording, with its «س» filled by the same `document_names` lookup the
# delivery uses. No trailing stop on the notices: the composer decides the
# punctuation, because the two endings are different sentences — a reason
# follows a colon, and the retry line follows a full stop.
SUMMARY_FAILURE_NOTICE_EN = "The summary could not be prepared"
SUMMARY_FAILURE_NOTICE_AR = "تعذّر إعدادُ الملخّص"
SUMMARY_FAILURE_NOTICE_NAMED_EN = 'The summary of "{name}" could not be prepared'
SUMMARY_FAILURE_NOTICE_NAMED_AR = "تعذّر إعدادُ ملخّص «{name}»"

# Said only when the reason is NOT shown, and that is the whole of its job.
# Every hidden reason means "it failed, ask again"; the one shown reason that
# would contradict it -- there is no indexed text -- is in the set above, and
# telling that reader to ask again would send them round the same loop.
SUMMARY_FAILURE_RETRY_EN = "You can ask for it again."
SUMMARY_FAILURE_RETRY_AR = "يمكنك طلبُه مرّةً أخرى."

# The staleness bound ب-9 measures against, mirroring
# `Limits.summarize_job_max_duration_s` rather than importing it: this module
# takes its configuration as ARGUMENTS and imports no `Settings` anywhere in
# its application or domain layers (س-24, and a test enforces it). The
# composition root passes the live value; this is what the number is when
# nobody says otherwise, exactly as `summarization._DEFAULT_CALL_TIMEOUT_S`
# mirrors its own sibling.
_DEFAULT_MAX_BUILD_DURATION_S = 1_800.0

# ب-10's ceiling, mirroring `Limits.max_active_summary_jobs_per_workspace` for
# the reason the bound above mirrors its own sibling: this module's
# application and domain layers import no `Settings` (س-24), so a second
# configured number arrives as a second ARGUMENT and this is what it is when
# nobody says otherwise.
_DEFAULT_MAX_ACTIVE_JOBS = 3

# The `QuotaLock` name for the ceiling above (capacity-plan 2.7). Public
# because the transaction that must hold it belongs to `RequestSummaryService`
# below and not to `RequestSummary`, and a constant rather than a literal
# because the string IS the lock's identity -- two spellings are two locks that
# serialise against nobody.
ACTIVE_SUMMARY_JOB_CEILING = "knowledge.max_active_summary_jobs"

# `F-9` (rag-summarization-fix-plan.md §3.10) — what a DELIVERED summary says
# when the build stopped at `_MAX_MAP_CHUNKS`. Two fixed sentences, phrased
# for the same reader as the two above; `delivered_summary_text` picks between
# them with `_is_rtl`, the one "this summary is Arabic" rule this module owns.
#
# PROSE here, not `summarization.py`'s `[...]` marker, and the difference is
# who reads it. That glyph was chosen over alpha's bracketed English phrase
# because the text it marks is fed back to a small local model, which
# `agents/rag_agent/prompts` measured copying such phrases into its answer
# instead of answering (24 of 40 samples). This sentence is read by a PERSON
# in their own thread and by no model at all, so the reason to prefer a mark
# over a sentence does not reach it — and a bare `[...]` would leave
# reader to guess what was left out.
#
# It carries NO NUMBER, deliberately. `source_chunks` is how many chunks were
# READ and the row stores no total, so a figure here would be a numerator with
# no denominator, in a unit ("chunks") this reader has never been shown —
# precise-looking and unusable. What they can act on is the one fact that is
# certain: the end of the document was not read.
SUMMARY_TRUNCATED_NOTICE_EN = (
    "Note: this document is longer than one summary can read. "
    "The text above covers its beginning, not the whole file."
)
SUMMARY_TRUNCATED_NOTICE_AR = (
    "تنبيه: هذا المستند أطول من أن يُقرأ كاملًا في ملخّصٍ واحد، وما سبق يغطّي أوّله لا الملفّ كلّه."
)

# ب-7ج (scenarios plan §4, gap ف-2) — which FILE the text below is a
# summary of. The receipt that accepted the build can name the
# document now (`RoutedAnswer.summary_target_name`), and this is the
# other half of the same sentence: a thread whose receipt said
# "«الميزانية»" and whose summary said nothing was still asking its
# reader to assume the two are about one file. Minutes of unrelated
# messages may sit between them.
#
# PREPENDED, where the truncation notice is appended, and the two
# rules agree rather than conflicting: a caveat qualifies the answer
# and belongs after it; a title says what is being read and is
# useless after it has been read. This is the one line that turns a
# wall of prose arriving unannounced into an identified artefact.
#
# It carries no id, no status and no timestamp. The reader asked
# about a file by name, and the name is the only part of a build
# they have a way to recognise.
SUMMARY_DELIVERY_HEADER_EN = 'Summary of "{name}":'
SUMMARY_DELIVERY_HEADER_AR = "ملخّص «{name}»:"


def _reflects_current_pipeline(document: Document) -> bool:
    """Whether re-indexing ``document`` right now would reproduce output
    identical to what is already stored (plan §3.6, decision س-14 = أ).

    Files are immutable once ``ready`` (INV-F4 — only the name may ever
    change), so a document's OWN recorded ``content_hash`` is, by
    construction, still an accurate fingerprint of its file's current bytes;
    there is no new hash to fetch and compare here. That collapses
    ``domain.pipeline.content_pipeline_unchanged``'s "pair" to a comparison
    against itself for the content half — still called through the SAME
    general predicate (not a bespoke ``==``) so the ONE rule that decides
    "unchanged" lives in exactly one place in the domain — while the
    pipeline-version half stays a REAL comparison against today's
    ``PIPELINE_VERSION``. A document with no recorded fingerprint at all
    (``content_hash is None`` — never successfully indexed) is never
    "unchanged": there is nothing yet to leave alone.
    """
    if document.content_hash is None:
        return False
    return content_pipeline_unchanged(
        stored_content_hash=document.content_hash,
        current_content_hash=document.content_hash,
        stored_pipeline_version=document.pipeline_version,
        current_pipeline_version=PIPELINE_VERSION,
    )


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


class IndexFile:
    """Register a file's document **because someone asked for it** — the
    manual-indexing face (``POST /knowledge/documents``).

    This is the whole of what "indexing is no longer automatic" means. Until
    this use-case existed, completing an upload published
    ``files.file.uploaded.v1``, the knowledge worker registered a document off
    it, and the ``DocumentRegistered`` that registration produced sent the
    same worker straight into the embedding pipeline: a file was indexed
    because it had been uploaded, and nobody was ever asked. Now the upload
    ends at storage, and THIS is the only thing that starts ingestion.

    It composes ``RegisterDocumentFromFile`` rather than re-implementing it,
    so a ``pending`` ``Document`` is still minted in exactly one place, and
    adds the two questions a REQUEST has to answer that an event did not:

    * **Are there bytes to index?** ``files.get_readable`` returns a view only
      for a ``ready`` file (INV-F2), so a file whose PUT never landed, or that
      was deleted or quarantined, is a 404 here — which is also, exactly, the
      "only after the upload completes" precondition the button in front of
      this route is drawn from. ``None`` is not diagnosed further: see
      ``ports/files.ReadableFiles``.

    * **Is it indexed already?** INV-K3 lets one file own many documents by
      design (a re-upload mints a new one), and an event stream that can
      redeliver NEEDS that latitude. A button that can be clicked twice does
      not: two live documents over one file means every search answers from
      that file twice, forever — the duplication ``ReindexDocuments`` deletes
      points up front to avoid. So a file that already has a document is a
      409 here, and rebuilding one is ``POST /knowledge/reindex``'s job, the
      face that knows to destroy what it supersedes.

      **One exception (plan §3.6/step 15, decision س-14 = أ):** if that
      existing document is already ``indexed`` under TODAY's
      ``domain.pipeline.PIPELINE_VERSION``, re-indexing it would reproduce
      output byte-for-byte identical to what is already there — files are
      immutable once ``ready`` (INV-F4), so nothing changed. That case
      "returns immediately": the existing ``Document``, no new one minted,
      no conflict raised. See ``_reflects_current_pipeline``.

    The space is the FILE's, read off the same view (spaces plan, step 8) and
    never taken from the caller: a client-supplied space could file a document
    under a space its file does not belong to, and retrieval would then
    answer, inside that space, out of content the space cannot see.
    """

    def __init__(self, documents: DocumentRepository, files: ReadableFiles) -> None:
        self._documents = documents
        self._register = RegisterDocumentFromFile(documents)
        self._files = files

    async def execute(
        self, ctx: ExecutionContext, *, file_id: Uuid
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        if not file_id.strip():
            raise ValidationError("file_id must not be empty")

        view = await self._files.get_readable(ctx, file_id)
        if view is None:
            raise NotFoundError("file not found")

        # Checked BEFORE the mint, not after: `add` has no uniqueness
        # constraint to lean on here (INV-K3 forbids one), so this read is the
        # only thing standing between a double click and a corpus that answers
        # twice.
        existing_ids = await self._documents.ids_for_files(ctx, [file_id])
        if existing_ids:
            existing = await self._documents.get(ctx, existing_ids[0])
            # §3.6/§4 step 15, decision س-14 = أ: re-indexing a document whose
            # fingerprint is unchanged returns immediately -- the existing
            # document, not a new one and not a conflict. See
            # `_reflects_current_pipeline`'s own docstring for why this is
            # the pair rule (content_hash AND pipeline_version) even though
            # only the pipeline half can genuinely differ at THIS call site.
            if existing is not None and _reflects_current_pipeline(existing):
                return existing, ()
            raise ConflictError(
                "this file already has a knowledge document;"
                " rebuild it through POST /knowledge/reindex"
            )

        return await self._register.execute(ctx, file_id=file_id, space_id=view.space_id)


class IndexFileService:
    """Wraps ``IndexFile`` with the Outbox append inside ONE request-scoped
    unit of work — the ``ReindexService`` shape, and for its reason.

    The document row and the ``DocumentRegistered`` that tells a worker to
    index it must commit together or not at all. A row without its event is a
    file the user asked to index, that reports itself ``pending``, and that no
    worker was ever told about — indistinguishable from the outside from one
    merely waiting its turn. So the append is not swallowed: it propagates,
    and rolls the mint back with it.
    """

    def __init__(self, index: IndexFile, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._index = index
        self._outbox = outbox
        self._uow = uow

    async def start(self, ctx: ExecutionContext, *, file_id: Uuid) -> Document:
        async with self._uow.begin(ctx):
            document, events = await self._index.execute(ctx, file_id=file_id)
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return document


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

    ``content_hash`` (plan step 15, §3.6) rides along only on a successful
    ``outcome`` -- the fingerprint of the bytes that outcome was built from,
    computed by the caller (``workers/content_resolver.py``, which already
    holds the raw bytes ``run`` never sees) and carried here so ``finalize``
    can stamp it on the row alongside ``complete_indexing``. ``None`` for
    every other case: a redelivery no-op changes nothing to fingerprint, and
    a failed attempt never produced output worth hashing.
    """

    document: Document
    outcome: IndexOutcome | None
    error: str | None
    content_hash: str | None = None

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
    terminal outcome: parent chunks (P-13, plan §3.3 -- minted and written
    BEFORE the ``Chunk`` rows that reference them via ``parent_id``) + chunks
    + status + the follow-on event. The worker
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
        content_hash: str,
    ) -> tuple[Document, tuple[KnowledgeEvent, ...]]:
        attempt = await self.run(
            ctx,
            document_id=document_id,
            parsed=parsed,
            model=model,
            api_key=api_key,
            content_hash=content_hash,
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
        content_hash: str,
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

        return IndexAttempt(document=doc, outcome=outcome, error=None, content_hash=content_hash)

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
            now = utc_now()

            # P-13 (plan §3.3): mint + persist this batch's parent chunks
            # BEFORE the `Chunk` rows that reference them -- `parent_id`
            # carries an un-cascaded FK to `parent_chunks(id)`
            # (`0005_parent_chunks.py`), so the referenced row must already
            # exist. Id minting happens HERE, one layer above
            # `IndexDocument`, mirroring where `Chunk.id` itself is minted --
            # `ParentChunkDraft`'s own docstring.
            parent_id_by_key: dict[str, str] = {}
            if attempt.outcome.parents:
                drafts = sorted(attempt.outcome.parents, key=lambda draft: draft.order)
                parent_rows = [
                    ParentChunk(
                        id=new_uuid7(),
                        document_id=doc.id,
                        workspace_id=ctx.workspace_id,
                        seq=seq,
                        text=draft.text,
                        created_at=now,
                        is_complete=draft.is_complete,
                    )
                    for seq, draft in enumerate(drafts)
                ]
                parent_id_by_key = {
                    draft.key: row.id for draft, row in zip(drafts, parent_rows, strict=True)
                }
                await self._documents.add_parent_chunks(ctx, parent_rows)

            chunks = [
                Chunk(
                    id=new_uuid7(),
                    document_id=doc.id,
                    workspace_id=ctx.workspace_id,
                    seq=indexed_chunk.seq,
                    text=indexed_chunk.text,
                    token_count=indexed_chunk.token_count,
                    vector_ref=VectorRef(attempt.outcome.collection, indexed_chunk.chunk_id),
                    parent_id=(
                        parent_id_by_key.get(indexed_chunk.parent_key)
                        if indexed_chunk.parent_key
                        else None
                    ),
                )
                for indexed_chunk in attempt.outcome.chunks
            ]
            await self._documents.add_chunks(ctx, chunks)

            # §3.6/plan step 15: stamp the content fingerprint pair in the
            # SAME transition that completes indexing -- see
            # `Document.complete_indexing`'s own docstring for why it is not
            # a separate write. `attempt.content_hash` is never `None` here:
            # `run` only reaches an `outcome` (this branch) by way of the
            # ONE `IndexAttempt` constructor call that sets it.
            doc.complete_indexing(
                len(chunks),
                now,
                content_hash=attempt.content_hash,
                pipeline_version=PIPELINE_VERSION,
                text_chunks=attempt.outcome.text_chunks,
                table_chunks=attempt.outcome.table_chunks,
                image_chunks=attempt.outcome.image_chunks,
            )
            await self._documents.set_status(
                ctx,
                doc.id,
                IndexStatus.INDEXED.value,
                content_hash=attempt.content_hash,
                pipeline_version=PIPELINE_VERSION,
                text_chunks=attempt.outcome.text_chunks,
                table_chunks=attempt.outcome.table_chunks,
                image_chunks=attempt.outcome.image_chunks,
            )

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

    **The file behind every target must still be READABLE.** A document
    whose file was deleted is not a rebuild candidate: rebuilding its corpus
    puts that file's content back into retrieval under its own name, which is
    the exact defect ``framework/di/file_deletion.py`` exists to prevent,
    arriving through the one door that cascade does not watch.

    That door is still open WITH the cascade in place, which is why the check
    belongs here rather than being left to it. ``DeleteFileService`` marks the
    row and purges the corpus in SEPARATE transactions (R2 -- the vector store
    is network I/O and may not run under an open one), and its docstring
    accepts out loud that an interrupted run "leaves a deleted file with part
    of its corpus still standing". In that window a document over a
    soft-deleted file exists, and without this guard a re-index rebuilds it --
    and unlike the corpus a re-run of ``DELETE /files/{id}`` repairs, the
    rebuild is a NEW document over the same file, which that re-run does find
    (``purge_file`` deletes by ``file_id``) but only if someone runs it again
    knowing it happened.

    ``IndexFile`` asks this same question of this same port for this same
    reason -- there are no bytes to index -- and the asymmetry between the two
    was a gap rather than a decision. The read is ONE bulk ``names_for_files``
    and not ``get_readable`` per target: 50 is the declared ceiling here, and
    the per-item shape is precisely the N+1 the corpus walks already removed
    (branch review §2). Absence from that mapping carries what ``None``
    carries there -- unknown, deleted, quarantined or still uploading,
    undistinguished -- so this raises ``NotFoundError`` without diagnosing
    which, exactly as ``IndexFile`` does.

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
        files: ReadableFiles,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._vectors = vectors
        self._files = files

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

        # The file behind every target must still be readable -- ONE bulk
        # read for up to `_MAX_REINDEX` of them, for the reason the corpus
        # walks stopped asking per document (branch review §2). Absence is
        # deliberately not diagnosed (`ports/files.ReadableFiles`): deleted,
        # quarantined, unknown and still-uploading are one answer here.
        readable = await self._files.names_for_files(
            ctx, [target.source.file_id for target in targets]
        )
        for target in targets:
            if target.source.file_id not in readable:
                raise NotFoundError("file not found")

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


def _is_abandoned(job: SummaryJob, now: datetime, max_build_duration_s: float) -> bool:
    """Whether a job holding the active key can no longer be finished by anyone.

    The bound is DERIVED, not a second number to keep in step:
    ``max_build_duration_s`` is ``Limits.summarize_job_max_duration_s``, the
    cap the worker's own message handler ends a long build at. A job that has
    passed it and is still not terminal is therefore one whose keeper is gone
    — a live worker would have ended it itself. Whoever raises that setting
    moves this with it, which is the point of deriving the bound rather than
    writing a second number next to it.

    ``running`` only, and the narrowing is deliberate. A ``queued`` job's
    wait is bounded by the QUEUE, not by the build, and those are different
    quantities: the three knowledge handlers share one consumer loop, so a
    job can sit queued behind a build that is itself allowed to run for the
    whole cap — and failing it there would kill a job a worker is still
    coming for. Nothing is lost by excluding it: a queued job's
    ``SummaryRequested`` cannot go silently missing, because the row and the
    outbox record are written in ONE transaction (``RequestSummaryService``)
    and a message whose consumer died is redelivered by the ghost sweep.

    ``updated_at`` is what makes the question askable at all, and it only
    reaches this layer because of ب-8.
    """
    if job.status is not SummaryJobStatus.RUNNING:
        return False
    return now - job.updated_at > timedelta(seconds=max_build_duration_s)


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

    **``conversation_id`` is where the answer is owed** (`F-7`), and it is
    not part of the key. Two threads asking for the same overview of the same
    document in the same language are still ONE build and still a 409 for the
    second — what it changes is only which thread the FIRST build's text is
    delivered to when it lands. Defaulted to ``None`` because the REST route
    genuinely has no thread: a build asked for through ``POST /documents/
    {id}/summary`` is read back through ``GET``, and there is nothing to
    deliver it to.

    It is not stored on the job. The row records an operation; where its
    output should be posted is a property of the REQUEST, and it travels on
    the message the same way the build key does (``SummaryRequested``).

    **The 409 has one exception, and it is where the key is freed** (ب-9).
    A build whose worker died stays ``running`` and keeps
    ``uq_summary_job_active`` forever, so every later request for that key is
    refused for a build that is not happening. The check for that costs
    nothing here: the row was read on the line above. Ending it — in
    ``failed``, with a written reason, inside the unit of work this call
    already runs in — releases the key at the moment somebody needs it, which
    is the one moment a periodic sweeper cannot pick.

    **And the workspace has a ceiling of its own** (ب-10, gap ف-7). The
    guards above are about one KEY; nothing bounded a tenant with fifty
    documents from queueing fifty builds that are each individually legal,
    every one of them minutes of provider calls on a worker every tenant
    shares. That check is last, deliberately: see ``execute``.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        jobs: SummaryJobRepository,
        *,
        max_build_duration_s: float = _DEFAULT_MAX_BUILD_DURATION_S,
        max_active_jobs: int = _DEFAULT_MAX_ACTIVE_JOBS,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._max_build_duration_s = max_build_duration_s
        self._max_active_jobs = max_active_jobs

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
        conversation_id: Uuid | None = None,
    ) -> tuple[SummaryJob, tuple[KnowledgeEvent, ...]]:
        document = await self._documents.get(ctx, document_id)
        if document is None:
            raise NotFoundError("document not found")
        # ب-4ب (scenarios plan section 5, gap ف-7) — the two refusals below are
        # `ConflictError` SUBCLASSES now, and everything about this route is
        # unchanged by that: both still carry `common.conflict`, both still
        # answer 409, and the catalogue still holds one entry (ق-6). What is
        # new is that a caller which renders a SENTENCE can tell them apart
        # without matching on a message written for a log — and these two
        # deserve opposite sentences. The order of the checks stays what it
        # was; the type each one raises is what changed.
        if document.status is not IndexStatus.INDEXED:
            raise SummaryTargetNotIndexed(
                f"document {document.id} is not indexed (status {document.status.value!r})"
                " and has no text to summarise"
            )

        now = utc_now()
        events: list[KnowledgeEvent] = []
        active = await self._jobs.active_for(ctx, document_id, kind, lang)
        if active is not None:
            if _is_abandoned(active, now, self._max_build_duration_s):
                # ب-9 — the key is held by a job nobody is holding. Ending it
                # here frees `uq_summary_job_active` AND gives the asker an
                # answer; the ghost sweep gives neither, because it was built
                # to reclaim dead Redis consumers and re-delivers a message
                # rather than settling a row. Everything below lands in the
                # transaction `RequestSummaryService` already opened: the
                # release, its event, the new job and its event are one write
                # (ق-1 — a guard inside the first phase, not a fourth phase).
                active.fail(SUMMARY_ABANDONED_REASON, now)
                await self._jobs.save(ctx, active)
                events.append(
                    SummaryBuildFailed(
                        active.id,
                        ctx.workspace_id,
                        active.document_id,
                        SUMMARY_ABANDONED_REASON,
                        # ب-11أ — no thread, and not for want of one to hand
                        # over: `conversation_id` is in scope right here. It
                        # belongs to the request being served, and that
                        # request is about to get its own receipt in that same
                        # thread. The thread owed this news is the one that
                        # asked for the DEAD build, and no row records it.
                        None,
                        now,
                    )
                )
            else:
                raise SummaryBuildInProgress(
                    f"a {kind.value} summary of this document in {lang.value}"
                    f" is already being built (job {active.id})"
                )

        # ب-10 (gap ف-7) — the workspace's ceiling, read LAST of the three
        # guards and after ب-9's release, both for reasons that would be
        # wrong the other way round.
        #
        # After the key check (decision 3): a caller re-asking for a file that
        # is already being built deserves «هذا قيد الإعداد» — true, specific,
        # and about their own request — not «مساحتك مشغولة», which is true of
        # the workspace and unhelpful about the file. The narrower answer wins
        # whenever both hold.
        #
        # After the release: a job ب-9 just failed is no longer active, and
        # the count must see that. Inside `RequestSummaryService`'s unit of
        # work every adapter call joins one session (`rls` docstring), so the
        # UPDATE above is visible to this SELECT — the freed slot is freed
        # for the very request that freed it, not for the one after.
        if await self._jobs.count_active(ctx) >= self._max_active_jobs:
            raise SummaryWorkspaceBusy(
                f"this workspace already has {self._max_active_jobs} summary builds"
                " queued or running; ask again when one of them finishes"
            )

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
            # Its birth value, and the last one this layer ever authors: from
            # the INSERT on, `trg_touch` owns the column (entity docstring).
            updated_at=now,
        )
        await self._jobs.add(ctx, job)
        events.append(
            SummaryRequested(
                job.id, ctx.workspace_id, document_id, kind.value, lang.value, conversation_id, now
            )
        )
        return job, tuple(events)


class RequestSummaryService:
    """Wraps ``RequestSummary`` with the Outbox append inside ONE unit of work
    (the ``ReindexService`` shape).

    Atomicity is the whole feature here: a queued job whose
    ``SummaryRequested`` event was lost is a job no worker will ever be told
    about, sitting at 0% forever while occupying ``uq_summary_job_active`` and
    blocking every retry of the same build. That is worse than the request
    having failed outright, which is what rolling both back produces.
    """

    def __init__(
        self,
        request: RequestSummary,
        outbox: EventOutbox,
        uow: UnitOfWork,
        lock: QuotaLock,
    ) -> None:
        self._request = request
        self._outbox = outbox
        self._uow = uow
        self._lock = lock

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
        conversation_id: Uuid | None = None,
    ) -> SummaryJob:
        async with self._uow.begin(ctx):
            # capacity-plan 2.7 -- `RequestSummary`'s ب-10 ceiling counts
            # active jobs and then inserts one, and READ COMMITTED lets every
            # concurrent asker read the same count: three slots admitted as
            # many builds as the process had spare connections. The count below
            # is inside THIS transaction (the ب-10 comment says why it must
            # be), so holding the workspace's lock here is what makes it a
            # count nothing can invalidate before it is acted on.
            await self._lock.hold(ctx, ACTIVE_SUMMARY_JOB_CEILING)
            job, events = await self._request.execute(
                ctx,
                document_id=document_id,
                kind=kind,
                lang=lang,
                conversation_id=conversation_id,
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

    **One narrow fallback: the ``auto`` row, when its text really is in the
    language asked for** (`F-6`). The chat path builds under ``lang = auto``
    (``routing._ROUTED_SUMMARY_LANG``, because the question never named a
    language), and a client that then reads ``ar`` — the language its reader
    is in — was told "no summary" for a summary that exists and is written in
    Arabic. When the requested key is empty, this read tries the ``auto`` row
    and returns it only if the MAJORITY of its letters are in that language's
    script (``_detects_lang``).

    That is not a fallback BETWEEN languages, which is what the key exists to
    prevent. An ``auto`` row in English is never handed to a request for
    ``ar``: the text has to BE the language asked for, so nobody is given a
    summary in a language they did not ask for and told it matched. A request
    for ``auto`` itself stays an exact match — there is nothing for it to fall
    back TO — and ``kind`` never falls back at all, because the overview and
    the full summary are different artefacts rather than two spellings of one.

    The 404 contract above is untouched: what changed is which row COUNTS as
    the match, not what happens when there is none. And the rule lives HERE
    rather than in ``SummaryRepository.get``, which stays the exact-key read
    its own docstring describes: a repository that quietly returned a row
    other than the one asked for would have no way to say that it had.
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
            summary = await self._auto_row_written_in(ctx, document_id, kind, lang)
        if summary is None:
            raise NotFoundError("summary not found")
        return summary

    async def _auto_row_written_in(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary | None:
        """The stored ``auto`` summary of this document and kind — but only
        when its text is written in ``lang``, and never for a request that is
        already ``auto``, which the exact read above has just answered.

        The second read happens only on a miss, so the ordinary case of an
        occupied key still costs one query. ``BuildSummary._translation_source``
        orders its two reads the same way, for the same reason.
        """
        if lang is SummaryLanguage.AUTO:
            return None
        stored = await self._summaries.get(ctx, document_id, kind, SummaryLanguage.AUTO)
        if stored is None or not _detects_lang(stored.text, lang):
            return None
        return stored


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
    the request never said, so the only honest source is the text itself —
    which is ``_detects_lang``, the same majority-of-letters rule that gates
    the ``auto`` fallback in ``GetSummary``. One definition of "this text is
    Arabic", shared by the three places that need one.

    The third (`F-9`, ``delivered_summary_text``) asks it as a LANGUAGE
    question rather than a layout one — which sentence to append — and
    is the same question this body answers: the name says what the first two
    callers do with the answer, not what the answer is.
    """
    if summary.lang is SummaryLanguage.AR:
        return True
    if summary.lang is SummaryLanguage.EN:
        return False
    return _detects_lang(summary.text, SummaryLanguage.AR)


def _detects_lang(text: str, lang: SummaryLanguage) -> bool:
    """Whether ``text`` is WRITTEN in ``lang``, judged by the majority of its
    LETTERS — not by whether one letter of that script appears at all.

    A summary in English quoting one Arabic term is an English document.
    Laying it out right to left because of that quote, or handing it to a
    reader who asked for ``ar``, would be worse than the mistake the check
    exists to avoid. Hence a majority — and hence a POSITIVE test per script
    rather than "not the other one": a summary in Cyrillic is not English
    either, and answering an ``en`` read with it would be exactly the
    mislabelling this rule is here to prevent.

    ``auto`` is never detected. It names an instruction — "whatever language
    the document is in" — and not a script, so no text can be evidence for
    it. ``GetSummary`` never asks; this is the second guard.
    """
    if lang is SummaryLanguage.AUTO:
        return False
    written_in = _is_arabic if lang is SummaryLanguage.AR else _is_latin
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    return sum(1 for char in letters if written_in(char)) * 2 > len(letters)


def _is_arabic(char: str) -> bool:
    """The Arabic block, U+0600..U+06FF. A range comparison and not
    ``unicodedata``, which would be a table lookup per letter of every body
    that is read."""
    return "؀" <= char <= "ۿ"


def _is_latin(char: str) -> bool:
    """ASCII letters plus Latin-1 Supplement and Latin Extended-A/B
    (U+00C0..U+024F), so "naïve" counts as the Latin it is."""
    return "a" <= char <= "z" or "A" <= char <= "Z" or "À" <= char <= "ɏ"


def delivered_summary_text(summary: Summary, file_name: str | None = None) -> str:
    """A summary as the THREAD that asked for it should receive it (`F-9`,
    plan §3.10): its text, plus one sentence when the build stopped at the
    map ceiling, plus a line naming the file when the caller could name
    it (ب-7ج, gap ف-2).

    **The one surface with no field to say it in.** ``truncated`` is stored
    on the row and published by ``SummaryOut``, so every REST reader can tell
    that a ``full`` summary of a long document is a true summary of a PREFIX.
    A conversation message is prose and has no such field, so until now a
    summary that crossed the ceiling arrived in the thread looking exactly
    like one that had covered the whole document — the silent cut
    plan §3.10 raises to a rule against, and the one ``retrieval.py``'s
    ``_PARENT_TRUNCATION_MARKER`` believed it had closed the last of.

    **The stored row is left alone.** The notice is composed at DELIVERY and
    never written into ``Summary.text``. A client reading the same summary
    through ``GET`` already has the flag, so folding the sentence into the
    artefact would state one fact twice there — and would put prose the
    model never wrote inside the text field, for every later reader of that
    row, including ``translate``, whose input is an already-built summary.

    **Appended, not prepended.** The summary is the answer and the notice
    qualifies it; a caveat placed above would stand between the reader and
    what they asked for, and read as an error rather than a footnote.

    An untruncated summary of a file that could not be named — every
    ``overview``, and every ``full`` build inside the ceiling — is
    returned UNCHANGED rather than unannotated: nothing is added and
    nothing is reformatted, so the plainest delivery is byte for byte
    the text that was built.

    **``file_name`` obeys every rule above, which is why it is a
    parameter here and not a second composer at the call site**
    (ب-7ج): the header is composed at DELIVERY and never written into
    ``Summary.text``, so ``GET`` sees the artefact and not a thread's
    framing of it, and ``translate`` still reads a summary rather
    than a summary wearing a title. Prepended, where the truncation
    notice is appended — see the constants for why the two
    directions agree.

    ``None`` means the caller could not name the file (deleted,
    quarantined, or a name read that failed), and it delivers the
    text exactly as it delivered it before this parameter existed. A
    blank name is the same case: a header reading ``Summary of "":``
    tells the reader their file is called nothing.
    """
    body = summary.text
    if summary.truncated:
        notice = SUMMARY_TRUNCATED_NOTICE_AR if _is_rtl(summary) else SUMMARY_TRUNCATED_NOTICE_EN
        body = f"{body}\n\n{notice}"
    if file_name is None or not file_name.strip():
        return body
    header = SUMMARY_DELIVERY_HEADER_AR if _is_rtl(summary) else SUMMARY_DELIVERY_HEADER_EN
    return f"{header.format(name=file_name.strip())}\n\n{body}"


def delivered_failure_text(reason: str, file_name: str | None = None) -> str:
    """A failed build as the THREAD that asked for it should hear about it
    (ب-11ب, gap ف-3).

    **The half of `F-7` that was never built.** A finished summary reaches
    its thread; a build that failed reached nobody, so a conversation that
    was told «سيصلك عند اكتماله» waited for a message that would never come
    and had no way to learn that. The receipt is a promise, and this is the
    only place that promise can be withdrawn.

    Composed HERE and not in the worker, exactly as ``delivered_summary_text``
    is: what a thread receives is this module's prose, and the worker's job is
    to append it. Nothing is written to the row — the job's ``error`` column
    already holds the reason, and the framing is for one reader.

    **The reason is shown only if this module wrote it**
    (``SUMMARY_DELIVERABLE_REASONS``, where the argument is). Everything else
    is a provider's ``str(exc)``, which belongs in a log and not in somebody's
    conversation.

    **The language follows the text the message will carry.** That is the
    whole rule, and it is picked with ``_detects_lang`` — the module's one
    "is this Arabic" test, the same one ``delivered_summary_text`` uses. When
    a reason is shown, the reason decides, so the sentence cannot end up an
    Arabic frame around an English clause; when it is not, the file's name
    decides, since that is then the only text in the message the user wrote.
    Neither available means English.

    A real limit, stated rather than hidden: ق-ز asks for the language of the
    QUESTION, and no worker has one — the query lives in the turn that asked,
    minutes ago and in another process. What is here is the best signal that
    exists at delivery, and it is a signal about the text, not about the
    reader.
    """
    shown = reason if reason in SUMMARY_DELIVERABLE_REASONS else None
    named = file_name is not None and bool(file_name.strip())
    probe = shown if shown is not None else (file_name or "")
    arabic = _detects_lang(probe, SummaryLanguage.AR)

    if named:
        template = SUMMARY_FAILURE_NOTICE_NAMED_AR if arabic else SUMMARY_FAILURE_NOTICE_NAMED_EN
        # `file_name` is not None wherever `named` is true; mypy needs saying.
        notice = template.format(name=(file_name or "").strip())
    else:
        notice = SUMMARY_FAILURE_NOTICE_AR if arabic else SUMMARY_FAILURE_NOTICE_EN

    if shown is not None:
        return f"{notice}: {shown}."
    retry = SUMMARY_FAILURE_RETRY_AR if arabic else SUMMARY_FAILURE_RETRY_EN
    return f"{notice}. {retry}"


class ReadStoredSummary:
    """The already-built summary under one exact key, delivered the way a
    thread receives one — or ``None`` (ب-8, gap ف-3).

    Satisfies ``routing.SummaryReading`` structurally, which is why its method
    is ``stored_text`` rather than this layer's usual ``execute``: it is the
    ONE capability the router asked for, named for what the router needs, the
    way ``RequestSummaryService.start`` is.

    **Why not ``GetSummary``.** That use-case answers a REST read, and it
    carries two behaviours this caller must not have. It raises
    ``NotFoundError`` on a miss — but a document nobody has summarised yet is
    the ordinary case here, and the answer to it is the build the router goes
    on to queue, not an error to unwind. And it falls back to the ``auto`` row
    when the requested language is empty (`F-6`), a widening that exists for a
    reader who asked for ``ar`` EXPLICITLY; the chat path asks for ``auto``
    itself (``routing._ROUTED_SUMMARY_LANG``), so it is already reading the
    row that fallback leads to. Reusing it would have meant borrowing an
    exception and a fallback and then working around both.

    **Why not the repository directly.** Because of the last line of this
    class: ``delivered_summary_text``. A summary read out of the store must
    reach a thread in the same shape as one a worker has just finished putting
    there — the truncation notice appended, the file-name header prepended,
    in that order. That composition already exists and already has one home;
    what this class does is put the read and that home together, so the router
    can depend on "a stored summary, ready to say" and never learn what
    ``truncated`` means.

    The stored row is untouched, as ever: the framing is composed at delivery
    and never written back.
    """

    def __init__(self, summaries: SummaryRepository) -> None:
        self._summaries = summaries

    async def stored_text(
        self,
        ctx: ExecutionContext,
        *,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
        file_name: str | None = None,
    ) -> str | None:
        """The exact-key read, and nothing wrapped around it.

        ``SummaryRepository.get``'s whole-triple rule is the contract this
        caller wants unchanged: an overview is not a full summary and an
        English one is not an Arabic one, so a miss here means a miss and the
        router queues the build the user asked for.
        """
        summary = await self._summaries.get(ctx, document_id, kind, lang)
        if summary is None:
            return None
        return delivered_summary_text(summary, file_name)


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
            # ب-11أ — `None`, and the item says so out loud rather than
            # leaving it to be discovered: this use case holds a `job_id` and
            # nothing else, and the thread that asked lives on the request
            # message. The recommendation is not to chase it — whoever pressed
            # Stop knows they pressed it (§9's deferral, with its criterion).
            job.id,
            ctx.workspace_id,
            job.document_id,
            SUMMARY_CANCELLED_REASON,
            None,
            now,
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

    ``translate_from`` is P-44 (plan §4 step 20, §3.10): the stored summary
    this build will TRANSLATE instead of building from chunks. ``None`` is
    the ordinary map-reduce build. Which of the two happens is decided in
    ``claim`` and recorded here rather than re-derived in ``run``, for this
    dataclass's whole reason: the work must not make decisions, or the same
    job could take a different path on a redelivery than it took the first
    time. ``chunks`` is empty exactly when this is set — a translation reads
    a few kilobytes of stored Markdown and never the corpus, which is the
    saving the step exists for.
    """

    job: SummaryJob
    chunks: tuple[str, ...]
    summarizer: ResolvedSummarizer
    translate_from: Summary | None = None


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

    **This is also P-44's only caller** (plan §4 step 20, §3.10): a build in a
    language nothing is stored under, for a document that HAS a summary in
    another one, is answered by translating that row rather than mapping the
    corpus again. ``claim`` decides it, ``run`` performs it, and ``finalize``
    stores it under the requested key like any other build — see ``claim``
    for the rule and ``SummaryBuildPlan.translate_from`` for why the decision
    is recorded rather than re-derived.
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

        **This is also where P-44 (plan §4 step 20, §3.10) decides to
        translate instead of build**, under one rule with two halves:

        * nothing is stored under the requested ``(document, kind, lang)``
          — an occupied key means the POST was a REBUILD (``RequestSummary``:
          "POST always builds", there is no ``force`` because ``GET`` is how
          you read what is stored), and answering a rebuild by re-translating
          a neighbouring language would quietly make the requested language
          unrebuildable for as long as any other one exists; AND
        * a summary of the same document and kind exists in some OTHER
          language, which is then the source.

        When both hold the corpus is never read: no ``chunk_texts``, and the
        job's total is the SOURCE's ``source_chunks`` — the number of chunks
        the text being translated actually stands for. Reporting the
        document's own chunk count instead would promise a map over the whole
        document that this build is precisely not doing.
        """
        job = await self._jobs.get(ctx, job_id)
        if job is None or job.is_terminal:
            return None

        resolved = await self._resolver.resolve_summarizer(ctx)

        source = await self._translation_source(ctx, job)
        if source is not None:
            job.start(source.source_chunks)
            await self._jobs.save(ctx, job)
            return SummaryBuildPlan(job=job, chunks=(), summarizer=resolved, translate_from=source)

        chunks = tuple(await self._documents.chunk_texts(ctx, job.document_id))
        job.start(len(chunks))
        await self._jobs.save(ctx, job)
        return SummaryBuildPlan(job=job, chunks=chunks, summarizer=resolved)

    async def _translation_source(self, ctx: ExecutionContext, job: SummaryJob) -> Summary | None:
        """The stored summary P-44 would translate for ``job``, or ``None``
        for the ordinary map-reduce build — ``claim``'s docstring states the
        rule; this is it in code, in one place so both halves stay together.

        The ``get`` comes FIRST and short-circuits: the common case by far is
        a key that is already occupied (every rebuild), and that case must
        cost one read, not two.
        """
        existing = await self._summaries.get(ctx, job.document_id, job.kind, job.lang)
        if existing is not None:
            return None
        return await self._summaries.newest_in_other_language(
            ctx, job.document_id, job.kind, job.lang
        )

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

    async def run(
        self,
        ctx: ExecutionContext,
        plan: SummaryBuildPlan,
        *,
        on_heartbeat: Callable[[], None] | None = None,
    ) -> SummaryAttempt:
        """The provider round trips. Never raises for a build failure — the
        reason is carried as data to ``finalize``, the ``IndexAttempt`` rule.

        Progress is written through ``record_progress`` and never through
        ``save``: see that port method for why a whole-row write here would
        let a running build overwrite a cancellation it had not noticed yet.

        **A translation (P-44) takes the same three-phase path**, and every
        guarantee below is the build's own: one provider call inside the same
        broad catch, the same ``SummaryAttempt`` carried to ``finalize``, the
        same terminal transaction. **Since ب-7 it also reports and listens
        like one.** It used to do neither, and the reason recorded here was
        that a single round trip has no step boundary to observe anything at
        — true until ب-6 moved the cancellation poll INSIDE a call. What a
        translation gets is one phase tick before its one call (never a
        count: it reads no chunk, and ``claim`` already fixed
        ``total_chunks`` at the source summary's own coverage) and the same
        ``_should_cancel`` every other shape passes down. The tick matters
        most for what rides on it: this was the ONLY path that beat not once,
        so a translation slow enough to matter was indistinguishable from a
        wedged worker to the health checker below.
        **``on_heartbeat`` (`F-4`, plan §3.5) is liveness, not progress.** The
        worker's loop beats once per completed cycle, so it cannot beat while
        a handler is still running, and a legitimate map-reduce occupies this
        one for minutes — long enough that ``HealthSettings.
        heartbeat_max_age_s`` reports a container busy doing exactly its job
        as dead, restarts it, and starts the whole build again. Passing the
        worker's own ``Heartbeat.beat`` here (no new port: it is the same
        object ``build_knowledge_worker`` already takes) lets a build that IS
        progressing say so.

        It is called from ``_progress`` and NOWHERE else — that is the
        guarantee that keeps it honest. A beat placed anywhere a build could
        reach without advancing would hide the very failure the health check
        exists to catch; from here, every beat stands behind a
        ``record_progress`` write that has already happened. A build stuck
        inside one provider call beats not at all and is caught by the
        checker, exactly as it should be; a build creeping forward too slowly
        to finish is caught instead by ``Limits.summarize_job_max_duration_s``
        in the handler, which is the second guard and the reason one beat per
        step is safe to give.
        """
        job = plan.job

        async def _progress(done: int) -> None:
            job.advance(done)
            await self._jobs.record_progress(ctx, job.id, job.done_chunks)
            # AFTER the write, never before: a beat is a claim that this build
            # moved, and until `record_progress` returns nothing has.
            if on_heartbeat is not None:
                on_heartbeat()

        async def _should_cancel() -> bool:
            fresh = await self._jobs.get(ctx, job.id)
            return fresh is None or fresh.is_terminal

        async def _translation_tick() -> None:
            """ب-7: what a translation has where a map-reduce has progress.

            It re-reports the count the job ALREADY holds rather than a new
            one. A translation reads no chunk, so there is no second step for
            a number to move between, and reporting anything else would be
            inventing coverage — the row would claim work that is not being
            done. What the write moves is the row's ``updated_at``, and what
            it fires is the heartbeat above it, which is the whole of what
            this path was missing.
            """
            await _progress(job.done_chunks)

        try:
            if plan.translate_from is not None:
                draft = await self._pipeline.translate(
                    ctx,
                    source=plan.translate_from,
                    lang=job.lang,
                    summarizer=plan.summarizer,
                    on_tick=_translation_tick,
                    should_cancel=_should_cancel,
                )
            else:
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
        self,
        ctx: ExecutionContext,
        attempt: SummaryAttempt,
        *,
        conversation_id: Uuid | None = None,
    ) -> tuple[SummaryJob, tuple[KnowledgeEvent, ...]]:
        """Store what the build produced and mint the events that follow it.

        ``conversation_id`` is a PASS-THROUGH (`F-7`): the handler reads it
        off the ``knowledge.summary.requested.v1`` message it is answering
        and hands it here, and the only thing done with it is stamping it on
        ``SummaryBuilt``. It is a parameter rather than a field on
        ``SummaryAttempt`` because ``claim``/``run`` never need it and an
        attempt is what the BUILD produced — threading it through both would
        make two functions carry a value neither reads.

        Defaulted to ``None``, so a build with no thread — and every caller
        that predates `F-7` — finalizes exactly as it did.

        **It is stamped on ``SummaryBuildFailed`` too now** (ب-11أ, gap ف-3).
        It was not, and the reason given was that nothing subscribed to a
        failure — true when it was written, and ب-11ب is what makes it false:
        the same worker that delivers a finished summary into its thread now
        delivers the news that there will not be one. A thread told «سيصلك
        عند اكتماله» and then left silent forever is the outcome this closes,
        and it is the SAME value on both events because it is the same
        question — where is this build's answer owed.
        """
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
                job.id,
                ctx.workspace_id,
                job.document_id,
                job.kind.value,
                job.lang.value,
                conversation_id,
                now,
            )
            return job, (event,)

        reason = attempt.error or SUMMARY_EMPTY_BUILD_REASON
        return job, await self._settle_failure(
            ctx, job, reason, now=now, conversation_id=conversation_id
        )

    async def _settle_failure(
        self,
        ctx: ExecutionContext,
        job: SummaryJob,
        reason: str,
        *,
        now: datetime | None = None,
        conversation_id: Uuid | None = None,
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
        return (
            SummaryBuildFailed(
                job.id, ctx.workspace_id, job.document_id, reason, conversation_id, at
            ),
        )


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


# The internal pagination batch `ListDocumentNames` walks the corpus with —
# NOT the caller's display cap (that is `limit`). A plain module constant:
# this only bounds how many rows one round trip to `DocumentRepository.list`
# fetches while counting the FULL corpus, and has no bearing on what ends up
# on the wire.
_LIST_PAGE_SIZE = 200

# The display cap applied when a caller names NO `limit` (retrieval plan
# §3.6/§4 row 6, `P-36`) — the number that used to be
# `rag_agent.agent._MAX_CORPUS_NAMES = 50`, one layer out.
#
# This default MIRRORS its `Settings` home (`RetrievalSettings.
# max_corpus_names`) byte for byte, the `RetrievalTuning` rule and for the
# same reason: a direct construction (a test, a script) must get the SHIPPED
# number rather than an accidental second configuration. The DEPLOYMENT's
# number arrives as a constructor ARGUMENT from the Composition Root — this
# layer reads neither `Settings` nor the environment (س-24 = أ), which is
# exactly why the agent could not hold the number and be configurable.
_DEFAULT_MAX_CORPUS_NAMES = 50


async def _named_documents(
    ctx: ExecutionContext,
    documents: DocumentRepository,
    files: ReadableFiles,
    *,
    space_id: Uuid | None,
    cap: int | None,
) -> tuple[list[tuple[Uuid, str]], int]:
    """ONE corpus walk: every document under ``space_id``, newest first,
    paired with the name of the file it was built from — plus ``total``, how
    many documents were walked in all.

    **One walk, written once, because the two use-cases below do the same
    thing** (branch review §2's «مشيٌ واحد لا اثنان»): the same repository,
    the same paging, the same every-lifecycle-status rule, and the same "a
    document whose file can no longer be read is skipped" answer. Everything
    they shared by coincidence is now shared by construction, so a change to
    the walk cannot land on one of them alone.

    **They differed on the space axis until س-32, and no longer do.**
    ``ListDocumentNames`` used to walk ``space_id=None`` on the argument that a
    corpus header describes the WHOLE workspace, while ``ListFileCandidates``
    walked the space the question would be ANSWERED from. The owner decision of
    2026-08-26 makes both walk the SAME space: the corpus a thread has is its
    space's, so a header spanning every space named documents no question of
    that thread could be answered from. The two use-cases stay two — they still
    differ on the display cap and on the empty-name rule — but the axis that
    used to separate them now unites them, and the ``WHERE space_id`` the
    review's §7 fix pushed INTO the query is on both paths instead of one.

    **Names arrive ONE read per page** (``ReadableFiles.names_for_files``),
    never one per document — the N+1 the plan's §7 recorded twice. A page of
    ``_LIST_PAGE_SIZE`` documents costs two round trips (its rows, then its
    names) instead of ``1 + _LIST_PAGE_SIZE``.

    **``cap`` bounds the NAMES collected, never the documents counted.**
    ``total`` is the full corpus count whatever the cap is — that number is
    the whole reason a header can honestly say "and N more". ``None`` means
    uncapped, which is what a resolver needs (a cap there would turn a
    refusal-to-guess into a confident answer computed over a partial corpus).

    **Once the cap is full the walk STOPS and asks ``count`` instead**
    (branch review ب-2). It used to keep paging to the end of the corpus for
    ``total`` alone: one round trip per ``_LIST_PAGE_SIZE`` documents,
    hydrating whole rows nobody would look at, to arrive at a single integer.
    One ``COUNT(*)`` IS that integer, and the pages in between are never read.
    The count runs only when pages actually REMAIN — a cap that fills on the
    last page returns the number already walked, which is exact and free — so
    the common small corpus pays nothing for this at all.

    **``count`` may disagree with the walk by a row, and that is the honester
    number.** They are two queries, so a document registered between them
    lands in one and not the other. But the walk was never atomic either (it
    spans one query per page), the old code had exactly the same race spread
    over more round trips, and of two answers taken at different instants the
    LATER one is the one a header should show.

    **Presence in the name mapping is the readability answer**, exactly as a
    non-``None`` ``get_readable`` view was: a document whose file is gone
    silently misses ``named`` and is still counted in ``total``. An EMPTY
    name is KEPT here, because dropping it is one caller's rule and not both
    (``ListFileCandidates``).
    """
    named: list[tuple[Uuid, str]] = []
    # `walked` and not `total`: it is the count of rows this loop actually
    # SAW, which is the total only when the loop reaches the end of the
    # corpus. The ب-2 short-circuit below returns a `count` instead.
    walked = 0
    cursor: str | None = None
    while True:
        page = await documents.list(ctx, space_id=space_id, limit=_LIST_PAGE_SIZE, cursor=cursor)
        # Decided BEFORE the page is consumed, so a cap that fills half-way
        # through does not strand this page's remaining names: they were
        # already fetched, and the loop below simply stops appending.
        wanting_names = (cap is None or len(named) < cap) and bool(page.data)
        names: Mapping[Uuid, str] = (
            await files.names_for_files(ctx, [document.file_id for document in page.data])
            if wanting_names
            else {}
        )
        for document in page.data:
            walked += 1
            if cap is not None and len(named) >= cap:
                # Counting continues; nothing else does. `continue` and not
                # `break`, because THIS page's remaining rows are already in
                # hand and `walked` has to include them for the exact-total
                # case below (a cap that fills on the last page).
                continue
            name = names.get(document.file_id)
            if name is not None:
                named.append((document.id, name))
        cursor = page.next_cursor
        if cursor is None:
            # The corpus ended: `walked` IS the total, exactly, at no extra
            # cost. Every uncapped walk and every corpus that fits under the
            # cap leaves here, so neither pays for the query below.
            return named, walked
        if cap is not None and len(named) >= cap:
            # Pages remain and not one name on them would be kept. The only
            # thing still unknown is `total` — so ask for it (ب-2) instead of
            # hydrating the rest of the corpus to arrive at the same integer.
            return named, await documents.count(ctx, space_id=space_id)


class ListDocumentNames:
    """ONE SPACE's corpus-awareness source (retrieval plan §3.6/§4 row 6,
    ``P-36``, decision س-23 = ج as amended by س-32): up to ``limit`` document
    file names, newest first, plus ``total`` — that space's FULL document
    count, so a caller can render an honest "N more files" tail without a
    second listing call of its own.

    **``limit`` is OPTIONAL, and ``None`` means the DEPLOYMENT's cap** —
    ``max_corpus_names``, injected here by the Composition Root from
    ``Settings.retrieval.max_corpus_names``. It is ``RetrieveContext``'s ``k =
    None`` in every respect (plan row 18, ``P-40``, س-24 = أ): the only caller
    that renders this header is the RAG agent, an agent reads no configuration
    and imports nothing (ح-11), so a default resolved on THIS side of the seam
    is the one route by which a configured number can reach it. Naming a
    ``limit`` stays allowed and still means exactly what it did — a caller
    asking for a result-set SIZE, not overriding a deployment knob.

    **Every lifecycle status is walked**, the ``ListDocuments`` rule: a
    ``pending``/``failed`` document still names a file the user genuinely
    uploaded, and excluding it would silently undercount the corpus a
    "how many files do you have?" question is asking about.

    ⚠️ **``space_id`` is REQUIRED here since س-32** (owner decision
    2026-08-26), and it used to be the one place in this module where the
    space axis was deliberately not offered. The old argument was that a
    header describing one space's slice would misreport the corpus as smaller
    than it is — «لا أملك معلومات كافية» about a workspace that is not empty.
    What the decision changes is which corpus the sentence is about: spaces
    are isolated completely, so the files a thread HAS are its space's, and
    naming the rest told a user about documents no question of theirs could
    ever be answered from. That is not a fuller answer, it is a leak with a
    helpful tone. ``ListFileCandidates`` below walks the same rows under the
    same space for the reason it always did, and the two now agree instead of
    differing by design.

    **Names are resolved ONE read per page, and only until ``limit`` of them
    are in hand** (``_named_documents``, branch review §2) — walking every
    page of ``DocumentRepository.list`` is cheap (row ids only), and the names
    behind a whole page now cost a single ``ReadableFiles.names_for_files``
    instead of a round trip per document. Pages reached after the cap is full
    ask for no names at all: they are walked to finish counting ``total``,
    which stays the FULL corpus count so the header's "N more" tail is
    honest. A document whose file can no longer be read (deleted,
    quarantined, or otherwise gone since it was indexed) is SKIPPED rather
    than shown as a bare id — the header names ACTUAL files, and a dangling
    reference is not one; ``total`` still counts it, so "N more" never
    silently drops a real document.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        files: ReadableFiles,
        *,
        max_corpus_names: int = _DEFAULT_MAX_CORPUS_NAMES,
    ) -> None:
        self._documents = documents
        self._files = files
        self._max_corpus_names = max_corpus_names

    async def execute(
        self, ctx: ExecutionContext, *, space_id: Uuid, limit: int | None = None
    ) -> DocumentNames:
        # `limit = None` means "however many names this deployment shows"
        # (`_DEFAULT_MAX_CORPUS_NAMES` above) — resolved HERE, once, so the
        # walk below reads one number whichever way the caller asked.
        cap = self._max_corpus_names if limit is None else limit
        # The space guard (س-32) on the header too, and not only on the search:
        # this walk goes through `DocumentRepository.list`, where `space_id=None`
        # still legitimately means "every space" for the paginated LISTING
        # route — so an unscoped value arriving here would not fail, it would
        # quietly answer with the whole workspace. That is exactly the shape
        # the decision removes.
        space_id = require_space_scope(space_id)
        named, total = await _named_documents(
            ctx, self._documents, self._files, space_id=space_id, cap=cap
        )
        return DocumentNames(names=tuple(name for _, name in named), total=total)


class ListFileCandidates:
    """The corpus ``domain/file_resolution.resolve_file`` matches a question
    against (retrieval plan §3.5/§4 rows 13-14, ``P-04``): every document in
    the space being searched, paired with the name of the file it was built
    from.

    The same walk ``ListDocumentNames`` does — literally the same one
    (``_named_documents``): same repository, same paging, same
    every-status rule (a ``pending`` document still NAMES a real file; hiding
    it would let a question about it resolve to a different file instead of
    saying the honest thing, and what "the honest thing" is stays
    ``RequestSummary``'s call — it refuses a document that is not indexed,
    with the reason).

    **And the SAME space**, which is where the two walks used to part: the
    header described the whole workspace until س-32 and this list never did.
    The reason this one never could is unchanged — a candidate list is matched
    against a question whose ANSWER will be retrieved under a ``space``
    filter, so a name resolved outside that space produces a scope the search
    can never satisfy: ``document_ids`` from one space ANDed with ``space``
    from another, zero chunks, and nothing to explain them
    (``RouteQuestion``'s module docstring has the whole failure). Resolving
    inside the searched space makes the miss an ordinary ``NoFileMatch``
    instead, which the router already knows how to answer honestly.

    ``space_id`` is therefore a required keyword with no default and no
    ``None``: "every space" is not a thing this signature can say.

    **Two differences, both required by what the resolver is for.** There is
    no display ``limit``: every candidate is resolved, because a cap turns a
    refusal-to-guess into a confident answer computed over a partial corpus
    (see ``FileCandidates``). And documents whose file name came back EMPTY
    are dropped — an empty name matches nothing lexically but would be shown
    to a user as a blank line in a clarification question.

    **The N+1 the plan's §7 recorded twice is gone** (branch review §2,
    remedy 1): names arrive one bulk ``names_for_files`` per PAGE rather than
    one ``get_readable`` per document, so a walk over ``D`` documents costs
    TWO round trips per page instead of one per page plus one per document —
    ``D = 1000`` falls from 1005 to 10. The WALK is still this use-case's own
    — ``_named_documents`` says why it cannot also be the header's.
    """

    def __init__(self, documents: DocumentRepository, files: ReadableFiles) -> None:
        self._documents = documents
        self._files = files

    async def execute(self, ctx: ExecutionContext, *, space_id: Uuid) -> tuple[FileCandidate, ...]:
        # `cap=None` — every candidate, for the reason two paragraphs up.
        named, _total = await _named_documents(
            ctx, self._documents, self._files, space_id=space_id, cap=None
        )
        # The empty-name drop is THIS walk's rule, not the shared walk's: a
        # blank line is unmatchable lexically and unusable in a clarification
        # question, while the header counts such a file as one it holds.
        return tuple(
            FileCandidate(document_id=document_id, file_name=name)
            for document_id, name in named
            if name
        )


class GetDocumentFileName:
    """The name of the file ONE document was built from, or ``None``
    (ب-7ج, scenarios plan §4, gap ف-2).

    **The one-document read the two corpus walks are not.**
    ``ListDocumentNames`` and ``ListFileCandidates`` answer "what
    is in this space", walk every page and are asked once per
    answering turn. This answers "what is this one document
    called", is asked by a worker delivering one finished summary
    into a thread, and knows the id already — walking a space to
    find a document that was named to us would be a corpus scan
    per delivery.

    ``None`` covers every reason a name cannot be produced, and
    they are the same reasons ``ReadableFiles`` already refuses
    to distinguish (its docstring): an unknown or deleted
    document, and a file that is gone, quarantined or was never
    ready. The caller wanted a title for a message, so "there is
    no name to show" is one answer and needs no cascade.

    A document is a real thing whose file has been deleted more
    often than anywhere else in this module: the summary was
    built from chunks stored at index time, and it stays
    deliverable long after the bytes it was built from are gone.
    That case is why this returns ``None`` rather than raising —
    the summary is still owed to the thread that asked.
    """

    def __init__(self, documents: DocumentRepository, files: ReadableFiles) -> None:
        self._documents = documents
        self._files = files

    async def execute(self, ctx: ExecutionContext, *, document_id: Uuid) -> str | None:
        document = await self._documents.get(ctx, document_id)
        if document is None:
            return None
        # The bulk read asked for one file: `names_for_files` is the
        # seam both corpus walks already resolve names through, so
        # this is the same authority answering the same question at
        # the size it is actually asked in. `get_readable` would
        # answer it too, and would make this the second reader of a
        # file's name in the module.
        names = await self._files.names_for_files(ctx, [document.file_id])
        name = names.get(document.file_id)
        # `ListFileCandidates`' empty-name rule, for its reason: a
        # readable file named nothing is a real state, and a header
        # reading `Summary of "":` shows it to a user as a defect.
        return name or None


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


class PurgeSpaceKnowledge:
    """Destroy one space's corpus — points first, then rows (steps 2, 3 and 4
    of ``docs/spaces-backend-plan.md`` §3.6, step 11).

    **Not in ``KnowledgeUseCases``, for ``PurgeSpaceFiles``' reason**: no
    request may empty a space's index without deleting the space. The only
    caller is the composition-root ``DeleteSpaceService``.

    **Points before rows, and this is the one ordering that cannot be
    reversed.** ``ReindexDocuments`` already argues it for one document and the
    argument only gets stronger here: a Qdrant point is reachable by retrieval
    alone — the payload filter never joins Postgres — so a point whose ``chunks``
    row is gone is content that answers searches with nothing left in the
    database to identify it, in a space the user was told is deleted. Deleting
    the points first inverts the failure: if the vector call dies, every row is
    still there, the next run of the cascade collects the very same refs, and
    deleting an already-deleted point is a no-op.

    Grouped by collection for ``_purge_vectors``' reason: every chunk of a
    workspace lives in ``kn-<workspace_id>`` today, but each ``VectorRef``
    names its own, and honouring that costs one dict.

    ``VectorStore`` and not ``HybridVectorStore``: this needs ``delete`` and
    nothing else, and §3.147 spent a step putting the hybrid-only method on the
    hybrid port. Asking for the wider one here would undo that in the other
    direction.
    """

    def __init__(self, documents: DocumentRepository, vectors: VectorStore) -> None:
        self._documents = documents
        self._vectors = vectors

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        by_collection: dict[str, list[Uuid]] = {}
        for ref in await self._documents.vector_refs_in_space(ctx, space_id):
            by_collection.setdefault(ref.collection, []).append(ref.point_id)
        for collection, point_ids in by_collection.items():
            await self._vectors.delete(collection, point_ids)
        return await self._documents.purge_space(ctx, space_id)


class PurgeFileKnowledge:
    """Destroy one FILE's corpus — points first, then rows: step 2 of the file
    cascade (``framework/di/file_deletion.py``).

    **Why this exists at all.** ``files.FileDeleted`` reached no receiver in
    this module: a deleted file kept its ``indexed`` document, its chunks and
    its points, so retrieval went on returning passages from a file the user
    had removed and the agent went on citing it. It is also how a file came to
    be indexed twice — delete, re-upload, and the first corpus outlives the
    file it was built from. This use-case is the receiver, reached
    SYNCHRONOUSLY through the cascade rather than through an event, and the
    module docstring of ``file_deletion.py`` argues that choice.

    **Not in ``KnowledgeUseCases``, for ``PurgeSpaceKnowledge``' reason**: no
    request may destroy a corpus on its own. The only caller is the
    composition-root ``DeleteFileService``.

    **Points before rows**, which is the one ordering that cannot be reversed —
    ``PurgeSpaceKnowledge`` makes the whole argument and it is unchanged at this
    scope: a Qdrant point is reachable by retrieval alone (the payload filter
    never joins Postgres), so a point whose ``chunks`` row is gone is content
    answering searches with nothing left in the database to identify it. If the
    vector call dies first, every row is still there, the next run of the
    cascade collects the very same refs, and deleting an already-deleted point
    is a no-op.

    ``VectorStore`` and not ``HybridVectorStore``, for that class's reason too:
    this needs ``delete`` and nothing else.
    """

    def __init__(self, documents: DocumentRepository, vectors: VectorStore) -> None:
        self._documents = documents
        self._vectors = vectors

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int:
        by_collection: dict[str, list[Uuid]] = {}
        for ref in await self._documents.vector_refs_for_file(ctx, file_id):
            by_collection.setdefault(ref.collection, []).append(ref.point_id)
        for collection, point_ids in by_collection.items():
            await self._vectors.delete(collection, point_ids)
        return await self._documents.purge_file(ctx, file_id)


class KnowledgeRetrievalService:
    """Implements the ``KnowledgeRetrieval`` inbound port (02 §2) over the
    3.k3 ``RetrieveContext`` use-case (the ``FilesQueryService`` precedent).

    Also composes ``ListDocumentNames`` (retrieval plan §3.6/§4 row 6,
    ``P-36``) over the SAME ``documents`` repository plus the module's
    existing ``files`` seam (``ports/files.py``, already injected for
    ``IndexFile``) — one class, one seed, both capabilities the RAG agent's
    ``KnowledgeAccess`` needs.

    And ``RouteQuestion`` (retrieval plan §3.4/§4 row 11, ``P-21``, س-16 = أ)
    over that same ``RetrieveContext`` plus the ``summaries`` starter, for
    the same reason: ONE seed the agent calls, three faces on it, no second
    injected port to keep in step with this one.

    ``stored_summaries`` (ب-8, gap ف-3) is the sixth seed and the router's
    READ. It is the ``SummaryRepository`` rather than a use-case because this
    class composes ``ReadStoredSummary`` over it here, exactly as it composes
    ``ListDocumentNames`` and ``ListFileCandidates`` over the seeds beside it
    — the Composition Root passes the repository it already holds, and the
    only object that knows a stored read is wanted is this one. ``summaries``
    stays a use-case for the reason it always was: a BUILD needs the atomic
    outbox and unit of work wrapped around it, and a read needs neither.
    """

    def __init__(
        self,
        retrieval: RetrieveContext,
        resolver: EmbeddingResolver,
        documents: DocumentRepository,
        files: ReadableFiles,
        summaries: SummaryStarting,
        stored_summaries: SummaryRepository,
        *,
        max_corpus_names: int = _DEFAULT_MAX_CORPUS_NAMES,
    ) -> None:
        self._retrieval = retrieval
        self._resolver = resolver
        self._documents = documents
        # Retrieval plan §3.6/§4 row 6 (`P-36`) — the corpus header's display
        # cap, travelling the way `RetrieveContext`'s `tuning` does: mapped
        # from `Settings` by the Composition Root and passed in as an
        # argument. Keyword-only with a mirrored default, so this service
        # composes in a test exactly as it did before the number moved.
        self._names = ListDocumentNames(documents, files, max_corpus_names=max_corpus_names)
        # Retrieval plan §3.5/§4 row 14 (`P-04`) — the router's candidate
        # source is composed from the SAME two seams this service already
        # holds for `ListDocumentNames`, so wiring row 14 added no
        # constructor argument and no second reader of `files`.
        # ب-8 (خطة السيناريوهات section 6, gap ف-3) — the router's READ,
        # composed here over the repository seed for `ListFileCandidates`'
        # reason: the router is owed a capability, not a store.
        #
        # Positional and undefaulted, which is the whole guard. A default
        # would let a deployment compose this service without a reader and
        # get the pre-ب-8 behaviour — every repeat request rebuilding — with
        # nothing in the code or the logs saying so.
        self._router = RouteQuestion(
            retrieval,
            summaries,
            ListFileCandidates(documents, files),
            ReadStoredSummary(stored_summaries),
        )

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
    ) -> list[RetrievedChunk]:
        """Retrieve the top ``k`` chunks, optionally scoped to ``file_ids``.

        ``k = None`` means "however many this deployment is configured to
        return" (retrieval plan §4 row 18, ``P-40``, س-24 = أ) — resolved by
        ``RetrieveContext`` from ``Settings.retrieval.default_k``, the single
        home of that number. ``POST /knowledge/search`` still names its own
        ``k``, because that is a request's result-set SIZE on a published
        contract (03 §2), not a retrieval tuning override; س-24 rules out the
        latter, not the former.

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

        ``space_id`` is the SECOND, independent narrowing (step 8), and since
        س-32 it is keyword-only, undefaulted AND non-nullable while ``file_ids``
        keeps its default and its ``None``. The asymmetry is deliberate:
        forgetting a pinned scope narrows nothing and answers from more of the
        caller's OWN space, while an absent space answered from other spaces —
        a widening across the very axis the product draws. That mistake had to
        be impossible to make silently; it is now impossible to express.

        The two are ANDed one layer down (``RetrieveContext``), not merged
        here: a pin from another space is already refused at pin time (§3.5),
        so a scope that survives both conditions is the only honest one.

        ``RetrieveContext.execute`` also returns the two raw confidence
        signals (retrieval plan §3.3, ``P-28``) alongside its chunks, but
        THIS port's contract (02 §2) is, and stays, ``list[RetrievedChunk]``
        — so only ``.chunks`` crosses here. Nothing downstream needs the
        signals yet (retrieval plan step 5's gate is "no results" only, no
        threshold on them); a later step decides how/whether they reach a
        caller of this port.
        """
        resolved = await self._resolver.resolve_embedding(ctx)
        document_ids = (
            None if file_ids is None else await self._documents.ids_for_files(ctx, file_ids)
        )
        result = await self._retrieval.execute(
            ctx,
            query=query,
            model=resolved.model,
            api_key=resolved.api_key,
            k=k,
            document_ids=document_ids,
            space_id=space_id,
        )
        return result.chunks

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
        conversation_id: Uuid | None = None,
        pending_candidates: Sequence[str] = (),
    ) -> RoutedAnswer:
        """Implements ``KnowledgeRetrieval.answer`` (retrieval plan §3.4/§4
        row 11, ``P-21``) — the port's third face, over ``RouteQuestion``.

        ``k`` means exactly what it means on ``retrieve`` above, ``None``
        included — and ``None`` is what the RAG agent passes since plan row 18
        (``P-40``), which is how it stopped carrying a retrieval number of its
        own.

        Everything ``retrieve`` does before delegating happens here FIRST and
        identically: the embedding provider is resolved for this call, and the
        pinned ``file_ids`` are translated to document ids inside the module.
        The translation is what makes the summarisation route reachable at all
        — a pin is a FILE to its caller, and the summary that route queues is
        keyed on the DOCUMENT built from it.

        The embedding is resolved even on a question that turns out to be a
        summarisation: the classifier runs one layer down, and hoisting it up
        here to save a resolver call would put the routing decision in two
        places. The resolver is a per-call credential lookup, not a network
        round trip to the embedding service.

        ``conversation_id`` (`F-7`) is passed straight down and read by
        exactly one of the two routes: SUMMARIZE_DOC stamps it on the build it
        queues, so the finished text can be posted back into the thread that
        asked. CONTENT ignores it — that route answers inside the turn, and
        the caller writes its own reply. Defaulted to ``None`` for the callers
        that have no thread (``POST /knowledge/search``), which is the same
        "a real value, not a forgotten one" ``AgentRequest.space_id`` states.
        """
        resolved = await self._resolver.resolve_embedding(ctx)
        document_ids = (
            None if file_ids is None else await self._documents.ids_for_files(ctx, file_ids)
        )
        return await self._router.execute(
            ctx,
            question=question,
            model=resolved.model,
            api_key=resolved.api_key,
            k=k,
            document_ids=document_ids,
            space_id=space_id,
            conversation_id=conversation_id,
            pending_candidates=pending_candidates,
        )

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: Uuid, limit: int | None = None
    ) -> DocumentNames:
        """Implements ``KnowledgeRetrieval.list_document_names`` (retrieval
        plan §3.6/§4 row 6, ``P-36``) — a straight delegation to
        ``ListDocumentNames``, this port's second face over the same
        ``documents``/``files`` seams ``retrieve`` already holds.

        ``space_id`` is required and passed straight down for the reason it is
        required on ``retrieve`` (س-32): the header names the corpus the next
        question will be answered from, so the two describe one space or they
        describe two different things.

        ``limit = None`` means "however many names this deployment shows"
        (``Settings.retrieval.max_corpus_names``), exactly as ``k = None``
        means the deployment's ``k`` on ``retrieve`` above — and it is what
        the RAG agent passes, which is how it stopped carrying a
        ``_MAX_CORPUS_NAMES`` of its own. Resolved one layer down, by
        ``ListDocumentNames``, so the number has one home and this delegation
        stays a delegation.
        """
        return await self._names.execute(ctx, space_id=space_id, limit=limit)


@dataclass(frozen=True, slots=True)
class KnowledgeUseCases:
    """The module's API-facing bundle (the ``CredentialUseCases`` precedent):
    ONE field on ``ApiServices`` per module, matching 03 §1's ``POST /search ·
    GET /documents · GET /documents/{id}``.

    ``IndexRegisteredDocument`` is pointedly absent, and ``IndexFile`` is
    pointedly not it. A bundle the API layer holds must not be able to re-drive
    a worker's pipeline from a request — that use-case embeds, writes chunks
    and closes a document's lifecycle, and it stays where the process that can
    reach an embedding provider composes it.

    **``index_file`` and ``reindex`` are not holes in that rule**
    (BE-RAG-007/008). Neither indexes anything: each registers a ``pending``
    document and publishes the ordinary ``DocumentRegistered`` event, then a
    worker decides when to run. What a request gains is the ability to ASK for
    ingestion — which, since indexing stopped happening automatically on
    upload, is the ONLY way ingestion is ever asked for. ``index_file`` starts
    a corpus entry that does not exist yet; ``reindex`` replaces one that does,
    destroying what it supersedes. The four fields are required rather than
    ``| None`` like ``search``: they need the vector store, which every
    composed deployment has, and no embedding adapter at all.

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
    index_file: IndexFileService
    reindex: ReindexService
    get_job: GetReindexJob
    cancel_job: CancelReindexJobService
    request_summary: RequestSummaryService
    get_summary: GetSummary
    export_summary: ExportSummary
    delete_summary: DeleteSummary
    get_summary_job: GetSummaryJob
    cancel_summary_job: CancelSummaryJobService
