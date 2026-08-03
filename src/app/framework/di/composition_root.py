r"""The single Composition Root (10-code-standards §4, D-17).

This is the *only* place allowed to import ``app.infrastructure`` (enforced by
import-linter contract 6). It builds concrete adapters, binds them to their
ports via Manual DI, and hands typed dependencies to the rest of the system —
no service locator, no global singletons, no ambient state.

Phase 1 wires configuration + structured logging. Phase 2 extends this to
construct the driven-port adapters (Postgres/Redis/MinIO/Qdrant/Vault/Firebase/
LLM) and expose them as an injectable container. Wired so far: the Postgres
persistence stack (2.2 -- engine/sessionmaker/RLS session factory that every
``modules/*/adapters/sql_repository.py`` is built against), the Redis
``CacheProvider`` (2.3 -- ``cache``, with the raw ``redis_client`` exposed for
shutdown ``aclose()``, mirroring ``engine``'s dispose-at-shutdown precedent),
the Qdrant ``HybridVectorStore`` (2.5 -- ``vector_store``, with the raw
``qdrant_client`` exposed for the same shutdown-``close()`` precedent), the
Vault ``SecretsProvider`` (2.6 -- ``secrets``, with the raw ``vault_client``
exposed alongside it, the same client/adapter pairing, and since ن-10 a
``vault_health`` probe WRAPPING that same ``secrets`` rather than a second
client), the Firebase
``AuthProvider`` (2.7 -- ``auth``, with the raw ``firebase_http`` client
exposed for shutdown ``aclose()``, the same client/adapter pairing), and the
Ollama + OpenAI ``LLMProvider``\ s (2.8-a + 2.8-ب-1 -- ``llm_providers``, a
``dict[str, LLMProvider]`` keyed by each adapter's OWN ``provider``
attribute rather than a hardcoded literal, with the raw ``ollama_http`` and
``openai_http`` clients each exposed for the same shutdown-``aclose()``
precedent -- a SECOND, deliberately separate client, since OpenAI's
``base_url`` is entirely different from Ollama's). Step 19 of
``deferred-adapters-plan.md`` adds the OpenAI Images ``ImageProvider``
(``image_http``/``OpenAIImage``, keyed by the same own-attribute rule) --
a THIRD OpenAI-hosted client rather than a reuse of ``openai_http``, because
its timeout budget is a different setting entirely (``media_timeout_s`` 300s
vs ``llm_timeout_s`` 60s) and sharing one client would force minutes-long
image work and 60s chat work onto the same deadline.

``llm_providers`` is a plain injected mapping, not a Service Locator: this
root builds it once, by construction, and hands the finished ``dict`` to
whatever needs it -- there is no ``get_provider(name)`` method here, and
nothing in this module resolves a provider by name at call time. The
Composition Root is the ONE place allowed to know every concrete
``LLMProvider`` exists; ``framework/providers/resolver.py``'s
``SettingsProviderResolver`` (built in 2.9) is the actual consumer, injected
with this SAME mapping and doing its own name-based lookup against
``Settings.provider_routing`` (D-16) -- a distinction with a real
consequence: swapping/adding a provider is still a Composition Root wiring
change, never a runtime registry mutation from elsewhere. ``llm_providers``
stays unchanged in SHAPE by adding OpenAI (still ``dict[str, LLMProvider]``)
-- 2.9 consumed it exactly as 2.8-a left it, zero debt.

Unlike Ollama, OpenAI adds no ``Settings`` field and reads no boot-time env
var: its ``base_url`` is a module constant
(``ai_providers/llm/openai_llm.py``'s own module docstring explains why),
and its API key is resolved per-request by ``CredentialResolver`` (not
built yet), never read here. ``from_env`` therefore gains no new boot
requirement from OpenAI (contrast Firebase's boot-mandatory
``FIREBASE_PROJECT_ID``, 2.7).

That client/adapter pairing is the whole reason ``disposables()`` exists (see
its docstring): every raw client exposed above is handed to the API's shutdown
lifespan as one teardown thunk, the same ``list[Disposable]`` each worker
entrypoint already closes (``workers/bootstrap.py``, ``framework/di/
lifecycle.py``).

``FIREBASE_PROJECT_ID`` is now boot-mandatory: ``FirebaseAuth.__init__``
fails fast with ``ValidationError`` on an empty value (D5, 2.7), so
``from_env()`` itself now raises rather than booting an app whose every
authenticated request would 401 (``08 §7``'s own troubleshooting row for
this: "401 دائماً ⇐ صلاحية توكن Firebase / ``FIREBASE_PROJECT_ID``"). The
blast radius today is zero -- nothing calls ``from_env()`` until the Phase 6
entrypoint exists -- but this is load-bearing from that point on.

**MinIO is wired in TWO phases (6.1-هـ-1 closed debt (ز), carried since
2.4).** Fetching MinIO's access/secret keys is an ASYNC read, ``await
secrets.get_secret("secret/data/minio")`` (05 §3), and ``from_env`` is a
plain, synchronous classmethod that cannot ``await`` anything. So ``from_env``
builds a ``StorageHandle`` — a structural ``StorageProvider`` whose real
adapter is bound later (``framework/di/storage_handle.py``) — and hands THAT
to everything that needs storage; the async ``connect_storage`` method on this
root delegates to ``framework/di/storage_binding.py::bind_minio``, which reads
the Vault secret, constructs the 2.4 ``MinioStorage`` adapter, and binds it
into the handle -- the SAME function the knowledge worker's own composition
root calls (``workers/bootstrap.py::build_knowledge_worker_from_env``, step
15 of ``deferred-adapters-plan.md``), so the secret-shape validation has one
home instead of two copies free to drift. The app's Phase-6 startup lifespan
calls ``connect_storage`` before the process serves traffic (``api.main``'s
``startup`` hooks), so by the first request the handle is live; a call on an
unbound handle — reachable only if startup was skipped — fails with the same
clean ``common.internal``/500 the old ``deps.storage = None`` guard produced.

**Phase 4.7-b-2 wired the application layer on top of the adapters** — the
2.9 ``SettingsProviderResolver``, the 4.1 agent runtime
(``InMemoryAgentRegistry`` + ``PluginLoader``) and the 4.7-b
``AgentOrchestrator``. This is the step both forward-looking
``.importlinter`` ignores were written for, and they are now CONSUMED rather
than merely tolerated: this module imports ``app.modules.credentials``/
``app.modules.files`` (contract 7's ``composition_root -> app.modules.**``)
and ``app.agents.orchestrator`` (a NEW, deliberately narrow entry — the root
builds the orchestrator but must still never import an agent PLUGIN, since
those are discovered dynamically by ``PluginLoader``, which is the whole
point of D-13/AC-04).

Everything in that block is synchronous by construction, which is the only
reason it fits in this classmethod: the one piece that needs an ``await``
(MinIO's secret) is also the one still deferred to the Phase-6 lifespan, so
no async composition path had to be invented here.

STILL NOT wired (``web_search``), for a stated reason — it leaves its
``AgentDependencies`` field ``None``, so an agent that needs it fails with a
clean ``common.internal``/500 (the uniform 4.4/4.6 guard) rather than
returning a silently wrong answer. ``storage`` graduated out of this list in
6.1-هـ-1 and ``knowledge`` graduated out of it in 2.10; both are kept below
as the record of how:

* ``knowledge`` (``deps.knowledge``) — **no longer ``None`` since 2.10**:
  ``KnowledgeRetrievalService`` now composes over a REAL ``RetrieveContext``
  (``embedding``, the ``ExternalEmbeddingProvider`` adapter over the central
  embedding service, + ``vector_store``), resolved per-call through
  ``_RoutedEmbeddingResolver`` over the SAME ``provider_resolver`` every LLM
  call already resolves through. The RAG agent now retrieves and cites real
  workspace chunks instead of running degraded; ``POST /search`` no longer
  answers 503 at the composition level. ``memory`` recall is unblocked by
  the SAME adapter (``workers/bootstrap.py``'s ``build_memory_worker_from_env``);
  the knowledge WORKER's index handler was blocked on a separate seam,
  ``DocumentContentResolver``, until steps 15-16 of
  ``deferred-adapters-plan.md`` closed it (``docs/log/3.100.md``) — and it
  closed it by REUSING this file: ``build_knowledge_worker_from_env`` binds
  MinIO through the same ``vault_binding``/``storage_binding`` pair
  ``connect_storage`` calls, and resolves the embedding model through the
  same ``SettingsProviderResolver`` built here, so index-time and search-time
  cannot drift into two different vector spaces.
* ``storage`` (``deps.storage``) — **no longer ``None`` since 6.1-هـ-1**: it
  is the ``StorageHandle`` described above, live once the startup lifespan has
  run ``connect_storage``. The Data-Analysis and File-Editing agents can read
  a workspace file in any correctly-deployed process; only a process whose
  startup was skipped still answers ``common.internal`` on the storage path.
* ``web_search`` (``deps.web_search``) — needs an Exa key (4.4's recorded
  limit); with none available the adapter is not constructed at all.

**4.7-e-1 wired the workflow seam** — ``workflow_registry`` (D-09) and
``conversations`` (the D-12 thread a workflow run owns). Both go into
``OrchestratorDependencies``, so ``invoke_workflow`` is live.

⚠️ **The workflow CATALOG is empty, and that is a finding rather than a
placeholder.** ``framework/workflows/definitions/`` still holds only its 0-byte
scaffold, so nothing is registered. Authoring one is blocked on a real
mismatch, not on effort: the single example the design offers (`11 §8`,
``rag_agent → data_analysis_agent → image_agent → video_agent →
file_editing_agent``) **cannot chain against the agents as built** —
``rag_agent`` emits ``{text, citations}`` while ``data_analysis_agent``
requires a ``file_id`` that no upstream step produces, and ``file_editing_agent``
needs both a ``file_id`` and an ``instruction``. ``input_map`` maps names, it
does not manufacture missing values. Picking a different pipeline is a product
decision about what the platform ships, so it is surfaced here instead of
invented; the machinery is proven on hand-built definitions in the tests.

The Phase-2.2 design brief additionally asked this method to construct
``SqlMediaJobRepository``/``RequestMedia`` directly. That was deferred at the
time because ``app.framework`` (the lowest layer) may not import
``app.modules``, and 2.2 was required to keep ``lint-imports`` 8/0 green
*without* editing ``.importlinter``. The exception the deferral waited for
(``app.framework.di.composition_root -> app.modules.**``) landed with the
``layers``/``framework-kernel`` contracts and is already consumed by the
``credentials``/``files``/``usage`` wiring above; **4.7-d-2 closes the
deviation** — ``MediaRequestService`` is composed here over exactly the
``tenant_session`` seam 2.2 exposed for it, unchanged. ``GetJobStatus`` is
finally constructed in 6.1-هـ-2 — inside the ``media`` bundle built for the
Phase-6 media router, its first-ever caller — honouring the rule that this
root constructs only what it hands to a live caller.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import hvac
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.plugin_loader import PluginLoader, PluginLoadReport
from app.framework.agent_runtime.registry import AgentRegistry, InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.lifecycle import Disposable
from app.framework.di.storage_binding import bind_minio
from app.framework.di.storage_handle import StorageHandle
from app.framework.di.vault_binding import build_vault
from app.framework.observability import configure_logging, get_logger
from app.framework.ports import (
    AuthProvider,
    CacheProvider,
    HybridVectorStore,
    LLMProvider,
    SecretsProvider,
)
from app.framework.ports.connector_provider import ConnectorProvider
from app.framework.ports.idempotency_store import IdempotencyStore
from app.framework.ports.metrics_source import MetricsSource
from app.framework.ports.vault_health import VaultHealth
from app.framework.providers.resolver import ProviderResolver, SettingsProviderResolver
from app.framework.settings import DatabaseSettings, Settings
from app.framework.streaming import ConnectionHub, make_notification_handler
from app.framework.workflows.registry import InMemoryWorkflowRegistry, WorkflowRegistry
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
from app.infrastructure.auth.firebase_auth import FirebaseAuth, create_firebase_http_client
from app.infrastructure.cache.redis_cache import (
    RedisCache,
    blocking_read_timeout_s,
    create_redis_client,
)
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from app.infrastructure.monitoring.metrics_source import SqlRedisMetricsSource
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.idempotency import SqlIdempotencyStore
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import PlatformSessionFactory, TenantSessionFactory
from app.infrastructure.streaming.redis_connection_registry import RedisWsConnectionRegistry
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client
from app.modules.access.adapters.sql_repository import SqlRoleAssignmentRepository
from app.modules.access.application.use_cases import AssignRole, Authorization
from app.modules.access.ports.inbound import AuthorizationService, RoleSeeding
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ConversationService,
    ConversationUseCases,
    GetConversation,
    ListConversationsByAgent,
    ListMessages,
    SoftDeleteConversation,
    StartConversation,
)
from app.modules.credentials.adapters.sql_repository import SqlCredentialRepository
from app.modules.credentials.application.use_cases import (
    AddUserCredential,
    CredentialUseCases,
    ListCredentials,
    ResolveCredential,
    RevokeCredential,
)
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FilesQueryService,
    FileTransferService,
    FileUseCases,
    RegisterUpload,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.integrations.adapters.sql_repository import (
    SqlConnectionRepository,
    SqlMcpServerRepository,
)
from app.modules.integrations.application.use_cases import (
    AuthorizeConnection,
    BeginConnection,
    CompleteOAuth,
    ConnectorDescriptor,
    DisableMcpServer,
    IntegrationsUseCases,
    ListConnections,
    ListConnectors,
    ListMcpServers,
    RegisterMcpServer,
    RevokeConnection,
    StartConnection,
)
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.retrieval import RetrieveContext
from app.modules.knowledge.application.use_cases import (
    GetDocument,
    KnowledgeRetrievalService,
    KnowledgeUseCases,
    ListDocuments,
)
from app.modules.knowledge.ports.retrieval import EmbeddingResolver, ResolvedEmbedding
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.use_cases import (
    GetJobStatus,
    MediaRequestService,
    MediaUseCases,
    RequestMedia,
)
from app.modules.usage.adapters.sql_repository import SqlUsageLedgerRepository
from app.modules.usage.application.use_cases import (
    CaptureUsage,
    EnforceLimit,
    GetUsageSummary,
    ListLimits,
    SetLimits,
    UsageCaptureService,
    UsageEnforcementService,
    UsageUseCases,
)
from app.modules.workspace.adapters.sql_repository import (
    SqlUserRepository,
    SqlWorkspaceRepository,
)
from app.modules.workspace.application.use_cases import (
    GetWorkspace,
    ProvisionUserOnFirstLogin,
    RenameWorkspace,
    WorkspaceUseCases,
)
from app.modules.workspace.ports.inbound import UserProvisioning
from app.modules.workspace.ports.repository import WorkspaceRepository

_logger = get_logger(__name__)

# The OAuth callback's path UNDER the configured API prefix (6.1-و-4-1). It
# lives here rather than in the router because a redirect URI is wiring, not
# routing: this value is registered with each third-party provider and is
# what `BeginConnection` checks against the configured base. و-4-2 mounts the
# route that must answer on it.
_OAUTH_CALLBACK_PATH = "/integrations/connections/oauth/callback"

# The notification bridge's consumer-group PREFIX and its stream→types map
# (04 §2's topology table + §4's global catalog): every `cg.notify.<hostname>.
# <pid>` group reads BOTH heavy-result streams and pushes the four
# client-facing outcomes to live sockets. It is a SEPARATE group family from
# the workers' `cg.knowledge`/`cg.media`, so it gets its own independent copy
# of every event on those streams; a type it does not handle (e.g.
# `knowledge.document.registered.v1`, which belongs to the workers) is the
# engine's normal XACK-skip, not an error.
#
# ⭐ A GROUP PER PROCESS, not one shared group -- P0-2
# (docs/pre-release-review.md §2, docs/log/3.81.md). Until 3.81 this was a
# single literal `"cg.notify"`, and that was a live-in-production bug, not a
# style choice: `ConnectionHub` (`framework/streaming/hub.py`) is a purely
# in-process registry, so a WebSocket session lives in exactly ONE OS
# process. Both deployment paths default `WEB_CONCURRENCY=2` -- i.e. even a
# SINGLE API replica is actually two sibling gunicorn workers on one host,
# each running its OWN copy of this bridge over its OWN hub. Redis Streams
# hands each stream entry to exactly ONE member of a consumer group, so a
# group shared by both siblings silently starved whichever one did not hold
# the target session -- roughly half of every `knowledge.document.*`/
# `media.job.*` notification, `XACK`ed and gone, no error anywhere. A group
# PER PROCESS (`_CG_NOTIFY_PREFIX.<hostname>.<pid>`, built by `from_env`
# below) makes every sibling an independent reader of the full stream --
# exactly the same mechanism that already lets `cg.knowledge`/`cg.media`
# read `stream.knowledge` independently of EACH OTHER (verified live,
# ``test_two_groups_on_one_stream_each_independently_receive_the_entry``,
# docs/log/3.46.md), one layer further down. The lifecycle this now requires
# (destroy on clean shutdown, sweep an orphan after an unclean kill) lives on
# `CompositionRoot` below (`teardown_notify_bridge`/
# `sweep_stale_notify_groups`).
_CG_NOTIFY_PREFIX = "cg.notify"
_NOTIFY_STREAMS: dict[str, tuple[str, ...]] = {
    "stream.knowledge": (
        "knowledge.document.indexed.v1",
        "knowledge.document.indexing_failed.v1",
    ),
    "stream.media": (
        "media.job.generated.v1",
        "media.job.failed.v1",
    ),
}


def build_notification_consumer(
    hub: ConnectionHub,
    redis_client: Redis,
    *,
    group: str,
    consumer_name: str,
    block_ms: int,
    batch_count: int,
    max_deliveries: int,
) -> tuple[StreamConsumer, list[Subscription]]:
    """Wire the notification bridge (5.3-د, per-process group since 3.81)
    over the generic 5.1 ``StreamConsumer``: two subscriptions
    (``stream.knowledge`` + ``stream.media``) under ONE caller-supplied
    ``group``, every 04 §4 notify type mapped onto the ONE
    ``make_notification_handler(hub)`` closure.

    ``group`` is a PARAMETER, not the module constant it used to be
    (``_CG_NOTIFY`` -- renamed ``_CG_NOTIFY_PREFIX`` above, since it is now
    only a prefix): ``from_env`` derives a group unique to THIS process
    (``f"{_CG_NOTIFY_PREFIX}.{hostname}.{pid}"``, the same tag already used
    for ``consumer_name``), and a test may pass any group it likes (the
    ``test_stream_consumer_live``/``test_notification_bridge_live``
    precedent: a unique throwaway name per run, R6). See the module-level
    comment above ``_NOTIFY_STREAMS`` for the full defect a shared, hardcoded
    group used to cause.

    A pure wiring function over its parameters — the ``build_*_worker``
    precedent (``workers/bootstrap.py``): the integration test calls it with
    a real Redis client and a real hub, and ``from_env`` calls it with the
    process-wide values. It is assembled by the API's Composition Root, NOT
    run as a standalone worker (``workers/main.py``): the hub it pushes to
    is the SAME in-process hub the WebSocket endpoint registers sessions on.

    **"Must live in the API process" is necessary, and was once mistaken for
    sufficient.** A separate deployable (a standalone worker) would indeed
    hold a different, empty hub with none of this replica's live
    connections — that half of the reasoning was always right. What it
    missed: under either deployment path's default ``WEB_CONCURRENCY=2``,
    the "API process" is not one process either — it is several SIBLING
    gunicorn workers on the same host, each already living inside the API
    deployable, each with its OWN hub. Running this bridge in the API
    process answers "which deployable"; it says nothing about "which one of
    several OS processes inside that deployable" — that second question is
    exactly what the ``group`` parameter answers.

    ``max_deliveries`` is required by the engine but is effectively inert here:
    the handler never raises (its own docstring), so handler-failure DLQ cannot
    fire. The engine's malformed/unroutable dead-lettering still applies — a
    corrupt envelope on the stream is dead-lettered before the handler sees it
    — which is the DLQ this path genuinely inherits.
    """
    handler = make_notification_handler(hub)
    subscriptions = [
        Subscription(
            stream=stream,
            group=group,
            handlers=dict.fromkeys(types, handler),
        )
        for stream, types in _NOTIFY_STREAMS.items()
    ]
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name=consumer_name,
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
    )
    return consumer, subscriptions


def _pid_is_alive(pid: int) -> bool:
    """Whether OS pid ``pid`` currently names a running process -- the
    standard POSIX liveness probe, ``os.kill(pid, 0)``: signal ``0`` sends
    nothing but still performs the kernel's existence/permission check.
    ``ProcessLookupError`` is the ONLY "no": the pid names no process right
    now. ``PermissionError`` means the pid exists but belongs to a process
    this one may not signal -- still alive. Any other outcome (including no
    exception) also means alive. Used only by ``_sweep_stale_notify_groups``
    below; see that function's docstring for why this check is what makes
    the sweep safe against a concurrently-booting sibling.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _sweep_stale_notify_groups(
    consumer: RedisStreamsConsumer, streams: Iterable[str], *, hostname: str
) -> list[str]:
    """Destroy every ``cg.notify.<hostname>.<pid>`` group on ``streams`` whose
    OWNING PROCESS is provably no longer running (docs/log/3.81.md) — the
    orphan a SIGKILLed sibling leaves behind forever: nothing will ever
    ``setup()`` under that exact dead pid's name again, so an un-``XGROUP
    DESTROY``\\ed group keeps its (empty, since nobody is reading it) pending-
    entries bookkeeping in Redis indefinitely. Returns the group names it
    actually destroyed, so a caller/test can assert on exactly what happened.

    **Why this cannot destroy a LIVE sibling's group, even though siblings
    boot concurrently.** Two independent guards, both necessary:

    1. **Host-scoped by construction.** Only groups whose name starts with
       THIS process's own ``hostname`` are even inspected — a different
       replica's groups (a different container, a different hostname under
       the default Compose/RunPod topology) are never touched, sweep or no
       sweep, regardless of what pid numbers happen to collide across hosts.
    2. **Liveness-checked, not merely "not mine".** Every surviving
       candidate's trailing segment is a pid, and it is destroyed ONLY if
       ``_pid_is_alive`` — the OS's own process table, via ``os.kill(pid,
       0)`` — answers NO. A sibling that is concurrently booting is, at the
       exact instant this sweep runs, in one of exactly two states: it has
       not yet created its OWN group (this hook is wired to run BEFORE the
       bridge's own ``setup()`` — ``api/main.py``'s ``startup=`` list
       completes before any ``background=`` task starts), so there is
       nothing yet to sweep; or it already has, in which case its pid is BY
       DEFINITION alive right now and guard 2 skips it. There is no third
       state — a process cannot hold a Redis consumer group open while
       simultaneously being dead.

    The one residual, honestly-named risk: OS pid reuse. If a crashed
    sibling's exact pid were reassigned to a brand-new process in the
    vanishingly small window between this sweep's liveness check and that
    new process reaching its own ``setup()``, the sweep would (correctly, by
    its own rules) destroy a group that pid does not yet own — indistinguishable,
    from here, from the ordinary orphan case this function exists to clean up.
    This is accepted rather than engineered around with a pid-start-time
    fingerprint this codebase has no other use for: the new process's own
    ``ensure_group`` simply recreates the group the next time it calls
    ``setup()``, so the failure mode this leaves is "one missed notification
    during an astronomically unlikely race", not data loss or a crash.
    """
    prefix = f"{_CG_NOTIFY_PREFIX}.{hostname}."
    swept: list[str] = []
    for stream in streams:
        for group in await consumer.list_groups(stream):
            if not group.startswith(prefix):
                continue
            pid_text = group[len(prefix) :]
            if not pid_text.isdigit() or _pid_is_alive(int(pid_text)):
                continue
            await consumer.destroy_group(stream, group)
            swept.append(group)
    if swept:
        _logger.warning("composition_root.notify_stale_groups_swept", extra={"groups": swept})
    return swept


