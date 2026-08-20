"""Fixtures for the container-backed local live-integration harness.

Talks to the real, already-provisioned Compose PostgreSQL 16 described by
08-local-runbook's role/DB topology: DB ``aizzak_test`` owned by
``aizzak_owner`` (runs Alembic,
owns every table, creates RLS policies) and the RLS-*subject* role ``app_rw``
(``NOINHERIT``, **not** ``BYPASSRLS``, not an owner) that repository tests
exercise through -- mirroring exactly how the app itself connects.

Login roles are cluster-level deployment state, provisioned once by
``deploy/postgres/initdb/10-roles.sh``. The harness deliberately does not
create or alter them. This matters for ``retention_sweeper`` and
``transit_rotator`` in particular: migrations name those roles in RLS
policies, so a correctly initialized cluster must contain them before the
test session starts.

Object GRANTs remain outside migrations (01-data-model §6: seeding
"الأدوار/الصلاحيات (app_rw)" is a runbook/deploy step, not schema DDL).
After rebuilding and migrating the test schemas, this conftest reapplies the
same grant tuples exported by ``app.ops.provision``, under ``aizzak_owner``.
That owner can grant privileges on its own objects but deliberately remains
``NOSUPERUSER``/not ``CREATEROLE``; the boundary is now enforced by keeping
cluster role creation out of the harness entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, fields
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

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
from app.modules.knowledge.adapters.sql_repository import (
    SqlDocumentRepository,
    SqlReindexJobRepository,
    SqlSummaryRepository,
)
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.memory.adapters.sql_repository import SqlMemoryRepository
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.usage.adapters.sql_repository import SqlUsageLedgerRepository
from app.modules.workspace.adapters.sql_repository import SqlUserRepository, SqlWorkspaceRepository
from app.ops.provision import (
    APP_RW_GRANTS,
    METRICS_GRANTS,
    OUTBOX_RELAY_GRANTS,
    PURGE_GRANTS,
    RETENTION_GRANTS,
    TRANSIT_ROTATOR_GRANTS,
    run_migrations,
)

# Every default below addresses the COMPOSE stack, on the offset host ports
# `docker-compose.yml` publishes (15432/16379/16333/18200), rather than the
# canonical ports used only inside the Compose network. `.env.test` supplies
# the real local credentials (`set -a; . ./.env.test; set +a` --
# docs/stack-commands.md 29-ب); if it is forgotten, the probes still address
# the intended stack, and the placeholder credentials they then carry are
# rejected by the HANDSHAKE below rather than by the first test to run: an
# unusable dependency skips the suite, with the driver's own sentence.
_OWNER_DSN_DEFAULT = "postgresql+asyncpg://aizzak_owner:aizzak_owner@127.0.0.1:15432/aizzak_test"
_APP_DSN_DEFAULT = "postgresql+asyncpg://app_rw:app_rw@127.0.0.1:15432/aizzak_test"
# 5.1-ب: the outbox_relay role's own DSN -- see `_grant_outbox_relay`.
_RELAY_DSN_DEFAULT = "postgresql+asyncpg://outbox_relay:outbox_relay@127.0.0.1:15432/aizzak_test"
# P1-5 (docs/p1-hardening-plan.md §3 step 8): the retention sweep's own DSN --
# see `_grant_retention_sweeper`.
_RETENTION_DSN_DEFAULT = (
    "postgresql+asyncpg://retention_sweeper:retention_sweeper@127.0.0.1:15432/aizzak_test"
)
# P1-3 (docs/p1-hardening-plan.md §3 step 10): the `/metrics` endpoint's own
# DSN -- see `_grant_metrics_reader`.
_METRICS_DSN_DEFAULT = (
    "postgresql+asyncpg://metrics_reader:metrics_reader@127.0.0.1:15432/aizzak_test"
)
# P1-9 (docs/p1-hardening-plan.md §3 step 12): the Transit key-rotation
# sweep's own DSN -- see `_grant_transit_rotator`.
_TRANSIT_ROTATOR_DSN_DEFAULT = (
    "postgresql+asyncpg://transit_rotator:transit_rotator@127.0.0.1:15432/aizzak_test"
)
# BE-ADM-014: the workspace content-purge sweep's own DSN -- see
# `_grant_workspace_purger`.
_PURGER_DSN_DEFAULT = (
    "postgresql+asyncpg://workspace_purger:workspace_purger@127.0.0.1:15432/aizzak_test"
)
_PROBE_TIMEOUT_S = 1.5
# The handshake that follows the port check talks to a real server and may
# have to wait for it (an auth rejection comes back fast; a loading model or
# a busy engine does not), so it gets its own, more patient budget.
_HANDSHAKE_TIMEOUT_S = 5.0

_REDIS_URL_DEFAULT = "redis://127.0.0.1:16379/0"

# Dedicated bucket-scoped MinIO service account. NEVER the server's root keys:
# this account can only see/read/write bucket `aizzak-test`.
#
# The account used to be provisioned BY HAND against a native `minio.service`
# on the canonical port (status-doc §3.19). That service is gone, and the
# bucket + account are now created by `deploy/minio/bootstrap.sh` against the
# Compose MinIO -- which publishes its stable host interface on 19000.
# Hand-provisioned state that no longer matched a rebuilt container is how this suite
# came to fail with `InvalidAccessKeyId` while looking correctly configured.
_MINIO_ENDPOINT_DEFAULT = "127.0.0.1:19000"
_MINIO_ACCESS_DEFAULT = "aizzak_test"
_MINIO_SECRET_DEFAULT = "aizzak-test-secret"
_MINIO_BUCKET_DEFAULT = "aizzak-test"

_QDRANT_URL_DEFAULT = "http://127.0.0.1:16333"

# The central embedding service (2.10) is container-only
# (``services/embedding/Dockerfile`` bakes model weights at build time).
# ``docker-compose.test.yml`` explicitly publishes it to the host for this
# harness; the deployment topology keeps it internal. The probe follows the
# same reachable-or-skip/REQUIRE_LIVE policy as the other local dependencies.
_EMBEDDING_URL_DEFAULT = "http://127.0.0.1:8080"

_VAULT_ADDR_DEFAULT = "http://127.0.0.1:18200"
_VAULT_CLIENT_TIMEOUT_S = 5.0
# There is deliberately NO token default. The one that used to live here was
# the root token of a ``vault server -dev`` that no longer exists: Vault is
# persistent now and its root token is generated at ``operator init`` into the
# `vault-init` volume. A stale default does not fail at the probe -- it passes
# it and then fails thirteen times with 403, which reads as broken tests
# rather than as an unexported secret. Absent, it says so once, in words.

# The platform's sole local LLMProvider (DD-13, 2.8-a): a native WSL systemd
# service, deliberately outside Compose, listening on port 11434.
_OLLAMA_BASE_URL_DEFAULT = "http://127.0.0.1:11434"
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
    # `spaces` is created by migrations/versions/platform/0004_spaces_schema.py
    # rather than by the baseline's own MODULE_SCHEMAS (that revision is
    # already applied everywhere), but it is dropped here exactly like the
    # other ten -- leaving it standing would leave `spaces.alembic_version`
    # standing with it, and the chain would then be recorded as applied
    # against a database whose `spaces.spaces` no longer exists.
    "spaces",
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
    ``transit_rotator`` added P1-9 step 12, ``purger`` added BE-ADM-014)."""

    owner: str
    app: str
    relay: str
    retention: str
    metrics: str
    transit_rotator: str
    purger: str


