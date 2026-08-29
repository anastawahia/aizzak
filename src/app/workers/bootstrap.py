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

**The honest-failure rule is retired: no builder here is blocked any more**
(step 20 of ``deferred-adapters-plan.md``). The rule — the design brief's
explicit instruction — said that a builder missing a real adapter must wire
everything that genuinely IS real, then RAISE a clear ``AppError`` naming
exactly what is missing, rather than silently substituting a fake (fakes
exist ONLY in tests). It is recorded here rather than deleted because it
governed this file for three phases and is what any FUTURE blocked builder
must do; what changed is only that the three seams it covered are now all
closed:

* ``EmbeddingProvider`` first, in 2.10
  (``infrastructure/ai_providers/embedding/external_embedding.py`` used to be
  0 bytes; ``composition_root.py``'s own module docstring records the
  history) — ``build_memory_worker_from_env`` builds the REAL
  ``ExternalEmbeddingProvider``.
* ``DocumentContentResolver`` second, in steps 15-16 (``docs/log/3.98.md`` ·
  ``docs/log/3.100.md``) — ``build_knowledge_worker_from_env`` binds MinIO
  through the same ``vault_binding``/``storage_binding`` pair
  ``CompositionRoot`` uses and composes ``WorkerDocumentContentResolver``.
* ``MediaGenerator`` last, in steps 19-20 (``docs/log/3.103.md`` ·
  ``docs/log/3.104.md``) — step 19 built the adapter
  (``workers/media_generation.py`` over ``OpenAIImage``) and step 20 the
  assembly, so ``build_media_worker_from_env`` returns a real consumer too.
  **All three Streams workers now build from env.**

``build_<name>_worker`` itself was never blocked at any point: it is a pure
wiring function over whatever the caller hands it, which is precisely how
the live e2e tests exercised the REAL register/run/index handlers all along,
without waiting on any of those gaps.

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
from collections.abc import Awaitable, Callable
from typing import Protocol

from redis.asyncio import Redis

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.lifecycle import Disposable
from app.framework.di.storage_binding import bind_minio
from app.framework.di.storage_handle import StorageHandle
from app.framework.di.vault_binding import build_vault
from app.framework.errors import AppError, ConflictError, UnsupportedTypeError, ValidationError
from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.framework.observability import Heartbeat, build_heartbeat, configure_logging, get_logger
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.llm_provider import LLMProvider
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.ports.vector_store import HybridVectorStore, VectorStore
from app.framework.providers.resolver import ProviderResolver, SettingsProviderResolver
from app.framework.settings.settings import DatabaseSettings
from app.framework.types import Json, Uuid
from app.infrastructure.ai_providers.embedding.external_embedding import (
    ExternalEmbeddingProvider,
    create_embedding_http_client,
)
from app.infrastructure.ai_providers.image.external_image import (
    OpenAIImage,
    create_openai_image_http_client,
)
from app.infrastructure.ai_providers.llm.ollama_llm import OllamaLLM, create_ollama_http_client
from app.infrastructure.ai_providers.llm.openai_llm import OpenAILLM, create_openai_http_client
from app.infrastructure.cache.redis_cache import blocking_read_timeout_s, create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.engine import EventHandler, StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import AppendMessage
from app.modules.credentials.adapters.sql_repository import SqlCredentialRepository
from app.modules.credentials.application.use_cases import ResolveCredential
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    FilesQueryService,
    RegisterUpload,
)
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.adapters.sql_repository import (
    SqlDocumentRepository,
    SqlSummaryJobRepository,
    SqlSummaryRepository,
)
from app.modules.knowledge.application.event_mapping import (
    to_outbox_record as _knowledge_to_outbox_record,
)
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.knowledge.application.summarization import SummarizeDocument
from app.modules.knowledge.application.use_cases import (
    BuildSummary,
    GetDocumentFileName,
    IndexRegisteredDocument,
    delivered_summary_text,
)
from app.modules.knowledge.domain.sparse import Bm25Params
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.content_extractor import ParsedDocument
from app.modules.knowledge.ports.files import ReadableFiles
from app.modules.knowledge.ports.repository import DocumentRepository, SummaryRepository
from app.modules.knowledge.ports.summarization import SUMMARIZE_CAPABILITY, ResolvedSummarizer
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.event_mapping import to_outbox_record as _media_to_outbox_record
from app.modules.media.application.use_cases import RunMediaJob
from app.modules.media.ports.generation import MediaGenerator
from app.modules.media.ports.repository import MediaJobRepository
from app.modules.memory.adapters.sql_repository import SqlMemoryRepository
from app.modules.memory.application.use_cases import IndexMemoryItem
from app.modules.memory.ports.repository import MemoryRepository
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.workers.content_resolver import WorkerDocumentContentResolver
from app.workers.media_generation import WorkerMediaGenerator

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

# `EnsureTopology` -- the closure `build_relay_from_env` now hands back
# alongside the relay itself (stream-topology-plan.md §3). Defined HERE,
# next to its only caller, on `Disposable`'s own precedent: that alias moved
# to `framework/di/lifecycle.py` only once TWO composition roots (this file
# and `composition_root.py`) needed the identical shape. `EnsureTopology` has
# exactly one root and one caller so far (`outbox_relay.py::run`) -- moving
# it into `framework/` now would be speculative sharing, not deduplication.
EnsureTopology = Callable[[], Awaitable[None]]


