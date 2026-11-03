"""BT38 / INC1-AC-6, AC-7, AC-8: STG-00 then STG-01 against real Postgres, MinIO and Redis.

The whole ingestion path a drawing takes, with only ele-parse's HTTP hop replaced by a call into
the same code in this process (the isolation of the container itself is ``O10``'s question, not
this one's):

1. **STG-00**, as the route calls it: ``DocumentService.register`` over the real repositories under
   ``app_rw`` and RLS writes the document, its ``queued`` job and
   ``electrical.document.registered.v1`` in ONE transaction.
2. The real relay (``outbox_relay`` role) publishes that row.
3. **STG-01**: the worker built by ``build_electrical_worker`` -- its production handlers, on this
   test's UNIQUE stream and group (R6: a test never touches ``stream.electrical``) -- takes the
   message, reads the PDF out of real MinIO in chunks into a staging directory, hashes it while
   writing, inspects it with ``services.ele_parse``' own code, classifies the pages and proposes a
   profile.
4. The same message is delivered a SECOND time: one row in ``platform.processed_events`` stays one.

The PDF is generated with PyMuPDF so its pages hold real text and real paths; the unit suites pin
what each stage does, and this one pins that the stages meet in the real stores.

Run in CI's ``integration`` job (``docker-compose.test.yml``) or on throwaway containers; never on
the live stack (``CLAUDE.md``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from redis.asyncio import Redis
from services.ele_parse import child
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings, ElectricalSettings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.storage.minio_storage import MinioStorage
from app.modules.electrical.drawings.adapters.ele_parse_client import (
    signals_from_json,
    summary_from_json,
)
from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.modules.electrical.drawings.adapters.source_stager import StreamingSourceStager
from app.modules.electrical.drawings.adapters.sql_repository import (
    SqlCharacterizationRepository,
    SqlDocumentRepository,
    SqlJobRepository,
    SqlPageRepository,
    SqlProfileRepository,
    SqlProjectRepository,
)
from app.modules.electrical.drawings.application.documents import (
    DocumentService,
    RegisteredDocument,
)
from app.modules.electrical.drawings.application.inspection import InspectDocument
from app.modules.electrical.drawings.application.jobs import JobService
from app.modules.electrical.drawings.application.projects import ProjectService
from app.modules.electrical.drawings.application.purge import SweepPurgedDocument
from app.modules.electrical.drawings.domain.document import DocumentStatus, ProfileState
from app.modules.electrical.drawings.domain.errors import PdfRejected
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pages import PageSignals, PdfSummary, StageStatus
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.project import Project
from app.modules.electrical.drawings.ports.source import StagedSource
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import FilesQueryService, FindNamesakes
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from app.workers.electrical_bootstrap import (
    CG_ELECTRICAL_DOCUMENT,
    GroupLedger,
    build_electrical_worker,
)
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_minio, pytest.mark.live_redis]

_PAGES = 3
_SAMPLE_TOKENS = 50


class _InProcessEleParse:
    """``PdfInspection`` over ``services.ele_parse``' own ``child.handle``: the code ele-parse runs
    in its sandboxed child, called in this process on the staged file."""

    def __init__(self, stage_root: Path) -> None:
        self._root = stage_root

    def _ask(self, source: StagedSource, **request: Any) -> dict[str, Any]:
        answer = child.handle(
            {"path": str(self._root / source.name), "token_sample": _SAMPLE_TOKENS, **request}
        )
        if not answer["ok"]:
            raise PdfRejected(str(answer["code"]), str(answer.get("detail", "")))
        result: dict[str, Any] = answer["result"]
        return result

    async def summary(self, source: StagedSource) -> PdfSummary:
        return summary_from_json(self._ask(source, op="summary"))

    async def pages(self, source: StagedSource, first: int, last: int) -> Sequence[PageSignals]:
        found = self._ask(source, op="pages", first=first, last=last)
        return [signals_from_json(page) for page in found["pages"]]


def _drawing_pdf(pages: int = _PAGES) -> bytes:
    """A small electrical drawing: IEC 81346 designations and PLC addresses as real text, and some
    vector paths, under an EPLAN producer."""
    doc = pymupdf.open()
    doc.set_metadata({"creator": "EPLAN Electric P8", "producer": "EPLAN"})
    for number in range(pages):
        page = doc.new_page(width=842, height=595)
        base = number * 12
        words = " ".join(f"-K{base + n} -Q{base + n} -X{n + 1}:{n + 1}" for n in range(1, 12))
        page.insert_text((40, 60), words, fontsize=8)
        page.insert_text((40, 90), "/12.3 %I0.0 24V rated", fontsize=8)
        page.draw_line((40, 110), (700, 110))
        page.draw_rect(pymupdf.Rect(40, 130, 200, 190))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


async def _owner_rows(
    owner_dsn: str, sql: str, *, workspace_id: str | None = None, **params: object
) -> list[dict[str, Any]]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            if workspace_id is not None:
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
                )
            result = await conn.execute(text(sql), params)
            return [dict(row) for row in result.mappings()]
    finally:
        await engine.dispose()


async def _retarget(owner_dsn: str, *, aggregate_id: str, stream: str) -> None:
    """Point the ``registered`` row at this test's UNIQUE stream before the relay reads it
    (``test_e2e_outbox_to_worker.py``'s rule: nothing ever lands on a production stream)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE platform.outbox SET stream = :stream WHERE aggregate_id = :id "
                    "AND event_type = 'electrical.document.registered.v1'"
                ),
                {"stream": stream, "id": aggregate_id},
            )
    finally:
        await engine.dispose()