def _tcp_reachable(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


# One TCP verdict per ``(host, port)``, BENEATH the per-service verdict cache
# further down. The seven Postgres role DSNs all address ONE socket, and a
# host that DROPS the SYN rather than refusing it (WSL's default posture for
# a stopped container) charges the full ``_PROBE_TIMEOUT_S`` for every ask --
# so asking once per address instead of once per socket would multiply the
# no-stack run's probe bill by seven for no new information. The credentials
# still differ per DSN, so the handshakes are NOT shared; only the socket is.
_reachable_sockets: dict[tuple[str, int], bool] = {}


def _reachable_once(host: str, port: int) -> bool:
    key = (host, port)
    if key not in _reachable_sockets:
        _reachable_sockets[key] = _tcp_reachable(host, port, _PROBE_TIMEOUT_S)
    return _reachable_sockets[key]


# Only the two schemes whose default port is part of the scheme's own meaning.
# `redis`/`postgresql` are deliberately absent: guessing 6379/5432 for a
# port-less override would re-create exactly the parallel number this module
# just deleted, and silently.
_SCHEME_PORTS = {"http": 80, "https": 443}


def _probe_target(address: str) -> tuple[str, int]:
    """Derive the ``(host, port)`` to probe from the address a fixture is
    about to CONNECT to -- a SQLAlchemy DSN, a URL, or a bare ``host:port``
    endpoint, whichever that fixture hands out.

    Every ``live_*`` fixture must probe what it will use. A probe pinned to
    its own constant is a second source of truth, and the two drift silently:
    ``live_minio`` already paid for this (see its docstring) -- a probe
    hard-wired to 9000 answered `reachable` because an unrelated stack
    happened to hold that port, so the suite ran in full against the wrong
    server instead of skipping honestly. Deriving means a ``TEST_*`` override
    moves the probe with the address, by construction rather than by anyone
    remembering to move a second constant.

    Raises ``ValueError`` on an address with no host or no discoverable port:
    an unusable override is worth one loud error, never a silent skip.
    """
    parts = urlsplit(address if "://" in address else f"//{address}")
    host = parts.hostname
    if host is None:
        raise ValueError(f"probe address carries no host: {address!r}")
    port = parts.port if parts.port is not None else _SCHEME_PORTS.get(parts.scheme)
    if port is None:
        raise ValueError(
            f"probe address carries no port, and its scheme has no default: {address!r}"
        )
    return host, port


class _HandshakeRefused(Exception):
    """The harness's OWN verdict that a reachable service is unusable -- a
    bucket that does not exist, a token the server answered ``no`` to.

    Separate from a driver exception only in how the skip reason reads: the
    message is already a full sentence written here, so it is not prefixed
    with an exception type nobody needs to see.
    """


# One verdict per ``(service, address)`` for the whole session. Every
# ``live_*`` fixture is session-scoped, so a handshake is already paid at
# most once per fixture; this additionally keeps two fixtures aimed at ONE
# address from paying twice, and makes a re-probe of an already-failed
# dependency free. NEVER per test.
_probe_verdicts: dict[tuple[str, str], str | None] = {}


def _probe_once(service: str, address: str, handshake: Callable[[], None]) -> str | None:
    """``None`` ⇒ ``service`` completed a real handshake at ``address``.
    Anything else ⇒ the exact reason it is not usable, in words.

    A port check alone was never enough. An open port carrying the WRONG
    PASSWORD is not "unreachable", so `live_db` setup proceeded and the whole
    integration suite ERRORED -- 392 times, on one
    ``asyncpg.exceptions.InvalidPasswordError`` -- where ``pyproject.toml``
    promised it would skip (rag-retrieval-plan-review.md §10). A suite that
    errors in full reads as a code catastrophe when it is a stale
    ``.env.test``.

    So the port check is only the fast first half; the second half CONNECTS
    and issues one trivial query/ping through the very factory the fixture
    will use, and ANY failure -- credentials included -- becomes a declared
    skip carrying the underlying message (or a hard failure under
    ``REQUIRE_LIVE=1``, which promises the stack was provisioned).
    """
    key = (service, address)
    if key in _probe_verdicts:
        return _probe_verdicts[key]
    # Outside the try: a malformed override is worth one loud error, never a
    # silent skip (``_probe_target``'s own docstring).
    host, port = _probe_target(address)
    verdict: str | None
    if not _reachable_once(host, port):
        verdict = f"no live {service} reachable at {host}:{port}"
    else:
        try:
            handshake()
        except _HandshakeRefused as exc:
            verdict = f"live {service} at {host}:{port} is not usable -- {exc}"
        # Deliberately broad: a handshake that raises ANYTHING has told us the
        # dependency is unusable, and the honest report of that is this
        # exception's own message, not a traceback in 392 test setups.
        except Exception as exc:
            verdict = (
                f"live {service} at {host}:{port} refused the handshake -- "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            verdict = None
    _probe_verdicts[key] = verdict
    return verdict


def _skip_unless_live(address: str, service: str, handshake: Callable[[], None]) -> None:
    """Skip the calling ``live_*`` fixture's whole suite unless ``service``
    answers a real handshake at ``address``, or fail when the live stack is
    required (``REQUIRE_LIVE=1``)."""
    verdict = _probe_once(service, address, handshake)
    if verdict is not None:
        _unavailable_live_dependency(verdict)


def _unavailable_live_dependency(reason: str) -> None:
    """Report a missing local live dependency without producing false green.

    An ordinary developer run may omit the Compose stack and skip cleanly.
    A run that explicitly sets ``REQUIRE_LIVE=1`` promises that the stack was
    provisioned, so the same absence is a failure.  Callers use this only for
    local infrastructure; optional paid API keys and opt-in load tests keep
    their direct ``pytest.skip`` calls.
    """
    if os.environ.get("REQUIRE_LIVE") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _validate_minio_secret_pair() -> None:
    """Reject drift between provisioning and test MinIO secret variables.

    Compose consumes ``MINIO_TEST_SECRET_KEY`` while the live fixture consumes
    ``TEST_MINIO_SECRET_KEY``.  Either variable may legitimately be absent
    from the pytest process, but when both are present they describe one
    account and must match.  The error deliberately names variables, never
    their values.
    """
    provisioned_secret = os.environ.get("MINIO_TEST_SECRET_KEY")
    test_secret = os.environ.get("TEST_MINIO_SECRET_KEY")
    if (
        provisioned_secret is not None
        and test_secret is not None
        and provisioned_secret != test_secret
    ):
        pytest.fail(
            "MINIO_TEST_SECRET_KEY and TEST_MINIO_SECRET_KEY differ; "
            "both must name the same MinIO test-account secret",
            pytrace=False,
        )


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


async def _grant_outbox_relay(owner_dsn: str) -> None:
    """5.1-ب: the relay's OWN least-privilege grants -- SELECT/
    UPDATE on ``platform.outbox`` and NOTHING else, the mirror image of
    ``app_rw``'s INSERT-only grant on that same table (``_grant_app_rw``'s
    docstring reserved this role by name since §3.40: "SELECT and UPDATE
    belong to the ``outbox_relay`` role").

    Runs under ``aizzak_owner``: granting on an object it already owns needs
    no elevated cluster privilege, exactly the same footing
    ``_grant_app_rw`` already stands on for every other table in this file.
    """
    await _execute_all(owner_dsn, OUTBOX_RELAY_GRANTS)


async def _grant_retention_sweeper(owner_dsn: str) -> None:
    """P1-5 step 8: the sweeper's OWN least-privilege grants --
    SELECT/DELETE on the three unbounded ledgers and nothing else
    (``app.ops.provision.RETENTION_GRANTS``), never a widened ``app_rw``
    (module docstring's ``_grant_app_rw``, "Neither ``outbox`` nor
    ``processed_events`` grow a DELETE grant here...")."""
    await _execute_all(owner_dsn, RETENTION_GRANTS)


async def _grant_metrics_reader(owner_dsn: str) -> None:
    """P1-3 step 10: the ``/metrics`` endpoint's OWN least-privilege
    grant -- SELECT-only on ``platform.outbox`` and nothing else
    (``app.ops.provision.METRICS_GRANTS``), never a widened ``app_rw``
    (module docstring's ``_grant_app_rw``: ``app_rw`` is INSERT-only there)."""
    await _execute_all(owner_dsn, METRICS_GRANTS)


async def _grant_transit_rotator(owner_dsn: str) -> None:
    """P1-9 step 12: the rotator's OWN least-privilege grants --
    SELECT plus column-scoped UPDATE (the ciphertext column alone) on the
    three Transit-ciphertext-bearing tables and nothing else
    (``app.ops.provision.TRANSIT_ROTATOR_GRANTS``), never a widened
    ``app_rw``."""
    await _execute_all(owner_dsn, TRANSIT_ROTATOR_GRANTS)


async def _grant_workspace_purger(owner_dsn: str) -> None:
    """BE-ADM-014: the purge sweep's OWN least-privilege grants -- imports
    ``PURGE_GRANTS`` from ``app.ops.provision`` rather than duplicating the
    grant list, the ``_grant_retention_sweeper``/``_grant_transit_rotator``
    precedent applied to a fourth least-privilege role."""
    await _execute_all(owner_dsn, PURGE_GRANTS)


async def _postgres_select_one(dsn: str) -> None:
    engine = create_engine(DatabaseSettings(url=dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def _postgres_handshake(dsn: str) -> None:
    """Log in as the DSN's own role and run ``SELECT 1`` -- through the SAME
    ``create_engine`` the fixtures use, so what is proven is exactly what
    they will do (OPS-02 connect args and all), not a parallel connection."""
    asyncio.run(_postgres_select_one(dsn))


@pytest.fixture(scope="session")
def live_db() -> Iterator[LiveDbDsns]:
    """Probe for a live local Postgres, rebuild+migrate+grant once per
    session, and hand out the least-privilege DSNs. Skips the whole live_db
    suite (rather than erroring) when Postgres is unreachable -- or when any
    of the seven roles cannot actually log in.

    EVERY DSN handed out is probed, not the owner's alone. The seven login
    roles are provisioned together by ``deploy/postgres/initdb/10-roles.sh``
    and a `.env.test` that has drifted for one of them fails exactly the way
    it fails for all of them -- but a probe that only covered the owner would
    let the drifted one through the gate and into the tests, which is the
    ERROR-instead-of-skip shape this whole probe exists to end. Seven
    handshakes, once per session (``_probe_once`` caches, and this fixture is
    session-scoped besides), is a cheap price for that.
    """
    dsns = LiveDbDsns(
        owner=os.environ.get("TEST_DATABASE_URL", _OWNER_DSN_DEFAULT),
        app=os.environ.get("TEST_DATABASE_URL_APP", _APP_DSN_DEFAULT),
        relay=os.environ.get("TEST_DATABASE_URL_RELAY", _RELAY_DSN_DEFAULT),
        retention=os.environ.get("TEST_DATABASE_URL_RETENTION", _RETENTION_DSN_DEFAULT),
        metrics=os.environ.get("TEST_DATABASE_URL_METRICS", _METRICS_DSN_DEFAULT),
        transit_rotator=os.environ.get(
            "TEST_DATABASE_URL_TRANSIT_ROTATOR", _TRANSIT_ROTATOR_DSN_DEFAULT
        ),
        purger=os.environ.get("TEST_DATABASE_URL_PURGER", _PURGER_DSN_DEFAULT),
    )
    for field in fields(dsns):
        dsn: str = getattr(dsns, field.name)
        _skip_unless_live(dsn, f"PostgreSQL (role {field.name})", partial(_postgres_handshake, dsn))

    asyncio.run(_rebuild_schema(dsns.owner))
    # Cluster roles already exist from `deploy/postgres/initdb/10-roles.sh`;
    # migrations may therefore name retention_sweeper/transit_rotator/
    # workspace_purger in RLS.
    _run_migrations(dsns.owner)
    asyncio.run(_grant_app_rw(dsns.owner))
    asyncio.run(_grant_outbox_relay(dsns.owner))
    asyncio.run(_grant_retention_sweeper(dsns.owner))
    asyncio.run(_grant_metrics_reader(dsns.owner))
    asyncio.run(_grant_transit_rotator(dsns.owner))
    asyncio.run(_grant_workspace_purger(dsns.owner))

    yield dsns


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


@pytest.fixture
async def purger_engine(live_db: LiveDbDsns) -> AsyncIterator[AsyncEngine]:
    """BE-ADM-014: the ``workspace_purger`` engine -- the ``transit_rotator_
    engine`` precedent, built inside the test's own event loop (``NullPool``
    -- no pooling across independently-awaited test connections/
    transactions, R3)."""
    engine = create_engine(DatabaseSettings(url=live_db.purger), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _redis_ping(url: str) -> None:
    client = create_redis_client(RedisSettings(url=url))
    try:
        await client.ping()
    finally:
        await client.aclose()


def _redis_handshake(url: str) -> None:
    """``PING`` through the adapter's own factory -- which also exercises the
    URL's credentials, if it carries any, and its database index."""
    asyncio.run(_redis_ping(url))


@pytest.fixture(scope="session")
def live_redis() -> str:
    """Probe for a live local Redis (Phase 2.3's live harness -- the
    ``live_db`` precedent) and hand out its URL; skips the ``live_redis``
    suite when it does not answer a ``PING``. Deliberately NO flush/rebuild
    here, unlike the Postgres harness: the server may hold unrelated data, so
    every test must key under its own unique prefix and clean up after
    itself."""
    url = os.environ.get("TEST_REDIS_URL", _REDIS_URL_DEFAULT)
    _skip_unless_live(url, "Redis", partial(_redis_handshake, url))
    return url


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


def _minio_handshake(live: LiveMinio) -> None:
    """A real, SIGNED call: ``bucket_exists`` on the very bucket the tests
    write to. This is the credential check a port probe cannot make -- the
    rotated-account story in ``_MINIO_ENDPOINT_DEFAULT``'s comment
    (``InvalidAccessKeyId``/``SignatureDoesNotMatch``) now ends in a skip
    with that message rather than in a suite-wide storm of failures -- and it
    doubles as proof that ``deploy/minio/bootstrap.sh`` has run at all."""
    client = create_minio_client(
        live.settings, access_key=live.access_key, secret_key=live.secret_key
    )
    if not client.bucket_exists(live.settings.bucket):
        raise _HandshakeRefused(
            f"bucket {live.settings.bucket!r} does not exist -- run deploy/minio/bootstrap.sh"
        )


@pytest.fixture(scope="session")
def live_minio() -> LiveMinio:
    """Probe for a live local MinIO (Phase 2.4's live harness) and hand out
    the bucket-scoped test-account material; skips the ``live_minio`` suite
    when unreachable. No bucket creation/wipe here: the bucket + scoped
    account are provisioned by ``deploy/minio/bootstrap.sh`` and each test
    writes only under its own unique object prefix, sweeping it afterwards.

    This fixture is where deriving the probe from the address was first
    forced -- a probe hard-wired to 9000 answered `reachable` because an
    unrelated stack happened to hold that port, so the suite ran in full
    against the wrong server instead of skipping honestly. That cure is now
    every fixture's, in ``_probe_target``, which carries the full account.
    """
    _validate_minio_secret_pair()
    endpoint = os.environ.get("TEST_MINIO_ENDPOINT", _MINIO_ENDPOINT_DEFAULT)
    live = LiveMinio(
        settings=MinioSettings(
            endpoint=endpoint,
            bucket=os.environ.get("TEST_MINIO_BUCKET", _MINIO_BUCKET_DEFAULT),
            secure=False,
        ),
        access_key=os.environ.get("TEST_MINIO_ACCESS_KEY", _MINIO_ACCESS_DEFAULT),
        secret_key=os.environ.get("TEST_MINIO_SECRET_KEY", _MINIO_SECRET_DEFAULT),
    )
    _skip_unless_live(endpoint, "MinIO", partial(_minio_handshake, live))
    return live


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


async def _qdrant_list_collections(url: str) -> None:
    client = create_qdrant_client(QdrantSettings(url=url))
    try:
        await client.get_collections()
    finally:
        await client.close()


def _qdrant_handshake(url: str) -> None:
    """List collections through the adapter's own factory: one real REST
    round trip, and the call an API-key-protected server would reject."""
    asyncio.run(_qdrant_list_collections(url))


@pytest.fixture(scope="session")
def live_qdrant() -> str:
    """Probe for a live local Qdrant (Phase 2.5's live harness -- the
    ``live_redis``/``live_minio`` precedent) and hand out its URL; skips the
    ``live_qdrant`` suite when it does not answer a real request."""
    url = os.environ.get("TEST_QDRANT_URL", _QDRANT_URL_DEFAULT)
    _skip_unless_live(url, "Qdrant", partial(_qdrant_handshake, url))
    return url


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


def _embedding_handshake(url: str) -> None:
    """``GET /health`` (``services/embedding/app.py``) -- which answers only
    once the model is actually loaded, so an open port on a container still
    warming up reads as "not ready yet" instead of as a broken suite."""
    with httpx.Client(base_url=url, timeout=_HANDSHAKE_TIMEOUT_S, trust_env=False) as client:
        client.get("/health").raise_for_status()


@pytest.fixture(scope="session")
def live_embedding() -> str:
    """Probe for a live local central embedding service (2.10's
    ``services/embedding/app.py`` -- normally reached only inside the
    Compose network, ``embedding:8080``; this probes wherever an operator
    has published the container's port locally instead, the ``live_qdrant``
    precedent) and hand out its URL; skips the ``live_embedding`` suite when
    unreachable -- which, absent Docker, is every environment this repo's
    own gates run in today (module docstring's own constant comment)."""
    url = os.environ.get("TEST_EMBEDDING_URL", _EMBEDDING_URL_DEFAULT)
    _skip_unless_live(url, "embedding service", partial(_embedding_handshake, url))
    return url


@dataclass(frozen=True, slots=True)
class LiveVault:
    """Connection material for the live Vault harness (the root token from
    ``operator init``, exported as ``TEST_VAULT_TOKEN``)."""

    addr: str
    token: str


def _vault_seal_handshake(addr: str) -> None:
    """Read the seal status -- an unauthenticated call, so it separates "no
    usable Vault here" from "your token is wrong" in the skip reason. A
    SEALED Vault answers HTTP on its port perfectly and then refuses every
    single request, which is the port-probe blind spot in its purest form."""
    client = create_vault_client(VaultSettings(addr=addr), token="")
    if client.sys.is_sealed():
        raise _HandshakeRefused(
            "Vault is sealed -- unseal it with the key in the `vault-init` volume "
            "(docs/stack-commands.md 22)"
        )


def _vault_token_handshake(addr: str, token: str) -> None:
    """Prove the exported ``TEST_VAULT_TOKEN`` is actually accepted, instead
    of discovering it thirteen times as a 403 (this fixture's own docstring).

    ``is_authenticated()`` is deliberately avoided in ``create_vault_client``
    -- it issues ``auth/token/lookup-self``, which an AppRole token's policy
    denies, so it returns False for a perfectly good token. That warning does
    not apply here: this harness authenticates with the ROOT token from
    ``operator init``, for which ``lookup-self`` is always permitted.
    """
    client = create_vault_client(VaultSettings(addr=addr), token=token)
    if not client.is_authenticated():
        raise _HandshakeRefused(
            "TEST_VAULT_TOKEN was rejected -- re-read it from the `vault-init` volume, "
            "see .env.test.example"
        )


@pytest.fixture(scope="session")
def live_vault() -> LiveVault:
    """Probe for a live local Vault (Phase 2.6's live harness -- the
    ``live_redis``/``live_minio``/``live_qdrant`` precedent) and hand out its
    address + root token; skips the ``live_vault`` suite when unreachable, and
    equally when no ``TEST_VAULT_TOKEN`` is exported (the ``TEST_MINIO_*``
    precedent: a missing secret is a skip with a reason, not a 403 storm)."""
    addr = os.environ.get("TEST_VAULT_ADDR", _VAULT_ADDR_DEFAULT)
    _skip_unless_live(addr, "Vault", partial(_vault_seal_handshake, addr))
    token = os.environ.get("TEST_VAULT_TOKEN")
    if not token:
        _unavailable_live_dependency(
            "no TEST_VAULT_TOKEN exported -- read it from the `vault-init` volume, "
            "see .env.test.example"
        )
    _skip_unless_live(
        addr, "Vault (TEST_VAULT_TOKEN)", partial(_vault_token_handshake, addr, token)
    )
    return LiveVault(addr=addr, token=token)


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


def _ollama_handshake(base_url: str) -> None:
    """List the models. Ollama has no credentials to reject, but a port held
    open by something that is not Ollama -- or by a daemon still starting --
    is just as unusable, and this is its cheapest real round trip."""
    with httpx.Client(base_url=base_url, timeout=_HANDSHAKE_TIMEOUT_S, trust_env=False) as client:
        client.get("/api/tags").raise_for_status()


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
    base_url = os.environ.get("TEST_OLLAMA_BASE_URL", _OLLAMA_BASE_URL_DEFAULT)
    _skip_unless_live(base_url, "Ollama", partial(_ollama_handshake, base_url))

    model = os.environ.get("TEST_OLLAMA_MODEL", _OLLAMA_MODEL_DEFAULT)

    # One sync client (httpx, plain -- no adapter code exists yet to build
    # one through), one generous timeout covering both calls below: the
    # tags listing is near-instant, the warmup alone is the ~43s-plus one.
    with httpx.Client(
        base_url=base_url, timeout=_OLLAMA_WARMUP_TIMEOUT_S, trust_env=False
    ) as client:
        # A second `/api/tags` after the handshake's own, deliberately: the
        # probe answers "is this Ollama usable", this call answers "which
        # models does it hold", and keeping them separate keeps the probe
        # cacheable and stateless. Near-instant, once per session.
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
            _unavailable_live_dependency(
                f"Ollama model {model!r} is not pulled locally (have: {sorted(names)})"
            )

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
def repo_spaces(tenant_session: TenantSessionFactory) -> SqlSpaceRepository:
    return SqlSpaceRepository(tenant_session)


@pytest.fixture
def repo_memory(tenant_session: TenantSessionFactory) -> SqlMemoryRepository:
    return SqlMemoryRepository(tenant_session)


@pytest.fixture
def repo_knowledge(tenant_session: TenantSessionFactory) -> SqlDocumentRepository:
    return SqlDocumentRepository(tenant_session)


@pytest.fixture
def repo_reindex_jobs(tenant_session: TenantSessionFactory) -> SqlReindexJobRepository:
    return SqlReindexJobRepository(tenant_session)


@pytest.fixture
def repo_summaries(tenant_session: TenantSessionFactory) -> SqlSummaryRepository:
    return SqlSummaryRepository(tenant_session)


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

    ``workspace.user_presence`` and ``platform.admin_audit_log`` are not here
    for isolation but because both carry an FK to ``workspace.users``: a
    ``TRUNCATE`` that names the referenced table without its referrers is
    refused OUTRIGHT, so omitting either did not merely leak their rows — it
    aborted the whole statement and left every table in the list populated.
    Any future table referencing one of these must join the list for the same
    reason.

    ``knowledge.summaries``/``knowledge.summary_jobs`` (BE-ADM-014,
    ``test_purge_ops_live.py`` -- the first live test to populate either)
    join for the identical FK reason: ``fk_summary_doc`` references
    ``knowledge.documents``, which IS in this list, so omitting
    ``summaries`` would abort the whole statement the moment a row exists.
    ``summary_jobs`` carries no FK to ``documents`` (module docstring of
    ``migrations/versions/knowledge/0003_summaries.py``) but joins anyway,
    for the same test-isolation reason ``usage_rollups``/``limits`` already
    do below.

    ``knowledge.parent_chunks`` (P-14, rag-indexing-plan.md §3.2, step 6)
    joins for the same FK reason as ``summaries``: ``fk_parent_chunk_doc``
    references ``knowledge.documents``, which IS in this list, so omitting
    it would abort the whole statement the moment a row exists.
    """
    yield
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE workspace.users, workspace.workspaces, "
                    "workspace.user_presence, platform.admin_audit_log, "
                    "credentials.credentials, access.role_assignments, media.media_jobs, "
                    "conversations.messages, conversations.conversation_files, "
                    "conversations.conversations, files.files, spaces.spaces, "
                    "memory.memory_items, knowledge.chunks, knowledge.parent_chunks, "
                    "knowledge.reindex_job_items, knowledge.reindex_jobs, "
                    "knowledge.summaries, knowledge.summary_jobs, "
                    "knowledge.documents, "
                    "integrations.connections, integrations.mcp_servers, "
                    "usage.usage_records, usage.usage_rollups, usage.limits, "
                    "platform.outbox, platform.processed_events, "
                    "platform.idempotency_keys"
                )
            )
    finally:
        await engine.dispose()