def build_relay_from_env() -> tuple[OutboxRelay, EnsureTopology, list[Disposable]]:
    """Build one ``OutboxRelay`` wired exactly as the ``outbox_relay``
    process runs it, plus an ``ensure_topology`` closure the entrypoint must
    ``await`` before the relay's first publish, plus the resources
    ``outbox_relay.py``'s entrypoint must close on shutdown (in no
    particular order -- disposing an engine and closing a Redis client are
    independent of each other).

    **Why a relay -- a PRODUCER -- provisions consumer groups.**
    ``ensure_topology`` walks ``STATIC_CONSUMER_TOPOLOGY``
    (``framework/events/topology.py``) calling ``RedisStreamsConsumer.
    ensure_group`` for every ``(stream, group)`` pair, so every consumer
    group this platform ever reads from exists at the stream's tail
    (``redis_streams.py``'s ``xgroup_create(..., id="$", mkstream=True)``,
    unchanged by this step -- stream-topology-plan.md §1-ب) BEFORE this
    process's first ``XADD``. This is not consumption: it is **topology
    provisioning**, the same kind of thing ``app.ops.provision`` does to a
    database schema before anyone writes a row -- a one-time "make the
    destination exist" step that happens to be owned by the party about to
    write to it, because the relay is the ONLY producer on these streams
    (stream-topology-plan.md §1-د) and is therefore the one place a single
    call sequenced before ``run_forever`` covers every process that ever
    boots (Compose's ``outbox-relay`` service AND RunPod's
    ``supervisord.conf``, which run this exact entrypoint --
    stream-topology-plan.md §1-هـ, no extra line needed in either). The real
    alternative to provisioning here was never architectural purity; it was
    a manual ordering rule in a runbook (stream-topology-plan.md §2's
    rejected alternatives (ب)/(ج)), which a forgotten deploy step could
    silently violate.
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

    # NOTE: this client keeps the SAME short `2.0` read timeout every other
    # client in this file derives away from (`blocking_read_timeout_s`,
    # `redis_cache.py`), even though `topology_consumer` below wraps it in a
    # `RedisStreamsConsumer` -- the type that, everywhere else in this
    # codebase, means "this process runs a BLOCKING `XREADGROUP ... BLOCK
    # <block_ms>` read". It does NOT here. `ensure_group` below issues one
    # `XGROUP CREATE`, a call that never blocks; the relay itself never calls
    # `RedisStreamsConsumer.read` at all -- it only ever polls with a short
    # `asyncio.sleep` (stream-topology-plan.md §3's coverage rule names this
    # client the FIFTH, explicit exception -- see also
    # `test_relay_client_stays_at_exactly_two_seconds`,
    # `tests/unit/test_redis_blocking_timeouts.py`). **Do not "fix" this by
    # passing the derived timeout here just because a `RedisStreamsConsumer`
    # is now involved** -- that consumer-shaped object performs no blocking
    # read, so there is nothing here for the derived timeout to protect.
    redis_client = create_redis_client(settings.redis)
    # The relay is the ONLY producer on these streams (02 §1.8), so this is
    # the single place the retention bound can be applied at all (7.3).
    publisher = RedisStreamsPublisher(redis_client, maxlen=settings.events.stream_maxlen)

    # Mounted over the SAME `redis_client` as the publisher above -- no
    # second connection (stream-topology-plan.md §3, guarded by
    # `test_relay_client_shared_with_topology_consumer` in
    # `tests/unit/test_relay_topology_provisioning.py`).
    topology_consumer = RedisStreamsConsumer(redis_client)

    async def ensure_topology() -> None:
        """Provision every static consumer group at the stream's tail
        (`STATIC_CONSUMER_TOPOLOGY`) before the relay's own ``run_forever``
        loop performs its first ``publish`` -- see this function's own
        docstring for why a producer owns this. Idempotent by construction:
        ``RedisStreamsConsumer.ensure_group`` swallows ``BUSYGROUP``, so
        re-running this on every relay restart costs four no-op calls."""
        for binding in STATIC_CONSUMER_TOPOLOGY:
            await topology_consumer.ensure_group(binding.stream, binding.group)

    relay = OutboxRelay(
        store,
        publisher,
        batch_size=settings.events.outbox_relay_batch_size,
        poll_interval_ms=settings.events.outbox_poll_interval_ms,
        max_backoff_ms=_MAX_BACKOFF_MS,
        # ت-3: the name is the Compose SERVICE's, matching what an operator
        # sees in `docker compose ps` -- `HEARTBEAT_PROCESS_NAMES` owns the
        # spelling shared with the checker.
        heartbeat=build_heartbeat(settings.health.heartbeat_dir, "outbox-relay"),
    )

    disposables: list[Disposable] = [engine.dispose, redis_client.aclose]
    return relay, ensure_topology, disposables


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


# The namespaces each worker deliberately carries no adapter for, and
# therefore must not be judged on. `_parse_routing` refuses construction on a
# route whose provider has no wired adapter, and that refusal is
# namespace-blind -- so without this narrowing an operator's PERFECTLY VALID
# `PROVIDER_ROUTING` would refuse to boot a process over a capability that
# process never claims and never reads.
#
# One table per worker, spelled out rather than derived: the set is a
# statement about what a process wires, and the honest way to write "the
# knowledge worker generates no images" is to say so. Step 18 added `image`
# to the knowledge set the moment the namespace became legal; step 20 adds
# the media set, which is the reason the pair below stopped being one
# constant named `..._TO_THIS_WORKER` -- with two workers narrowing
# differently, "this worker" no longer names anything.
# BE-RAG-009 REMOVED `llm` from the knowledge set, and that is a real change
# of fact rather than a loosening. This worker used to make no LLM call at
# all; it now runs the summarisation map-reduce, so the `summarize` route is
# one it both reads and depends on. Leaving `llm` foreign would have meant a
# process that resolves a model through a table it declined to be judged on --
# exactly the "indexed here, searched there" drift step 16 rejected for
# embeddings, in the other direction. `image` stays foreign: no summary
# generates a picture.
_FOREIGN_TO_KNOWLEDGE = frozenset({"image"})
_FOREIGN_TO_MEDIA = frozenset({"llm", "embedding"})


def _routing_for(routing: Json, *, foreign: frozenset[str]) -> Json:
    """``PROVIDER_ROUTING`` with the namespaces the calling worker has no
    adapter for dropped -- what that worker's ``SettingsProviderResolver`` is
    given (step 16 for ``knowledge``, step 20 for ``media``; each factory's
    docstring argues its own narrowing).

    Written as "drop the known-foreign names" rather than "keep ``embedding``"
    on purpose: an unknown/misspelled namespace still reaches
    ``_parse_routing``, which refuses construction naming it. Keeping only a
    known key would swallow that typo and boot a worker on a routing table its
    operator believes says something else.
    """
    return {namespace: entry for namespace, entry in routing.items() if namespace not in foreign}


class _WorkerSummarizerResolver:
    """Adapts ``SettingsProviderResolver`` to the knowledge module's
    ``SummarizerResolver`` seam (BE-RAG-009).

    A near-twin of ``composition_root._RoutedSummarizerResolver``, and
    deliberately a second copy rather than an import: the ``layers`` contract
    keeps ``app.workers`` off ``app.framework.di``, and this is four lines of
    delegation. What must not drift is the CAPABILITY string, and it does not
    — both read ``SUMMARIZE_CAPABILITY`` from the module that owns it.
    """

    def __init__(self, providers: ProviderResolver) -> None:
        self._providers = providers

    async def resolve_summarizer(self, ctx: ExecutionContext) -> ResolvedSummarizer:
        provider, resolved = await self._providers.resolve_llm(ctx, capability=SUMMARIZE_CAPABILITY)
        return ResolvedSummarizer(provider=provider, model=resolved.model, api_key=resolved.api_key)


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
    ``ParsedDocument``, the embedding model/key to index it with (the
    ``knowledge.EmbeddingResolver`` seam's shape, ``ports/retrieval.py``,
    generalized to cover fetch + parse too, not just model/key resolution),
    and — plan step 15, §3.6 — the ``sha256`` fingerprint of the SAME bytes
    that were parsed, computed here because this is the one seam that still
    holds them (``IndexRegisteredDocument`` itself never sees raw bytes).

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
    ) -> tuple[ParsedDocument, str, str, str]: ...


# ── `files.file.uploaded.v1` has no handler here any more ─────────────────
#
# It used to have one: `build_knowledge_register_handler` turned every
# completed upload into a `pending` `Document` plus the
# `knowledge.document.registered.v1` that sent this same worker into the
# embedding pipeline below. Completing an upload was, transitively, an order
# to index -- and nobody was ever asked.
#
# Indexing is now REQUESTED, once, per file: `POST /knowledge/documents`
# (`IndexFileService`) mints that document and publishes that event inside one
# request-scoped transaction, which is why nothing was moved here rather than
# deleted -- the registration this worker used to do still happens, at a
# different moment and at somebody's asking. The index handler below is
# untouched and does not know the difference: it consumes
# `knowledge.document.registered.v1` exactly as before, from whichever
# producer wrote it.
#
# `files.file.uploaded.v1` keeps being PUBLISHED (04 §5 promotes it; a file
# completing is a true fact about the workspace). It simply has no consumer in
# this process, and `build_knowledge_worker` no longer subscribes to
# `stream.files` at all.


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
            parsed, model, api_key, content_hash = await content.resolve(
                ctx, file_id=data["file_id"]
            )
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
                ctx,
                document_id=data["document_id"],
                parsed=parsed,
                model=model,
                api_key=api_key,
                content_hash=content_hash,
            )
        if attempt.is_redelivery_noop:
            return
        async with uow.begin(ctx):
            if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                return  # Duplicate delivery -- clean return, the engine XACKs.
            _, events = await index.finalize(ctx, attempt)
            await outbox.append(ctx, [_knowledge_to_outbox_record(ctx, event) for event in events])

    return _handle


# `F-4` (rag-summarization-fix-plan.md §3.5) -- what a person reads on the
# job whose build ran out of time. Phrased for them and not for a log, the
# `SummarizeDocument.execute` "no indexed text" precedent: this sentence is
# the whole explanation they will get for why the button they pressed ended
# where it did. `:g` rather than `:d` so a float budget from a direct caller
# prints as 1800 and not 1800.0 -- the `content_resolver.py` phrasing.
_SUMMARY_TIMEOUT_REASON = "building this summary exceeded the {seconds:g}s limit and was stopped"

# `F-7`. A plain string because that is what `AppendMessage` takes: `role`
# crosses into `conversations` as a `str` and is validated against
# `MessageRole` on the way in, which is the same discipline
# `ConversationThreads` states for `kind` -- a caller that imported the enum
# would be importing another module's domain to say one word.
_ROLE_ASSISTANT = "assistant"


def build_knowledge_summary_handler(
    build: BuildSummary,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    *,
    consumer_group: str = _CG_KNOWLEDGE,
    heartbeat: Heartbeat | None = None,
    max_duration_s: float | None = None,
) -> EventHandler:
    """``knowledge.summary.requested.v1`` -> ``BuildSummary.claim`` (the
    ``queued → running`` claim + the chunk read + route resolution) ->
    ``BuildSummary.run`` (the map-reduce's provider calls, all OUTSIDE any
    transaction — R2) -> ONE ``uow.begin`` block holding the DD-09 claim +
    ``BuildSummary.finalize`` (the stored summary + the job's terminal state)
    + the follow-on ``knowledge.summary.built.v1``/``...build_failed.v1``
    outbox append.

    Structurally the indexing handler (``build_knowledge_index_handler``),
    because it is the same problem: external I/O that must not hold a
    transaction, followed by a terminal write that must not be separable from
    its event. Three branches differ, and each answers a failure that would
    otherwise be permanent:

    * **``claim`` raising** — no ``summarize`` route resolves a key for this
      workspace. A terminal fact about the deployment, not a transient fault:
      redelivering it forever would leave the job ``queued`` and holding
      ``uq_summary_job_active``, so the user could not even ask again. Routed
      through ``fail`` into the SAME ``finalize``, so there stays one path to
      a failed job. ``AppError`` and ``ValueError`` only — a Postgres or
      Redis outage still escapes and is retried, which is correct, because
      those may succeed next time.
    * **``claim`` returning ``None``** — the DD-09 no-op, including a job
      cancelled before any worker reached it. No transaction is opened to
      record nothing.
    * **``finalize`` raising ``ConflictError``** — the document was destroyed
      by a re-index while this build was running, so ``fk_summary_doc``
      rejects the summary. The aborted transaction cannot also record that,
      hence the SECOND ``uow.begin``: without it the handler would redeliver,
      rebuild, and be rejected again until the DLQ ate it, with the job stuck
      ``running`` forever.
    * **``conversation_id`` on the message** (`F-7`) — carried straight
      through to ``finalize``, which stamps it on
      ``knowledge.summary.built.v1``. Nothing here reads it, and that is the
      point: this handler builds summaries, and where a finished one is owed
      is the next handler's business.
    * **``run`` outliving ``max_duration_s``** (`F-4`) — a build that is not
      going to finish. Routed through the SAME ``fail`` as the first branch,
      so there is still one path to a failed job, and caught HERE rather than
      inside ``run``: ``asyncio.timeout`` cancels, and a ``CancelledError`` is
      a ``BaseException`` that ``run``'s broad ``except Exception`` correctly
      declines to swallow. The ``TimeoutError`` it becomes at this boundary is
      the caller's to answer.

    **The two `F-4` parameters do different jobs and neither replaces the
    other.** ``heartbeat`` keeps a container healthy while a legitimate build
    is running (a beat cannot land while a handler is, so a five-minute build
    otherwise looks exactly like a wedged loop); ``max_duration_s`` ends a
    build that is genuinely stuck, with a written reason, instead of leaving
    it to be redelivered until the DLQ. Both default to off, so every direct
    caller -- the live integration tests included -- keeps today's behaviour,
    the ``sweep_interval_s`` precedent in ``build_knowledge_worker``.
    """
    # `Heartbeat` has no `read` side by design, so there is nothing to ask it
    # here; the whole binding is one bound method handed to `run`.
    beat = None if heartbeat is None else heartbeat.beat

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        data = envelope["data"]
        event_id: str = envelope["id"]
        job_id: str = data["job_id"]
        # `F-7` -- read off the MESSAGE, never off the job row, because the
        # row does not have it: the thread a build owes its answer to is a
        # property of the request, and this is the request. `.get` and not
        # `[...]`: a build asked for outside a thread omits the key entirely
        # (the `space_id` rule in `files`' mapping), and so does every
        # `knowledge.summary.requested.v1` published before this step -- one
        # of which may still be pending redelivery when this deploys.
        conversation_id: str | None = data.get("conversation_id")
        try:
            plan = await build.claim(ctx, job_id=job_id)
        except (AppError, ValueError) as exc:
            attempt = await build.fail(ctx, job_id=job_id, reason=str(exc))
        else:
            if plan is None:
                return  # DD-09 no-op, or a job cancelled before it was claimed.
            try:
                # `asyncio.timeout(None)` is the documented no-op, so the
                # default needs no branch of its own here.
                async with asyncio.timeout(max_duration_s):
                    attempt = await build.run(ctx, plan, on_heartbeat=beat)
            except TimeoutError:
                attempt = await build.fail(
                    ctx,
                    job_id=job_id,
                    reason=_SUMMARY_TIMEOUT_REASON.format(seconds=max_duration_s),
                )
        if attempt is None:
            return

        try:
            async with uow.begin(ctx):
                if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                    return  # Duplicate delivery -- clean return, the engine XACKs.
                _, events = await build.finalize(ctx, attempt, conversation_id=conversation_id)
                await outbox.append(
                    ctx, [_knowledge_to_outbox_record(ctx, event) for event in events]
                )
        except ConflictError as exc:
            # The summary's document went away underneath the build. Record
            # the job's failure in a FRESH transaction -- the one above is
            # aborted -- so this delivery ends terminally instead of looping.
            failure = await build.fail(ctx, job_id=job_id, reason=str(exc))
            if failure is None:
                return
            async with uow.begin(ctx):
                if not await ledger.claim(ctx, consumer_group=consumer_group, event_id=event_id):
                    return
                _, events = await build.finalize(ctx, failure, conversation_id=conversation_id)
                await outbox.append(
                    ctx, [_knowledge_to_outbox_record(ctx, event) for event in events]
                )

    return _handle


async def _summary_file_name(
    ctx: ExecutionContext, document_names: GetDocumentFileName, document_id: str
) -> str | None:
    """The name of the file a finished summary is about, or ``None``
    when it cannot be read (ب-7ج, scenarios plan §4).

    **A header is cosmetic; a summary is what the user asked for.**
    A build that took minutes of provider calls must not be
    dropped because a name lookup failed — the same rule ب-2 states
    for the RAG agent's corpus header, and the same shape:
    ``Exception``, logged with its traceback, degraded to
    "no name" rather than to a blank.

    The catch is broad because everything under it is one optional
    read whose every failure has the same answer. It wraps THIS
    call and nothing else, so a fault in the append below still
    fails the delivery and is still retried.
    """
    try:
        return await document_names.execute(ctx, document_id=document_id)
    except Exception as exc:
        _logger.warning(
            "summary_delivery_name_unavailable",
            exc_info=exc,
            extra={"document_id": document_id},
        )
        return None


def build_knowledge_summary_delivery_handler(
    summaries: SummaryRepository,
    conversation_messages: AppendMessage,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    document_names: GetDocumentFileName,
    *,
    consumer_group: str = _CG_KNOWLEDGE,
) -> EventHandler:
    """``knowledge.summary.built.v1`` -> the finished text, appended to the
    thread that asked for it as one assistant message (`F-7`).

    **The missing half of the chat summarisation route.** Asking for a
    summary in a conversation queues a build and answers with a receipt
    (``rag_agent``'s ``_summary_queued_answer``); the build then finishes
    minutes later in this process, and until this handler existed the text it
    produced reached nobody — the event was minted, published, and consumed
    by nothing in the platform. Nothing is being ported here: alpha returned
    the summary SYNCHRONOUSLY inside the answer, which means holding a
    streaming chat turn open for the length of a map-reduce.

    **A DURABLE group, not the notify family.** ``knowledge.summary.built.v1``
    already reaches the API's ``cg.notify.<host>.<pid>`` consumers, and
    leaving the delivery to them would have made whether the summary is ever
    written depend on whether a browser happened to be connected — those
    groups are created and torn down with the process. A message in a thread
    is a stored artefact and needs a group that outlives every reader.

    **The SAME group and the same subscription as the build handler**, for
    the reason ``build_knowledge_summary_handler`` itself was not given one:
    04 §4's binding table gives this worker one group on ``stream.knowledge``.
    A second group would receive EVERY knowledge event in order to answer one
    type, and would bring a second pending list and a second dead-letter
    queue with it. The DD-09 ledger is keyed ``(consumer_group, event_id)``
    and this is a different event id from the request that started the build,
    so sharing the group costs no idempotency.

    Three quiet returns, none of them an error:

    * **no ``conversation_id``** — the build was asked for through ``POST
      /documents/{id}/summary``, which reads its result back through ``GET``.
      There is nowhere to deliver, and that is the normal case for every
      summary the REST route builds.
    * **no stored summary** — deleted, or its document re-indexed away,
      between the build committing and this delivery. The thread is better
      off with the receipt it already has than with a message about a
      summary that no longer exists.
    * **an ``AppError`` from the append** — the thread was deleted or is
      unknown. Terminal facts, so the delivery ends rather than being
      redelivered five times into the DLQ; a Postgres or Redis outage is not
      an ``AppError`` and still escapes to be retried, which is the rule
      ``build_knowledge_summary_handler`` states for its own broad branch.

    The summary is READ here rather than carried on the event. A summary is
    thousands of characters and the stream is not where a document's prose
    belongs — 04 §4's payload for this type is four ids, and the row is one
    tenant-scoped read away in the process that just wrote it.

    **What is appended is ``delivered_summary_text(summary)``, not
    ``summary.text`` (`F-9`).** A build that ran past the map ceiling
    produces a true summary of the document's BEGINNING, and every REST
    reader is told so by ``SummaryOut.truncated``; a thread message has no
    field to carry a flag in, so this was the one surface where the cut was
    silent. The sentence is composed here at delivery and never stored —
    see that function for why the row stays clean.

    **And it NAMES the file** (ب-7ج, scenarios plan §4, gap ف-2). The
    receipt this message finally answers can name the document
    (``RoutedAnswer.summary_target_name``); without a name here, a
    thread that acknowledged «الميزانية» minutes ago and now
    receives a wall of prose is still asking its reader to assume
    the two are about one file — and messages about other things
    may sit between them. ``document_names`` reads it from the
    ``files`` seam AT DELIVERY rather than carrying it on the
    event: a name is the one thing about a file that may change
    (INV-F4), and an event minted at build time would deliver the
    name the file had then.

    **A failed name lookup is SWALLOWED, never a failed delivery** —
    ``_fetch_corpus_header``'s rule in ``rag_agent``, and ب-2's: a
    header is cosmetic and a summary is what was asked for. The
    catch is deliberately broad because everything under it is one
    optional read, and it wraps THAT CALL only, never the append.

    ``AppendMessage`` returns a ``MessageAppended`` this handler drops, the
    same way ``ConversationService.append`` drops it: 04 §5 lists it among the
    conversations events that are internal and never promoted to a stream, so
    there is no outbox record owed for it.
    """

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        data = envelope["data"]
        conversation_id: str | None = data.get("conversation_id")
        if conversation_id is None:
            return
        summary = await summaries.get(
            ctx,
            data["document_id"],
            SummaryKind(data["kind"]),
            SummaryLanguage(data["lang"]),
        )
        if summary is None:
            return
        # ب-7ج -- outside the transaction below on purpose: it is a
        # read, it is optional, and a name lookup that opened the
        # unit of work would make a cosmetic header share a fate
        # with the append that is the actual delivery.
        file_name = await _summary_file_name(ctx, document_names, data["document_id"])
        try:
            async with uow.begin(ctx):
                if not await ledger.claim(
                    ctx, consumer_group=consumer_group, event_id=envelope["id"]
                ):
                    return  # Duplicate delivery -- clean return, the engine XACKs.
                await conversation_messages.execute(
                    ctx,
                    conversation_id,
                    role=_ROLE_ASSISTANT,
                    # `F-9` -- the summary plus a sentence when it covers only
                    # the document's beginning. ب-7ج -- prefixed by the file's
                    # name when one could be read. Unchanged text otherwise.
                    text=delivered_summary_text(summary, file_name),
                )
        except AppError as exc:
            _logger.info(
                "summary_delivery_declined",
                extra={
                    "conversation_id": conversation_id,
                    "document_id": data["document_id"],
                    "reason": str(exc),
                },
            )

    return _handle


def build_knowledge_worker(
    *,
    redis_client: Redis,
    documents: DocumentRepository,
    files: ReadableFiles,
    pipeline: IndexDocument,
    content_resolver: DocumentContentResolver,
    summary_builder: BuildSummary,
    summaries: SummaryRepository,
    conversation_messages: AppendMessage,
    outbox: EventOutbox,
    uow: UnitOfWork,
    ledger: ProcessedEventLedger,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
    heartbeat: Heartbeat | None = None,
    summarize_max_duration_s: float | None = None,
    sweep_interval_s: float = 0.0,
    stale_idle_ms: int = 0,
    dlq_watch_interval_s: float = 0.0,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the knowledge worker's ONE subscription under the ``cg.knowledge``
    consumer group (04 §4's binding table, `docs/log/3.45.md`'s recorded
    ``cg.knowledge``-also-on-``stream.knowledge`` sync gap): ``stream.knowledge``,
    for indexing and for summaries. Every dependency here is a plain parameter
    -- this function is never itself blocked by the honest-failure rule
    (``build_knowledge_worker_from_env``'s docstring) and is exactly what
    ``tests/integration/test_e2e_outbox_to_worker.py`` calls directly with
    real Postgres-backed dependencies.

    **It was two subscriptions until indexing became manual.** The second was
    ``stream.files``, where a completed upload registered a document and thereby
    started the pipeline; the API now registers it instead, on request, and the
    comment above ``build_knowledge_index_handler`` records what moved where.
    ``documents``/``uow``/``ledger`` are still parameters, all three still used
    by the handlers below.

    **`F-7` added a THIRD handler, not a third subscription.** ``summaries``
    and ``conversation_messages`` are what
    ``build_knowledge_summary_delivery_handler`` needs to read a finished
    summary and post it into the thread that asked; both are required
    parameters, like every other dependency here, because a worker wired
    without them would still boot and would silently drop every summary a
    conversation ever asked for.

    **``files`` is ب-7ج's (scenarios plan §4)**, and it is a
    parameter for the same reason rather than a seam composed
    inside: the delivery names the file its summary is about, and
    a worker wired without a way to read names would deliver
    every summary untitled — the exact state ف-2 describes, and
    silent again. Paired with ``documents`` (already here) into
    ``GetDocumentFileName`` at the handler below, so the two
    readers of a file's name in this process stay one.
    """
    subscriptions = [
        Subscription(
            stream="stream.knowledge",
            group=_CG_KNOWLEDGE,
            handlers={
                "knowledge.document.registered.v1": build_knowledge_index_handler(
                    documents, pipeline, content_resolver, outbox, uow, ledger
                ),
                # BE-RAG-009 -- the same stream and the same consumer group.
                # A third subscription would have meant a second group on one
                # stream, and 04 §4's binding table gives this worker one.
                # `F-4`: the SAME `heartbeat` the consumer below is given,
                # not a second one. The engine beats between messages and
                # cannot beat during one; a summary build is the one handler
                # here long enough for that gap to matter, so it beats for
                # itself, into the same file, through the same object.
                "knowledge.summary.requested.v1": build_knowledge_summary_handler(
                    summary_builder,
                    outbox,
                    uow,
                    ledger,
                    heartbeat=heartbeat,
                    max_duration_s=summarize_max_duration_s,
                ),
                # `F-7` -- the worker consuming its own event, exactly as it
                # already consumes `document.registered.v1` to produce
                # `document.indexed.v1`. Still ONE subscription and one group;
                # see the handler's docstring for why a second group would
                # have been the expensive way to answer one event type.
                "knowledge.summary.built.v1": build_knowledge_summary_delivery_handler(
                    summaries,
                    conversation_messages,
                    uow,
                    ledger,
                    # ب-7ج -- composed HERE, out of two seams this
                    # function already takes, so naming the delivered
                    # summary cost this builder one parameter rather
                    # than two.
                    GetDocumentFileName(documents, files),
                ),
            },
        ),
    ]
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
        heartbeat=heartbeat,
        # ت-2: both default to 0 (off) so every direct caller of this builder
        # -- the live integration tests included -- keeps a consumer that only
        # ever reads, and only the  path below turns the sweep on.
        sweep_interval_s=sweep_interval_s,
        stale_idle_ms=stale_idle_ms,
        # ت-6: same default and the same reason -- a direct caller (a live
        # integration test) gets a consumer that reads and nothing else, and
        # only the `_from_env` path below turns the DLQ report on.
        dlq_watch_interval_s=dlq_watch_interval_s,
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
    # P-16 (rag-indexing-plan.md §4 step 9): the real per-chunk token budget
    # comes from `Settings`, not a bare default -- the same `embedding_
    # service` settings object this worker already resolves the model out of
    # a few lines above.
    pipeline = IndexDocument(
        embeddings,
        vectors,
        embedding_max_input_tokens=settings.embedding_service.embedding_max_input_tokens,
        # §3-ج -- Okapi's document-side parameters, from `Settings` for the
        # same reason the token budget above is: a deployment fact, resolved
        # once at the composition edge and handed down as a plain value.
        # A change here does not reach documents already indexed;
        # `PIPELINE_VERSION` is what does.
        bm25=Bm25Params(
            k1=settings.sparse.bm25_k1,
            b=settings.sparse.bm25_b,
            avg_len=settings.sparse.bm25_avg_len,
        ),
    )

    # Step 15 -- the SAME Vault + MinIO wiring `CompositionRoot` uses, so
    # this worker's storage adapter is bound the identical way the API's is
    # (one secret-shape validation site, not two free to drift).
    vault_client, secrets, _ = build_vault(settings)
    storage = StorageHandle()
    await bind_minio(storage, secrets, settings.minio)

    # BE-RAG-009 -- this worker now makes LLM calls (the summarisation
    # map-reduce), so it wires the LLM adapters the API wires, keyed by each
    # adapter's OWN `provider` attribute rather than a literal. Constructing
    # the clients opens no socket; what it buys is that the `summarize` route
    # is parsed strictly here too, so a table naming a provider this process
    # cannot reach refuses to boot instead of failing every build at run time.
    ollama_http = create_ollama_http_client(
        settings.ollama, timeout_s=settings.limits.llm_timeout_s
    )
    openai_http = create_openai_http_client(timeout_s=settings.limits.llm_timeout_s)
    llm_adapters: tuple[LLMProvider, ...] = (OllamaLLM(ollama_http), OpenAILLM(openai_http))

    # `F-1` (rag-summarization-fix-plan.md §3.1) -- a SECOND pair of clients
    # under the SAME two adapter classes, differing in exactly one thing:
    # `summarize_timeout_s` (300 s) where the pair above carries
    # `llm_timeout_s` (60 s). httpx sets its timeout on the CLIENT, so "this
    # one call gets a longer budget" is not expressible on the clients above;
    # a second pair is what that setting costs, and the `image_http` client
    # in `build_media_worker_from_env` is the same shape for the same reason.
    #
    # Two clients rather than a raised `llm_timeout_s`: ONE number would move
    # every call in the platform, the agent cycle included, and that cycle is
    # meant to fail fast. Which calls are minutes-scale is a fact about the
    # summarisation map-reduce, not about LLM calls.
    summarize_ollama_http = create_ollama_http_client(
        settings.ollama, timeout_s=settings.limits.summarize_timeout_s
    )
    summarize_openai_http = create_openai_http_client(timeout_s=settings.limits.summarize_timeout_s)
    summarize_adapters: tuple[LLMProvider, ...] = (
        OllamaLLM(summarize_ollama_http),
        OpenAILLM(summarize_openai_http),
    )

    # Step 16 -- the embedding route, resolved the SAME way the API resolves
    # it (see the docstring). `embedding_providers` is keyed by the adapter's
    # OWN `provider` attribute, never a literal (the `composition_root.py`
    # precedent), so the strict parse below proves the configured route
    # points at the very adapter `pipeline` above was built from.
    #
    # ONE `key_resolver` instance, shared with the summarisation resolver
    # below: it holds a repository over the same session factory and the same
    # Vault handle, so a second copy would resolve the same rows twice and
    # give the two resolvers two different views of a rotated key.
    credentials = ResolveCredential(SqlCredentialRepository(tenant_session), secrets)
    providers = SettingsProviderResolver(
        routing=_routing_for(settings.provider_routing, foreign=_FOREIGN_TO_KNOWLEDGE),
        llm_providers={adapter.provider: adapter for adapter in llm_adapters},
        embedding_providers={embeddings.provider: embeddings},
        image_providers={},  # step 18 -- and `_routing_for` drops the namespace
        key_resolver=credentials,
        keyless_providers=_KEYLESS_PROVIDERS,
    )
    # `F-1` -- the summarisation twin. The SAME routing table, the SAME
    # embedding adapter, the SAME key resolver, so it boots -- and REFUSES to
    # boot -- on precisely the arguments the resolver above does: a routing
    # table this process cannot serve is still caught once, at boot, and
    # cannot now be caught by one resolver and missed by the other. Only
    # `llm_providers` differs, and only in which HTTP client sits underneath.
    #
    # Handed to `_WorkerSummarizerResolver` and to nothing else. In
    # particular `content_resolver` below keeps `providers`: a parser's
    # vision route is one image-to-text call, not a map-reduce, and it is
    # sized by `parser_timeout_seconds` already.
    summarize_providers = SettingsProviderResolver(
        routing=_routing_for(settings.provider_routing, foreign=_FOREIGN_TO_KNOWLEDGE),
        llm_providers={adapter.provider: adapter for adapter in summarize_adapters},
        embedding_providers={embeddings.provider: embeddings},
        image_providers={},
        key_resolver=credentials,
        keyless_providers=_KEYLESS_PROVIDERS,
    )
    # `limits` carries the OCR caps of rag-indexing-plan.md §3.8 into the
    # parser routes (plan step 5 / `P-09` `P-11`). Without it the numbers would
    # sit in `Settings` and mean nothing — the extractor would keep falling
    # back to its own `Limits()` and no deployment could move them.
    content_resolver = WorkerDocumentContentResolver(
        files,
        storage,
        DocumentContentExtractor(limits=settings.limits),
        providers,
        timeout_s=settings.limits.parser_timeout_seconds,
    )
    # Hoisted out of `BuildSummary`'s argument list by `F-7`: the delivery
    # handler reads back the summary the build just wrote, and two adapters
    # over one session factory would be two objects describing one table.
    summaries = SqlSummaryRepository(tenant_session)
    summary_builder = BuildSummary(
        documents,
        summaries,
        SqlSummaryJobRepository(tenant_session),
        SummarizeDocument(),
        _WorkerSummarizerResolver(summarize_providers),  # `F-1` -- 300 s, not 60 s
    )
    # `F-7` -- the ONE conversations capability this process needs, taken
    # nominally the way `documents`/`pipeline` are: a worker entrypoint is a
    # composition root for its process, and knowing both modules is what a
    # composition root is for. The narrower alternative, `ConversationThreads`,
    # would have meant building three more use-cases to satisfy a Protocol
    # whose other methods nothing here calls.
    conversation_messages = AppendMessage(SqlConversationRepository(tenant_session))

    redis_client = create_redis_client(
        settings.redis,
        read_timeout_s=blocking_read_timeout_s(settings.events.consumer_block_ms),
    )
    consumer_name = _consumer_name("knowledge")

    consumer, subscriptions = build_knowledge_worker(
        redis_client=redis_client,
        documents=documents,
        # ب-7ج -- the `files` seam, bound the way every other
        # knowledge seam in this process is: `FilesQueryService`
        # over the repository already built above, satisfying
        # `ReadableFiles` structurally. One instance, and the
        # `names_for_files` read both corpus walks use.
        files=FilesQueryService(files),
        pipeline=pipeline,
        content_resolver=content_resolver,
        summary_builder=summary_builder,
        summaries=summaries,
        conversation_messages=conversation_messages,
        outbox=outbox,
        uow=tenant_session,
        ledger=ledger,
        consumer_name=consumer_name,
        block_ms=settings.events.consumer_block_ms,
        batch_count=settings.events.consumer_batch_count,
        max_deliveries=settings.events.max_retries_before_dlq,
        heartbeat=build_heartbeat(settings.health.heartbeat_dir, "knowledge"),
        # `F-4` -- the number stays configuration, not a constant buried in
        # the handler, because what counts as "too long" is a fact about the
        # model a deployment runs, not about this code.
        summarize_max_duration_s=settings.limits.summarize_job_max_duration_s,
        sweep_interval_s=settings.events.consumer_sweep_interval_s,
        stale_idle_ms=int(settings.events.consumer_stale_idle_s * 1000),
        dlq_watch_interval_s=settings.events.dlq_watch_interval_s,
    )

    # `CompositionRoot.disposables()`'s own `_close_vault` precedent -- hvac
    # is synchronous (it wraps a `requests.Session`), so closing it needs the
    # same `asyncio.to_thread` offload. Written in step 15 against a function
    # that could not yet reach it (the raise was unconditional then); step 16
    # is what makes it reachable, with no rework, exactly as intended.
    async def _close_vault() -> None:
        await asyncio.to_thread(vault_client.adapter.close)

    # The LLM clients close here too, the way `CompositionRoot.disposables()`
    # closes its own `ollama_http`/`openai_http`. The 60 s pair had been
    # absent from this list since BE-RAG-009 first wired it -- one leaked
    # connection pool per worker shutdown; `F-1`'s second pair would have
    # made that two, so all four go in together rather than half the list
    # being right.
    disposables: list[Disposable] = [
        engine.dispose,
        qdrant_client.close,
        embedding_http.aclose,
        ollama_http.aclose,
        openai_http.aclose,
        summarize_ollama_http.aclose,
        summarize_openai_http.aclose,
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
    heartbeat: Heartbeat | None = None,
    sweep_interval_s: float = 0.0,
    stale_idle_ms: int = 0,
    dlq_watch_interval_s: float = 0.0,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the media worker's single ``stream.media``/``cg.media``
    subscription (04 §4). Every dependency here is a plain parameter -- this
    function is never itself blocked (``build_media_worker_from_env``'s
    docstring) and is exactly what
    ``tests/integration/test_media_worker_live.py`` calls directly -- since
    step 19, with the REAL ``WorkerMediaGenerator`` rather than a fake."""
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
        heartbeat=heartbeat,
        # ت-2: both default to 0 (off) so every direct caller of this builder
        # -- the live integration tests included -- keeps a consumer that only
        # ever reads, and only the  path below turns the sweep on.
        sweep_interval_s=sweep_interval_s,
        stale_idle_ms=stale_idle_ms,
        # ت-6: same default and the same reason -- a direct caller (a live
        # integration test) gets a consumer that reads and nothing else, and
        # only the `_from_env` path below turns the DLQ report on.
        dlq_watch_interval_s=dlq_watch_interval_s,
    )
    return consumer, subscriptions


async def build_media_worker_from_env() -> tuple[
    StreamConsumer, list[Subscription], list[Disposable]
]:
    """Build the media worker exactly as the ``media`` process runs it.
    **Nothing is blocked any more** (step 20 of
    ``deferred-adapters-plan.md``) — this was the LAST builder still raising
    under the module docstring's honest-failure rule, and with it that rule
    now covers nothing.

    The gap this closes was never a missing capability, only a missing
    assembly: step 19 shipped the ``MediaGenerator`` adapter
    (``workers/media_generation.py`` over ``OpenAIImage``) and every
    collaborator it needs had existed since step 15 (``build_vault``/
    ``bind_minio``) and step 18 (the resolver's ``image`` namespace). This
    function composes them, and composes them the SAME way
    ``build_knowledge_worker_from_env`` does — one Vault-secret shape
    validated in one place, not two free to drift.

    **``async def`` for the same one reason knowledge is** (step 15): binding
    MinIO is an ``await`` (05 §3's Vault read), and ``media_worker.py``'s
    entrypoint already runs inside ``asyncio.run``.

    **Only the ``image`` namespace of the routing table reaches the
    resolver**, mirroring the knowledge worker's ``embedding``-only
    narrowing and for the identical reason: this process wires no LLM and no
    embedding adapter, so a route it will never read must not be allowed to
    refuse its boot. The half it DOES read stays fully strict — an image
    route naming an unwired provider still stops this process dead, which is
    the point.

    **The generator is handed ``storage`` — the ``StorageHandle``, not the
    MinIO adapter directly.** Identical to the knowledge worker: the handle
    is what ``bind_minio`` installs into, and passing it keeps ONE binding
    step per process rather than a second reference that could be left
    unbound.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    _logger.info("media_worker.bootstrap_initialized", extra={"app_env": settings.app_env})

    engine = create_engine(_worker_db(settings.database))
    sessionmaker = create_sessionmaker(engine)
    tenant_session = TenantSessionFactory(sessionmaker)
    jobs = SqlMediaJobRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    ledger = SqlProcessedEventLedger(tenant_session)

    # Step 15's pair, unchanged -- the generated bytes land in the same
    # bucket, through the same adapter, as every user upload.
    vault_client, secrets, _ = build_vault(settings)
    storage = StorageHandle()
    await bind_minio(storage, secrets, settings.minio)

    # `media_timeout_s` (300s), not `llm_timeout_s` (60s): image generation is
    # a minutes-scale call, and this is the SAME choice `CompositionRoot`
    # makes for its own image client. `image_providers` is keyed by the
    # adapter's OWN `provider` attribute, never a literal, so the strict
    # parse below proves the configured route points at the very adapter the
    # generator will call.
    image_http = create_openai_image_http_client(timeout_s=settings.limits.media_timeout_s)
    image_provider = OpenAIImage(image_http)
    providers = SettingsProviderResolver(
        routing=_routing_for(settings.provider_routing, foreign=_FOREIGN_TO_MEDIA),
        llm_providers={},
        embedding_providers={},
        image_providers={image_provider.provider: image_provider},
        key_resolver=ResolveCredential(SqlCredentialRepository(tenant_session), secrets),
        keyless_providers=_KEYLESS_PROVIDERS,
    )
    generator: MediaGenerator = WorkerMediaGenerator(
        providers,
        # `spaces-backend-plan.md` step 6 — the existence seam is wired REAL
        # even though this process registers with `space_id=None` today and so
        # never reaches it (`WorkerMediaGenerator.generate` says why it has no
        # space to name yet). Wiring it now is what makes step 7 a one-line
        # change here instead of a new dependency to thread through boot.
        RegisterUpload(
            files, settings.limits, SpacesQueryService(SqlSpaceRepository(tenant_session))
        ),
        CompleteUpload(files),
        storage,
    )

    redis_client = create_redis_client(
        settings.redis,
        read_timeout_s=blocking_read_timeout_s(settings.events.consumer_block_ms),
    )
    consumer_name = _consumer_name("media")

    consumer, subscriptions = build_media_worker(
        redis_client=redis_client,
        jobs=jobs,
        generator=generator,
        outbox=outbox,
        uow=tenant_session,
        ledger=ledger,
        consumer_name=consumer_name,
        block_ms=settings.events.consumer_block_ms,
        batch_count=settings.events.consumer_batch_count,
        max_deliveries=settings.events.max_retries_before_dlq,
        heartbeat=build_heartbeat(settings.health.heartbeat_dir, "media"),
        sweep_interval_s=settings.events.consumer_sweep_interval_s,
        stale_idle_ms=int(settings.events.consumer_stale_idle_s * 1000),
        dlq_watch_interval_s=settings.events.dlq_watch_interval_s,
    )

    # The knowledge worker's `_close_vault` precedent -- hvac wraps a
    # synchronous `requests.Session`, so closing it needs the thread offload.
    async def _close_vault() -> None:
        await asyncio.to_thread(vault_client.adapter.close)

    disposables: list[Disposable] = [
        engine.dispose,
        image_http.aclose,
        redis_client.aclose,
        _close_vault,
    ]
    return consumer, subscriptions, disposables


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
    heartbeat: Heartbeat | None = None,
    sweep_interval_s: float = 0.0,
    stale_idle_ms: int = 0,
    dlq_watch_interval_s: float = 0.0,
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
        heartbeat=heartbeat,
        # ت-2: both default to 0 (off) so every direct caller of this builder
        # -- the live integration tests included -- keeps a consumer that only
        # ever reads, and only the  path below turns the sweep on.
        sweep_interval_s=sweep_interval_s,
        stale_idle_ms=stale_idle_ms,
        # ت-6: same default and the same reason -- a direct caller (a live
        # integration test) gets a consumer that reads and nothing else, and
        # only the `_from_env` path below turns the DLQ report on.
        dlq_watch_interval_s=dlq_watch_interval_s,
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

    redis_client = create_redis_client(
        settings.redis,
        read_timeout_s=blocking_read_timeout_s(settings.events.consumer_block_ms),
    )
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
        heartbeat=build_heartbeat(settings.health.heartbeat_dir, "memory"),
        sweep_interval_s=settings.events.consumer_sweep_interval_s,
        stale_idle_ms=int(settings.events.consumer_stale_idle_s * 1000),
        dlq_watch_interval_s=settings.events.dlq_watch_interval_s,
    )
    disposables: list[Disposable] = [
        engine.dispose,
        qdrant_client.close,
        embedding_http.aclose,
        redis_client.aclose,
    ]
    return consumer, subscriptions, disposables
