"""Per-process composition root for the ``outbox_relay`` worker (5.1-ب ·
08-local-runbook §4 · 10-code-standards §4).

``app.workers`` is deliberately EXCLUDED from ``.importlinter`` contract 6
("infra-only-via-composition-root") — the comment on that contract names
worker entrypoints as "composition roots for their process", plural,
alongside ``framework.di.composition_root``: each worker is its own
standalone OS process (Docker Compose's ``outbox-relay``/``worker`` services,
08 §2/§4), so each gets to wire its OWN small dependency graph rather than
sharing the API's. This module is that graph for the relay specifically.

**Not ``CompositionRoot.from_env()``.** That classmethod boots the ENTIRE
agent runtime (LLM providers, the plugin loader, the orchestrator, ...) and
hard-fails without ``FIREBASE_PROJECT_ID`` (``composition_root.py``'s own
docstring) — none of which the relay needs or should pay the cost of
booting. This module instead reuses only the two pieces of the existing
config/adapter layer the relay actually needs: ``load_settings()``
(``infrastructure/config/env_settings.py`` — the platform's ONE ``.env``
reader, 10 §9: never re-implemented per-process) and the SAME engine/
sessionmaker/Redis-client factories every other adapter is built from
(``infrastructure/persistence/database.py``, ``infrastructure/cache/
redis_cache.py``).

**``DATABASE_URL`` is per-process, not a new setting.** ``load_settings()``
reads the SAME env key every process reads — what differs is the VALUE each
process's own environment supplies it. In production (08 §2's Compose
topology) the ``outbox-relay`` service's compose/deploy config points
``DATABASE_URL`` at credentials for the ``outbox_relay`` Postgres role
(SELECT/UPDATE on ``platform.outbox`` only — ``persistence/outbox.py``'s
module docstring), while the ``app``/``worker`` services' ``DATABASE_URL``
points at ``app_rw`` (INSERT-only on that same table). Nothing in this
module chooses that value; it is an ops/deploy-time concern (01 §6: grants
and role wiring are a runbook step, not code), exactly like ``app_rw`` vs.
``aizzak_owner`` already is for the live test harness.

**Redis reuses ``settings.redis`` — the SAME setting ``CacheProvider`` binds
to**, not a second URL: there is one Redis deployment in this topology (08
§2's service table), and the relay's Streams traffic and the API's cache
traffic are simply different USES of the one client-configuration surface,
not different servers.

**Small pool.** The relay is a single process (D-26) issuing at most one
in-flight statement at a time (``SqlOutboxRelayStore``'s own docstring: three
short, sequential, non-overlapping transactions per cycle) — the default
``DatabaseSettings.pool_size``/``max_overflow`` (10/20) are sized for the API
server's concurrent-request fan-out and would be pure waste here.

**``max_backoff_ms`` has no ``Settings`` field.** The 5.1-ب design brief adds
exactly one new field, ``EventSettings.outbox_relay_batch_size`` — the relay
loop's backoff ceiling was not asked for as operator-tunable configuration,
so it stays a wiring-time constant here, the same footing
``infrastructure/cache/redis_cache.py``'s ``_SOCKET_TIMEOUT_S`` /
``_CONNECT_TIMEOUT_S`` already stand on.

---

**5.1-ج extends this module with three more per-process composition roots**
— one per Streams worker (``knowledge_worker``/``media_worker``/
``memory_worker``, 08 §4) — over the SAME reused pieces (``load_settings``,
the engine/sessionmaker factories, ``create_redis_client``) plus the new
generic engine (``infrastructure/messaging/consumers/engine.py``). Every
worker gets exactly TWO functions:

* ``build_<name>_worker(...)`` takes every dependency as a PARAMETER
  (repositories, the Redis client, the blocked ports below) — this is the
  form ``tests/integration/test_e2e_outbox_to_worker.py``/
  ``test_media_worker_live.py`` call directly with real-or-fake
  dependencies, and it never touches ``.env``/env vars at all.
* ``build_<name>_worker_from_env()`` wires the process path (``load_settings``
  → real adapters), mirroring ``build_relay_from_env`` above.

**Handler closures live HERE, not in the engine.** ``infrastructure/
messaging/consumers/`` must import NO ``app.modules.*`` (that module's own
docstring) — so each worker's handler functions (which DO need
module-specific use-cases/repositories/event-mappings) are built in this
file, a per-process composition root exactly like the relay's, and handed to
the module-agnostic ``StreamConsumer``/``Subscription`` as opaque closures.

**Honest-failure rule for blocked dependencies (the design brief's explicit
instruction, not a shortcut taken here).** ONE seam still has no real
adapter: ``MediaGenerator`` (``media/ports/generation.py``'s own docstring:
"the deferred, Phase-5 infra adapter"). Rather than silently wiring a fake
in its place (fakes exist ONLY in tests), ``build_media_worker_from_env``
builds everything that genuinely IS real from env — repositories, the
Outbox, the unit of work, the Redis client — and then RAISES a clear
``AppError`` naming exactly what is still missing.
``build_<name>_worker`` itself is never blocked: it is a pure wiring
function over whatever the caller hands it, which is precisely how the live
e2e tests exercise the REAL register/run/index handlers today without
waiting on that gap.

**Two of the three seams the rule used to cover are now closed, and this
paragraph shrinks with them.** ``EmbeddingProvider`` went first, in 2.10
(``infrastructure/ai_providers/embedding/external_embedding.py`` used to be
0 bytes; ``composition_root.py``'s own module docstring records the
history): ``build_memory_worker_from_env`` builds the REAL
``ExternalEmbeddingProvider`` and returns a fully working consumer.
``DocumentContentResolver`` went second, in steps 15-16 of
``deferred-adapters-plan.md`` (``docs/log/3.98.md`` · ``docs/log/3.100.md``):
``build_knowledge_worker_from_env`` binds MinIO through the same
``vault_binding``/``storage_binding`` pair ``CompositionRoot`` uses, and
composes ``WorkerDocumentContentResolver`` (``workers/content_resolver.py``)
over storage + the 3.k1 parser dispatch table + a ``SettingsProviderResolver``
— so it no longer raises either. **Only ``media`` still fails honestly.**

---

**5.2-أ makes every handler closure idempotent (DD-09 · 04 §3).** Each
handler claims ``(consumer_group, event_id)`` in ``platform.processed_events``
as the FIRST statement inside its effect transaction (``uow.begin``), through
the injected ``ProcessedEventLedger`` seam below; a ``False`` claim is a
duplicate delivery — the handler returns cleanly WITHOUT touching anything,
and the engine ``XACK``\\ s it like any success («تكرار = تجاهُل + XACK»).
The claim goes FIRST so a duplicate skips the effect instead of discovering
the conflict after half-performing it; it shares the effect's transaction so
a crash mid-handler rolls the claim back WITH the effect — redelivery then
finds a clean slate. For the ``run``/``finalize``-split use-cases
(``IndexRegisteredDocument``/``RunMediaJob``/``IndexMemoryItem``, 5.2-أ),
the same ``uow.begin`` block also closes 5.1-ج's documented D5 terminal
window: terminal status + follow-on outbox rows + the claim now land in ONE
transaction, so «terminal state without its event» is no longer a reachable
crash outcome. The redelivery-no-op path (already-terminal aggregate /
already-indexed item) deliberately opens NO transaction and claims NOTHING —
the use-cases' own guards already made a second run free, and a claim row
for a no-op would spend a write to record nothing.
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Protocol

from redis.asyncio import Redis

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.lifecycle import Disposable
from app.framework.di.storage_binding import bind_minio
from app.framework.di.storage_handle import StorageHandle
from app.framework.di.vault_binding import build_vault
from app.framework.errors import AppError, UnsupportedTypeError, ValidationError
from app.framework.observability import configure_logging, get_logger
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.ports.vector_store import HybridVectorStore, VectorStore
from app.framework.providers.resolver import SettingsProviderResolver
from app.framework.settings.settings import DatabaseSettings
from app.framework.types import Json, Uuid
from app.infrastructure.ai_providers.embedding.external_embedding import (
    ExternalEmbeddingProvider,
    create_embedding_http_client,
)
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.engine import EventHandler, StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client
from app.modules.credentials.adapters.sql_repository import SqlCredentialRepository
from app.modules.credentials.application.use_cases import ResolveCredential
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.event_mapping import (
    to_outbox_record as _knowledge_to_outbox_record,
)
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.use_cases import (
    IndexRegisteredDocument,
    RegisterDocumentFromFile,
)
from app.modules.knowledge.ports.content_extractor import ParsedDocument
from app.modules.knowledge.ports.repository import DocumentRepository
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.event_mapping import to_outbox_record as _media_to_outbox_record
from app.modules.media.application.use_cases import RunMediaJob
from app.modules.media.ports.generation import MediaGenerator
from app.modules.media.ports.repository import MediaJobRepository
from app.modules.memory.adapters.sql_repository import SqlMemoryRepository
from app.modules.memory.application.use_cases import IndexMemoryItem
from app.modules.memory.ports.repository import MemoryRepository
from app.workers.content_resolver import WorkerDocumentContentResolver

_logger = get_logger(__name__)

# Sequential single-process workload -- see the module docstring's "Small
# pool" paragraph. `max_overflow=0`: an accidental second concurrent
# checkout is a bug worth surfacing as a pool-exhaustion error, not a
# capacity this process is ever meant to need.
_RELAY_POOL_SIZE = 2
_RELAY_MAX_OVERFLOW = 0

# See the module docstring's "`max_backoff_ms` has no `Settings` field"
# paragraph -- 30s is a conventional outer ceiling for exponential backoff:
# long enough that a flapping dependency is not hammered, short enough that
# a recovered Redis/Postgres is noticed well within any reasonable SLO.
_MAX_BACKOFF_MS = 30_000

# `Disposable` -- the resource-teardown thunk every builder below hands back --
# was DEFINED here until 3.79, when the API server's lifespan needed the
# identical seam. It now lives in `framework/di/lifecycle.py` and is imported
# above: one alias, two composition roots, rather than two structurally
# identical aliases free to drift. Nothing else changed: the builders below still
# return `list[Disposable]`, and each worker entrypoint still closes what it
# was handed.


def build_relay_from_env() -> tuple[OutboxRelay, list[Disposable]]:
    """Build one ``OutboxRelay`` wired exactly as the ``outbox_relay``
    process runs it, plus the resources ``outbox_relay.py``'s entrypoint
    must close on shutdown (in no particular order -- disposing an engine
    and closing a Redis client are independent of each other).
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    _logger.info(
        "outbox_relay.bootstrap_initialized",
        extra={
            "app_env": settings.app_env,
            "batch_size": settings.events.outbox_relay_batch_size,
            "poll_interval_ms": settings.events.outbox_poll_interval_ms,
        },
    )

    relay_db = DatabaseSettings(
        url=settings.database.url,
        pool_size=_RELAY_POOL_SIZE,
        max_overflow=_RELAY_MAX_OVERFLOW,
    )
    engine = create_engine(relay_db)
    sessionmaker = create_sessionmaker(engine)
    store = SqlOutboxRelayStore(sessionmaker)

    redis_client = create_redis_client(settings.redis)
    # The relay is the ONLY producer on these streams (02 §1.8), so this is
    # the single place the retention bound can be applied at all (7.3).
    publisher = RedisStreamsPublisher(redis_client, maxlen=settings.events.stream_maxlen)

    relay = OutboxRelay(
        store,
        publisher,
        batch_size=settings.events.outbox_relay_batch_size,
        poll_interval_ms=settings.events.outbox_poll_interval_ms,
        max_backoff_ms=_MAX_BACKOFF_MS,
    )

    disposables: list[Disposable] = [engine.dispose, redis_client.aclose]
    return relay, disposables