def _build_embedding(settings: Settings) -> tuple[httpx.AsyncClient, ExternalEmbeddingProvider]:
    """The central embedding service's client + adapter (2.10) — a helper so
    ``from_env`` stays under its statement ceiling, the ``_build_integrations``/
    ``_build_identity_faces`` precedent. Same client/adapter pairing as every
    other http-backed driven adapter this root builds (``ollama_http``/
    ``ollama_llm``, ``openai_http``/``openai_llm``)."""
    embedding_http = create_embedding_http_client(settings.embedding_service)
    embedding = ExternalEmbeddingProvider(embedding_http, settings.embedding_service)
    return embedding_http, embedding


def _build_metrics_source(
    settings: Settings, redis_client: Redis
) -> tuple[AsyncEngine, SqlRedisMetricsSource]:
    """The `/metrics` endpoint's OWN engine + adapter (P1-3, docs/
    p1-hardening-plan.md §3 step 10) -- a helper so `from_env` stays under
    its statement ceiling, the `_build_embedding`/`_build_integrations`
    precedent.

    A SEPARATE ``AsyncEngine`` from ``engine`` above, over ``settings.metrics
    .database_url`` -- never the same DSN ``app_rw`` connects with. See
    ``MetricsSettings``'s own docstring for why: ``app_rw`` is INSERT-only on
    ``platform.outbox`` (``app.ops.provision``'s deliberate grant), so
    reading it at all needs a role that never otherwise exists in this
    process. Small pool (``pool_size=2, max_overflow=2``) rather than the
    app's own ``db.pool_size``/``db.max_overflow`` -- a scrape is infrequent
    and quick, and this connection has no other caller to share a bigger
    pool with.

    ``redis_client`` is the SAME shared client every other Redis-backed
    collaborator in this root already holds -- there is no per-role ACL
    split on the Redis side the way there is on Postgres's, so a SECOND
    client here would be a redundant connection for no isolation gained
    (``SqlRedisMetricsSource``'s own module docstring).
    """
    metrics_engine = create_engine(
        DatabaseSettings(url=settings.metrics.database_url, pool_size=2, max_overflow=2)
    )
    return metrics_engine, SqlRedisMetricsSource(metrics_engine, redis_client)


