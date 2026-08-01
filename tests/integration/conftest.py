"""Live-Postgres fixtures (no Docker) for ``pytest.mark.live_db`` tests.

Talks to a real, already-provisioned local PostgreSQL 16 (08-local-runbook's
role/DB topology, adapted to a native WSL harness rather than
docker-compose): DB ``aizzak_test`` owned by ``aizzak_owner`` (runs Alembic,
owns every table, creates RLS policies) and the RLS-*subject* role ``app_rw``
(``NOINHERIT``, **not** ``BYPASSRLS``, not an owner) that repository tests
exercise through -- mirroring exactly how the app itself connects.

GRANTs are deliberately not part of any migration (01-data-model §6: seeding
"الأدوار/الصلاحيات (app_rw)" is a runbook/deploy step, not schema DDL), so
this conftest reproduces that seeding step itself, once per session, as the
owner.

**5.1-ب adds a THIRD role, ``outbox_relay``** (SELECT/UPDATE on
``platform.outbox`` only — the mirror image of ``app_rw``'s INSERT-only
grant on that same table, reserved by name in this file since §3.40). Unlike
``aizzak_owner``/``app_rw`` (provisioned once, out of band, before this
harness's first session — ``docs/log/3.14.md``), ``outbox_relay`` did not
exist anywhere until this sub-step, so this conftest CREATEs it too, not
only grants it — see ``_create_outbox_relay_role``/``_grant_outbox_relay``.

**Discovery: ``aizzak_owner`` cannot ``CREATE ROLE``.** It owns every table
(so it can ``GRANT`` on its own objects, and ``ALTER TABLE ... FORCE ROW
LEVEL SECURITY``) but is deliberately ``NOSUPERUSER``/not ``CREATEROLE`` —
role management is cluster-wide, a strictly bigger privilege than owning
this one test database's objects, and this harness's local ``postgres``
superuser (a separate, already-recorded throwaway dev credential, distinct
from ``aizzak_owner``/``app_rw``) is the one role that CAN create a new
login role. So role CREATION runs under the superuser DSN, once, and the
SELECT/UPDATE grants still run under ``aizzak_owner`` — the same split
``_grant_app_rw`` already uses for granting on objects it owns.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import hvac
import pytest
from hvac.exceptions import InvalidRequest
from minio import Minio
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import (
    DatabaseSettings,
    Limits,
    MinioSettings,
    OllamaSettings,
    QdrantSettings,
    RedisSettings,
    VaultSettings,
)
from app.infrastructure.ai_providers.llm.ollama_llm import OllamaLLM, create_ollama_http_client
from app.infrastructure.ai_providers.llm.openai_llm import OpenAILLM, create_openai_http_client
from app.infrastructure.cache.redis_cache import RedisCache, create_redis_client
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.rls import PlatformSessionFactory, TenantSessionFactory
from app.infrastructure.secrets.vault_secrets import VaultSecrets, create_vault_client
from app.infrastructure.storage.minio_storage import MinioStorage, create_minio_client
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client
from app.modules.access.adapters.sql_repository import SqlRoleAssignmentRepository
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.credentials.adapters.sql_repository import SqlCredentialRepository
from app.modules.credentials.domain.entities import Credential
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.integrations.adapters.sql_repository import (
    SqlConnectionRepository,
    SqlMcpServerRepository,
)
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.memory.adapters.sql_repository import SqlMemoryRepository
from app.modules.usage.adapters.sql_repository import SqlUsageLedgerRepository
from app.modules.workspace.adapters.sql_repository import SqlUserRepository, SqlWorkspaceRepository
from app.ops.provision import (
    APP_RW_GRANTS,
    METRICS_GRANTS,
    OUTBOX_RELAY_GRANTS,
    RETENTION_GRANTS,
    TRANSIT_ROTATOR_GRANTS,
    run_migrations,
)

_OWNER_DSN_DEFAULT = "postgresql+asyncpg://aizzak_owner:aizzak_owner@127.0.0.1:5432/aizzak_test"
_APP_DSN_DEFAULT = "postgresql+asyncpg://app_rw:app_rw@127.0.0.1:5432/aizzak_test"
# 5.1-ب: the outbox_relay role's own DSN -- see `_grant_outbox_relay`.
_RELAY_DSN_DEFAULT = "postgresql+asyncpg://outbox_relay:outbox_relay@127.0.0.1:5432/aizzak_test"
# P1-5 (docs/p1-hardening-plan.md §3 step 8): the retention sweep's own DSN --
# see `_create_retention_sweeper_role`/`_grant_retention_sweeper`.
_RETENTION_DSN_DEFAULT = (
    "postgresql+asyncpg://retention_sweeper:retention_sweeper@127.0.0.1:5432/aizzak_test"
)
# P1-3 (docs/p1-hardening-plan.md §3 step 10): the `/metrics` endpoint's own
# DSN -- see `_create_metrics_reader_role`/`_grant_metrics_reader`.
_METRICS_DSN_DEFAULT = (
    "postgresql+asyncpg://metrics_reader:metrics_reader@127.0.0.1:5432/aizzak_test"
)
# P1-9 (docs/p1-hardening-plan.md §3 step 12): the Transit key-rotation
# sweep's own DSN -- see `_create_transit_rotator_role`/`_grant_transit_rotator`.
_TRANSIT_ROTATOR_DSN_DEFAULT = (
    "postgresql+asyncpg://transit_rotator:transit_rotator@127.0.0.1:5432/aizzak_test"
)
# 5.1-ب: the harness's local Postgres superuser -- the ONE role that can
# `CREATE ROLE outbox_relay` (`aizzak_owner` is deliberately not CREATEROLE;
# see the module docstring's "Discovery" paragraph). A separate, already
# locally-recorded throwaway dev credential, never `aizzak_owner`/`app_rw`.
_SUPERUSER_DSN_DEFAULT = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/aizzak_test"
_PROBE_HOST = "127.0.0.1"
_PROBE_PORT = 5432
_PROBE_TIMEOUT_S = 1.5

_REDIS_URL_DEFAULT = "redis://127.0.0.1:6379/0"
_REDIS_PROBE_PORT = 6379

# Dedicated bucket-scoped MinIO service account. NEVER the server's root keys:
# this account can only see/read/write bucket `aizzak-test`.
#
# The account used to be provisioned BY HAND against a native `minio.service`
# on the canonical port (status-doc §3.19). That service is gone, and the
# bucket + account are now created by `deploy/minio/bootstrap.sh` against the
# Compose MinIO -- which publishes on 19000, because 9000 is left free for a
# native server (`docker-compose.yml`'s own host-offset rule). Hand-provisioned
# state that no longer matched a rebuilt container is exactly how this suite
# came to fail with `InvalidAccessKeyId` while looking correctly configured.
_MINIO_ENDPOINT_DEFAULT = "127.0.0.1:19000"
_MINIO_ACCESS_DEFAULT = "aizzak_test"
_MINIO_SECRET_DEFAULT = "aizzak-test-secret"
_MINIO_BUCKET_DEFAULT = "aizzak-test"

_QDRANT_URL_DEFAULT = "http://127.0.0.1:6333"
_QDRANT_PROBE_PORT = 6333

# The central embedding service (2.10) is Docker-only (``services/embedding/
# Dockerfile`` bakes model weights at build time) -- there is no native-run
# convention for it the way postgresql@16-main/redis-server/minio have. The
# probe below still follows the ``live_qdrant``/``live_ollama`` precedent
# exactly (TCP-reachable -> real, unreachable -> clean skip), so it starts
# working the moment an operator publishes the container's 8080 locally;
# absent Docker (09-testing-strategy §7's own documented limit) it skips in
# every environment this repo's own gates run in today.
_EMBEDDING_URL_DEFAULT = "http://127.0.0.1:8080"
_EMBEDDING_PROBE_PORT = 8080

_VAULT_ADDR_DEFAULT = "http://127.0.0.1:8200"
_VAULT_PROBE_PORT = 8200
_VAULT_CLIENT_TIMEOUT_S = 5.0
# Dev-mode root token for the local Vault dev server (08-local-runbook §3) --
# tokens of this kind exist ONLY for a local, in-memory ``vault server -dev``;
# a real deployment authenticates via AppRole instead (D-22).
_VAULT_TOKEN_DEFAULT = "aizzak-dev-root"

# The platform's sole local LLMProvider (DD-13, 2.8-a): no container, no
# Docker -- a native Ollama instance reachable from WSL by passthrough
# (confirmed live: Windows-side ``ollama serve``, port 11434).
_OLLAMA_BASE_URL_DEFAULT = "http://127.0.0.1:11434"
_OLLAMA_PROBE_PORT = 11434
_OLLAMA_MODEL_DEFAULT = "gemma3:1b"
# Comfortably above the confirmed-live ~43s cold ``load_duration`` for a
# first call against an unloaded model.
_OLLAMA_WARMUP_TIMEOUT_S = 90.0

# The platform's first CLOUD LLMProvider (DD-13, 2.8-ب-1): no local server to
# probe -- readiness is entirely gated on TEST_OPENAI_API_KEY being exported
# (the TEST_MINIO_*/TEST_VAULT_TOKEN precedent). This default model is never
# exercised by this harness today (no key is available as of this adapter's
# authorship, §3.24) -- it only needs to be a real, inexpensive, tool-calling
# model so the suite runs correctly the day a key appears.
_OPENAI_MODEL_DEFAULT = "gpt-4o-mini"

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Literal copy of migrations/versions/platform/0001_baseline_platform.py's
# MODULE_SCHEMAS -- `migrations/` is not importable application code, and
# this tuple only needs to name schemas to drop, not share behaviour.
_MODULE_SCHEMAS = (
    "workspace",
    "access",
    "credentials",
    "conversations",
    "memory",
    "files",
    "knowledge",
    "media",
    "integrations",
    "usage",
)


@dataclass(frozen=True, slots=True)
class LiveDbDsns:
    """The DSNs live_db tests are built around (``relay`` added 5.1-ب,
    ``retention`` added P1-5 step 8, ``metrics`` added P1-3 step 10,
    ``transit_rotator`` added P1-9 step 12)."""

    owner: str
    app: str
    relay: str
    retention: str
    metrics: str
    transit_rotator: str


def _tcp_reachable(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


async def _rebuild_schema(owner_dsn: str) -> None:
    """Drop every module schema (+ ``platform``) and let ``_run_migrations``
    re-run both chains from scratch, so the hardened ``media`` RLS policy
    (the ``NULLIF`` fix, migrations/versions/media/0001_media.py) is
    guaranteed to be the one actually applied -- even if an earlier harness
    run already applied the pre-fix version.

    Historical note: the design's first-choice rebuild -- a programmatic
    ``alembic.command.downgrade(cfg, "platform@base")`` -- originally failed
    against an ordering bug in
    ``migrations/versions/platform/0001_baseline_platform.py::downgrade()``
    (it dropped ``platform.touch_updated_at()`` *before* the module schemas
    whose triggers still referenced it). That downgrade has since been fixed
    (module schemas now drop first), but this raw ``DROP SCHEMA ... CASCADE``
    fallback is kept: it is faster, independent of any chain's recorded
    revision state, and also wipes schemas a downgrade of only two chains
    would miss.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for schema in _MODULE_SCHEMAS:
                # DDL identifiers cannot be bound parameters; safe because
                # ``schema`` comes from the hardcoded _MODULE_SCHEMAS tuple
                # (same precedent as 0001_baseline_platform.py's CREATE SCHEMA).
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text('DROP SCHEMA IF EXISTS "platform" CASCADE'))
            await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    finally:
        await engine.dispose()