# --------------------------------------------------------------------------- #
# Shared worker-bootstrap plumbing (5.1-ج)                                    #
# --------------------------------------------------------------------------- #
# Same reasoning as `_RELAY_POOL_SIZE` above, applied to each of the three
# per-stream workers instead of the relay: one worker process, one in-flight
# statement at a time (`StreamConsumer.run_once` dispatches messages
# sequentially, never concurrently), so the API server's concurrent-
# request pool sizing would be pure waste here too.
_WORKER_POOL_SIZE = 2
_WORKER_MAX_OVERFLOW = 0

# 04 §2's consumer-group topology, named once: each constant feeds BOTH the
# group's `Subscription`s and the DD-09 ledger claims its handlers write
# (5.2-أ) -- a group name that drifted between the two would silently split
# one group's idempotency history in `platform.processed_events`.
_CG_KNOWLEDGE = "cg.knowledge"
_CG_MEDIA = "cg.media"
_CG_MEMORY = "cg.memory"

# Which adapters take no credential at all -- structural, never user-editable
# configuration (2.9 decision 6). Deliberately the SAME pair
# `CompositionRoot.from_env` passes its own `SettingsProviderResolver`
# (`composition_root.py`, `keyless_providers=`): step 16 made the knowledge
# worker a second builder of that resolver, and the two must agree, or a
# workspace that resolves fine through the API would be told at INDEX time
# that it has no credential for a provider that needs none. Drift here fails
# loudly (`credentials.none_available`), never silently.
_KEYLESS_PROVIDERS = frozenset({"ollama", "embedding-local"})