def _build_notify_bridge(
    settings: Settings, redis_client: Redis
) -> tuple[ConnectionHub, StreamConsumer, list[Subscription], Redis]:
    """The 5.3 live-streaming pieces — a helper so ``from_env`` stays under
    its statement ceiling, the ``_build_embedding``/``_build_integrations``
    precedent. The hub is shared by the WS endpoint and the notify bridge
    (both live in THIS process); the bridge reads ``cg.notify.<host>.<pid>``
    — a group unique to THIS process (3.81's fix, see the comment above
    ``_NOTIFY_STREAMS``) — off the two heavy-result streams over the same 5.1
    engine, and Phase 6's lifespan starts ``notify_consumer.run(...)`` as a
    background task next to the mounted WS router (after
    ``CompositionRoot.sweep_stale_notify_groups`` has run, and before
    ``teardown_notify_bridge`` destroys THIS same group).

    **P1-8 (step 11) — the hub's per-user CAP is not per process.** The
    routing half above is correctly per process; the admission half is not,
    and used to be (``hub.py``'s old ``_user_counts`` dict), which made the
    announced ``Limits.ws_connections_per_user`` really "5 x gunicorn workers
    x replicas" — the same defect root as P0-2 one layer over. The count now
    lives in Redis behind ``WsConnectionRegistry``, bound HERE (only the
    Composition Root may import ``app.infrastructure``) over the SAME shared
    ``redis_client`` every other short-lived Redis call this process makes
    holds: registry reads/writes are quick, non-blocking round trips, so they
    belong on the fail-fast client, not the one built for a consumer that
    parks on a socket for seconds at a time. The renewal loop that keeps live
    entries from ageing out is started/stopped by the API lifespan
    (``api/main.py::create_production_app``), not here.

    Returns a SEPARATE Redis client too (stream-topology-plan.md §3, item 4),
    built with ``blocking_read_timeout_s`` and used for nothing but
    ``build_notification_consumer`` below: the notify bridge is a blocking
    consumer of the exact shape the three workers are (§1-أ-2), so it needs
    the same longer read timeout, and it cannot share the socket-level
    ``2.0`` fail-fast client the request path (``RedisCache``, above) and the
    registry above depend on to stay fast. ``from_env`` adds it to
    ``disposables()`` exactly like ``redis_client``, so it is closed the same
    way — a second connection pool, never a leaked one.
    """
    hub = ConnectionHub(
        max_connections_per_user=settings.limits.ws_connections_per_user,
        registry=RedisWsConnectionRegistry(redis_client),
    )
    notify_redis_client = create_redis_client(
        settings.redis,
        read_timeout_s=blocking_read_timeout_s(settings.events.consumer_block_ms),
    )
    notify_process_tag = f"{socket.gethostname()}.{os.getpid()}"
    notify_consumer, notify_subscriptions = build_notification_consumer(
        hub,
        notify_redis_client,
        group=f"{_CG_NOTIFY_PREFIX}.{notify_process_tag}",
        consumer_name=f"notify.{notify_process_tag}",
        block_ms=settings.events.consumer_block_ms,
        batch_count=settings.events.consumer_batch_count,
        max_deliveries=settings.events.max_retries_before_dlq,
    )
    return hub, notify_consumer, notify_subscriptions, notify_redis_client


