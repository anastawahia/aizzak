"""Drawings domain events (pure): plain frozen records with no shared base class, one per
published type (LLD 3.3.11). Dispatch to the outbox happens in the application layer's
``event_mapping``; every payload carries references, never data (HLD §7.8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pipeline import Stage


class JobRequestReason(StrEnum):
    RESUME = "resume"
    REPROCESS = "reprocess"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True, slots=True)
class DocumentRegistered:
    """``electrical.document.registered.v1``: STG-00 accepted a file (-> the document role)."""

    document_id: str
    workspace_id: str
    project_id: str
    file_id: str
    job_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class JobRequested:
    """``electrical.job.requested.v1``: a requeued job needs a worker again."""

    job_id: str
    workspace_id: str
    document_id: str
    reason: JobRequestReason
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentProfileProposed:
    """``electrical.document.profile_proposed.v1`` (-> notification)."""

    document_id: str
    workspace_id: str
    job_id: str
    confidence: float
    needs_confirmation: bool
    mixed_standards: bool
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class JobProgressed:
    """``electrical.job.progressed.v1`` (-> notification); ``seq`` rises with each one."""

    job_id: str
    workspace_id: str
    document_id: str
    seq: int
    stage: Stage
    done: int
    total: int
    failed: int
    needs_review: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class JobCompleted:
    """``electrical.job.completed.v1`` (-> notification)."""

    job_id: str
    workspace_id: str
    document_id: str
    status: JobStatus  # succeeded | failed | cancelled
    failure_code: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentPurged:
    """``electrical.document.purged.v1``: phase 1 of a purge is done; phase 2 sweeps."""

    document_id: str
    workspace_id: str
    project_id: str
    file_id: str
    occurred_at: datetime


DrawingsEvent = (
    DocumentRegistered
    | JobRequested
    | DocumentProfileProposed
    | JobProgressed
    | JobCompleted
    | DocumentPurged
)