class ProcessedEventLedger(Protocol):
    """The DD-09 idempotency claim, as each handler closure consumes it
    (5.2-أ) -- a worker-composition-only seam like ``DocumentContentResolver``
    below, not a framework/module port: its only real implementation is
    ``SqlProcessedEventLedger`` (``infrastructure/persistence/
    processed_events.py``, whose docstring owns the semantics), and its only
    callers are the handler closures this file builds. Tests inject fakes.
    """

    async def claim(
        self, ctx: ExecutionContext, *, consumer_group: str, event_id: Uuid
    ) -> bool: ...


def _worker_db(db: DatabaseSettings) -> DatabaseSettings:
    """Same ``DATABASE_URL``, a worker-sized pool -- the ``build_relay_from_env``
    precedent (its own ``relay_db`` local), applied to each Streams worker."""
    return DatabaseSettings(
        url=db.url, pool_size=_WORKER_POOL_SIZE, max_overflow=_WORKER_MAX_OVERFLOW
    )


def _embedding_routing(routing: Json) -> Json:
    """``PROVIDER_ROUTING`` with the ``llm`` namespace dropped -- what the
    knowledge worker's ``SettingsProviderResolver`` is given (step 16; the
    factory's docstring argues the narrowing).

    Written as "drop ``llm``" rather than "keep ``embedding``" on purpose:
    an unknown/misspelled namespace still reaches ``_parse_routing``, which
    refuses construction naming it. Keeping only a known key would swallow
    that typo and boot a worker on a routing table its operator believes
    says something else.
    """
    return {namespace: entry for namespace, entry in routing.items() if namespace != "llm"}


