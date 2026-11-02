"""In-memory drawings fakes shared by the WireTwin unit tests (BT14).

Not a ``test_*`` module, so pytest never collects it (``support_files_media``'s precedent).
The repositories are faithful on what the use cases depend on: every read is scoped by the
caller's workspace (the RLS stand-in), a save checks the optimistic ``version``, and the unique
indexes the SQL adapter raises (one project per space, one document per file, one open job per
document, one ACTIVE document per content hash) raise the same ``ConflictError`` codes.
``FakePdfInspection`` replays ele-parse's ``inspect`` JSON (``{schema, summary, pages[]}``), so a
pinned file stands in for the real service.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.electrical.drawings.domain.characterization import Characterization
from app.modules.electrical.drawings.domain.document import (
    Document,
    DocumentStatus,
    ProfileState,
)
from app.modules.electrical.drawings.domain.errors import (
    InspectionUnavailable,
    PageRangeFailed,
    PdfRejected,
)
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import (
    FontSignal,
    PageClassification,
    PageResult,
    PageRow,
    PageSignals,
    PdfSummary,
    StageReason,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from app.modules.electrical.drawings.domain.profile import ProfileVersion
from app.modules.electrical.drawings.domain.project import Project
from app.modules.electrical.drawings.ports.source import StagedSource
from app.modules.electrical.shared.rules import IdentifierRules
from tests.eval.electrical.m04_identifiers import load_rules

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
WS = "11111111-1111-7111-8111-111111111111"
SPACE = "22222222-2222-7222-8222-222222222222"


def make_ctx(workspace_id: str = WS, user_id: str = "u1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def make_project(**changes: Any) -> Project:
    fields: dict[str, Any] = {
        "id": "p1",
        "workspace_id": WS,
        "space_id": SPACE,
        "name": "Plant 7",
        "description": None,
        "created_by": "u1",
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
    }
    return Project(**{**fields, **changes})


def make_document(**changes: Any) -> Document:
    fields: dict[str, Any] = {
        "id": "d1",
        "workspace_id": WS,
        "space_id": SPACE,
        "project_id": "p1",
        "file_id": "f1",
        "status": DocumentStatus.ACTIVE,
        "exclusion_reason": None,
        "content_sha256": None,
        "page_count": None,
        "pdf_creator": None,
        "pdf_producer": None,
        "profile_state": ProfileState.NONE,
        "pipeline_version": None,
        "created_by": "u1",
        "created_at": NOW,
        "updated_at": NOW,
        "purging_at": None,
        "version": 1,
    }
    return Document(**{**fields, **changes})


def make_job(**changes: Any) -> IngestionJob:
    fields: dict[str, Any] = {
        "id": "j1",
        "workspace_id": WS,
        "document_id": "d1",
        "kind": JobKind.INGEST,
        "pages": None,
        "status": JobStatus.QUEUED,
        "stage": None,
        "failure_code": None,
        "failure_detail": None,
        "attempt": 1,
        "lease_until": None,
        "seq": 0,
        "cancel_requested_at": None,
        "pipeline_version": PIPELINE_VERSION,
        "created_by": "u1",
        "created_at": NOW,
        "updated_at": NOW,
        "started_at": None,
        "finished_at": None,
        "version": 1,
    }
    return IngestionJob(**{**fields, **changes})


def make_signals(page_no: int = 1, **changes: Any) -> PageSignals:
    """A clean vector-text page (a lot of Latin text, nothing else) unless told otherwise."""
    fields: dict[str, Any] = {
        "page_no": page_no,
        "width_pt": 842.0,
        "height_pt": 595.0,
        "rotation": 0,
        "char_count": 500,
        "unmapped_char_count": 0,
        "pua_char_count": 0,
        "cjk_char_count": 0,
        "latin_char_count": 480,
        "word_count": 90,
        "vector_path_count": 300,
        "short_curve_path_count": 0,
        "image_count": 0,
        "image_area_ratio": 0.0,
        "link_count": 0,
        "fonts": (),
        "tokens": (),
    }
    return PageSignals(**{**fields, **changes})


def make_summary(**changes: Any) -> PdfSummary:
    fields: dict[str, Any] = {
        "page_count": 1,
        "pdf_version": "1.7",
        "encrypted": False,
        "creator": None,
        "producer": None,
        "outline_count": 0,
    }
    return PdfSummary(**{**fields, **changes})


def make_rules() -> IdentifierRules:
    return load_rules()


# --------------------------------------------------------------------------------------
# repositories
# --------------------------------------------------------------------------------------
def _page(rows: list[Any], limit: int, cursor: str | None) -> Page[Any]:
    """A keyset-less page over rows already sorted; the cursor is the index, opaque enough."""
    start = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    chunk = rows[start : start + limit]
    more = start + limit < len(rows)
    token = base64.urlsafe_b64encode(str(start + limit).encode()).decode() if more else None
    return Page(data=chunk, next_cursor=token, limit=limit)


def _check_version(stored: Any | None, incoming: Any, what: str) -> None:
    if stored is None:
        raise NotFoundError(f"{what} does not exist")
    if stored.version != incoming.version:
        raise ConflictError(f"{what} was changed concurrently")


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Project] = {}

    async def get(self, ctx: ExecutionContext, project_id: Uuid) -> Project | None:
        row = self.rows.get(project_id)
        return copy(row) if row and row.workspace_id == ctx.workspace_id else None

    async def get_by_space(self, ctx: ExecutionContext, space_id: Uuid) -> Project | None:
        for row in self.rows.values():
            if row.workspace_id == ctx.workspace_id and row.space_id == space_id:
                return copy(row)
        return None

    async def add(self, ctx: ExecutionContext, project: Project) -> None:
        if await self.get_by_space(ctx, project.space_id) is not None:
            raise ConflictError(
                "The space already has a WireTwin project", code="electrical.project_exists"
            )
        self.rows[project.id] = copy(project)

    async def save(self, ctx: ExecutionContext, project: Project) -> None:
        stored = self.rows.get(project.id)
        _check_version(
            stored if stored and stored.workspace_id == ctx.workspace_id else None,
            project,
            "project",
        )
        project.version += 1
        self.rows[project.id] = copy(project)

    async def list_by_space(
        self, ctx: ExecutionContext, space_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[Project]:
        rows = sorted(
            (
                r
                for r in self.rows.values()
                if r.workspace_id == ctx.workspace_id and r.space_id == space_id
            ),
            key=lambda r: r.id,
        )
        return _page([copy(r) for r in rows], limit, cursor)

    async def delete_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        doomed = [
            k
            for k, r in self.rows.items()
            if r.workspace_id == ctx.workspace_id and r.space_id == space_id
        ]
        for key in doomed:
            del self.rows[key]
        return len(doomed)


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.rows: list[ProfileVersion] = []

    async def latest(
        self, ctx: ExecutionContext, *, project_id: Uuid, document_id: Uuid | None
    ) -> ProfileVersion | None:
        owned = [
            v
            for v in self.rows
            if v.workspace_id == ctx.workspace_id
            and v.project_id == project_id
            and v.document_id == document_id
        ]
        return max(owned, key=lambda v: v.version, default=None)

    async def append(self, ctx: ExecutionContext, version: ProfileVersion) -> ProfileVersion:
        latest = await self.latest(
            ctx, project_id=version.project_id, document_id=version.document_id
        )
        stored = replace(version, version=(latest.version if latest else 0) + 1)
        self.rows.append(stored)
        return stored


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Document] = {}

    def _mine(self, ctx: ExecutionContext) -> list[Document]:
        return [r for r in self.rows.values() if r.workspace_id == ctx.workspace_id]

    async def get(self, ctx: ExecutionContext, document_id: Uuid) -> Document | None:
        row = self.rows.get(document_id)
        return copy(row) if row and row.workspace_id == ctx.workspace_id else None

    async def lock_live(self, ctx: ExecutionContext, document_id: Uuid) -> Document | None:
        row = await self.get(ctx, document_id)
        return row if row is not None and row.is_live else None

    async def not_purging_for_file(
        self, ctx: ExecutionContext, file_id: Uuid
    ) -> Sequence[Document]:
        return [
            copy(r)
            for r in self._mine(ctx)
            if r.file_id == file_id and r.status is not DocumentStatus.PURGING
        ]

    async def live_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> Sequence[Document]:
        return [copy(r) for r in self._mine(ctx) if r.space_id == space_id and r.is_live]

    async def active_files(self, ctx: ExecutionContext, project_id: Uuid) -> Mapping[Uuid, Uuid]:
        return {
            r.id: r.file_id for r in self._mine(ctx) if r.project_id == project_id and r.is_live
        }

    async def active_with_content(
        self, ctx: ExecutionContext, project_id: Uuid, sha256: str
    ) -> Document | None:
        for r in self._mine(ctx):
            if r.project_id == project_id and r.is_live and r.content_sha256 == sha256:
                return copy(r)
        return None

    async def add(self, ctx: ExecutionContext, document: Document) -> None:
        if any(r.file_id == document.file_id for r in self._mine(ctx)):
            raise ConflictError(
                "File is already a drawing document", code="electrical.document_exists"
            )
        self.rows[document.id] = copy(document)

    async def save(self, ctx: ExecutionContext, document: Document) -> None:
        stored = self.rows.get(document.id)
        _check_version(
            stored if stored and stored.workspace_id == ctx.workspace_id else None,
            document,
            "document",
        )
        if document.is_live and document.content_sha256 is not None:
            clash = await self.active_with_content(
                ctx, document.project_id, document.content_sha256
            )
            if clash is not None and clash.id != document.id:
                raise ConflictError(
                    "The same content is already a document of this project",
                    code="electrical.duplicate_document",
                )
        document.version += 1
        self.rows[document.id] = copy(document)

    async def list_by_project(
        self,
        ctx: ExecutionContext,
        project_id: Uuid,
        *,
        status: DocumentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]:
        rows = sorted(
            (
                r
                for r in self._mine(ctx)
                if r.project_id == project_id and (status is None or r.status is status)
            ),
            key=lambda r: r.id,
        )
        return _page([copy(r) for r in rows], limit, cursor)


class InMemoryJobRepository:
    def __init__(self) -> None:
        self.rows: dict[str, IngestionJob] = {}
        self.run_seconds: list[float] = []

    def _mine(self, ctx: ExecutionContext) -> list[IngestionJob]:
        return [r for r in self.rows.values() if r.workspace_id == ctx.workspace_id]

    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> IngestionJob | None:
        row = self.rows.get(job_id)
        return copy(row) if row and row.workspace_id == ctx.workspace_id else None

    async def add(self, ctx: ExecutionContext, job: IngestionJob) -> None:
        if job.is_open and any(
            r.document_id == job.document_id and r.is_open for r in self._mine(ctx)
        ):
            raise ConflictError(
                "Ingestion job is not in a state that allows this", code="electrical.job_conflict"
            )
        self.rows[job.id] = copy(job)

    async def save(self, ctx: ExecutionContext, job: IngestionJob) -> None:
        stored = self.rows.get(job.id)
        _check_version(
            stored if stored and stored.workspace_id == ctx.workspace_id else None, job, "job"
        )
        others = [r for r in self._mine(ctx) if r.id != job.id and r.document_id == job.document_id]
        if job.is_open and any(r.is_open for r in others):
            raise ConflictError(
                "Ingestion job is not in a state that allows this", code="electrical.job_conflict"
            )
        job.version += 1
        self.rows[job.id] = copy(job)

    async def open_for_document(
        self, ctx: ExecutionContext, document_id: Uuid
    ) -> IngestionJob | None:
        for r in self._mine(ctx):
            if r.document_id == document_id and r.is_open:
                return copy(r)
        return None

    async def latest_for_document(
        self, ctx: ExecutionContext, document_id: Uuid
    ) -> IngestionJob | None:
        owned = [r for r in self._mine(ctx) if r.document_id == document_id]
        return copy(max(owned, key=lambda r: (r.created_at, r.id))) if owned else None

    async def open_count(self, ctx: ExecutionContext) -> int:
        return sum(1 for r in self._mine(ctx) if r.is_open)

    async def queue_position(self, ctx: ExecutionContext, job: IngestionJob) -> int:
        ahead = [
            r
            for r in self._mine(ctx)
            if r.status is JobStatus.QUEUED and (r.created_at, r.id) < (job.created_at, job.id)
        ]
        return len(ahead) + 1

    async def recent_run_seconds(self, ctx: ExecutionContext, *, limit: int) -> Sequence[float]:
        return self.run_seconds[:limit]


@dataclass
class _StoredPage:
    result: PageResult
    status: StageStatus
    reason: StageReason | None


class InMemoryPageRepository:
    def __init__(self) -> None:
        # (workspace, document, page, stage) -> state; results only once recorded
        self.stages: dict[tuple[str, str, int, Stage], _StoredPage | None] = {}
        self.results: dict[tuple[str, str, int], PageResult] = {}

    async def seed(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        pages: Sequence[int],
        stage: Stage,
        pipeline_version: str,
    ) -> None:
        del pipeline_version
        for page_no in pages:
            self.stages.setdefault((ctx.workspace_id, document_id, page_no, stage), None)

    async def record(
        self, ctx: ExecutionContext, document_id: Uuid, results: Sequence[PageResult]
    ) -> None:
        for result in results:
            c = result.classification
            self.results[(ctx.workspace_id, document_id, c.page_no)] = result
            self.stages[(ctx.workspace_id, document_id, c.page_no, Stage.CLASSIFY)] = _StoredPage(
                result, c.status, c.reason
            )

    def _status(self, key: tuple[str, str, int, Stage]) -> StageStatus:
        stored = self.stages[key]
        return stored.status if stored is not None else StageStatus.PENDING

    async def unfinished(
        self, ctx: ExecutionContext, document_id: Uuid, stage: Stage
    ) -> Sequence[int]:
        done = {StageStatus.SUCCEEDED, StageStatus.NEEDS_REVIEW, StageStatus.FAILED}
        return sorted(
            k[2]
            for k in self.stages
            if k[0] == ctx.workspace_id
            and k[1] == document_id
            and k[3] is stage
            and self._status(k) not in done
        )

    async def stage_counts(
        self, ctx: ExecutionContext, document_id: Uuid, stage: Stage
    ) -> Mapping[StageStatus, int]:
        counts: dict[StageStatus, int] = {}
        for k in self.stages:
            if k[0] == ctx.workspace_id and k[1] == document_id and k[3] is stage:
                status = self._status(k)
                counts[status] = counts.get(status, 0) + 1
        return counts

    async def classifications(
        self, ctx: ExecutionContext, document_id: Uuid
    ) -> Sequence[PageClassification]:
        return [
            r.classification
            for (ws, doc, _), r in sorted(self.results.items(), key=lambda kv: kv[0][2])
            if ws == ctx.workspace_id and doc == document_id
        ]

    async def list(
        self, ctx: ExecutionContext, document_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[PageRow]:
        rows: list[PageRow] = []
        for k in sorted(self.stages, key=lambda key: key[2]):
            if k[0] != ctx.workspace_id or k[1] != document_id:
                continue
            result = self.results.get((k[0], k[1], k[2]))
            rows.append(
                PageRow(
                    page_no=k[2],
                    classification=result.classification if result else None,
                    stages={k[3]: self._status(k)},
                    attempts=result.attempts if result else 0,
                )
            )
        return _page(rows, limit, cursor)


class InMemoryCharacterizationRepository:
    def __init__(self, documents: InMemoryDocumentRepository | None = None) -> None:
        self.rows: dict[tuple[str, str], tuple[str, Characterization]] = {}
        self._documents = documents

    async def put(self, ctx: ExecutionContext, item: Characterization, *, project_id: Uuid) -> None:
        self.rows[(ctx.workspace_id, item.document_id)] = (project_id, item)

    async def get(self, ctx: ExecutionContext, document_id: Uuid) -> Characterization | None:
        found = self.rows.get((ctx.workspace_id, document_id))
        return found[1] if found else None

    async def for_project(
        self, ctx: ExecutionContext, project_id: Uuid
    ) -> Sequence[Characterization]:
        out: list[Characterization] = []
        for (ws, doc_id), (project, item) in sorted(self.rows.items()):
            if ws != ctx.workspace_id or project != project_id:
                continue
            if self._documents is not None:
                doc = self._documents.rows.get(doc_id)
                if doc is None or not doc.is_live:
                    continue
            out.append(item)
        return out


# --------------------------------------------------------------------------------------
# inspection, files, staging
# --------------------------------------------------------------------------------------
def signals_from_json(raw: Mapping[str, Any]) -> PageSignals:
    fonts = tuple(FontSignal(**font) for font in raw.get("fonts", ()))
    fields = {k: v for k, v in raw.items() if k not in ("fonts", "tokens")}
    return PageSignals(**fields, fonts=fonts, tokens=tuple(raw.get("tokens", ())))


class FakePdfInspection:
    """ele-parse's ``inspect`` output (``{schema: 1, summary, pages[]}``) played back as the
    ``PdfInspection`` port. ``failures`` makes a range holding such a page raise
    ``PageRangeFailed`` (the page alone, when asked alone); ``rejects`` and ``unavailable``
    raise the whole-file errors."""

    def __init__(
        self,
        summary: PdfSummary,
        pages: Sequence[PageSignals],
        *,
        failures: Mapping[int, StageReason] | None = None,
        rejects: PdfRejected | None = None,
        unavailable: bool = False,
    ) -> None:
        self._summary = summary
        self._pages = {p.page_no: p for p in pages}
        self._failures = dict(failures or {})
        self._rejects = rejects
        self._unavailable = unavailable
        self.calls: list[tuple[str, int, int]] = []

    @classmethod
    def from_json(cls, text: str, **kwargs: Any) -> FakePdfInspection:
        data = json.loads(text)
        if data.get("schema") != 1:
            raise ValueError("an inspect file must have schema 1")
        summary = PdfSummary(**data["summary"])
        return cls(summary, [signals_from_json(p) for p in data["pages"]], **kwargs)

    async def summary(self, source: StagedSource) -> PdfSummary:
        self.calls.append(("summary", 0, 0))
        self._raise_whole_file()
        return self._summary

    async def pages(self, source: StagedSource, first: int, last: int) -> Sequence[PageSignals]:
        self.calls.append(("pages", first, last))
        self._raise_whole_file()
        wanted = range(first, last + 1)
        failing = [n for n in wanted if n in self._failures]
        if failing:
            if first == last:
                raise PageRangeFailed(self._failures[first])
            raise PageRangeFailed(self._failures[failing[0]])
        return [self._pages[n] for n in wanted if n in self._pages]

    def _raise_whole_file(self) -> None:
        if self._unavailable:
            raise InspectionUnavailable("ele-parse is down")
        if self._rejects is not None:
            raise self._rejects


@dataclass(frozen=True)
class FakeDrawingFile:
    file_id: str
    space_id: str | None = SPACE
    name: str = "plant.pdf"
    content_type: str = "application/pdf"
    size_bytes: int = 1024
    storage_key: str = f"{WS}/f1"
    tags: Mapping[str, str] = field(default_factory=dict)


class FakeDrawingFiles:
    """Readable files by workspace; anything not added is ``None`` (unknown, foreign or
    not ready look alike, as in ``FilesQuery.get_readable``)."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, str], FakeDrawingFile] = {}

    def add(self, file: FakeDrawingFile, *, workspace_id: str = WS) -> FakeDrawingFile:
        self._files[(workspace_id, file.file_id)] = file
        return file

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FakeDrawingFile | None:
        return self._files.get((ctx.workspace_id, file_id))

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        return {
            fid: self._files[(ctx.workspace_id, fid)].name
            for fid in file_ids
            if (ctx.workspace_id, fid) in self._files
        }


class FakeStager:
    """Stages nothing on disk: it answers with a ``StagedSource`` whose hash and size come from
    ``bodies`` (storage key -> bytes), raising ``source_unreadable`` on a size mismatch."""

    def __init__(self, bodies: Mapping[str, bytes] | None = None) -> None:
        self.bodies = dict(bodies or {})
        self.staged: list[StagedSource] = []
        self.discarded: list[StagedSource] = []

    async def stage(
        self, ctx: ExecutionContext, *, document_id: Uuid, storage_key: str, expected_size: int
    ) -> StagedSource:
        body = self.bodies.get(storage_key)
        if body is None:
            raise NotFoundError("object does not exist in storage")
        if len(body) != expected_size:
            raise ValidationError(
                "The file's bytes could not be read", code="electrical.source_unreadable"
            )
        source = StagedSource(
            name=f"{ctx.workspace_id}/{document_id}.pdf",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
        )
        self.staged.append(source)
        return source

    async def discard(self, source: StagedSource) -> None:
        self.discarded.append(source)


def later(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)