def _run_migrations(owner_dsn: str) -> None:
    """``alembic upgrade platform@head`` then each module chain's own
    ``-x vts=<module> upgrade <module>@head``.

    7.1 moved that sequence into ``app.ops.provision`` -- the deploy artifact
    that runs it in the container -- and this harness now DELEGATES to it
    rather than keeping a second copy. The list of chains is a fact about the
    product, not about the tests: a module chain added to one and forgotten
    in the other is exactly the divergence that made ``permission denied``
    invisible until the first containerised boot."""
    run_migrations(owner_dsn)


async def _grant_app_rw(owner_dsn: str) -> None:
    """R7: USAGE on each module schema + full CRUD on each module's tenant
    tables for ``app_rw``. Not a migration (01-data-model §6) -- the runbook's
    seeding step, which since 7.1 lives in ``app.ops.provision`` as the
    artifact the CONTAINER runs. This harness executes the very same
    statements; see ``_run_migrations`` for why it delegates rather than
    duplicates.

    ``platform.outbox`` is the one deliberate exception to "full CRUD": the
    app is a PRODUCER only (D-18), so it gets INSERT and nothing else. SELECT
    and UPDATE belong to the ``outbox_relay`` role (Phase 5.1) -- a producer
    able to UPDATE ``published_at`` could make an event vanish unpublished.

    ``platform.processed_events`` (5.2-أ) follows the same INSERT-only shape:
    the claim is a plain INSERT under a SAVEPOINT with ``23505`` consumed as
    "duplicate" -- deliberately NOT ``ON CONFLICT DO NOTHING``, which was
    verified live to demand SELECT (arbiter inference reads the conflicting
    row). A role that can only INSERT can neither enumerate the ledger nor
    un-process an event to force a replay
    (``SqlProcessedEventLedger``'s own docstring)."""
    await _execute_all(owner_dsn, APP_RW_GRANTS)