def _consumer_name(prefix: str) -> str:
    """A per-process Streams consumer identity: ``<prefix>.<hostname>.<pid>``
    -- deterministic and inspectable (which worker instance owns a given
    pending entry is visible straight from ``XPENDING``'s own consumer-name
    field), unique per running process without any coordination service."""
    return f"{prefix}.{socket.gethostname()}.{os.getpid()}"


# --------------------------------------------------------------------------- #
# knowledge_worker                                                            #
# --------------------------------------------------------------------------- #
class DocumentContentResolver(Protocol):
    """Resolves everything ``IndexRegisteredDocument`` needs beyond
    ``document_id``: the file's bytes fetched + parsed into a
    ``ParsedDocument``, plus the embedding model/key to index it with (the
    ``knowledge.EmbeddingResolver`` seam's shape, ``ports/retrieval.py``,
    generalized to cover fetch + parse too, not just model/key resolution).

    A worker-composition-only seam -- not a module port, not a framework
    port -- and it STAYS one now that it has a real adapter, for a reason
    that outlived the gap: its implementation
    (``WorkerDocumentContentResolver``, ``workers/content_resolver.py``,
    step 16 of ``deferred-adapters-plan.md``) joins ``files`` and
    ``knowledge``, which the ``modules-independent`` import contract forbids
    inside ``app.modules.*``. ``app.workers`` is where that composition is
    allowed to live, so the Protocol belongs beside its callers rather than
    in ``framework/ports/``.

    Steps 15-16 built it out of pieces that already existed separately:
    ``StorageHandle``'s MinIO adapter bound from env exactly the way
    ``CompositionRoot.connect_storage`` binds it (step 15), the 3.k1 parser
    dispatch table (``DocumentContentExtractor``), and the same
    ``SettingsProviderResolver`` the API resolves the embedding route
    through. Tests still inject fakes -- but only to isolate a branch, not
    for want of a real implementation.
    """

    async def resolve(
        self, ctx: ExecutionContext, *, file_id: Uuid
    ) -> tuple[ParsedDocument, str, str]: ...


def build_knowledge_register_handler(
    documents: DocumentRepository,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    *,
    consumer_group: str = _CG_KNOWLEDGE,
) -> EventHandler:
    """``files.file.uploaded.v1`` -> ``RegisterDocumentFromFile`` -> a
    ``pending`` ``Document`` row + its mapped ``knowledge.document.
    registered.v1`` outbox row, ATOMICALLY: the DD-09 claim (5.2-أ, module
    docstring), the aggregate write (inside ``RegisterDocumentFromFile``,
    through ``documents``), and the outbox append below all resolve their
    session through the SAME ``uow.begin(ctx)`` block -- 04 §3.1's "الأثر
    النطاقي + صف Outbox في نفس المعاملة" guarantee, applied to a
    WORKER-originated write for the first time (every producer service so
    far, ``MediaRequestService``/``RememberInteractionService``, has been
    request-scoped, not event-driven).

    The claim matters MOST here: registration is non-idempotent BY DESIGN
    (INV-K3 -- a re-upload mints a brand-new document), so unlike the three
    ``run``/``finalize`` handlers below there is no natural aggregate guard;
    without the claim, every redelivered ``file.uploaded`` would mint a
    duplicate document (exactly the hazard 5.1-ج's R3 froze production
    publishing over)."""
    register = RegisterDocumentFromFile(documents)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        file_id = envelope["data"]["file_id"]
        event_id: str = envelope["id"]
        async with uow.begin(ctx):
            if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                return  # Duplicate delivery -- clean return, the engine XACKs.
            _, events = await register.execute(ctx, file_id=file_id)
            await outbox.append(ctx, [_knowledge_to_outbox_record(ctx, event) for event in events])

    return _handle