def _build_integrations(
    settings: Settings,
    cache: CacheProvider,
    tenant_session: TenantSessionFactory,
    secrets: SecretsProvider,
) -> IntegrationsUseCases:
    """The API-facing integrations bundle (6.1-و-4-1…و-4-3) — a helper so
    ``from_env`` stays under its statement ceiling.

    Three deliberate emptinesses:

    ``connector_providers`` is empty because
    ``infrastructure/integrations/oauth_connector.py`` is still an empty file
    (the same shape of Phase-2 scheduling gap as the embedding adapter).
    Unlike that one it needs no 503 seam: an empty catalog is a state the
    contract already models, so ``GET /connectors`` answers with an empty
    collection and ``POST /connections`` refuses every key with
    ``integrations.connector_unknown`` — both true statements.

    ``connector_catalog`` is what those adapters WOULD describe. It is
    Composition-Root code rather than settings for the 2.9 decision-6 reason:
    which connectors exist is a structural property of the adapters this root
    builds, not user-editable configuration.

    The redirect URI is derived here and never accepted from a client (the
    open-redirect hole); ``BeginConnection``'s guard then checks it against
    the configured base, so an unconfigured deployment fails closed with a
    422 rather than sending a user anywhere.

    ``secrets`` (6.1-و-4-2) is the SAME Vault Transit provider ``credentials``
    holds: the ``tenant-secrets`` key is unified across both modules by SEC-07,
    and ``CompleteOAuth`` is the first integrations face to need it — it is
    where a provider's raw tokens exist for the few statements between the
    exchange and ``encrypt`` (INV-I1).

    ``tools=None`` (6.1-و-4-3) is the third: ``WorkspaceToolCatalog`` needs a
    ``ConnectorToolset`` **and** an ``MCPClient``, and both adapter files are
    still empty. This one DOES need the 503 seam the other two do not,
    because an empty tool list would be a false statement rather than a true
    one — a workspace with a live MCP server has tools this deployment simply
    cannot go and discover. Note that MCP *registration* is fully wired
    regardless: a server can be registered, listed and disabled today, and
    its tools appear the moment the client adapter lands.
    """
    connector_providers: dict[str, ConnectorProvider] = {}
    connector_catalog: tuple[ConnectorDescriptor, ...] = ()
    oauth_base = settings.integrations.oauth_redirect_base_url or ""
    oauth_redirect_uri = f"{oauth_base.rstrip('/')}{settings.api_prefix}{_OAUTH_CALLBACK_PATH}"
    connections = SqlConnectionRepository(tenant_session)
    servers = SqlMcpServerRepository(tenant_session)
    begin = BeginConnection(
        connections, connector_providers, cache, settings.integrations, settings.limits
    )
    return IntegrationsUseCases(
        list_connectors=ListConnectors(connector_catalog),
        list_connections=ListConnections(connections),
        start=StartConnection(begin, oauth_redirect_uri),
        authorize=AuthorizeConnection(connections, begin, oauth_redirect_uri),
        revoke=RevokeConnection(connections),
        complete=CompleteOAuth(connections, connector_providers, cache, secrets),
        list_mcp_servers=ListMcpServers(servers),
        register_mcp_server=RegisterMcpServer(
            servers,
            secrets,
            settings.integrations,
            settings.limits,
            # The catalog's keys, so a server name can never shadow a
            # connector prefix in `<prefix>.<tool>` routing. Empty today,
            # exactly as empty as the catalog it is derived from.
            frozenset(descriptor.key for descriptor in connector_catalog),
        ),
        disable_mcp_server=DisableMcpServer(servers),
        tools=None,
    )


