"""Drawings' driven persistence ports. Every method takes ``ctx`` first: tenancy is the
repository's job (RLS, ``SET LOCAL app.workspace_id`` per transaction, DD-04). Implemented by
``adapters/sql_repository.py`` (data-engineer, E11)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.electrical.drawings.domain.characterization import Characterization
from app.modules.electrical.drawings.domain.document import Document, DocumentStatus
from app.modules.electrical.drawings.domain.job import IngestionJob
from app.modules.electrical.drawings.domain.pages import (
    PageEvidence,
    PageResult,
    PageRow,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.profile import ProfileVersion
from app.modules.electrical.drawings.domain.project import Project


class ProjectRepository(Protocol):
    async def get(self, ctx: ExecutionContext, project_id: Uuid) -> Project | None: ...

    async def get_by_space(self, ctx: ExecutionContext, space_id: Uuid) -> Project | None: ...

    async def add(self, ctx: ExecutionContext, project: Project) -> None:
        """ConflictError(code="electrical.project_exists") when the space has one."""
        ...

    async def save(self, ctx: ExecutionContext, project: Project) -> None:
        """Optimistic on ``version``."""
        ...

    async def list_by_space(
        self, ctx: ExecutionContext, space_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[Project]: ...

    async def delete_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> int: ...


class ProfileRepository(Protocol):
    async def latest(
        self, ctx: ExecutionContext, *, project_id: Uuid, document_id: Uuid | None
    ) -> ProfileVersion | None: ...

    async def append(self, ctx: ExecutionContext, version: ProfileVersion) -> ProfileVersion:
        """Assigns ``version = max + 1`` under the owner's unique index; a lost race is a
        ConflictError and the caller retries once. Returns the stored version."""
        ...


class DocumentRepository(Protocol):
    async def get(self, ctx: ExecutionContext, document_id: Uuid) -> Document | None: ...

    async def lock_live(self, ctx: ExecutionContext, document_id: Uuid) -> Document | None:
        """SELECT ... FOR SHARE inside the caller's unit of work; None unless ACTIVE."""
        ...

    async def not_purging_for_file(
        self, ctx: ExecutionContext, file_id: Uuid
    ) -> Sequence[Document]: ...

    async def active_files(self, ctx: ExecutionContext, project_id: Uuid) -> Mapping[Uuid, Uuid]:
        """document_id -> file_id of the project's ACTIVE documents."""
        ...

    async def active_with_content(
        self, ctx: ExecutionContext, project_id: Uuid, sha256: str
    ) -> Document | None: ...

    async def add(self, ctx: ExecutionContext, document: Document) -> None:
        """ConflictError(code="electrical.document_exists") when the file is a document."""
        ...

    async def save(self, ctx: ExecutionContext, document: Document) -> None:
        """ConflictError(code="electrical.duplicate_document") when ``content_sha256``
        collides among ACTIVE documents of the project."""
        ...

    async def list_by_project(
        self,
        ctx: ExecutionContext,
        project_id: Uuid,
        *,
        status: DocumentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]: ...


class JobRepository(Protocol):
    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> IngestionJob | None: ...

    async def add(self, ctx: ExecutionContext, job: IngestionJob) -> None:
        """ConflictError(code="electrical.job_conflict") when the document has an open job."""
        ...

    async def save(self, ctx: ExecutionContext, job: IngestionJob) -> None: ...

    async def open_for_document(
        self, ctx: ExecutionContext, document_id: Uuid
    ) -> IngestionJob | None: ...

    async def latest_for_document(
        self, ctx: ExecutionContext, document_id: Uuid
    ) -> IngestionJob | None: ...

    async def open_count(self, ctx: ExecutionContext) -> int:
        """The workspace's open jobs (queue protection, ``ELE_QUEUE_PROTECTION_LIMIT``)."""
        ...

    async def queue_position(self, ctx: ExecutionContext, job: IngestionJob) -> int: ...

    async def recent_run_seconds(self, ctx: ExecutionContext, *, limit: int) -> Sequence[float]:
        """Run times of the latest succeeded jobs, for the wait estimate."""
        ...


class PageRepository(Protocol):
    async def seed(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        pages: Sequence[int],
        stage: Stage,
        pipeline_version: str,
    ) -> None:
        """Pending rows; ON CONFLICT DO NOTHING (a resume re-seeds harmlessly)."""
        ...

    async def record(
        self, ctx: ExecutionContext, document_id: Uuid, results: Sequence[PageResult]
    ) -> None:
        """Upsert pages and their stage rows, keyed (document, page, stage)."""
        ...

    async def unfinished(
        self, ctx: ExecutionContext, document_id: Uuid, stage: Stage
    ) -> Sequence[int]: ...

    async def stage_counts(
        self, ctx: ExecutionContext, document_id: Uuid, stage: Stage
    ) -> Mapping[StageStatus, int]: ...

    async def list(
        self, ctx: ExecutionContext, document_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[PageRow]: ...

    async def reset(
        self, ctx: ExecutionContext, document_id: Uuid, pages: Sequence[int] | None, stage: Stage
    ) -> int:
        """Return the stage rows of ``pages`` (every page when ``None``) to ``pending``, their
        reason and attempts cleared: a reprocessing starts from a clean stage. Additive to the
        LLD (``seed`` never overwrites). Returns the rows changed."""
        ...

    async def evidence(self, ctx: ExecutionContext, document_id: Uuid) -> Sequence[PageEvidence]:
        """Every page whose classify stage finished (succeeded or needs_review), in page
        order, with its stored signals (no token sample) and tally. Additive to the LLD: a
        resumed job folds in what an earlier run recorded."""
        ...


class CharacterizationRepository(Protocol):
    async def put(
        self, ctx: ExecutionContext, item: Characterization, *, project_id: Uuid
    ) -> None: ...

    async def get(self, ctx: ExecutionContext, document_id: Uuid) -> Characterization | None: ...

    async def for_project(
        self, ctx: ExecutionContext, project_id: Uuid
    ) -> Sequence[Characterization]:
        """ACTIVE documents only."""
        ...


class DrawingsSweep(Protocol):
    async def sweep_document(self, ctx: ExecutionContext, document_id: Uuid) -> int:
        """stages -> pages -> characterization -> jobs -> document profiles -> document, in
        one transaction, only while the document is PURGING; 0 when already gone."""
        ...