def build_knowledge_index_handler(
    documents: DocumentRepository,
    pipeline: IndexDocument,
    content: DocumentContentResolver,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    *,
    consumer_group: str = _CG_KNOWLEDGE,
) -> EventHandler:
    """``knowledge.document.registered.v1`` -> ``IndexRegisteredDocument.run``
    (the ``pending → indexing`` claim + the pipeline's embedding calls, all
    OUTSIDE any transaction here — R2) -> ONE ``uow.begin`` block holding the
    DD-09 claim + ``IndexRegisteredDocument.finalize`` (chunks + terminal
    status) + the follow-on ``knowledge.document.indexed.v1``/
    ``...indexing_failed.v1`` outbox append.

    5.1-ج shipped this handler with a documented D5 window — terminal status
    and its follow-on event in two separate transactions, so a crash between
    them lost the event forever (the terminal guard makes redelivery a
    no-op). The 5.2-أ ``run``/``finalize`` split closes it: everything after
    the external I/O now commits atomically or not at all. No transaction at
    all on the idempotent-redelivery no-op path (an already-INDEXED/FAILED
    document short-circuits in ``run``; opening a transaction to claim a
    no-op would spend a write to record nothing).

    **Step 16 adds the ``content.resolve`` failure branch** — the poison-pill
    gap (deferred-adapters-plan.md §1-ج). See the ``except`` clause's own
    comment for which failures are terminal and why the rest still escape.
    The terminal write it produces goes through the SAME ``finalize`` +
    claim + outbox append as every other outcome, so there stays exactly ONE
    path to a terminal document, not two."""
    index = IndexRegisteredDocument(documents, pipeline)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        data = envelope["data"]
        event_id: str = envelope["id"]
        try:
            parsed, model, api_key = await content.resolve(ctx, file_id=data["file_id"])
        except (UnsupportedTypeError, ValidationError) as exc:
            # Step 16 (§1-ج): a file this deployment cannot parse is a
            # TERMINAL fact about the file, not a transient fault -- and the
            # ONLY two error types that say so. Everything else raised out of
            # `resolve` (a Vault/MinIO/Postgres outage, a `NotFoundError`
            # for a file row that vanished) still escapes to the engine, is
            # redelivered, and eventually reaches the DLQ, which is correct:
            # those may succeed on the next try. These two never will, and
            # before this branch existed they took the same redelivery path
            # anyway -- so an ordinary user uploading an unparseable file
            # burned `max_deliveries` attempts and left their document
            # `pending` forever, with no failure event to explain it.
            attempt = await index.fail(ctx, document_id=data["document_id"], reason=str(exc))
        else:
            attempt = await index.run(
                ctx, document_id=data["document_id"], parsed=parsed, model=model, api_key=api_key
            )
        if attempt.is_redelivery_noop:
            return
        async with uow.begin(ctx):
            if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                return  # Duplicate delivery -- clean return, the engine XACKs.
            _, events = await index.finalize(ctx, attempt)
            await outbox.append(ctx, [_knowledge_to_outbox_record(ctx, event) for event in events])

    return _handle


