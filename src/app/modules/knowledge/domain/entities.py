"""Knowledge aggregate + entity (pure — 06-domain-models §7).

``Document`` is the ingestion-lifecycle aggregate root; ``Chunk`` is the
immutable per-window slice produced once indexing completes (persisted
alongside its owning ``Document`` — see ``ports/repository.py``). Status
transitions are one-way and terminal (INV-K2: ``pending -> indexing ->
(indexed | failed)``); INV-K3 forbids any retry/reset back onto a
``failed``/``indexed`` document — reprocessing is always a logically NEW
document, so this aggregate deliberately exposes no such method. Identifiers
are UUIDv7 text; timestamps are timezone-aware UTC (DD-03).

``ReindexJob`` (+ its ``ReindexItem`` entity) is the second aggregate root
here, added by BE-RAG-007/008: the record of a manual rebuild. It is the
aggregate that OBEYS INV-K3 rather than bends it — each target is superseded
by a new ``Document`` and the old one is destroyed (INV-K4) — and its whole
state is derived from those documents' statuses (INV-K5), so it holds
counters for nothing.

``Summary``/``SummaryJob`` (BE-RAG-009/010/011) are the third and fourth.
Read them against ``ReindexJob`` rather than beside it: the job stores its
progress where the re-index job derives its own, because a summary has no
second witness in the corpus to derive it from until the moment it exists
(``SummaryJobStatus``), and its cancellation needs the worker's cooperation
where the re-index job's needed nobody's (``SummaryJob.cancel``). Both
divergences are consequences of the same fact — a summary is produced by one
process in one pass, not by a lifecycle other rows already record.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.modules.knowledge.domain.errors import DocumentStateError, SummaryJobStateError
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    ReindexJobStatus,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)

# start_indexing is re-entrant from INDEXING itself -- see its docstring.
_STARTABLE = (IndexStatus.PENDING, IndexStatus.INDEXING)

# A document that will never move again: the DD-09 redelivery guard's set,
# and therefore also the set a `ReindexJob` counts as finished and the set
# `cancel` can no longer claim.
_TERMINAL = (IndexStatus.INDEXED, IndexStatus.FAILED)

# A summary job that will never move again -- this aggregate's own version of
# the same guard, and the set `cancel` refuses to re-stamp.
_JOB_TERMINAL = (
    SummaryJobStatus.SUCCEEDED,
    SummaryJobStatus.FAILED,
    SummaryJobStatus.CANCELLED,
)


@dataclass(slots=True)
class Document:
    """A file's ingestion/indexing lifecycle (06 §7 AR ``Document``)."""

    id: str
    workspace_id: str
    file_id: str
    status: IndexStatus
    chunk_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def start_indexing(self, now: datetime) -> None:
        """``pending|indexing -> indexing``.

        Re-entrant from ``indexing`` on purpose: at-least-once event
        redelivery (DD-09) may call this again after a worker crashed
        mid-run, and the restarted worker simply re-runs the pipeline from
        scratch rather than treating that as an illegal transition — safe
        because re-indexing is itself idempotent (INV-K1's
        ``UNIQUE(document_id, seq)`` plus the deterministic Qdrant point ids,
        3.k3).
        """
        if self.status not in _STARTABLE:
            raise DocumentStateError(f"cannot start indexing from status {self.status.value!r}")
        self.status = IndexStatus.INDEXING
        self.updated_at = now

    def complete_indexing(self, chunk_count: int, now: datetime) -> None:
        """``indexing -> indexed`` (INV-K2): records the final chunk count
        and clears any error left over from... nothing, in practice (a
        document only ever reaches here via ``indexing``), but clearing is
        cheap insurance against a future relaxation of that rule."""
        if self.status is not IndexStatus.INDEXING:
            raise DocumentStateError(f"cannot complete indexing from status {self.status.value!r}")
        self.status = IndexStatus.INDEXED
        self.chunk_count = chunk_count
        self.error = None
        self.updated_at = now

    def fail_indexing(self, reason: str, now: datetime) -> None:
        """``indexing -> failed`` (INV-K2) — a terminal state. INV-K3
        forbids any retry/reset back onto this same document; reprocessing
        means registering a brand-new one."""
        if self.status is not IndexStatus.INDEXING:
            raise DocumentStateError(f"cannot fail indexing from status {self.status.value!r}")
        self.status = IndexStatus.FAILED
        self.error = reason
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class ReindexItem:
    """One document a ``ReindexJob`` is rebuilding (06 §7 E ``ReindexItem``).

    ``document_id`` is the NEW document; ``source_document_id`` is the one it
    replaced, which the job DESTROYED before registering this one (INV-K4) —
    a historical id with no row behind it any more, kept so a job can still
    say what it did rather than only what it made.

    ``status`` is that new document's live ingestion status, read from the
    corpus every time the job is loaded. It is a snapshot of another
    aggregate, deliberately: copying it onto the item row would create a
    second place for the truth to live and a second writer to keep honest.
    """

    document_id: str
    file_id: str
    source_document_id: str
    status: IndexStatus

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