class _RoutedEmbeddingResolver:
    """Adapts the framework ``ProviderResolver`` (2.9) to the knowledge
    module's temporary ``EmbeddingResolver`` seam (``ports/retrieval.py``,
    3.k3) — exactly the adaptation that seam's own docstring anticipated:
    "once ``ProviderResolver`` exists, the Composition Root can adapt it to
    this same seam ... without touching the inbound port's signature." 2.10
    is what finally makes a ``ProviderResolver`` with a wired embedding route
    exist to adapt.

    A one-method glue class, not a use-case: it performs no I/O of its own
    beyond the single delegated ``await``, and it owns no state but the
    resolver it wraps.
    """

    def __init__(self, provider_resolver: ProviderResolver) -> None:
        self._provider_resolver = provider_resolver

    async def resolve_embedding(self, ctx: ExecutionContext) -> ResolvedEmbedding:
        _, resolved = await self._provider_resolver.resolve_embedding(ctx)
        return ResolvedEmbedding(model=resolved.model, api_key=resolved.api_key)


if TYPE_CHECKING:

    def _embedding_resolver_conforms(resolver: _RoutedEmbeddingResolver) -> EmbeddingResolver:
        """mypy-gate structural proof that ``_RoutedEmbeddingResolver``
        satisfies the knowledge module's ``EmbeddingResolver`` seam — the
        ``providers/resolver.py`` ``_conforms`` precedent, applied at the
        one wiring site allowed to import both sides."""
        return resolver


def _build_identity_faces(
    tenant_session: TenantSessionFactory,
    sessionmaker: async_sessionmaker[AsyncSession],
    workspaces: WorkspaceRepository,
) -> tuple[UserProvisioning, RoleSeeding, AuthorizationService]:
    """The three faces the authentication path runs on (6.4-أ) — a helper so
    ``from_env`` stays under its statement ceiling.

    Each is returned as a NARROW inbound port rather than as the use-case
    behind it, and that is the security shape rather than a typing nicety:
    ``api/middleware/auth.py`` runs BEFORE any RBAC guard, so whatever it can
    reach is reachable un-guarded. It gets "mirror an identity", "seed the
    first owner of a workspace you just minted", and "read roles" — not
    ``AssignRole.execute``, which would let the authentication path grant any
    role in any workspace.

    Both role faces are built over ONE repository instance, so the roles a
    login seeds are exactly the roles the next request reads.

    ``PlatformSessionFactory`` is constructed HERE and nowhere else (R1): the
    ``get_by_firebase_uid`` lookup must run before any tenant scope exists, so
    it sets the ``app.platform_read`` sentinel instead of ``app.workspace_id``.
    ``rls.py`` calls any new consumer of that factory a security-review item —
    building it inside its single consumer's construction, rather than as a
    root-level local, is what keeps that true by construction.
    """
    role_assignments = SqlRoleAssignmentRepository(tenant_session)
    users = SqlUserRepository(tenant_session, PlatformSessionFactory(sessionmaker))
    assign_role = AssignRole(role_assignments)
    return (
        ProvisionUserOnFirstLogin(workspaces, users),
        assign_role,
        Authorization(role_assignments),
    )