@dataclass
class _Rig:
    """The real stores and services, composed as the Composition Root and the worker compose
    them, plus the worker built by ``build_electrical_worker`` on a UNIQUE stream and group."""

    files: SqlFileRepository
    spaces: SqlSpaceRepository
    projects: SqlProjectRepository
    profiles: SqlProfileRepository
    documents: SqlDocumentRepository
    jobs: SqlJobRepository
    pages: SqlPageRepository
    characterizations: SqlCharacterizationRepository
    project_service: ProjectService
    document_service: DocumentService
    consumer: StreamConsumer
    subscription: Subscription
    relay: OutboxRelay
    stage_dir: Path


def _rig(
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    minio_storage: MinioStorage,
    tmp_path: Path,
    *,
    stream: str,
    group: str,
) -> _Rig:
    files = SqlFileRepository(tenant_session)
    spaces = SqlSpaceRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    projects = SqlProjectRepository(tenant_session)
    profiles = SqlProfileRepository(tenant_session)
    documents = SqlDocumentRepository(tenant_session)
    jobs = SqlJobRepository(tenant_session)
    pages = SqlPageRepository(tenant_session)
    characterizations = SqlCharacterizationRepository(tenant_session)
    rules = PackagedRuleSource()
    files_query = FilesQueryService(files)
    settings = ElectricalSettings(stage_dir=str(tmp_path / "stage"), inspect_batch_pages=2)
    ledger = GroupLedger(SqlProcessedEventLedger(tenant_session), CG_ELECTRICAL_DOCUMENT)
    inspect = InspectDocument(
        documents,
        jobs,
        pages,
        profiles,
        characterizations,
        files_query,
        StreamingSourceStager(
            minio_storage, Path(settings.stage_dir), chunk_bytes=settings.stage_chunk_bytes
        ),
        _InProcessEleParse(Path(settings.stage_dir)),
        rules,
        ledger,
        outbox,
        tenant_session,
        _NoHeartbeat(),
        settings,
    )
    consumer, (subscription,) = build_electrical_worker(
        redis_client=redis_client,
        roles=frozenset({"document"}),
        inspect=inspect,
        sweep=SweepPurgedDocument(_NoSweep(), ledger, tenant_session),
        consumer_name="bt38",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )
    return _Rig(
        files=files,
        spaces=spaces,
        projects=projects,
        profiles=profiles,
        documents=documents,
        jobs=jobs,
        pages=pages,
        characterizations=characterizations,
        project_service=ProjectService(
            projects, profiles, SpacesQueryService(spaces), rules, tenant_session
        ),
        document_service=DocumentService(
            projects,
            documents,
            jobs,
            pages,
            profiles,
            files_query,
            FindNamesakes(files),
            rules,
            JobService(jobs, documents, pages, outbox, tenant_session),
            outbox,
            tenant_session,
            queue_protection_limit=settings.queue_protection_limit,
        ),
        consumer=consumer,
        # the production handlers, on this test's stream and group (R6)
        subscription=Subscription(stream=stream, group=group, handlers=subscription.handlers),
        relay=OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=10,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        ),
        stage_dir=Path(settings.stage_dir),
    )


