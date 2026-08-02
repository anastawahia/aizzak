"""Live-Postgres + live-Redis + live-MinIO test for the media worker's REAL
run handler (5.1-ج) driving the REAL ``WorkerMediaGenerator`` (step 19 of
``deferred-adapters-plan.md``).

The ``FakeMediaGenerator`` this module used to inject is gone: the adapter
``media/ports/generation.py`` deferred to "Phase 5" now exists, and a fake
standing in for a shipped adapter proves progressively less as the real one
gains behaviour. What remains stubbed is ONLY the third-party HTTP boundary
(``httpx.MockTransport`` under the adapter's own factory) -- everything on
this side of it is production code: ``OpenAIImage``, the real
``SettingsProviderResolver`` over a real routing table, the real
``RegisterUpload``/``CompleteUpload`` against live Postgres under RLS, and
the real ``MinioStorage``.

Same chain as ``test_e2e_outbox_to_worker.py``: a real ``RequestMedia`` call
persists a queued job (``app_rw``), its mapped ``media.job.requested.v1``
event is appended (retargeted at a UNIQUE test stream, R6) → the real relay
(5.1-ب, ``outbox_relay`` role) → ``build_media_run_handler`` (``app_rw``)
runs the generator and persists the job's terminal state + its mapped
follow-on event, atomically (D5's own documented shape).

**And it proves the ⚠️ decision of step 19 where it actually matters:** after
a full successful generation there is NO ``files.file.uploaded.v1`` row in
the outbox for the produced file. That event is what wakes the ``knowledge``
worker, so its absence is what keeps agent-generated images out of the
workspace's knowledge base -- a property no unit test can establish, because
"nothing was written to a table" is only meaningful against the real table.
"""

from __future__ import annotations

import base64
import contextlib
import json
from dataclasses import dataclass, replace

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.providers.resolver import SettingsProviderResolver
from app.framework.settings.settings import DatabaseSettings, Limits
from app.infrastructure.ai_providers.image.external_image import (
    OpenAIImage,
    create_openai_image_http_client,
)
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.storage.minio_storage import MinioStorage
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import CompleteUpload, RegisterUpload
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.event_mapping import to_outbox_record
from app.modules.media.application.use_cases import RequestMedia
from app.workers.bootstrap import build_media_run_handler
from app.workers.media_generation import WorkerMediaGenerator
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis, pytest.mark.live_minio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"live-test-pixels"
_ROUTED_MODEL = "gpt-image-1"
_PROMPT = "a cat wearing sunglasses"


@dataclass(frozen=True, slots=True)
class _StubKey:
    """Structurally a ``ResolvedKeyView`` -- the one attribute the resolver
    reads. Vault/credentials have their own live suites; wiring them in here
    would make this test skip for a reason that has nothing to do with what
    it proves."""

    api_key: str


class _StubKeyResolver:
    async def resolve(self, ctx: ExecutionContext, provider: str) -> _StubKey:
        return _StubKey(api_key="sk-live-test")