@dataclass(slots=True)
class CompositionRoot:
    """Holds process-wide, request-independent wiring built once at startup."""

    settings: Settings
    engine: AsyncEngine
    tenant_session: TenantSessionFactory
    redis_client: Redis
    # stream-topology-plan.md §3, item 4 — a SECOND client, for the notify
    # bridge's blocking read alone (``build_notification_consumer`` via
    # ``_build_notify_bridge``). ``redis_client`` above stays on the 2.0
    # fail-fast socket timeout every request-path caller needs; this one is
    # built with ``blocking_read_timeout_s`` because the bridge parks on
    # ``XREADGROUP ... BLOCK`` exactly like the three workers do (§1-أ-2).
    notify_redis_client: Redis
    cache: CacheProvider
    qdrant_client: AsyncQdrantClient
    vector_store: HybridVectorStore
    vault_client: hvac.Client
    secrets: SecretsProvider
    # ن-10 (docs/p1-hardening-plan.md §5-ب; the outage in docs/log/3.94.md) --
    # the `/metrics` gauge that says whether this process can still
    # authenticate to Vault. A thin wrapper AROUND `secrets` above, never a
    # second Vault client: sharing the client means it shares the token and
    # the relogin closure too, so the probe answers for the credential the
    # request path actually uses.
    vault_health: VaultHealth
    firebase_http: httpx.AsyncClient
    auth: AuthProvider
    ollama_http: httpx.AsyncClient
    openai_http: httpx.AsyncClient
    embedding_http: httpx.AsyncClient
    # Step 19 -- the OpenAI Images client, exposed for the same
    # shutdown-`aclose()` reason as every other raw HTTP client above.
    image_http: httpx.AsyncClient
    llm_providers: dict[str, LLMProvider]
    # 4.7-b-2 — the application layer, wired on top of the adapters above.
    provider_resolver: ProviderResolver
    agent_registry: AgentRegistry
    plugin_report: PluginLoadReport
    orchestrator: AgentOrchestrator
    # 4.7-e-1 — exposed alongside `agent_registry` for the same reason: the
    # Phase-6 `GET /workflows` listing reads the catalog directly, without
    # going through the orchestrator (which only RUNS one).
    workflow_registry: WorkflowRegistry
    # 6.1-ج-2 — the conversations use-cases the API layer calls directly
    # (`10 §3`: the API is thin and calls a Use-Case). Bundled by the module so
    # `ApiServices` grows ONE field, and built off the same `tenant_session`
    # every other repository uses.
    conversations: ConversationUseCases
    # 5.3 — the live-streaming pieces, shared IN THIS PROCESS. `hub` is handed
    # to the WebSocket endpoint (`create_ws_router`, 5.3-ج) AND to the
    # notification bridge below (5.3-د), so both the sockets a client holds and
    # the worker results pushed to them meet on one registry. Phase 6's
    # lifespan mounts the router and starts `notify_consumer.run(...)` as a
    # background task; here they are built real so the process boots complete.
    hub: ConnectionHub
    notify_consumer: StreamConsumer
    notify_subscriptions: list[Subscription]
    # 6.1-هـ-2 — the two bundles the API layer consumes (the `conversations`
    # precedent: the module packages its faces so `ApiServices` grows ONE
    # field per module in هـ-3). `files` holds the presigned faces over the
    # storage handle below; `media` holds `submit` + the first-ever
    # `GetJobStatus` construction.
    files: FileUseCases
    media: MediaUseCases
    # 6.1-و-1 — the workspace/usage bundles the API layer consumes. Each
    # carries ONLY the client-reachable use-cases: `workspace` leaves out
    # provisioning (the auth path's) and archiving (no v1 route); `usage`
    # leaves out enforcement/capture, which stay wired to the orchestrator
    # alone (INV-U4) — the same `usage_ledger` instance backs both, so the
    # numbers a client reads are the ones enforcement reasons over.
    workspace: WorkspaceUseCases
    usage: UsageUseCases
    # 6.1-و-2 — the credentials bundle the API layer consumes. It holds
    # list/add/revoke and NOT `ResolveCredential`: the decrypting face stays
    # where it already is, inside `provider_resolver`'s `key_resolver` slot
    # (D-16). Both are built over the SAME repository instance, so the keys a
    # workspace manages here are exactly the ones resolution reads.
    credentials: CredentialUseCases
    # 6.1-و-3 / 2.10 — the knowledge bundle the API layer consumes: the two
    # document reads, plus `search`, now a REAL `KnowledgeRetrievalService`
    # (module docstring's "knowledge" bullet) rather than the `None` this
    # field carried before 2.10.
    knowledge: KnowledgeUseCases
    # 6.1-و-4-1 — the integrations bundle the API layer consumes: the
    # connector catalog plus the connection lifecycle. `invoke_tool` is
    # pointedly not in it (INV-I4 — the agent runtime reaches the tool system
    # through `ToolCatalog`, never an HTTP request), and neither is the
    # public OAuth callback, which و-4-2 mounts on its own unauthenticated
    # route because it takes no `ExecutionContext` at all.
    integrations: IntegrationsUseCases
    # 6.4-أ — the authentication path's three faces, typed as the narrow
    # inbound ports rather than the use-cases behind them. Not part of
    # `ApiServices`: a router must not be able to provision a user or seed a
    # role, and the only consumer is the `ApiAuthenticator` the API's app
    # factory builds from these three. `authorization` is also what 6.4-ب's
    # RBAC guards decide with.
    provisioning: UserProvisioning
    seeding: RoleSeeding
    authorization: AuthorizationService
    # 6.1-هـ-1 — the late-binding storage slot (debt (ز)). Built empty by
    # `from_env` (which cannot `await` the Vault read MinIO's keys need) and
    # bound by the async `connect_storage` below, which the Phase-6 startup
    # lifespan runs before the process serves traffic. Everything that needs
    # storage — the orchestrator's agent seam today, the files services next —
    # holds THIS handle, so binding it once lights them all up together.
    storage: StorageHandle
    # 3.79 — the `Idempotency-Key` ledger over `platform.idempotency_keys`.
    # Built here rather than inside any module because one table serves three
    # modules' create endpoints; handed to `ApiServices` and to nothing else,
    # so no use-case, worker or agent can reach it.
    idempotency: IdempotencyStore
    # P1-3 (docs/p1-hardening-plan.md §3 step 10) — `/metrics`'s OWN engine
    # (the `metrics_reader` role, `_build_metrics_source`'s own docstring) and
    # the adapter built over it + the shared `redis_client`. `metrics_engine`
    # is exposed alongside `metrics_source` for the same client/adapter
    # dispose-at-shutdown precedent every other raw client on this root
    # follows (`disposables()`).
    metrics_engine: AsyncEngine
    metrics_source: MetricsSource

    @classmethod
    def from_env(cls) -> CompositionRoot:
        """Load configuration, configure logging, and build the root container."""
        settings = load_settings()
        configure_logging(settings.log_level)
        _logger.info(
            "composition_root.initialized",
            extra={"app_env": settings.app_env, "api_prefix": settings.api_prefix},
        )

        engine = create_engine(settings.database)
        sessionmaker = create_sessionmaker(engine)
        tenant_session = TenantSessionFactory(sessionmaker)

        redis_client = create_redis_client(settings.redis)
        cache = RedisCache(redis_client)

        qdrant_client = create_qdrant_client(settings.qdrant)
        vector_store = QdrantVectorStore(qdrant_client)

        # 2.6 + 7.3 + ن-10 -- the three Vault objects, built together
        # (`framework/di/vault_binding.py::build_vault`, extracted in step
        # 15 of `deferred-adapters-plan.md` so the knowledge worker's own
        # composition root can build the SAME three objects without
        # importing this entire module).
        vault_client, secrets, vault_health = build_vault(settings)

        firebase_http = create_firebase_http_client()
        auth = FirebaseAuth(
            firebase_http,
            settings.firebase.project_id,
            settings.firebase.jwks_cache_ttl,
        )

        ollama_http = create_ollama_http_client(
            settings.ollama, timeout_s=settings.limits.llm_timeout_s
        )
        ollama_llm = OllamaLLM(ollama_http)

        openai_http = create_openai_http_client(timeout_s=settings.limits.llm_timeout_s)
        openai_llm = OpenAILLM(openai_http)

        # Step 19 -- `media_timeout_s` (300s), NOT `llm_timeout_s` (60s):
        # image generation is minutes-scale work. This process never
        # GENERATES an image (that is the media worker's job); it builds the
        # adapter so the ONE `PROVIDER_ROUTING` table can legally carry an
        # `image` route at all -- an unwired provider refuses to boot,
        # namespace-blind, so without this the API would die on a table the
        # media worker needs. Constructing the client opens no socket.
        image_http = create_openai_image_http_client(timeout_s=settings.limits.media_timeout_s)
        openai_image = OpenAIImage(image_http)

        # 2.10 — the central embedding service's client + adapter (the SAME
        # client/adapter pairing as every http-backed driven adapter above).
        # Closes the "knowledge"/"memory" scheduling gap the module docstring
        # used to record: `external_embedding.py` was 0 bytes until now.
        embedding_http, embedding = _build_embedding(settings)

        # Keyed by each adapter's OWN `provider` attribute, never a
        # hardcoded literal -- see the module docstring's `llm_providers`
        # paragraph (2.8-a). The explicit `tuple[LLMProvider, ...]`
        # annotation is REQUIRED for `mypy --strict`: without it, the
        # comprehension below infers `dict[str, OllamaLLM | OpenAILLM]`,
        # not `dict[str, LLMProvider]`.
        llm_adapters: tuple[LLMProvider, ...] = (ollama_llm, openai_llm)
        llm_providers: dict[str, LLMProvider] = {a.provider: a for a in llm_adapters}

        # ------------------------------------------------------------------
        # 4.7-b-2 — the application layer. Everything below is synchronous by
        # construction, which is why it fits in this classmethod at all: the
        # ONE piece that needs an `await` (MinIO's secret) is also the one
        # piece still deferred to the Phase-6 lifespan, so no async factory has
        # to be invented here yet.
        # ------------------------------------------------------------------
        # 6.1-و-2 — ONE credential repository behind both faces (the ج-2
        # precedent): the resolver's `key_resolver` below and the API bundle
        # further down. Hoisted out of the `key_resolver=` argument it used to
        # be constructed inline in, so the sharing is visible rather than
        # accidental.
        credential_repository = SqlCredentialRepository(tenant_session)
        # 6.1-و-3 — the knowledge row store, built for the first time here:
        # until now the module reached the process only through the worker
        # path, which constructs its own. The API's two document reads need
        # nothing else — no vector store, no embedding provider.
        document_repository = SqlDocumentRepository(tenant_session)

        provider_resolver = SettingsProviderResolver(
            routing=settings.provider_routing,
            llm_providers=llm_providers,
            # 2.10 -- keyed by the adapter's OWN `provider` attribute (the
            # `llm_providers` precedent above), never a hardcoded literal.
            embedding_providers={embedding.provider: embedding},
            # Step 19 -- no longer empty. Keyed by the adapter's OWN
            # `provider` attribute (`image:openai`, 06 §3's modality key),
            # never a hardcoded literal: the routing table, the wired
            # mapping, and the credential a tenant stores all have to agree
            # on one string, and reading it off the adapter is what makes
            # that impossible to get wrong in two places.
            image_providers={openai_image.provider: openai_image},
            key_resolver=ResolveCredential(credential_repository, secrets),
            # Composition-Root code on purpose (2.9 decision 6): which adapter
            # takes no credential is a structural property of the adapters
            # this root builds, not user-editable configuration. 2.10 adds
            # "embedding-local" alongside "ollama" -- the local embedding
            # model has no authentication of its own either.
            keyless_providers=frozenset({"ollama", "embedding-local"}),
        )

        agent_registry: AgentRegistry = InMemoryAgentRegistry()
        plugin_report = PluginLoader().load_into(agent_registry)
        _logger.info(
            "composition_root.agents_loaded",
            extra={
                "loaded": len(plugin_report.loaded),
                "failures": len(plugin_report.failures),
            },
        )

        # 4.7-c-2 -- metering (FR-131/132). INV-U4: these inbound ports are
        # handed ONLY to the orchestrator, never into `AgentDependencies`, so
        # no agent can reach `usage` even by accident.
        usage_ledger = SqlUsageLedgerRepository(tenant_session)

        # 6.1-و-1 — the usage bundle the API layer reads through. The SAME
        # `usage_ledger` instance backs the orchestrator's enforcement/capture
        # ports below, so `GET /usage` reports the very rows enforcement
        # decides on (the ج-2 one-repository precedent). The two inbound ports
        # are pointedly NOT in this bundle: INV-U4 keeps metering unreachable
        # from the API.
        usage = UsageUseCases(
            summary=GetUsageSummary(usage_ledger),
            list_limits=ListLimits(usage_ledger),
            set_limits=SetLimits(usage_ledger),
        )

        # 6.1-و-1 — the workspace bundle: read + rename the tenant root. Built
        # over the same `tenant_session`. `ProvisionUserOnFirstLogin` is built
        # SEPARATELY below (6.4-أ) rather than joining this bundle: its caller
        # is the authentication path, not a route, and it needs a user
        # repository the client-facing bundle has no business holding.
        workspace_repository = SqlWorkspaceRepository(tenant_session)
        workspace = WorkspaceUseCases(
            get=GetWorkspace(workspace_repository),
            rename=RenameWorkspace(workspace_repository),
        )

        # 6.4-أ — the three narrow faces the authentication path needs.
        provisioning, seeding, authorization = _build_identity_faces(
            tenant_session, sessionmaker, workspace_repository
        )

        # 6.1-و-2 — the credentials bundle, over the repository hoisted above
        # and the same `secrets` provider the resolver holds. `AddUserCredential`
        # is the only face here that touches Vault, and only to ENCRYPT.
        credentials = CredentialUseCases(
            list=ListCredentials(credential_repository),
            add=AddUserCredential(credential_repository, secrets),
            revoke=RevokeCredential(credential_repository),
        )

        # 6.1-و-3 / 2.10 — the knowledge bundle, `search` now real:
        # `RetrieveContext` (3.k3) composed over the SAME `embedding` adapter
        # and `vector_store` this root already built, resolved per-call
        # through `_RoutedEmbeddingResolver` over the SAME `provider_resolver`
        # every LLM call already resolves through. `knowledge.search` (not a
        # separate local — the statement-ceiling precedent every helper
        # function above already follows) is the exact same instance handed
        # to `OrchestratorDependencies` below as the RAG agent's
        # `KnowledgeAccess.retrieve` seam (module docstring) — one retrieval
        # path, reached two ways. Neither ingestion face is built here — a
        # request has no business registering or indexing a document.
        knowledge = KnowledgeUseCases(
            list_documents=ListDocuments(document_repository),
            get_document=GetDocument(document_repository),
            search=KnowledgeRetrievalService(
                RetrieveContext(embeddings=embedding, vectors=vector_store),
                _RoutedEmbeddingResolver(provider_resolver),
            ),
        )

        # 6.1-و-4-1 — the integrations bundle (built by the helper above, which
        # is where the two deliberate emptinesses are explained).
        integrations = _build_integrations(settings, cache, tenant_session, secrets)

        # 4.7-d-2 -- the media seam (D-04/D-18), request-atomic since 5.1-أ.
        # The SAME `tenant_session` backs THREE roles at once: the job
        # repository, the outbox, and the unit of work itself -- so the
        # aggregate write and the outbox append share one transaction (see
        # `MediaRequestService`'s atomicity note). One outbox / one jobs-repo
        # instance behind every face of each module (the ج-2 one-repository
        # precedent): the adapters are stateless, but two instances would
        # misrepresent the wiring.
        outbox = SqlEventOutbox(tenant_session)
        media_jobs = SqlMediaJobRepository(tenant_session)
        media_requests = MediaRequestService(
            RequestMedia(media_jobs, settings.limits),
            outbox,
            tenant_session,
        )
        # 6.1-هـ-2 -- the media bundle the API layer consumes: `submit` (the
        # full-aggregate face of the same atomic service) + `GetJobStatus`,
        # constructed NOW because its first-ever caller (the هـ-3 media
        # router) is what this bundle exists to serve.
        media = MediaUseCases(requests=media_requests, get_status=GetJobStatus(media_jobs))

        # 4.7-e-1 -- the workflow catalog (D-09: definitions are code). It is
        # built and wired REAL but registers nothing yet: authoring the
        # definitions is a separate act, and the one example the design gives
        # (`11 §8`) does not chain against the agents as built -- see the
        # module docstring's "not wired" section. An empty catalog is the
        # honest state: `GET /workflows` lists nothing and every
        # `POST /workflows/{key}/run` is a truthful `workflow.unknown` 404.
        workflow_registry: WorkflowRegistry = InMemoryWorkflowRegistry()

        # 6.1-ج-2/ج-3 — ONE repository instance behind both faces of the
        # module: the use-cases the API router calls, and the inbound port the
        # orchestrator writes turns through. Two instances would be harmless
        # (the adapter is stateless) but would misrepresent the wiring: there
        # is one conversations store, reached two ways.
        conversation_repository = SqlConversationRepository(tenant_session)
        conversations = ConversationUseCases(
            start=StartConversation(conversation_repository),
            get=GetConversation(conversation_repository),
            list_by_agent=ListConversationsByAgent(conversation_repository),
            list_messages=ListMessages(conversation_repository),
            soft_delete=SoftDeleteConversation(conversation_repository),
        )

        # 6.1-هـ-1 — the storage slot, empty here by necessity (module
        # docstring: MinIO's keys are an async Vault read). `connect_storage`
        # fills it at startup; handing the HANDLE to the orchestrator now means
        # the Data-Analysis/File-Editing agents go live at that same moment,
        # with no orchestrator rebuild.
        storage = StorageHandle()

        # 6.1-هـ-2 -- the files bundle the API layer consumes. ONE repository
        # instance behind both faces of the module (the orchestrator's
        # `FilesQuery` below shares it), and the presigned faces hold the SAME
        # storage handle the agents do: `connect_storage`'s single `bind`
        # lights everything up together.
        file_repository = SqlFileRepository(tenant_session)
        files_use_cases = FileUseCases(
            transfers=FileTransferService(
                RegisterUpload(file_repository, settings.limits),
                file_repository,
                storage,
                put_ttl_s=settings.minio.presign_put_ttl_s,
                get_ttl_s=settings.minio.presign_get_ttl_s,
            ),
            complete=CompleteUploadService(CompleteUpload(file_repository), outbox, tenant_session),
            delete=SoftDeleteFileService(SoftDeleteFile(file_repository), outbox, tenant_session),
        )

        orchestrator = AgentOrchestrator(
            OrchestratorDependencies(
                agents=agent_registry,
                executor=AgentLifecycleExecutor(),
                providers=provider_resolver,
                files=FilesQueryService(file_repository),
                media=media_requests,
                usage_enforcement=UsageEnforcementService(
                    EnforceLimit(usage_ledger, settings.usage)
                ),
                usage_capture=UsageCaptureService(CaptureUsage(usage_ledger)),
                workflows=workflow_registry,
                conversations=ConversationService(
                    StartConversation(conversation_repository),
                    AppendMessage(conversation_repository),
                ),
                # 5.3-أ — the total per-response stream deadline (07 §4).
                stream_max_duration_s=float(settings.limits.stream_max_duration_s),
                # 6.1-هـ-1 — the handle, not an adapter: live after startup's
                # `connect_storage`, a clean `common.internal` before it.
                storage=storage,
                # 6.4-ب — the per-agent RBAC decision (the manifest's own
                # `required_permissions`). The SAME `authorization` the API
                # guards and the authentication path hold, so the roles a login
                # resolved are the roles every layer decides on.
                authorization=authorization,
                # 2.10 — the SAME `KnowledgeRetrievalService` instance the
                # `knowledge` bundle above hands the API's `/search` route:
                # `KnowledgeRetrieval.retrieve(ctx, query, k)` structurally
                # satisfies `KnowledgeAccess` (`agent_runtime/deps_ports.py`)
                # verbatim, so no adapter class is needed for this seam. The
                # RAG agent now RETRIEVES and CITES real workspace chunks.
                knowledge=knowledge.search,
                # web_search: see the module docstring. Stays None
                # deliberately — no Exa key — and the agent that needs it
                # fails with a clean `common.internal`/500 rather than a
                # silently wrong answer.
            )
        )

        # 5.3 -- the live-streaming pieces (helper above: `_build_notify_bridge`).
        hub, notify_consumer, notify_subscriptions, notify_redis_client = _build_notify_bridge(
            settings, redis_client
        )

        # P1-3 (step 10) -- `/metrics`'s own engine + adapter (helper above).
        metrics_engine, metrics_source = _build_metrics_source(settings, redis_client)

        return cls(
            settings=settings,
            engine=engine,
            tenant_session=tenant_session,
            redis_client=redis_client,
            notify_redis_client=notify_redis_client,
            cache=cache,
            qdrant_client=qdrant_client,
            vector_store=vector_store,
            vault_client=vault_client,
            secrets=secrets,
            vault_health=vault_health,
            firebase_http=firebase_http,
            auth=auth,
            ollama_http=ollama_http,
            openai_http=openai_http,
            embedding_http=embedding_http,
            image_http=image_http,
            llm_providers=llm_providers,
            provider_resolver=provider_resolver,
            agent_registry=agent_registry,
            plugin_report=plugin_report,
            orchestrator=orchestrator,
            workflow_registry=workflow_registry,
            conversations=conversations,
            hub=hub,
            notify_consumer=notify_consumer,
            notify_subscriptions=notify_subscriptions,
            files=files_use_cases,
            media=media,
            workspace=workspace,
            usage=usage,
            credentials=credentials,
            knowledge=knowledge,
            integrations=integrations,
            provisioning=provisioning,
            seeding=seeding,
            authorization=authorization,
            storage=storage,
            idempotency=SqlIdempotencyStore(tenant_session),
            metrics_engine=metrics_engine,
            metrics_source=metrics_source,
        )

    async def connect_storage(self) -> None:
        """Bind the MinIO ``StorageProvider`` into ``self.storage`` (6.1-هـ-1,
        closing debt (ز)) — the ONE wiring step that could not live in
        ``from_env`` because it must ``await`` a Vault read (05 §3).

        Runs as a startup hook in the app's lifespan, BEFORE the server accepts
        traffic. This is the API-side startup-hook contract; the actual
        secret-shape validation and client construction now live in ONE
        shared place, ``framework/di/storage_binding.py::bind_minio`` (step
        15 of `deferred-adapters-plan.md`) — this method is a two-line
        caller, and the knowledge worker's own composition root
        (``workers/bootstrap.py::build_knowledge_worker_from_env``) is the
        SECOND caller of that same function. Failure policy and idempotence
        are ``bind_minio``'s own (fail-fast, the ``FIREBASE_PROJECT_ID``/D5
        boot precedent; a second call is a no-op).
        """
        await bind_minio(self.storage, self.secrets, self.settings.minio)

    async def sweep_stale_notify_groups(self) -> None:
        """Startup hook (docs/log/3.81.md, P0-2's orphan-cleanup half): sweep
        every ``cg.notify.<hostname>.*`` group off THIS process's own two
        notify streams, destroying only the ones a dead sibling left behind
        -- see ``_sweep_stale_notify_groups``'s docstring for the full safety
        argument (host-scoping + a real OS liveness check on the pid).

        Wired into the Phase-6 lifespan's ``startup=`` list BEFORE
        ``notify_consumer.run(...)`` ever starts as a ``background=`` task
        (``api/main.py::create_production_app``): every ``startup=`` hook
        completes before any ``background=`` task begins
        (``_make_lifespan``'s own ordering contract), so this sweep always
        runs before THIS process's own group could possibly exist yet --
        which is exactly the "not yet created" half of the concurrent-sibling
        safety argument.
        """
        await _sweep_stale_notify_groups(
            RedisStreamsConsumer(self.redis_client),
            _NOTIFY_STREAMS,
            hostname=socket.gethostname(),
        )

    async def teardown_notify_bridge(self) -> None:
        """Shutdown hook (docs/log/3.81.md): ``XGROUP DESTROY`` THIS
        process's own ``cg.notify.<hostname>.<pid>`` group over every stream
        it read, so a CLEAN exit never leaves an orphan for the startup sweep
        above to find. MUST run before ``disposables()`` closes
        ``redis_client`` out from under it -- ``api/main.py::
        create_production_app`` wires this first in its ``shutdown=`` tuple
        for exactly that reason, and the lifespan's own ordering guarantee
        (``_make_lifespan``) has already cancelled and reaped the bridge's
        ``background=`` task by the time any ``shutdown=`` thunk runs, so the
        group is not being read from when it is destroyed.
        """
        await self.notify_consumer.teardown(self.notify_subscriptions)

    def disposables(self) -> list[Disposable]:
        """Every raw client this root owns, as teardown thunks for the API's
        shutdown lifespan — the ``build_relay_from_env`` precedent (5.1-ب),
        applied to the server process.

        Only the ROOT can write this list: it is the one place that holds each
        client alongside the adapter wrapping it, and an adapter never exposes
        a ``close`` of its own (the port contracts have no lifecycle method,
        deliberately — a use-case must not be able to shut the process's Redis
        down). Order is irrelevant: disposing an engine, closing a Qdrant
        client and closing an HTTP pool are mutually independent, and
        ``dispose_all`` isolates each failure from the rest anyway.

        ``vault_client`` is in the list even though the documented debt omitted
        it: hvac is synchronous (it wraps a ``requests.Session``), so it needs
        the same ``asyncio.to_thread`` offload every ``VaultSecrets`` method
        already uses rather than an ``aclose``.

        The two MinIO clients are deliberately NOT here: minio-py exposes no
        close at all, its ``urllib3.PoolManager`` is a private attribute of a
        client that lives inside ``MinioStorage`` rather than on this root, and
        reaching through both would buy nothing the interpreter's own teardown
        does not already do for a pool of idle keep-alive sockets.
        """

        async def _close_vault() -> None:
            await asyncio.to_thread(self.vault_client.adapter.close)

        return [
            self.engine.dispose,
            self.metrics_engine.dispose,
            self.redis_client.aclose,
            self.notify_redis_client.aclose,
            self.qdrant_client.close,
            _close_vault,
            self.firebase_http.aclose,
            self.ollama_http.aclose,
            self.openai_http.aclose,
            self.embedding_http.aclose,
            self.image_http.aclose,
        ]