def build_knowledge_worker(
    *,
    redis_client: Redis,
    documents: DocumentRepository,
    pipeline: IndexDocument,
    content_resolver: DocumentContentResolver,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the knowledge worker's TWO subscriptions under one ``cg.knowledge``
    consumer group (04 §4's binding table, `docs/log/3.45.md`'s recorded
    ``cg.knowledge``-also-on-``stream.knowledge`` sync gap): ``stream.files``
    for registration, ``stream.knowledge`` for indexing. Every dependency
    here is a plain parameter -- this function is never itself blocked by
    the honest-failure rule (``build_knowledge_worker_from_env``'s docstring)
    and is exactly what ``tests/integration/test_e2e_outbox_to_worker.py``
    calls directly with real Postgres-backed dependencies.
    """
    subscriptions = [
        Subscription(
            stream="stream.files",
            group=_CG_KNOWLEDGE,
            handlers={
                "files.file.uploaded.v1": build_knowledge_register_handler(
                    documents, outbox, uow, ledger
                )
            },
        ),
        Subscription(
            stream="stream.knowledge",
            group=_CG_KNOWLEDGE,
            handlers={
                "knowledge.document.registered.v1": build_knowledge_index_handler(
                    documents, pipeline, content_resolver, outbox, uow, ledger
                )
            },
        ),
    ]
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
    )
    return consumer, subscriptions


async def build_knowledge_worker_from_env() -> tuple[
    StreamConsumer, list[Subscription], list[Disposable]
]:
    """Build the knowledge worker exactly as the ``knowledge`` process runs
    it. **Nothing is blocked any more** (step 16 of
    ``deferred-adapters-plan.md``): the last seam this function used to raise
    over, ``DocumentContentResolver``, now has a real adapter --
    ``WorkerDocumentContentResolver`` (``workers/content_resolver.py``),
    composed here out of pieces that already existed separately.

    **``async def`` since step 15.** Binding MinIO needs an ``await`` (05
    §3's Vault read), and this entrypoint already runs inside
    ``asyncio.run`` (``knowledge_worker.py``'s own ``if __name__ ==
    "__main__":`` block), so the cost is one more ``await`` at the top, not
    a new event loop. **Rejected alternative: a ``startup=`` hook list**,
    the ``create_production_app``/``api/main.py`` pattern the API's lifespan
    uses to sequence several startup hooks (mount the WS router, sweep
    stale notify groups, start the hub's renewal loop, ...). That
    abstraction earns its keep there because there genuinely ARE several
    hooks to order. A worker has exactly ONE: there is no second startup
    hook to sequence against ``bind_minio``, so wrapping a list around a
    single ``await`` would be ceremony bought for nothing.

    Every dependency is real: ``documents``/``files``
    (``SqlDocumentRepository``/``SqlFileRepository``), ``outbox``
    (``SqlEventOutbox``), ``tenant_session`` (both the ``UnitOfWork`` and the
    RLS session provider), the Redis client/consumer identity, the full
    ``IndexDocument`` pipeline (``vectors``/``embeddings``, 2.10),
    ``storage`` -- a ``StorageHandle`` bound to a REAL MinIO adapter, built
    the exact same way ``CompositionRoot.connect_storage`` builds it
    (``framework/di/vault_binding.py::build_vault`` +
    ``framework/di/storage_binding.py::bind_minio``, step 15) -- and now the
    content resolver over all of them.

    **The embedding route is resolved, not assumed (step 16's carrying
    decision).** This worker builds the SAME ``SettingsProviderResolver`` +
    ``ResolveCredential`` pair ``CompositionRoot`` builds, over the SAME
    ``PROVIDER_ROUTING`` entry, rather than reading a model name straight out
    of ``settings.embedding_service`` the way ``build_memory_worker_from_env``
    does. The difference is a correctness argument, not a stylistic one:
    ``memory`` writes AND reads its own vectors through one process's
    settings, whereas ``knowledge`` is indexed HERE and queried by the API
    (``POST /search``). Two processes resolving the model two different ways
    is a config edit away from indexing into one vector space and searching
    another -- which produces no error at all, just silently empty or
    nonsensical retrieval.

    **Only the ``embedding`` namespace of the routing table is handed to that
    resolver.** ``_parse_routing`` refuses construction on any route whose
    provider has no wired adapter, and this process deliberately wires no LLM
    adapters -- it never makes an LLM call. Passing the whole table would
    force the worker to build (and hold open) OpenAI/Ollama clients purely to
    satisfy a parse of routes it will never read. Narrowing loses nothing
    that matters: the strictness the plan is buying is over the embedding
    route, the one both processes must agree on, and that half stays fully
    strict.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    _logger.info("knowledge_worker.bootstrap_initialized", extra={"app_env": settings.app_env})

    engine = create_engine(_worker_db(settings.database))
    sessionmaker = create_sessionmaker(engine)
    tenant_session = TenantSessionFactory(sessionmaker)
    documents = SqlDocumentRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    ledger = SqlProcessedEventLedger(tenant_session)

    qdrant_client = create_qdrant_client(settings.qdrant)
    vectors: HybridVectorStore = QdrantVectorStore(qdrant_client)
    embedding_http = create_embedding_http_client(settings.embedding_service)
    embeddings: EmbeddingProvider = ExternalEmbeddingProvider(
        embedding_http, settings.embedding_service
    )
    pipeline = IndexDocument(embeddings, vectors)

    # Step 15 -- the SAME Vault + MinIO wiring `CompositionRoot` uses, so
    # this worker's storage adapter is bound the identical way the API's is
    # (one secret-shape validation site, not two free to drift).
    vault_client, secrets, _ = build_vault(settings)
    storage = StorageHandle()
    await bind_minio(storage, secrets, settings.minio)

    # Step 16 -- the embedding route, resolved the SAME way the API resolves
    # it (see the docstring). `embedding_providers` is keyed by the adapter's
    # OWN `provider` attribute, never a literal (the `composition_root.py`
    # precedent), so the strict parse below proves the configured route
    # points at the very adapter `pipeline` above was built from.
    providers = SettingsProviderResolver(
        routing=_embedding_routing(settings.provider_routing),
        llm_providers={},
        embedding_providers={embeddings.provider: embeddings},
        key_resolver=ResolveCredential(SqlCredentialRepository(tenant_session), secrets),
        keyless_providers=_KEYLESS_PROVIDERS,
    )
    content_resolver = WorkerDocumentContentResolver(
        files, storage, DocumentContentExtractor(), providers
    )

    redis_client = create_redis_client(settings.redis)
    consumer_name = _consumer_name("knowledge")

    consumer, subscriptions = build_knowledge_worker(
        redis_client=redis_client,
        documents=documents,
        pipeline=pipeline,
        content_resolver=content_resolver,
        outbox=outbox,
        uow=tenant_session,
        ledger=ledger,
        consumer_name=consumer_name,
        block_ms=settings.events.consumer_block_ms,
        batch_count=settings.events.consumer_batch_count,
        max_deliveries=settings.events.max_retries_before_dlq,
    )

    # `CompositionRoot.disposables()`'s own `_close_vault` precedent -- hvac
    # is synchronous (it wraps a `requests.Session`), so closing it needs the
    # same `asyncio.to_thread` offload. Written in step 15 against a function
    # that could not yet reach it (the raise was unconditional then); step 16
    # is what makes it reachable, with no rework, exactly as intended.
    async def _close_vault() -> None:
        await asyncio.to_thread(vault_client.adapter.close)

    disposables: list[Disposable] = [
        engine.dispose,
        qdrant_client.close,
        embedding_http.aclose,
        redis_client.aclose,
        _close_vault,
    ]
    return consumer, subscriptions, disposables


# --------------------------------------------------------------------------- #
# media_worker                                                                #
# --------------------------------------------------------------------------- #
def build_media_run_handler(
    jobs: MediaJobRepository,
    generator: MediaGenerator,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    *,
    consumer_group: str = _CG_MEDIA,
) -> EventHandler:
    """``media.job.requested.v1`` -> ``RunMediaJob.run`` (the ``queued →
    running`` claim + the generator call, external I/O OUTSIDE any
    transaction — R2) -> ONE ``uow.begin`` block holding the DD-09 claim +
    ``RunMediaJob.finalize`` (terminal status) + the follow-on
    ``media.job.generated.v1``/``...failed.v1`` outbox append — the
    knowledge index handler's shape exactly, D5 window closed the same way
    (5.2-أ). No transaction at all on the idempotent-redelivery no-op path
    (an already-terminal job short-circuits in ``run``)."""
    runner = RunMediaJob(jobs, generator)

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        job_id = envelope["data"]["job_id"]
        event_id: str = envelope["id"]
        attempt = await runner.run(ctx, job_id=job_id)
        if attempt.is_redelivery_noop:
            return
        async with uow.begin(ctx):
            if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                return  # Duplicate delivery -- clean return, the engine XACKs.
            _, events = await runner.finalize(ctx, attempt)
            await outbox.append(ctx, [_media_to_outbox_record(ctx, event) for event in events])

    return _handle


def build_media_worker(
    *,
    redis_client: Redis,
    jobs: MediaJobRepository,
    generator: MediaGenerator,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the media worker's single ``stream.media``/``cg.media``
    subscription (04 §4). Every dependency here is a plain parameter -- this
    function is never itself blocked (``build_media_worker_from_env``'s
    docstring) and is exactly what
    ``tests/integration/test_media_worker_live.py`` calls directly with a
    ``FakeMediaGenerator`` injected in place of the still-nonexistent real
    adapter."""
    subscriptions = [
        Subscription(
            stream="stream.media",
            group=_CG_MEDIA,
            handlers={
                "media.job.requested.v1": build_media_run_handler(
                    jobs, generator, outbox, uow, ledger
                )
            },
        )
    ]
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
    )
    return consumer, subscriptions


def build_media_worker_from_env() -> tuple[StreamConsumer, list[Subscription], list[Disposable]]:
    """Build everything about the media worker that genuinely IS real from
    env, then RAISE naming exactly what is not (module docstring's
    "Honest-failure rule").

    Real today: ``jobs`` (``SqlMediaJobRepository``), ``outbox``
    (``SqlEventOutbox``), ``tenant_session``, and the Redis client/consumer
    identity. NOT real: a ``MediaGenerator`` -- no adapter exists at all
    (``media/ports/generation.py``'s own docstring: "the deferred, Phase-5
    infra adapter" combining ``ImageProvider``/``VideoProvider`` with
    ``files`` storage). ``build_media_worker`` itself is unaffected --
    ``tests/integration/test_media_worker_live.py`` calls it directly with a
    ``FakeMediaGenerator``.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    _logger.info("media_worker.bootstrap_initialized", extra={"app_env": settings.app_env})

    engine = create_engine(_worker_db(settings.database))
    sessionmaker = create_sessionmaker(engine)
    tenant_session = TenantSessionFactory(sessionmaker)
    jobs = SqlMediaJobRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    ledger = SqlProcessedEventLedger(tenant_session)

    redis_client = create_redis_client(settings.redis)
    consumer_name = _consumer_name("media")

    _logger.error(
        "media_worker.generator_wiring_blocked",
        extra={
            "app_env": settings.app_env,
            "consumer_name": consumer_name,
            # Confirms exactly what DID wire successfully before the block --
            # every name here is a real, connected adapter, not a placeholder.
            "jobs": type(jobs).__name__,
            "outbox": type(outbox).__name__,
            "ledger": type(ledger).__name__,
            "redis_client": type(redis_client).__name__,
        },
    )
    raise AppError(
        "media_worker's handler needs a MediaGenerator, and no adapter for "
        "it exists yet (media/ports/generation.py's own docstring names it "
        "a deferred Phase-5 infra adapter); jobs/outbox/ledger/tenant_session/"
        "the Redis client above ARE real -- only the generator is blocked. "
        "build_media_worker itself is unaffected by this gap; see its own "
        "docstring.",
        code="common.internal",
    )


# --------------------------------------------------------------------------- #
# memory_worker                                                               #
# --------------------------------------------------------------------------- #
def build_memory_index_handler(
    use_case: IndexMemoryItem,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    *,
    consumer_group: str = _CG_MEMORY,
    model: str,
    api_key: str,
) -> EventHandler:
    """``memory.item.stored.v1`` -> ``IndexMemoryItem.run`` (the R3 guard +
    embed + upsert, external I/O OUTSIDE any transaction — R2) -> ONE
    ``uow.begin`` block holding the DD-09 claim + ``IndexMemoryItem.finalize``
    (the ``vector_ref`` write). Memory produces no follow-on wire event for
    indexing (04 §5), so unlike the knowledge/media handlers above there is
    no outbox append — 5.2-أ gave this handler its FIRST ``uow.begin``,
    purely so the claim and the one terminal write share a transaction. No
    transaction at all on the idempotent-redelivery no-op path (an
    already-indexed item short-circuits in ``run``)."""

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        memory_id = envelope["data"]["memory_id"]
        event_id: str = envelope["id"]
        attempt = await use_case.run(ctx, memory_id=memory_id, model=model, api_key=api_key)
        if attempt.is_redelivery_noop:
            return
        async with uow.begin(ctx):
            if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                return  # Duplicate delivery -- clean return, the engine XACKs.
            await use_case.finalize(ctx, attempt)

    return _handle


def build_memory_worker(
    *,
    redis_client: Redis,
    memory: MemoryRepository,
    embeddings: EmbeddingProvider,
    vectors: VectorStore,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    model: str,
    api_key: str,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the memory worker's single ``stream.memory``/``cg.memory``
    subscription (04 §4). Every dependency here is a plain parameter -- this
    function is never itself blocked (``build_memory_worker_from_env``'s
    docstring)."""
    handler = build_memory_index_handler(
        IndexMemoryItem(memory, embeddings, vectors), uow, ledger, model=model, api_key=api_key
    )
    subscriptions = [
        Subscription(
            stream="stream.memory", group=_CG_MEMORY, handlers={"memory.item.stored.v1": handler}
        )
    ]
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
    )
    return consumer, subscriptions


def build_memory_worker_from_env() -> tuple[StreamConsumer, list[Subscription], list[Disposable]]:
    """Build the memory worker exactly as the ``memory`` process runs it
    (2.10 closes the LAST gap this function used to name): every dependency
    is real, INCLUDING ``embeddings`` -- the ``ExternalEmbeddingProvider``
    adapter over the central embedding service (``EMBEDDING_SERVICE_URL``,
    ``settings.embedding_service``). ``model``/``api_key`` are the LOCAL
    model's own settings/the keyless-provider empty string (the
    ``composition_root.py`` ``keyless_providers`` precedent) -- there is no
    per-request routing decision here the way ``ProviderResolver`` makes one
    for the API path, because a Streams worker has no per-request
    ``ExecutionContext`` to route from; the pinned local model is this
    deployment's only embedding choice, by construction.

    ``vectors`` is a REAL ``QdrantVectorStore`` -- unlike ``knowledge``'s
    still-blocked storage/extractor pipeline, memory's vector store needs no
    async secret, so it was already buildable synchronously before 2.10,
    exactly as ``CompositionRoot.from_env`` builds it.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    _logger.info("memory_worker.bootstrap_initialized", extra={"app_env": settings.app_env})

    engine = create_engine(_worker_db(settings.database))
    sessionmaker = create_sessionmaker(engine)
    tenant_session = TenantSessionFactory(sessionmaker)
    memory = SqlMemoryRepository(tenant_session)
    ledger = SqlProcessedEventLedger(tenant_session)

    qdrant_client = create_qdrant_client(settings.qdrant)
    vectors: VectorStore = QdrantVectorStore(qdrant_client)

    embedding_http = create_embedding_http_client(settings.embedding_service)
    embeddings: EmbeddingProvider = ExternalEmbeddingProvider(
        embedding_http, settings.embedding_service
    )

    redis_client = create_redis_client(settings.redis)
    consumer_name = _consumer_name("memory")

    consumer, subscriptions = build_memory_worker(
        redis_client=redis_client,
        memory=memory,
        embeddings=embeddings,
        vectors=vectors,
        uow=tenant_session,
        ledger=ledger,
        model=settings.embedding_service.model,
        api_key="",
        consumer_name=consumer_name,
        block_ms=settings.events.consumer_block_ms,
        batch_count=settings.events.consumer_batch_count,
        max_deliveries=settings.events.max_retries_before_dlq,
    )
    disposables: list[Disposable] = [
        engine.dispose,
        qdrant_client.close,
        embedding_http.aclose,
        redis_client.aclose,
    ]
    return consumer, subscriptions, disposables
