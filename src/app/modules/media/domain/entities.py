"""Media aggregate (pure — 06-domain-models §8).

``MediaJob`` tracks one image/video generation job end-to-end. Behaviour
lives on the aggregate; mutations touch only state + ``updated_at``. The
optimistic ``version`` is advanced by the repository on ``save``
(02-port-contracts §2) — never here, matching every other aggregate in this
codebase (``files.File``, ``knowledge.Document``, ``conversations.Conversation``).
Identifiers are UUIDv7 text (``str``); timestamps are timezone-aware UTC
(DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.media.domain.errors import InvalidMediaInput, MediaJobStateError
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind

# start_running is re-entrant from RUNNING itself -- see its docstring.
_RUNNABLE = (JobStatus.QUEUED, JobStatus.RUNNING)


@dataclass(slots=True)
class MediaJob:
    """One image/video generation job (06 §8 AR ``MediaJob``)."""

    id: str
    workspace_id: str
    agent_key: AgentKey
    kind: MediaKind
    prompt: str
    params: GenParams
    status: JobStatus
    result_file_id: str | None
    error: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def start_running(self, now: datetime) -> None:
        """``queued|running -> running`` (INV-MJ2).

        Re-entrant from ``running`` on purpose: at-least-once event
        redelivery (DD-09) may call this again after a worker crashed
        mid-generation, and the restarted worker simply re-runs the
        generation call from scratch rather than treating that as an illegal
        transition — the ``knowledge.Document.start_indexing`` precedent.
        """
        if self.status not in _RUNNABLE:
            raise MediaJobStateError(f"cannot start running from status {self.status.value!r}")
        self.status = JobStatus.RUNNING
        self.updated_at = now

    def complete(self, result_file_id: str | None, now: datetime) -> None:
        """``running -> succeeded`` (INV-MJ2): records the generated file's
        id and clears any error left over from... nothing, in practice (a job
        only ever reaches here via ``running``), but clearing is cheap
        insurance against a future relaxation of that rule.

        INV-MJ1 (``succeeded ⇒ result_file_id NOT NULL``) is enforced here as
        an argument invariant, not merely a type hint: a blank/``None``
        ``result_file_id`` raises ``InvalidMediaInput`` rather than silently
        completing the job with nothing to show for it.
        """
        if self.status is not JobStatus.RUNNING:
            raise MediaJobStateError(f"cannot complete from status {self.status.value!r}")
        if not result_file_id:
            raise InvalidMediaInput("result_file_id is required to complete a media job (INV-MJ1)")
        self.status = JobStatus.SUCCEEDED
        self.result_file_id = result_file_id
        self.error = None
        self.updated_at = now

    def fail(self, reason: str, now: datetime) -> None:
        """``running -> failed`` (INV-MJ2) — a terminal state. Like
        ``succeeded``, there is no way back onto ``running``; recovering
        means submitting a brand-new job via ``RequestMedia``."""
        if self.status is not JobStatus.RUNNING:
            raise MediaJobStateError(f"cannot fail from status {self.status.value!r}")
        self.status = JobStatus.FAILED
        self.error = reason
        self.updated_at = now