async def _execute_all(dsn: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(DatabaseSettings(url=dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _create_outbox_relay_role(superuser_dsn: str) -> None:
    """5.1-ب, step 1/2: CREATE the ``outbox_relay`` login role itself.

    Runs under the harness's local Postgres SUPERUSER, not ``aizzak_owner``
    -- ``CREATE ROLE`` needs ``CREATEROLE``/superuser, a cluster-wide
    privilege ``aizzak_owner`` deliberately does not have (module docstring's
    "Discovery" paragraph; confirmed live: ``aizzak_owner`` attempting this
    statement raises ``InsufficientPrivilegeError``). ``CREATE ROLE`` has no
    ``IF NOT EXISTS`` clause of its own (unlike ``CREATE SCHEMA``/``CREATE
    TABLE`` elsewhere in this harness), hence the explicit existence check --
    this runs once per session and must not fail the SECOND time a
    session-scoped test run provisions against an already-provisioned
    cluster.
    """
    engine = create_engine(DatabaseSettings(url=superuser_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT FROM pg_catalog.pg_roles WHERE rolname = 'outbox_relay'
                        ) THEN
                            CREATE ROLE outbox_relay LOGIN PASSWORD 'outbox_relay' NOINHERIT;
                        END IF;
                    END
                    $$;
                    """
                )
            )
    finally:
        await engine.dispose()


async def _grant_outbox_relay(owner_dsn: str) -> None:
    """5.1-ب, step 2/2: the relay's OWN least-privilege grants -- SELECT/
    UPDATE on ``platform.outbox`` and NOTHING else, the mirror image of
    ``app_rw``'s INSERT-only grant on that same table (``_grant_app_rw``'s
    docstring reserved this role by name since §3.40: "SELECT and UPDATE
    belong to the ``outbox_relay`` role").

    Runs under ``aizzak_owner`` -- unlike role CREATION, granting on an
    object ``aizzak_owner`` already OWNS needs no elevated cluster privilege,
    exactly the same footing ``_grant_app_rw`` already stands on for every
    other table in this file.
    """
    await _execute_all(owner_dsn, OUTBOX_RELAY_GRANTS)


async def _create_retention_sweeper_role(superuser_dsn: str) -> None:
    """P1-5 step 8, step 1/3: CREATE the ``retention_sweeper`` login role --
    the ``_create_outbox_relay_role`` precedent (same superuser DSN, same
    idempotent existence check; ``aizzak_owner`` still deliberately lacks
    ``CREATEROLE``, module docstring's "Discovery" paragraph).

    Unlike ``outbox_relay``, this role must exist BEFORE migrations run, not
    only before its own grants: ``migrations/versions/platform/
    0003_retention_sweep.py`` issues ``CREATE POLICY ... TO retention_sweeper``,
    which Alembic validates against `pg_roles` at DDL time -- a role created
    only after `_run_migrations` would make that migration itself fail.
    """
    engine = create_engine(DatabaseSettings(url=superuser_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT FROM pg_catalog.pg_roles WHERE rolname = 'retention_sweeper'
                        ) THEN
                            CREATE ROLE retention_sweeper LOGIN PASSWORD 'retention_sweeper'
                                NOINHERIT;
                        END IF;
                    END
                    $$;
                    """
                )
            )
    finally:
        await engine.dispose()


async def _grant_retention_sweeper(owner_dsn: str) -> None:
    """P1-5 step 8, step 2/3: the sweeper's OWN least-privilege grants --
    SELECT/DELETE on the three unbounded ledgers and nothing else
    (``app.ops.provision.RETENTION_GRANTS``), never a widened ``app_rw``
    (module docstring's ``_grant_app_rw``, "Neither ``outbox`` nor
    ``processed_events`` grow a DELETE grant here...")."""
    await _execute_all(owner_dsn, RETENTION_GRANTS)


async def _create_metrics_reader_role(superuser_dsn: str) -> None:
    """P1-3 step 10, step 1/2: CREATE the ``metrics_reader`` login role --
    the ``_create_outbox_relay_role``/``_create_retention_sweeper_role``
    precedent (same superuser DSN, same idempotent existence check). Unlike
    ``retention_sweeper``, no migration issues a ``CREATE POLICY ... TO
    metrics_reader`` (``platform.outbox`` carries no RLS policy at all), so
    this role has no "must exist before migrations" constraint -- it only
    needs to exist before its own grant, the ``outbox_relay`` precedent.
    """
    engine = create_engine(DatabaseSettings(url=superuser_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metrics_reader'
                        ) THEN
                            CREATE ROLE metrics_reader LOGIN PASSWORD 'metrics_reader' NOINHERIT;
                        END IF;
                    END
                    $$;
                    """
                )
            )
    finally:
        await engine.dispose()


async def _grant_metrics_reader(owner_dsn: str) -> None:
    """P1-3 step 10, step 2/2: the ``/metrics`` endpoint's OWN least-privilege
    grant -- SELECT-only on ``platform.outbox`` and nothing else
    (``app.ops.provision.METRICS_GRANTS``), never a widened ``app_rw``
    (module docstring's ``_grant_app_rw``: ``app_rw`` is INSERT-only there)."""
    await _execute_all(owner_dsn, METRICS_GRANTS)


async def _create_transit_rotator_role(superuser_dsn: str) -> None:
    """P1-9 step 12, step 1/3: CREATE the ``transit_rotator`` login role --
    the ``_create_retention_sweeper_role`` precedent (same superuser DSN,
    same idempotent existence check).

    Like ``retention_sweeper``, this role must exist BEFORE migrations run,
    not only before its own grants: ``migrations/versions/credentials/
    0002_transit_rotator.py`` and ``migrations/versions/integrations/
    0002_transit_rotator.py`` both issue ``CREATE POLICY ... TO
    transit_rotator``, which Alembic validates against `pg_roles` at DDL
    time -- a role created only after `_run_migrations` would make those
    migrations themselves fail.
    """
    engine = create_engine(DatabaseSettings(url=superuser_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT FROM pg_catalog.pg_roles WHERE rolname = 'transit_rotator'
                        ) THEN
                            CREATE ROLE transit_rotator LOGIN PASSWORD 'transit_rotator'
                                NOINHERIT;
                        END IF;
                    END
                    $$;
                    """
                )
            )
    finally:
        await engine.dispose()


async def _grant_transit_rotator(owner_dsn: str) -> None:
    """P1-9 step 12, step 2/3: the rotator's OWN least-privilege grants --
    SELECT plus column-scoped UPDATE (the ciphertext column alone) on the
    three Transit-ciphertext-bearing tables and nothing else
    (``app.ops.provision.TRANSIT_ROTATOR_GRANTS``), never a widened
    ``app_rw``."""
    await _execute_all(owner_dsn, TRANSIT_ROTATOR_GRANTS)


@pytest.fixture(scope="session")
def live_db() -> Iterator[LiveDbDsns]:
    """Probe for a live local Postgres, rebuild+migrate+grant once per
    session, and hand out all four DSNs. Skips the whole live_db suite
    (rather than erroring) when Postgres is unreachable."""
    if not _tcp_reachable(_PROBE_HOST, _PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live PostgreSQL reachable at {_PROBE_HOST}:{_PROBE_PORT}")

    owner_dsn = os.environ.get("TEST_DATABASE_URL", _OWNER_DSN_DEFAULT)
    app_dsn = os.environ.get("TEST_DATABASE_URL_APP", _APP_DSN_DEFAULT)
    relay_dsn = os.environ.get("TEST_DATABASE_URL_RELAY", _RELAY_DSN_DEFAULT)
    retention_dsn = os.environ.get("TEST_DATABASE_URL_RETENTION", _RETENTION_DSN_DEFAULT)
    metrics_dsn = os.environ.get("TEST_DATABASE_URL_METRICS", _METRICS_DSN_DEFAULT)
    transit_rotator_dsn = os.environ.get(
        "TEST_DATABASE_URL_TRANSIT_ROTATOR", _TRANSIT_ROTATOR_DSN_DEFAULT
    )
    superuser_dsn = os.environ.get("TEST_DATABASE_URL_SUPERUSER", _SUPERUSER_DSN_DEFAULT)

    asyncio.run(_rebuild_schema(owner_dsn))
    # `retention_sweeper`/`transit_rotator` must exist BEFORE migrations run --
    # see `_create_retention_sweeper_role`'s/`_create_transit_rotator_role`'s
    # docstrings.
    asyncio.run(_create_retention_sweeper_role(superuser_dsn))
    asyncio.run(_create_transit_rotator_role(superuser_dsn))
    _run_migrations(owner_dsn)
    asyncio.run(_grant_app_rw(owner_dsn))
    asyncio.run(_create_outbox_relay_role(superuser_dsn))
    asyncio.run(_grant_outbox_relay(owner_dsn))
    asyncio.run(_grant_retention_sweeper(owner_dsn))
    asyncio.run(_create_metrics_reader_role(superuser_dsn))
    asyncio.run(_grant_metrics_reader(owner_dsn))
    asyncio.run(_grant_transit_rotator(owner_dsn))

    yield LiveDbDsns(
        owner=owner_dsn,
        app=app_dsn,
        relay=relay_dsn,
        retention=retention_dsn,
        metrics=metrics_dsn,
        transit_rotator=transit_rotator_dsn,
    )


@pytest.fixture
async def app_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """The ``app_rw`` engine, built inside the test's own event loop
    (``NullPool`` -- no pooling across independently-awaited test
    connections/transactions, R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.app), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sessionmaker_app(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(app_engine)


@pytest.fixture
def tenant_session(sessionmaker_app: async_sessionmaker[AsyncSession]) -> TenantSessionFactory:
    return TenantSessionFactory(sessionmaker_app)


@pytest.fixture
def platform_session(
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> PlatformSessionFactory:
    """R1: the ``app.platform_read`` sentinel session factory -- exercised
    only through ``repo_user.get_by_firebase_uid`` in these tests, mirroring
    its sole sanctioned production consumer."""
    return PlatformSessionFactory(sessionmaker_app)


@pytest.fixture
async def relay_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """5.1-ب: the ``outbox_relay`` engine -- the ``app_engine`` precedent,
    built inside the test's own event loop (``NullPool`` -- no pooling
    across independently-awaited test connections/transactions, R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.relay), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def relay_sessionmaker(relay_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(relay_engine)


@pytest.fixture
async def retention_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """P1-5 step 8: the ``retention_sweeper`` engine -- the ``relay_engine``
    precedent, built inside the test's own event loop (``NullPool`` -- no
    pooling across independently-awaited test connections/transactions,
    R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.retention), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def metrics_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """P1-3 step 10: the ``metrics_reader`` engine -- the ``retention_engine``
    precedent, built inside the test's own event loop (``NullPool`` -- no
    pooling across independently-awaited test connections/transactions,
    R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.metrics), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def transit_rotator_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """P1-9 step 12: the ``transit_rotator`` engine -- the ``retention_engine``
    precedent, built inside the test's own event loop (``NullPool`` -- no
    pooling across independently-awaited test connections/transactions,
    R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.transit_rotator), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def live_redis() -> str:
    """Probe for a live local Redis (Phase 2.3's live harness -- the
    ``live_db`` precedent) and hand out its URL; skips the ``live_redis``
    suite when unreachable. Deliberately NO flush/rebuild here, unlike the
    Postgres harness: the server may hold unrelated data, so every test must
    key under its own unique prefix and clean up after itself."""
    if not _tcp_reachable(_PROBE_HOST, _REDIS_PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live Redis reachable at {_PROBE_HOST}:{_REDIS_PROBE_PORT}")
    return os.environ.get("TEST_REDIS_URL", _REDIS_URL_DEFAULT)


@pytest.fixture
async def redis_client(live_redis: str) -> AsyncIterator[Redis]:
    """One real async client per test, built by the adapter's own factory
    (so its ``decode_responses``/timeout choices are what gets exercised)."""
    client = create_redis_client(RedisSettings(url=live_redis))
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def redis_cache(redis_client: Redis) -> RedisCache:
    return RedisCache(redis_client)


@dataclass(frozen=True, slots=True)
class LiveMinio:
    """Connection material for the live MinIO harness (bucket-scoped)."""

    settings: MinioSettings
    access_key: str
    secret_key: str


@pytest.fixture(scope="session")
def live_minio() -> LiveMinio:
    """Probe for a live local MinIO (Phase 2.4's live harness) and hand out
    the bucket-scoped test-account material; skips the ``live_minio`` suite
    when unreachable. No bucket creation/wipe here: the bucket + scoped
    account are provisioned by ``deploy/minio/bootstrap.sh`` and each test
    writes only under its own unique object prefix, sweeping it afterwards.

    The probe address is DERIVED from the endpoint actually in use rather
    than read off a second constant. The two drifted apart the moment MinIO
    moved to Compose: a probe hard-wired to 9000 answered `reachable` because
    an unrelated stack happened to hold that port, so the suite ran in full
    against the wrong server instead of skipping honestly. Deriving it means
    ``TEST_MINIO_ENDPOINT`` moves the probe with it, by construction.
    """
    endpoint = os.environ.get("TEST_MINIO_ENDPOINT", _MINIO_ENDPOINT_DEFAULT)
    host, _, port_text = endpoint.rpartition(":")
    if not _tcp_reachable(host, int(port_text), _PROBE_TIMEOUT_S):
        pytest.skip(f"no live MinIO reachable at {endpoint}")
    return LiveMinio(
        settings=MinioSettings(
            endpoint=endpoint,
            bucket=os.environ.get("TEST_MINIO_BUCKET", _MINIO_BUCKET_DEFAULT),
            secure=False,
        ),
        access_key=os.environ.get("TEST_MINIO_ACCESS_KEY", _MINIO_ACCESS_DEFAULT),
        secret_key=os.environ.get("TEST_MINIO_SECRET_KEY", _MINIO_SECRET_DEFAULT),
    )


@pytest.fixture
def minio_client(live_minio: LiveMinio) -> Minio:
    """One real (sync) client per test, built by the adapter's own factory so
    its timeout/retry choices are what gets exercised."""
    return create_minio_client(
        live_minio.settings,
        access_key=live_minio.access_key,
        secret_key=live_minio.secret_key,
    )


@pytest.fixture
def minio_storage(minio_client: Minio, live_minio: LiveMinio) -> MinioStorage:
    return MinioStorage(minio_client, live_minio.settings.bucket)


@pytest.fixture(scope="session")
def live_qdrant() -> str:
    """Probe for a live local Qdrant (Phase 2.5's live harness -- the
    ``live_redis``/``live_minio`` precedent) and hand out its URL; skips the
    ``live_qdrant`` suite when unreachable."""
    if not _tcp_reachable(_PROBE_HOST, _QDRANT_PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live Qdrant reachable at {_PROBE_HOST}:{_QDRANT_PROBE_PORT}")
    return os.environ.get("TEST_QDRANT_URL", _QDRANT_URL_DEFAULT)


@pytest.fixture
async def qdrant_client(live_qdrant: str) -> AsyncIterator[AsyncQdrantClient]:
    """One real async client per test, built by the adapter's own factory (so
    its timeout/``check_compatibility`` choices are what gets exercised)."""
    client = create_qdrant_client(QdrantSettings(url=live_qdrant))
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def qdrant_store(qdrant_client: AsyncQdrantClient) -> QdrantVectorStore:
    return QdrantVectorStore(qdrant_client)


@pytest.fixture
async def qdrant_collection(qdrant_client: AsyncQdrantClient) -> AsyncIterator[str]:
    """A unique per-test collection name, deleted after the test so the
    shared local Qdrant never accumulates suite leftovers. Deleting an
    already-absent collection is a confirmed-silent no-op against the live
    server, so this is safe even for tests that never actually create it."""
    name = f"aizzak-test-{new_uuid7()}"
    try:
        yield name
    finally:
        await qdrant_client.delete_collection(name)


@pytest.fixture(scope="session")
def live_embedding() -> str:
    """Probe for a live local central embedding service (2.10's
    ``services/embedding/app.py`` -- normally reached only inside the
    Compose network, ``embedding:8080``; this probes wherever an operator
    has published the container's port locally instead, the ``live_qdrant``
    precedent) and hand out its URL; skips the ``live_embedding`` suite when
    unreachable -- which, absent Docker, is every environment this repo's
    own gates run in today (module docstring's own constant comment)."""
    if not _tcp_reachable(_PROBE_HOST, _EMBEDDING_PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live embedding service reachable at {_PROBE_HOST}:{_EMBEDDING_PROBE_PORT}")
    return os.environ.get("TEST_EMBEDDING_URL", _EMBEDDING_URL_DEFAULT)


@dataclass(frozen=True, slots=True)
class LiveVault:
    """Connection material for the live Vault harness (dev-mode root token)."""

    addr: str
    token: str


@pytest.fixture(scope="session")
def live_vault() -> LiveVault:
    """Probe for a live local Vault (Phase 2.6's live harness -- the
    ``live_redis``/``live_minio``/``live_qdrant`` precedent) and hand out its
    address + dev-mode root token; skips the ``live_vault`` suite when
    unreachable."""
    if not _tcp_reachable(_PROBE_HOST, _VAULT_PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live Vault reachable at {_PROBE_HOST}:{_VAULT_PROBE_PORT}")
    return LiveVault(
        addr=os.environ.get("TEST_VAULT_ADDR", _VAULT_ADDR_DEFAULT),
        token=os.environ.get("TEST_VAULT_TOKEN", _VAULT_TOKEN_DEFAULT),
    )


@pytest.fixture(scope="session")
def vault_client_raw(live_vault: LiveVault) -> hvac.Client:
    """A raw root-token ``hvac.Client`` for harness setup/teardown ONLY --
    provisioning and removing throwaway Transit keys and KV paths, neither of
    which the ``SecretsProvider`` port itself exposes. This is NOT the client
    under test: that is ``vault_secrets`` below, built through the adapter's
    own factory."""
    return hvac.Client(url=live_vault.addr, token=live_vault.token, timeout=_VAULT_CLIENT_TIMEOUT_S)


@pytest.fixture(scope="session")
def ensure_transit(vault_client_raw: hvac.Client) -> None:
    """Idempotently enable the ``transit`` secrets engine once per session.
    The live dev Vault this harness talks to may already have it on (08's dev
    bootstrap enables it) or not (a freshly-started dev server) --
    ``InvalidRequest("path is already in use at transit/")`` is the
    already-on case and is swallowed; anything else is a genuine setup
    failure and must not be hidden."""
    try:
        vault_client_raw.sys.enable_secrets_engine("transit")
    except InvalidRequest as exc:
        if "already in use" not in str(exc):
            raise


@pytest.fixture
def vault_secrets(live_vault: LiveVault) -> VaultSecrets:
    """The adapter under test, built through its OWN factory
    (``create_vault_client``) so the factory's DD-11-safe url/token handling
    is what actually gets exercised -- the ``minio_client``/``qdrant_client``/
    ``redis_client`` precedent."""
    client = create_vault_client(VaultSettings(addr=live_vault.addr), token=live_vault.token)
    return VaultSecrets(client)


@pytest.fixture
def transit_key(vault_client_raw: hvac.Client, ensure_transit: None) -> Iterator[str]:
    """A fresh, uniquely-named Transit key per test, created directly via the
    raw root client (key lifecycle is a Vault-admin concern, outside the
    ``SecretsProvider`` port); best-effort torn down afterwards
    (``deletion_allowed`` defaults to ``false`` in Vault, hence the
    ``update_key_configuration`` step before ``delete_key``) so the shared
    local Vault never accumulates suite leftovers."""
    name = f"aizzak-test-{new_uuid7()}"
    vault_client_raw.secrets.transit.create_key(name=name, mount_point="transit")
    try:
        yield name
    finally:
        with contextlib.suppress(Exception):
            vault_client_raw.secrets.transit.update_key_configuration(
                name=name, deletion_allowed=True, mount_point="transit"
            )
            vault_client_raw.secrets.transit.delete_key(name=name, mount_point="transit")


@pytest.fixture
def kv_path(vault_client_raw: hvac.Client) -> Iterator[str]:
    """A unique catalog-literal KV v2 path per test
    (``secret/data/aizzak-test-<uuid7>``) -- the fixture only reserves the
    name; each test writes under it itself via ``vault_client_raw`` (KV
    writes are a harness-setup concern, outside the read-only
    ``SecretsProvider`` port). Best-effort ``delete_metadata_and_all_versions``
    afterwards so the shared local Vault never accumulates suite leftovers."""
    rel = f"aizzak-test-{new_uuid7()}"
    try:
        yield f"secret/data/{rel}"
    finally:
        with contextlib.suppress(Exception):
            vault_client_raw.secrets.kv.v2.delete_metadata_and_all_versions(
                path=rel, mount_point="secret"
            )


@dataclass(frozen=True, slots=True)
class LiveOllama:
    """Connection material for the live Ollama harness: the settings the
    adapter's own factory needs, plus the ONE model confirmed pulled
    locally. The model is as much a hard dependency here as the server
    itself -- 08-local-runbook does not pull models automatically -- so its
    absence skips the suite exactly like an unreachable port would."""

    settings: OllamaSettings
    model: str


@pytest.fixture(scope="session")
def live_ollama() -> LiveOllama:
    """Probe for a live local Ollama (the ``live_redis``/``live_minio``/
    ``live_qdrant``/``live_vault`` precedent) and hand out its settings plus
    a confirmed-available model name; skips the ``live_ollama`` suite when
    unreachable OR when the expected model was never pulled.

    Warms the model into VRAM once per session (a confirmed-live ~43s cold
    ``load_duration`` on the very first call against an unloaded model) so
    every individual test that follows runs "hot" (~0.3s, confirmed live)
    against the adapter's OWN production timeout (``Limits.llm_timeout_s``)
    -- without this, the FIRST live test to run would need to absorb the
    entire cold-start cost itself and would be one flaky, arbitrarily-picked
    test away from timing out under the 60s production budget.

    ``TEST_OLLAMA_BASE_URL``/``TEST_OLLAMA_MODEL`` override both defaults.
    """
    if not _tcp_reachable(_PROBE_HOST, _OLLAMA_PROBE_PORT, _PROBE_TIMEOUT_S):
        pytest.skip(f"no live Ollama reachable at {_PROBE_HOST}:{_OLLAMA_PROBE_PORT}")

    base_url = os.environ.get("TEST_OLLAMA_BASE_URL", _OLLAMA_BASE_URL_DEFAULT)
    model = os.environ.get("TEST_OLLAMA_MODEL", _OLLAMA_MODEL_DEFAULT)

    # One sync client (httpx, plain -- no adapter code exists yet to build
    # one through), one generous timeout covering both calls below: the
    # tags listing is near-instant, the warmup alone is the ~43s-plus one.
    with httpx.Client(
        base_url=base_url, timeout=_OLLAMA_WARMUP_TIMEOUT_S, trust_env=False
    ) as client:
        tags = client.get("/api/tags")
        tags.raise_for_status()
        names: set[str] = set()
        for entry in tags.json().get("models", []):
            if isinstance(entry, dict):
                for key in ("name", "model"):
                    value = entry.get(key)
                    if isinstance(value, str):
                        names.add(value)
        if model not in names:
            pytest.skip(f"Ollama model {model!r} is not pulled locally (have: {sorted(names)})")

        warmup = client.post(
            "/api/generate", json={"model": model, "prompt": "", "keep_alive": "30m"}
        )
        warmup.raise_for_status()

    return LiveOllama(settings=OllamaSettings(base_url=base_url), model=model)


@pytest.fixture
async def ollama_llm(live_ollama: LiveOllama) -> AsyncIterator[OllamaLLM]:
    """One real adapter per test, built through its OWN factory (the
    ``qdrant_client``/``redis_client``/``vault_secrets`` precedent) at the
    SAME production timeout (``Limits().llm_timeout_s``) real requests use
    -- ``live_ollama``'s session-scoped warmup is what keeps that timeout
    realistic here instead of flaky."""
    client = create_ollama_http_client(live_ollama.settings, timeout_s=Limits().llm_timeout_s)
    try:
        yield OllamaLLM(client)
    finally:
        await client.aclose()


@dataclass(frozen=True, slots=True)
class LiveOpenAI:
    """Connection material for the live OpenAI harness: the real API key the
    user exported (never Settings, never Vault -- 2.8-ب-1's own decision,
    ``openai_llm.py``'s module docstring) plus a model name to exercise.
    Unlike ``LiveOllama``, there is no ``settings`` field at all -- OpenAI's
    ``base_url`` is a module constant, not a per-deployment variable."""

    api_key: str
    model: str


@pytest.fixture(scope="session")
def live_openai() -> LiveOpenAI:
    """Skips the ``live_openai`` suite unless the user has exported
    ``TEST_OPENAI_API_KEY`` in their OWN shell (the
    ``TEST_MINIO_*``/``TEST_VAULT_TOKEN`` precedent) -- the secret is never
    pasted into chat and never read by the assistant, only by this fixture,
    at test-run time. Unlike every other ``live_*`` fixture, there is no TCP
    probe here (there is no local port to check): readiness IS the presence
    of the key; reachability/auth failures surface as ordinary test failures
    instead, exactly like a wrong key would in production."""
    api_key = os.environ.get("TEST_OPENAI_API_KEY")
    if not api_key:
        pytest.skip("no TEST_OPENAI_API_KEY exported -- live_openai suite skipped")
    model = os.environ.get("TEST_OPENAI_MODEL", _OPENAI_MODEL_DEFAULT)
    return LiveOpenAI(api_key=api_key, model=model)


@pytest.fixture
async def openai_llm(live_openai: LiveOpenAI) -> AsyncIterator[OpenAILLM]:
    """One real adapter per test, built through its OWN factory (the
    ``ollama_llm``/``qdrant_client``/``redis_client`` precedent) at the SAME
    production timeout (``Limits().llm_timeout_s``) real requests use.
    Depends on ``live_openai`` (even though it needs none of its DATA to
    build the client -- OpenAI's factory takes no settings) purely so the
    skip-if-no-key behaviour applies transitively to any test requesting
    only this fixture."""
    client = create_openai_http_client(timeout_s=Limits().llm_timeout_s)
    try:
        yield OpenAILLM(client)
    finally:
        await client.aclose()


@pytest.fixture
def repo(tenant_session: TenantSessionFactory) -> SqlMediaJobRepository:
    return SqlMediaJobRepository(tenant_session)


@pytest.fixture
def repo_workspace(tenant_session: TenantSessionFactory) -> SqlWorkspaceRepository:
    return SqlWorkspaceRepository(tenant_session)


@pytest.fixture
def repo_user(
    tenant_session: TenantSessionFactory, platform_session: PlatformSessionFactory
) -> SqlUserRepository:
    return SqlUserRepository(tenant_session, platform_session)


@pytest.fixture
def repo_credentials(tenant_session: TenantSessionFactory) -> SqlCredentialRepository:
    return SqlCredentialRepository(tenant_session)


@pytest.fixture
def repo_access(tenant_session: TenantSessionFactory) -> SqlRoleAssignmentRepository:
    return SqlRoleAssignmentRepository(tenant_session)


@pytest.fixture
def repo_conversations(tenant_session: TenantSessionFactory) -> SqlConversationRepository:
    return SqlConversationRepository(tenant_session)


@pytest.fixture
def repo_files(tenant_session: TenantSessionFactory) -> SqlFileRepository:
    return SqlFileRepository(tenant_session)


@pytest.fixture
def repo_memory(tenant_session: TenantSessionFactory) -> SqlMemoryRepository:
    return SqlMemoryRepository(tenant_session)


@pytest.fixture
def repo_knowledge(tenant_session: TenantSessionFactory) -> SqlDocumentRepository:
    return SqlDocumentRepository(tenant_session)


@pytest.fixture
def repo_connections(tenant_session: TenantSessionFactory) -> SqlConnectionRepository:
    return SqlConnectionRepository(tenant_session)


@pytest.fixture
def repo_mcp_servers(tenant_session: TenantSessionFactory) -> SqlMcpServerRepository:
    return SqlMcpServerRepository(tenant_session)


@pytest.fixture
def repo_usage(tenant_session: TenantSessionFactory) -> SqlUsageLedgerRepository:
    return SqlUsageLedgerRepository(tenant_session)


@pytest.fixture
def seed_platform_credential(
    live_db: LiveDbDsns,
) -> Callable[[Credential], Awaitable[None]]:
    """R3/R9: seed a ``scope='platform'`` row as the table owner, bypassing
    RLS via a transient ``NO FORCE`` toggle -- ``credentials.credentials`` is
    ``FORCE ROW LEVEL SECURITY`` (01 §3), so even the owner is normally
    policy-subject and could not otherwise insert a ``workspace_id IS NULL``
    row (no GUC value ever satisfies ``tenant_isolation``'s ``WITH CHECK``).
    No RLS-subject path inserts platform credentials in v1 (R3) --
    production seeding is an ops/runbook concern (Phase 7), out of this
    wave's scope; this fixture reproduces that seeding step directly for
    tests. ``FORCE`` is restored in a ``finally``, as its OWN statement (not
    relying on transactional DDL rollback), so a failed insert never leaves
    the table permanently RLS-relaxed for the rest of the session.
    Function-scoped (the default): a fresh toggle/seed per test.
    """

    async def _seed(credential: Credential) -> None:
        engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("ALTER TABLE credentials.credentials NO FORCE ROW LEVEL SECURITY")
                )
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO credentials.credentials
                                (id, workspace_id, provider, scope, label, ciphertext_ref,
                                 key_id, status, created_by, created_at, updated_at, version)
                            VALUES
                                (:id, :workspace_id, :provider, :scope, :label, :ciphertext_ref,
                                 :key_id, :status, :created_by, :created_at, :updated_at, :version)
                            """
                        ),
                        {
                            "id": credential.id,
                            "workspace_id": credential.workspace_id,
                            "provider": credential.provider.value,
                            "scope": credential.scope.value,
                            "label": credential.label,
                            "ciphertext_ref": credential.ciphertext_ref.ciphertext,
                            "key_id": credential.ciphertext_ref.key_name,
                            "status": credential.status.value,
                            "created_by": credential.created_by,
                            "created_at": credential.created_at,
                            "updated_at": credential.updated_at,
                            "version": credential.version,
                        },
                    )
            finally:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("ALTER TABLE credentials.credentials FORCE ROW LEVEL SECURITY")
                    )
        finally:
            await engine.dispose()

    return _seed


@pytest.fixture(autouse=True)
async def truncate_tables(live_db: LiveDbDsns) -> AsyncIterator[None]:
    """Owner-run ``TRUNCATE`` after every test. ``TRUNCATE`` is RLS-exempt
    for the owner, so this resets rows across every tenant a test touched,
    not just the one whose context happened to be set last. A single
    statement across every module's tables resolves FK order for us
    (``workspace.users`` -> ``workspace.workspaces`` via ``fk_user_ws``)
    regardless of the list order below.

    ``platform.outbox`` (5.1-أ) is in this list too: before the request-scoped
    unit of work landed, no atomicity test needed row-for-row isolation across
    tests, so the table was left to accumulate for the life of the session
    (``test_outbox.py``/``test_media_request_seam.py`` only ever asserted
    deltas). ``test_producer_atomicity.py`` reads it back by exact row count,
    so leaving it untruncated would make that test order-dependent on
    whatever ran before it in the same session.

    ``platform.processed_events`` (5.2-أ) joins for the same reason: the
    idempotency tests assert exact claim outcomes per ``(group, event_id)``,
    and a ledger row surviving one test would flip a later test's first
    ``claim`` from ``True`` to ``False``.

    ``platform.idempotency_keys`` (3.79) joins on the identical argument, one
    boundary out: ``test_idempotency_live.py`` asserts exact claim outcomes
    per ``(workspace, endpoint, key)``, and a surviving row would turn a later
    test's ``CLAIMED`` into an ``IN_PROGRESS``.
    """
    yield
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE workspace.users, workspace.workspaces, "
                    "credentials.credentials, access.role_assignments, media.media_jobs, "
                    "conversations.messages, conversations.conversations, files.files, "
                    "memory.memory_items, knowledge.chunks, knowledge.documents, "
                    "integrations.connections, integrations.mcp_servers, "
                    "usage.usage_records, usage.usage_rollups, usage.limits, "
                    "platform.outbox, platform.processed_events, "
                    "platform.idempotency_keys"
                )
            )
    finally:
        await engine.dispose()