@dataclass(slots=True)
class ReindexJob:
    """A manual re-index of one or more documents (06 §7 AR ``ReindexJob``).

    **Everything about progress is computed here, nothing is stored** (INV-K5)
    — see ``ReindexJobStatus``. The only mutable state the aggregate owns is
    ``cancelled_at``, because "someone asked this to stop" is a fact about the
    job that no document's status could express.
    """

    id: str
    workspace_id: str
    items: tuple[ReindexItem, ...]
    cancelled_at: datetime | None
    created_at: datetime

    @property
    def finished(self) -> int:
        """Items in a terminal status — the numerator of ``percent``."""
        return sum(1 for item in self.items if item.is_terminal)

    @property
    def status(self) -> ReindexJobStatus:
        """``cancelled`` outranks ``completed``: once someone stopped a job,
        that is what happened to it, even though cancelling is precisely what
        drove its remaining documents terminal. Reporting ``completed`` there
        would describe the mechanism and hide the intent."""
        if self.cancelled_at is not None:
            return ReindexJobStatus.CANCELLED
        if self.finished == len(self.items):
            return ReindexJobStatus.COMPLETED
        return ReindexJobStatus.RUNNING

    @property
    def percent(self) -> int:
        """Terminal items as a whole percentage. An empty job is 100: a job
        with nothing left to do is done, and the alternative is a division by
        zero guarding a state the use-case already refuses to create."""
        if not self.items:
            return 100
        return self.finished * 100 // len(self.items)

    @property
    def current(self) -> ReindexItem | None:
        """The item a worker is processing right now, or ``None`` between
        documents. First match wins — jobs are processed one document at a
        time, and if two are somehow in flight, naming one is still more use
        than naming neither."""
        return next((item for item in self.items if item.status is IndexStatus.INDEXING), None)

    def cancel(self, now: datetime) -> tuple[str, ...]:
        """Claim every document this job has not started, and return their ids.

        The claim is a transition to ``failed`` on each ``pending`` item —
        which is exactly the terminal state the worker's own DD-09 redelivery
        guard refuses to run against, so the already-published event arrives
        and does nothing. Cancellation needs no cooperation from the worker
        and no way to un-publish a message, because the lifecycle it borrows
        already declines terminal work.

        ``indexing`` items are deliberately NOT claimed. That pipeline is
        running in another process against an aggregate this one does not
        hold; failing the row here would only be overwritten by the worker's
        own terminal write, and the interim would be a status the corpus does
        not agree with. They keep their outcome, and the response shows them
        still running rather than claiming a stop that did not happen.

        Cancelling twice is a no-op that writes nothing (the returned tuple is
        empty and ``cancelled_at`` keeps its ORIGINAL instant — the moment
        someone stopped the job is not re-datable). Cancelling a job with
        nothing left to stop is the caller's ``ConflictError`` to raise; this
        method has no view of whether that is a race or a mistake.
        """
        if self.cancelled_at is not None:
            return ()
        claimed = tuple(
            item.document_id for item in self.items if item.status is IndexStatus.PENDING
        )
        self.items = tuple(
            replace(item, status=IndexStatus.FAILED) if item.status is IndexStatus.PENDING else item
            for item in self.items
        )
        self.cancelled_at = now
        return claimed