async def _seed_project_and_file(
    rig: _Rig,
    ctx: ExecutionContext,
    minio_storage: MinioStorage,
    *,
    file_id: str,
    storage_key: str,
    body: bytes,
) -> tuple[Space, Project]:
    """A space, the space's project, and a READY PDF whose bytes are really in MinIO."""
    now = utc_now()
    space = Space(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        name=SpaceName("Plant 7"),
        created_by=new_uuid7(),
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )
    await rig.spaces.add(ctx, space)
    await rig.files.add(
        ctx,
        File(
            id=file_id,
            workspace_id=ctx.workspace_id,
            space_id=space.id,
            name=FileName("plant.pdf"),
            content_type=ContentType("application/pdf"),
            size_bytes=len(body),
            storage_key=StorageKey(storage_key),
            checksum=None,
            status=FileStatus.READY,
            uploaded_by=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            version=1,
        ),
    )
    await minio_storage.put(storage_key, body, "application/pdf")
    detail = await rig.project_service.create(
        ctx, space_id=space.id, name="Plant 7", description=None, profile=None
    )
    return space, detail.project


async def _assert_the_proposal_is_in_the_stores(
    rig: _Rig,
    live_db: LiveDbDsns,
    ctx: ExecutionContext,
    *,
    registered: RegisteredDocument,
    project: Project,
    sha: str,
) -> int:
    """Everything STG-01 leaves behind, read back through the real repositories. Returns the
    number of outbox rows of the workspace."""
    job = await rig.jobs.get(ctx, registered.job.id)
    document = await rig.documents.get(ctx, registered.document.id)
    assert job is not None and document is not None
    # the default profile accepts nothing on its own (INC1-R-16): the person confirms
    assert job.status is JobStatus.AWAITING_CONFIRMATION and job.stage is Stage.CLASSIFY
    assert document.content_sha256 == sha  # hashed while the chunks were written
    assert document.page_count == _PAGES and document.pdf_creator == "EPLAN Electric P8"
    assert document.profile_state is ProfileState.PROPOSED
    counts = await rig.pages.stage_counts(ctx, document.id, Stage.CLASSIFY)
    assert counts == {StageStatus.SUCCEEDED: _PAGES}
    listed = await rig.pages.list(ctx, document.id, limit=10, cursor=None)
    assert [p.page_no for p in listed.data] == [1, 2, 3]
    assert all(p.classification is not None for p in listed.data)
    record = await rig.characterizations.get(ctx, document.id)
    assert record is not None and record.producer_family == "eplan"
    assert record.page_count == _PAGES and record.content_sha256 == sha
    detected = await rig.profiles.latest(ctx, project_id=project.id, document_id=document.id)
    assert detected is not None and detected.detection is not None
    assert detected.detection["field_confidence"]
    rows = await _owner_rows(
        live_db.owner,
        "SELECT event_type FROM platform.outbox WHERE workspace_id = :ws ORDER BY created_at, id",
        ws=ctx.workspace_id,
    )
    types = [row["event_type"] for row in rows]
    assert "electrical.document.profile_proposed.v1" in types
    assert "electrical.job.progressed.v1" in types
    assert not list(rig.stage_dir.rglob("*.pdf"))  # the staged copy is gone
    return len(types)


