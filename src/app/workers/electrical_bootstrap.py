"""Per-process composition root for ``worker-electrical`` (WireTwin CMP-09, LLD 3.3.15).

A file of its own rather than more lines in ``bootstrap.py`` (already 2,000+ lines): the shape is
``build_memory_worker``'s, applied to ``stream.electrical``. ``app.workers`` is a composition
root for its process (``.importlinter``, contract 6 excludes it), so this module may name
``app.infrastructure`` and several modules at once.

Role ``document`` subscribes ``cg.electrical.document`` with three handlers:

* ``electrical.document.registered.v1`` and ``electrical.job.requested.v1`` ->
  ``InspectDocument.run`` (STG-01). The use case opens its own short transactions and starts the
  last one with the DD-09 claim, so a duplicate delivery changes nothing;
* ``electrical.document.purged.v1`` -> ``SweepPurgedDocument.execute`` (phase 2 of a purge): one
  transaction that claims, then sweeps the document's rows.

``stale_idle_ms`` is at least twice the job lease, so a sibling never reclaims the message of an
inspection that is simply long; if one did, the job's lease would refuse the second run
(``INC1-D-9``). The DLQ watch is on in this role, the only one in INC-1.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from redis.asyncio import Redis

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.lifecycle import Disposable
from app.framework.di.storage_binding import bind_minio
from app.framework.di.storage_handle import StorageHandle
from app.framework.di.vault_binding import build_vault
from app.framework.observability import Heartbeat, build_heartbeat, configure_logging, get_logger
from app.framework.ports.task_ledger import TaskLedger
from app.framework.types import Json, Uuid
from app.infrastructure.cache.redis_cache import blocking_read_timeout_s, create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.engine import EventHandler, StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from app.infrastructure.monitoring.task_ledger import RedisTaskLedger
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.electrical.drawings.adapters.ele_parse_client import EleParseClient
from app.modules.electrical.drawings.adapters.rule_files import PackagedRuleSource
from app.modules.electrical.drawings.adapters.source_stager import StreamingSourceStager
from app.modules.electrical.drawings.adapters.sql_repository import (
    SqlCharacterizationRepository,
    SqlDocumentRepository,
    SqlDrawingsSweep,
    SqlJobRepository,
    SqlPageRepository,
    SqlProfileRepository,
)
from app.modules.electrical.drawings.application.inspection import InspectDocument
from app.modules.electrical.drawings.application.purge import SweepPurgedDocument
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import FilesQueryService
from app.workers.bootstrap import _consumer_name, _worker_batch_count, _worker_db

_logger = get_logger(__name__)

CG_ELECTRICAL_DOCUMENT = "cg.electrical.document"
STREAM_ELECTRICAL = "stream.electrical"
ROLE_DOCUMENT = "document"

EV_REGISTERED = "electrical.document.registered.v1"
EV_REQUESTED = "electrical.job.requested.v1"
EV_PURGED = "electrical.document.purged.v1"


class GroupLedger:
    """``SqlProcessedEventLedger`` with the group closed over, the shape
    ``drawings.ports.ledger.ProcessedEvents`` asks for."""

    def __init__(self, ledger: SqlProcessedEventLedger, group: str) -> None:
        self._ledger = ledger
        self._group = group

    async def claim(self, ctx: ExecutionContext, *, event_id: Uuid) -> bool:
        return await self._ledger.claim(ctx, consumer_group=self._group, event_id=event_id)


def electrical_stale_idle_ms(consumer_stale_idle_s: float, job_lease_s: int) -> int:
    """``max(consumer_stale_idle_s, 2 x job_lease_s)`` in milliseconds (LLD 3.3.15)."""
    return int(max(consumer_stale_idle_s, 2 * job_lease_s) * 1000)


def build_electrical_inspect_handler(inspect: InspectDocument) -> EventHandler:
    """``registered`` / ``job.requested`` -> ``InspectDocument.run`` (STG-01)."""

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await inspect.run(ctx, job_id=envelope["data"]["job_id"], event_id=envelope["id"])

    return _handle


def build_electrical_sweep_handler(sweep: SweepPurgedDocument) -> EventHandler:
    """``document.purged`` -> ``SweepPurgedDocument.execute`` (phase 2)."""

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        await sweep.execute(ctx, envelope["data"]["document_id"], event_id=envelope["id"])

    return _handle


def build_electrical_worker(
    *,
    redis_client: Redis,
    roles: frozenset[str],
    inspect: InspectDocument,
    sweep: SweepPurgedDocument,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
    heartbeat: Heartbeat | None = None,
    sweep_interval_s: float = 0.0,
    stale_idle_ms: int = 0,
    dlq_watch_interval_s: float = 0.0,
    dlq_watch_ledger: TaskLedger | None = None,
    dlq_watch_max_runtime_s: float = 0.0,
    concurrency: int = 1,
    drain_timeout_s: float = 0.0,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the ``document`` role's single ``stream.electrical`` / ``cg.electrical.document``
    subscription. Every dependency is a plain parameter; a role INC-1 does not know yields no
    subscription (the settings validate the names before this is reached)."""
    subscriptions: list[Subscription] = []
    if ROLE_DOCUMENT in roles:
        inspect_handler = build_electrical_inspect_handler(inspect)
        subscriptions.append(
            Subscription(
                stream=STREAM_ELECTRICAL,
                group=CG_ELECTRICAL_DOCUMENT,
                handlers={
                    EV_REGISTERED: inspect_handler,
                    EV_REQUESTED: inspect_handler,
                    EV_PURGED: build_electrical_sweep_handler(sweep),
                },
            )
        )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
        heartbeat=heartbeat,
        sweep_interval_s=sweep_interval_s,
        stale_idle_ms=stale_idle_ms,
        dlq_watch_interval_s=dlq_watch_interval_s,
        dlq_watch_ledger=dlq_watch_ledger,
        dlq_watch_max_runtime_s=dlq_watch_max_runtime_s,
        concurrency=concurrency,
        drain_timeout_s=drain_timeout_s,
    )
    return consumer, subscriptions


