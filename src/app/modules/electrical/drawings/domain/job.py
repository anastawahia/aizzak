"""The ingestion job aggregate (FR-ING-04/05/10, LLD 3.3.6). Stdlib only.

State machine (the table in LLD 3.3.6)::

    queued ──take_lease──▶ running ──succeed──────────────▶ succeeded
       ▲                     │  ├───await_confirmation─────▶ awaiting_confirmation
       │                     │  └───fail────────────────────▶ failed
       └──requeue◀── failed · cancelled · awaiting_confirmation · running (lease expired)
    queued · awaiting_confirmation · running ──cancel──▶ cancelled
    running ──request_cancel──▶ (cancel_requested_at; the worker cancels at the next batch)

A transient fault (ele-parse down, MinIO) leaves the job ``running`` but the run releases its
lease (``release_lease``): the message is redelivered and the next run takes the job over, while
a lease that is still live guards against a double run. ``version`` is the repository's
optimistic-lock counter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.modules.electrical.drawings.domain.errors import JobStateError
from app.modules.electrical.drawings.domain.pipeline import Stage

MAX_FAILURE_DETAIL_CHARS: Final = 2_000


class JobKind(StrEnum):
    INGEST = "ingest"
    REPROCESS = "reprocess"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_OPEN: Final = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.AWAITING_CONFIRMATION})
_REQUEUEABLE: Final = frozenset(
    {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.AWAITING_CONFIRMATION}
)


@dataclass(slots=True)
class IngestionJob:
    id: str
    workspace_id: str
    document_id: str
    kind: JobKind
    pages: tuple[int, ...] | None  # REPROCESS subset; None = every page
    status: JobStatus
    stage: Stage | None
    failure_code: str | None  # an ERROR_CATALOG code (INC1-D-29)
    failure_detail: str | None  # <= 2,000
    attempt: int
    lease_until: datetime | None
    seq: int  # progress sequence, +1 per published progress event (FR-ING-04)
    cancel_requested_at: datetime | None
    pipeline_version: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    version: int

    @property
    def is_open(self) -> bool:
        """QUEUED | RUNNING | AWAITING_CONFIRMATION: at most one per document may be."""
        return self.status in _OPEN

    def is_stale(self, now: datetime) -> bool:
        """RUNNING with an expired lease: its worker is gone."""
        return (
            self.status is JobStatus.RUNNING
            and self.lease_until is not None
            and self.lease_until < now
        )

    def take_lease(self, now: datetime, lease_s: int) -> bool:
        """``queued -> running`` for the worker that claims it, or a takeover of a RUNNING job
        whose lease expired (``attempt + 1``). ``False`` when someone else holds it or the job
        is no longer runnable: the caller then drops the message."""
        if self.status is JobStatus.QUEUED:
            self.status = JobStatus.RUNNING
            if self.started_at is None:
                self.started_at = now
        elif self.is_stale(now):
            self.attempt += 1
        else:
            return False
        self.lease_until = now + timedelta(seconds=lease_s)
        self.updated_at = now
        return True

    def extend_lease(self, now: datetime, lease_s: int) -> None:
        self._require(JobStatus.RUNNING, "extend the lease of")
        self.lease_until = now + timedelta(seconds=lease_s)
        self.updated_at = now

    def release_lease(self, now: datetime) -> None:
        """A run that is ending on a fault gives its lease back so the redelivery of the same
        message (the engine's retry) takes the job over at once instead of finding a live lease
        and being dropped. ``is_stale`` reads ``lease_until < now``, so the lease ends a tick
        BEFORE ``now``."""
        self._require(JobStatus.RUNNING, "release the lease of")
        self.lease_until = now - timedelta(microseconds=1)
        self.updated_at = now

    def next_seq(self) -> int:
        """The sequence number of the next progress event (strictly increasing)."""
        self.seq += 1
        return self.seq

    def await_confirmation(self, now: datetime) -> None:
        """``running -> awaiting_confirmation``: the proposed profile needs a person."""
        self._require(JobStatus.RUNNING, "await confirmation of")
        self.status = JobStatus.AWAITING_CONFIRMATION
        self.cancel_requested_at = None  # a request is about the run that has just ended
        self.lease_until = None
        self.updated_at = now

    def requeue(self, now: datetime) -> None:
        """Confirmation or resume: back to ``queued`` with ``attempt + 1``. From a failed or
        cancelled job, one awaiting confirmation, or a running one whose lease expired."""
        if self.status not in _REQUEUEABLE and not self.is_stale(now):
            raise JobStateError(f"cannot requeue a job that is {self.status.value}")
        self.status = JobStatus.QUEUED
        self.attempt += 1
        self.lease_until = None
        self.cancel_requested_at = None
        self.failure_code = None
        self.failure_detail = None
        self.finished_at = None
        self.updated_at = now

    def succeed(self, now: datetime) -> None:
        self._require(JobStatus.RUNNING, "succeed")
        self.status = JobStatus.SUCCEEDED
        self.cancel_requested_at = None
        self._finish(now)

    def fail(self, code: str, detail: str, now: datetime) -> None:
        self._require(JobStatus.RUNNING, "fail")
        self.status = JobStatus.FAILED
        self.failure_code = code
        self.failure_detail = detail[:MAX_FAILURE_DETAIL_CHARS]
        self.cancel_requested_at = None
        self._finish(now)

    def request_cancel(self, now: datetime) -> None:
        """RUNNING only: a cooperative request the worker honours at its next batch boundary.
        Repeating it keeps the first timestamp."""
        self._require(JobStatus.RUNNING, "request cancellation of")
        if self.cancel_requested_at is None:
            self.cancel_requested_at = now
            self.updated_at = now

    def cancel(self, now: datetime, *, code: str | None = None) -> None:
        """Any open status -> ``cancelled``. ``code`` records why when the platform, not a
        person, cancelled it (``electrical.document_not_live`` after a purge); additive to the
        LLD signature."""
        if not self.is_open:
            raise JobStateError(f"cannot cancel a job that is {self.status.value}")
        self.status = JobStatus.CANCELLED
        self.failure_code = code
        self._finish(now)

    def _finish(self, now: datetime) -> None:
        self.lease_until = None
        self.finished_at = now
        self.updated_at = now

    def _require(self, status: JobStatus, what: str) -> None:
        if self.status is not status:
            raise JobStateError(f"cannot {what} a job that is {self.status.value}")