async def test_a_drawing_goes_from_registration_to_a_proposal_through_the_real_stores(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    minio_storage: MinioStorage,
    tmp_path: Path,
) -> None:
    workspace_id, file_id = new_uuid7(), new_uuid7()
    stream, group = f"stream.test.{new_uuid7()}", f"cg.test.{new_uuid7()}"
    ctx = _ctx(workspace_id)
    body = _drawing_pdf()
    sha = hashlib.sha256(body).hexdigest()
    storage_key = f"{workspace_id}/{file_id}/plant.pdf"
    rig = _rig(
        tenant_session,
        relay_sessionmaker,
        redis_client,
        minio_storage,
        tmp_path,
        stream=stream,
        group=group,
    )

    try:
        await rig.consumer.setup([rig.subscription])
        _space, project = await _seed_project_and_file(
            rig, ctx, minio_storage, file_id=file_id, storage_key=storage_key, body=body
        )

        # ---- STG-00: document + job + event, one transaction, under RLS
        registered = await rig.document_service.register(
            ctx, project.id, file_id=file_id, checksum=sha
        )
        assert registered.document.status is DocumentStatus.ACTIVE
        assert registered.job.status is JobStatus.QUEUED
        events = await _owner_rows(
            live_db.owner,
            "SELECT event_type, workspace_id FROM platform.outbox WHERE aggregate_id = :id",
            id=registered.document.id,
        )
        assert [e["event_type"] for e in events] == ["electrical.document.registered.v1"]
        assert str(events[0]["workspace_id"]) == workspace_id

        # ---- the relay, then STG-01 by the worker's production handler
        await _retarget(live_db.owner, aggregate_id=registered.document.id, stream=stream)
        assert await rig.relay.run_once() == 1
        assert await rig.consumer.run_once([rig.subscription]) == 1
        before = await _assert_the_proposal_is_in_the_stores(
            rig, live_db, ctx, registered=registered, project=project, sha=sha
        )
        assert await redis_client.xpending(stream, group) == {
            "pending": 0,
            "min": None,
            "max": None,
            "consumers": [],
        }

        # ---- the same message delivered again changes nothing and leaves ONE ledger row
        ((_entry_id, fields),) = await redis_client.xrange(stream)
        event_id = json.loads(fields[b"ce"])["id"]
        await redis_client.xadd(stream, fields)  # a redelivery of the very same envelope
        assert await rig.consumer.run_once([rig.subscription]) == 1
        claimed = await _owner_rows(
            live_db.owner,
            "SELECT count(*) AS n FROM platform.processed_events "
            "WHERE consumer_group = :g AND event_id = :e",
            g=CG_ELECTRICAL_DOCUMENT,
            e=event_id,
        )
        assert claimed[0]["n"] == 1
        again = await rig.jobs.get(ctx, registered.job.id)
        assert again is not None and again.status is JobStatus.AWAITING_CONFIRMATION
        after = await _owner_rows(
            live_db.owner,
            "SELECT count(*) AS n FROM platform.outbox WHERE workspace_id = :ws",
            ws=workspace_id,
        )
        assert after[0]["n"] == before
    finally:
        with contextlib.suppress(Exception):
            await rig.consumer.teardown([rig.subscription])
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)
        with contextlib.suppress(Exception):
            await minio_storage.delete(storage_key)


class _NoHeartbeat:
    def beat(self) -> None:
        return None


class _NoSweep:
    """The purge sweep is not under test here."""

    async def sweep_document(self, ctx: ExecutionContext, document_id: str) -> int:
        return 0