async def build_electrical_worker_from_env() -> tuple[
    StreamConsumer, list[Subscription], list[Disposable]
]:
    """Build ``worker-electrical`` as its process runs it.

    MinIO is bound through the same ``build_vault`` + ``bind_minio`` pair every worker uses (no
    new secret: the worker reads the credential ``worker-knowledge`` reads). ele-parse is reached
    over ``ELE_PARSE_URL`` on the internal network. Staged files older than twice the job lease
    are deleted at start (``INC1-D-20``): they belong to a worker that died.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    electrical = settings.electrical
    _logger.info("electrical_worker.bootstrap_initialized", extra={"app_env": settings.app_env})

    engine = create_engine(_worker_db(settings))
    tenant_session = TenantSessionFactory(create_sessionmaker(engine))
    documents = SqlDocumentRepository(tenant_session)
    jobs = SqlJobRepository(tenant_session)
    pages = SqlPageRepository(tenant_session)
    ledger = GroupLedger(SqlProcessedEventLedger(tenant_session), CG_ELECTRICAL_DOCUMENT)
    outbox = SqlEventOutbox(tenant_session)

    vault_client, secrets, _ = build_vault(settings)
    storage = StorageHandle()
    await bind_minio(storage, secrets, settings.minio)

    ele_parse_http = httpx.AsyncClient(base_url=electrical.ele_parse_url)
    stager = StreamingSourceStager(
        storage, Path(electrical.stage_dir), chunk_bytes=electrical.stage_chunk_bytes
    )
    removed = await stager.remove_older_than(2 * electrical.job_lease_s)
    if removed:
        _logger.info("electrical_worker.stage_swept", extra={"removed": removed})

    redis_client = create_redis_client(
        settings.redis,
        read_timeout_s=blocking_read_timeout_s(settings.events.consumer_block_ms),
    )
    heartbeat = build_heartbeat(settings.health.heartbeat_dir, "electrical")

    inspect = InspectDocument(
        documents,
        jobs,
        pages,
        SqlProfileRepository(tenant_session),
        SqlCharacterizationRepository(tenant_session),
        FilesQueryService(SqlFileRepository(tenant_session)),
        stager,
        EleParseClient(ele_parse_http, timeout_s=electrical.ele_parse_timeout_s),
        PackagedRuleSource(),
        ledger,
        outbox,
        tenant_session,
        heartbeat,
        electrical,
    )
    sweep = SweepPurgedDocument(SqlDrawingsSweep(tenant_session), ledger, tenant_session)

    consumer, subscriptions = build_electrical_worker(
        redis_client=redis_client,
        roles=frozenset(electrical.worker_roles),
        inspect=inspect,
        sweep=sweep,
        consumer_name=_consumer_name("electrical"),
        block_ms=settings.events.consumer_block_ms,
        batch_count=_worker_batch_count(settings),
        max_deliveries=settings.events.max_retries_before_dlq,
        heartbeat=heartbeat,
        sweep_interval_s=settings.events.consumer_sweep_interval_s,
        stale_idle_ms=electrical_stale_idle_ms(
            settings.events.consumer_stale_idle_s, electrical.job_lease_s
        ),
        dlq_watch_interval_s=settings.events.dlq_watch_interval_s,
        dlq_watch_ledger=RedisTaskLedger(redis_client),
        dlq_watch_max_runtime_s=float(electrical.job_lease_s),
        concurrency=settings.events.worker_concurrency,
        drain_timeout_s=settings.events.worker_drain_timeout_s,
    )

    async def _close_vault() -> None:
        await asyncio.to_thread(vault_client.adapter.close)

    disposables: list[Disposable] = [
        engine.dispose,
        ele_parse_http.aclose,
        redis_client.aclose,
        _close_vault,
    ]
    return consumer, subscriptions, disposables