class _OpenAIImageStub:
    """The ONLY stubbed boundary: OpenAI's HTTP endpoint. Records every
    request so the test can assert what the real adapter actually sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json={"created": 1, "data": [{"b64_json": base64.b64encode(_PNG).decode()}]}
        )


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


async def _read_outbox_as_owner(
    owner_dsn: str, *, aggregate_id: str, event_type: str
) -> list[RowMapping]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM platform.outbox WHERE aggregate_id = :id AND event_type = :et"),
                {"id": aggregate_id, "et": event_type},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


def _build_generator(
    files: SqlFileRepository, storage: MinioStorage
) -> tuple[WorkerMediaGenerator, _OpenAIImageStub, httpx.AsyncClient]:
    """The production generator over the production resolver -- stubbed at
    the HTTP boundary alone. Returns the stub and the client so the test can
    assert what was sent and close the pool afterwards."""
    endpoint = _OpenAIImageStub()
    image_http = create_openai_image_http_client(
        timeout_s=30.0, transport=httpx.MockTransport(endpoint)
    )
    adapter = OpenAIImage(image_http)
    generator = WorkerMediaGenerator(
        SettingsProviderResolver(
            routing={"image": {"default": {"provider": adapter.provider, "model": _ROUTED_MODEL}}},
            llm_providers={},
            embedding_providers={},
            image_providers={adapter.provider: adapter},
            key_resolver=_StubKeyResolver(),
            keyless_providers=frozenset(),
        ),
        RegisterUpload(files, Limits()),
        CompleteUpload(files),
        storage,
    )
    return generator, endpoint, image_http


def _assert_what_the_adapter_sent(endpoint: _OpenAIImageStub) -> None:
    """Exactly one call, carrying the ROUTED model, the job's prompt, the
    job's dimensions as OpenAI's ``WxH`` string, and the resolved key as a
    per-request header."""
    (sent,) = endpoint.requests
    assert str(sent.url).endswith("/images/generations")
    assert sent.headers["authorization"] == "Bearer sk-live-test"
    assert json.loads(sent.content) == {
        "model": _ROUTED_MODEL,
        "prompt": _PROMPT,
        "size": "64x64",
        "n": 1,
    }


async def _assert_file_landed(
    files: SqlFileRepository, storage: MinioStorage, *, ctx: ExecutionContext, file_id: str
) -> str:
    """The row is ``ready`` in Postgres under RLS AND the bytes are in the
    live bucket under the key that row names. Returns the key so the caller
    can sweep it."""
    row = await files.get(ctx, file_id)
    assert row is not None
    assert row.status.value == "ready"
    assert row.content_type.value == "image/png"
    assert row.size_bytes == len(_PNG)
    assert await storage.get(row.storage_key.value) == _PNG
    return row.storage_key.value


@pytest.mark.anyio
async def test_a_real_requested_job_flows_through_the_relay_to_the_real_generator(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    minio_storage: MinioStorage,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    ctx = _ctx(new_uuid7())

    jobs = SqlMediaJobRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    files = SqlFileRepository(tenant_session)
    generator, endpoint, image_http = _build_generator(files, minio_storage)

    handler = build_media_run_handler(
        jobs,
        generator,
        outbox,
        tenant_session,
        SqlProcessedEventLedger(tenant_session),
        # The ledger key must be THE group that owns this subscription
        # (production builders feed both from one constant); this test's
        # group is unique per run, so it is passed explicitly.
        consumer_group=group,
    )
    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.requested.v1": handler}
    )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="e2e-media-test",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )

    storage_key: str | None = None
    try:
        # 0) setup() FIRST -- ensure_group's own `$` (tail-start) semantics
        #    mean a group created AFTER an entry already exists on the
        #    stream never sees that entry (a real worker's setup() always
        #    runs at process boot, before it consumes anything).
        await consumer.setup([subscription])

        # 1) The real RequestMedia use-case persists a queued job (app_rw).
        job, events = await RequestMedia(jobs, Limits()).execute(
            ctx,
            agent_key="image-agent",
            kind="image",
            prompt=_PROMPT,
            params={"width": 64, "height": 64},
        )
        # 2) Its mapped event, retargeted at a UNIQUE test stream (R6) --
        #    the real event_mapping.py shape, real data, only .stream differs.
        record = replace(to_outbox_record(ctx, events[0]), stream=stream)
        await outbox.append(ctx, [record])

        # 3) The real relay (5.1-ب), as the real outbox_relay role.
        relay = OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=10,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        )
        published = await relay.run_once()
        assert published == 1

        # 4) The real run handler + the real generator, on app_rw.
        handled = await consumer.run_once([subscription])
        assert handled == 1

        # -- the job ------------------------------------------------------- #
        stored = await jobs.get(ctx, job.id)
        assert stored is not None
        assert stored.status.value == "succeeded", stored.error
        assert stored.result_file_id is not None

        _assert_what_the_adapter_sent(endpoint)

        # -- the file, end to end through `files` + live MinIO -------------- #
        storage_key = await _assert_file_landed(
            files, minio_storage, ctx=ctx, file_id=stored.result_file_id
        )

        # -- ⚠️ the point of this test (module docstring) ------------------- #
        # The generated file MUST NOT be announced: `files.file.uploaded.v1`
        # is what wakes the knowledge worker, and indexing an agent's own
        # output would let it later retrieve and cite what it invented.
        uploaded = await _read_outbox_as_owner(
            live_db.owner,
            aggregate_id=stored.result_file_id,
            event_type="files.file.uploaded.v1",
        )
        assert uploaded == []

        # ...while the media follow-on event IS published, so the absence
        # above is a deliberate exclusion and not a dead outbox.
        follow_on = await _read_outbox_as_owner(
            live_db.owner, aggregate_id=job.id, event_type="media.job.generated.v1"
        )
        assert len(follow_on) == 1
        assert str(follow_on[0]["workspace_id"]) == ctx.workspace_id

        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        second = await consumer.run_once([subscription])
        assert second == 0
    finally:
        await image_http.aclose()
        if storage_key is not None:
            with contextlib.suppress(Exception):
                await minio_storage.delete(storage_key)
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)
