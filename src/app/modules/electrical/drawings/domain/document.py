"""The drawing document aggregate (LLD 3.3.6, SRS D-22: one file, one document). Stdlib only.

A document is ``ACTIVE`` (read everywhere), ``EXCLUDED`` (out of every read, row kept: a
registration the guard or STG-01 turned away, HLD §8.11) or ``PURGING`` (out of every read, being
swept, HLD §8.8). ``PURGING`` is terminal and reachable from any status; ``EXCLUDED`` is
reachable only from ``ACTIVE``.

``version`` is the optimistic-lock counter: the repository's ``save`` bumps it, the aggregate
never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.modules.electrical.drawings.domain.errors import DocumentStateError, InvalidDrawingInput

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class DocumentStatus(StrEnum):
    ACTIVE = "active"  # read everywhere
    EXCLUDED = "excluded"  # out of every read, row kept (HLD §8.11)
    PURGING = "purging"  # out of every read, being swept (HLD §8.8)


class ExclusionReason(StrEnum):
    FILE_UNREADABLE = "file_unreadable"  # an orphan: its file is gone
    DUPLICATE_CONTENT = "duplicate_content"  # FR-ING-02, found at STG-01


class ProfileState(StrEnum):
    NONE = "none"  # no proposal yet
    PROPOSED = "proposed"  # detected below the threshold: awaiting confirmation
    ACCEPTED = "accepted"  # detected at or above the threshold
    CONFIRMED = "confirmed"  # confirmed (as is, or edited) by a person


@dataclass(slots=True)
class Document:
    id: str
    workspace_id: str
    space_id: str
    project_id: str
    file_id: str
    status: DocumentStatus
    exclusion_reason: ExclusionReason | None
    content_sha256: str | None
    page_count: int | None
    pdf_creator: str | None
    pdf_producer: str | None
    profile_state: ProfileState
    pipeline_version: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    purging_at: datetime | None
    version: int

    @property
    def is_live(self) -> bool:
        """ACTIVE only: the one status every read includes."""
        return self.status is DocumentStatus.ACTIVE

    def exclude(self, reason: ExclusionReason, now: datetime) -> None:
        """``ACTIVE -> EXCLUDED``: the row stays, every read stops seeing it."""
        if self.status is not DocumentStatus.ACTIVE:
            raise DocumentStateError(f"cannot exclude a document that is {self.status.value}")
        self.status = DocumentStatus.EXCLUDED
        self.exclusion_reason = reason
        self.updated_at = now

    def mark_purging(self, now: datetime) -> bool:
        """Any status -> ``PURGING``. ``False`` (and nothing written) when it already is, so a
        re-run of a cascade is a no-op (idempotent). The exclusion reason goes with the
        ``EXCLUDED`` status it explained: ``ck_drawings_documents_exclusion`` allows a reason
        only on an excluded document."""
        if self.status is DocumentStatus.PURGING:
            return False
        self.status = DocumentStatus.PURGING
        self.exclusion_reason = None
        self.purging_at = now
        self.updated_at = now
        return True

    def record_inspection(
        self,
        *,
        sha256: str,
        page_count: int,
        creator: str | None,
        producer: str | None,
        pipeline_version: str,
        now: datetime,
    ) -> None:
        """What STG-01 learned from the bytes. Only a live document takes it: a document
        purged or excluded while the worker ran must not be written to."""
        self._require_live("record an inspection")
        if _SHA256.fullmatch(sha256) is None:
            raise InvalidDrawingInput("sha256 must be 64 lower-case hexadecimal characters")
        if page_count < 0:
            raise InvalidDrawingInput("page_count cannot be negative")
        self.content_sha256 = sha256
        self.page_count = page_count
        self.pdf_creator = creator
        self.pdf_producer = producer
        self.pipeline_version = pipeline_version
        self.updated_at = now

    def set_profile_state(self, state: ProfileState, now: datetime) -> None:
        self._require_live("change its profile state")
        if state is self.profile_state:
            return
        self.profile_state = state
        self.updated_at = now

    def _require_live(self, what: str) -> None:
        if not self.is_live:
            raise DocumentStateError(f"cannot {what}: the document is {self.status.value}")