@dataclass(frozen=True, slots=True)
class Summary:
    """One document summarised, in one kind and one requested language
    (06 §7 AR ``Summary`` · BE-RAG-009/010).

    Frozen, because a summary is never edited: rebuilding one REPLACES it in
    place under the same ``(document_id, kind, lang)`` key. There is no
    transition to model and therefore no method to expose.

    ``model`` is on the record for the reason ``host`` is on a system-stats
    reading (BE-ADM-007): this is the one artefact in the corpus whose content
    depends on which model produced it, and a summary that cannot say what
    wrote it cannot be compared with the one that replaces it after an
    operator re-points the ``summarize`` route.

    ``source_chunks``/``truncated`` are the same kind of honesty about a
    different thing. A ``full`` summary of a document longer than the map cap
    covers a PREFIX of it, and a reader who is not told that will believe the
    conclusion drawn from chapter one is a conclusion about the book.
    """

    id: str
    workspace_id: str
    document_id: str
    kind: SummaryKind
    lang: SummaryLanguage
    text: str
    model: str
    source_chunks: int
    truncated: bool
    built_at: datetime


@dataclass(slots=True)
class SummaryJob:
    """One build of one ``Summary`` (06 §7 AR ``SummaryJob`` · BE-RAG-009/011).

    **This aggregate stores its progress**, unlike ``ReindexJob`` — see
    ``SummaryJobStatus`` for why deriving it was not available here rather
    than not preferred.

    **Cancellation is COOPERATIVE, and that is a real difference from
    ``ReindexJob.cancel``.** There, cancelling claims documents the worker has
    not started, landing them in the terminal state the worker's own guard
    already declines — so the published message arrives and does nothing, and
    no worker needed changing. Here the work is a chain of LLM round trips
    inside one handler, and no lifecycle it consults will stop it: the job is
    stamped ``cancelled`` and the worker re-reads it BETWEEN map steps.

    Two consequences, both stated rather than smoothed over: a job still
    ``queued`` stops for free (the guard declines a terminal job exactly like
    the re-index case), and a ``running`` one stops at the next step boundary
    — the request already in flight is paid for. What cancelling never does is
    delete a summary that a previous build stored: this job is abandoned, not
    the artefact of the one before it.
    """

    id: str
    workspace_id: str
    document_id: str
    kind: SummaryKind
    lang: SummaryLanguage
    status: SummaryJobStatus
    total_chunks: int
    done_chunks: int
    error: str | None
    cancelled_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in _JOB_TERMINAL

    @property
    def percent(self) -> int:
        """Map steps completed, as a whole percentage.

        A succeeded job is 100 unconditionally — not because the arithmetic
        would disagree, but because the reduce step that turns the mapped
        pieces into the summary is real work that ``total_chunks`` does not
        count, so a job can be at 100% of its chunks and genuinely not done.
        Reporting the ratio there would show 100 twice, seconds apart, and
        mean two different things.

        A job with no measured total is 0 rather than a division by zero:
        ``total_chunks`` is written when the worker claims the job, so 0 means
        "still queued", which is exactly 0% done.
        """
        if self.status is SummaryJobStatus.SUCCEEDED:
            return 100
        if self.total_chunks <= 0:
            return 0
        return min(self.done_chunks * 100 // self.total_chunks, 99)

    def start(self, total_chunks: int) -> None:
        """``queued -> running``, recording how much there is to do.

        Takes no instant, unlike every other transition here: ``created_at``
        already records when the job was asked for and ``finished_at`` when it
        stopped, and a third timestamp for "a worker picked it up" would be
        one nobody reads — the row's ``updated_at`` trigger already moves.

        Re-entrant from ``running`` for the reason ``Document.start_indexing``
        is: at-least-once redelivery (DD-09) may hand the same job to a
        restarted worker, which re-runs the build from the first chunk. The
        total is re-measured on that pass rather than trusted from the last
        one, because the chunk rows may have changed underneath it.
        """
        if self.status not in (SummaryJobStatus.QUEUED, SummaryJobStatus.RUNNING):
            raise SummaryJobStateError(
                f"cannot start a summary job in status {self.status.value!r}"
            )
        self.status = SummaryJobStatus.RUNNING
        self.total_chunks = total_chunks
        self.done_chunks = 0

    def advance(self, done_chunks: int) -> None:
        """Record map progress. Monotonic by clamping rather than by raising:
        a redelivered job restarting from zero is a legitimate history, and
        refusing it would turn a recoverable crash into a stuck job."""
        if self.status is not SummaryJobStatus.RUNNING:
            raise SummaryJobStateError("cannot advance a summary job that is not running")
        self.done_chunks = max(0, min(done_chunks, self.total_chunks))

    def succeed(self, now: datetime) -> None:
        """``running -> succeeded``."""
        if self.status is not SummaryJobStatus.RUNNING:
            raise SummaryJobStateError(
                f"cannot complete a summary job in status {self.status.value!r}"
            )
        self.status = SummaryJobStatus.SUCCEEDED
        self.done_chunks = self.total_chunks
        self.error = None
        self.finished_at = now

    def fail(self, reason: str, now: datetime) -> None:
        """``queued|running -> failed``.

        Startable from ``queued`` as well, unlike ``succeed``: a job can fail
        before it ever runs — the document turned out to have no indexed text,
        or no ``summarize`` route resolves a key — and forcing that through a
        pointless ``running`` write would spend a transaction to make a state
        machine look tidier than the world is.
        """
        if self.is_terminal:
            raise SummaryJobStateError(f"cannot fail a summary job in status {self.status.value!r}")
        self.status = SummaryJobStatus.FAILED
        self.error = reason
        self.finished_at = now

    def cancel(self, reason: str, now: datetime) -> bool:
        """``queued|running -> cancelled``. Returns whether anything changed.

        ``reason`` is recorded on the job, not only carried on the event it
        provokes. A cancelled build really did end without a summary, and the
        person who later opens the job and sees ``cancelled`` with an empty
        ``error`` has been told the mechanism and not the outcome — while
        every OTHER way this job can end fills that field. The text comes
        from the application layer (``SUMMARY_CANCELLED_REASON``) so the
        domain stays free of user-facing copy.

        Cancelling an already-cancelled job writes nothing and keeps its
        ORIGINAL ``cancelled_at`` — the ``ReindexJob.cancel`` rule, for the
        same reason: the moment someone stopped it is not re-datable.
        A job that already finished is the caller's ``ConflictError`` to
        raise; this method has no view of whether that is a race or a mistake.
        """
        if self.is_terminal:
            return False
        self.status = SummaryJobStatus.CANCELLED
        self.error = reason
        self.cancelled_at = now
        self.finished_at = now
        return True


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexed window of a ``Document`` (06 §7 E ``Chunk``); immutable
    once created. ``token_count``/``vector_ref`` are nullable to match the
    nullable DDL columns (01-data-model §2.7), though the 3.k4 indexing flow
    always sets both."""

    id: str
    document_id: str
    workspace_id: str
    seq: int
    text: str
    token_count: int | None
    vector_ref: VectorRef | None
