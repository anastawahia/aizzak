"""Unit tests for ``stream-topology-plan.md`` §3, "الخطوة 1" -- the read
timeout that wraps a blocking ``XREADGROUP ... BLOCK <block_ms>`` read must
be LONGER than the window it wraps, everywhere a caller passes ``block_ms``
at all, and must stay untouched everywhere a caller does not.

Hermetic throughout, the ``test_workers_bootstrap.py``/``test_composition_
root.py`` precedent: every client factory involved
(``create_engine``/``create_redis_client``/``create_qdrant_client``/
``create_embedding_http_client``/``create_openai_image_http_client``) is
lazy -- not one connection is opened here -- and ``build_vault``/
``bind_minio``, the only genuinely-eager I/O, are monkeypatched exactly as
``test_workers_bootstrap.py`` does it.

Coverage rule under test (the plan's own words): "من يمرّر ``block_ms`` يجب
أن يمرّر المهلة المشتقّة معه" -- four sites today (``bootstrap.py`` x3,
``composition_root.py:605``) -- and the two named exceptions that must NOT
move: the API's own cache client (``composition_root.py:885``) and the
relay's client (``bootstrap.py:249``), both fixed at ``2.0`` on purpose.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from redis.asyncio import Redis

from app.framework.di.composition_root import CompositionRoot
from app.framework.di.lifecycle import Disposable
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import (
    _CONNECT_TIMEOUT_S,
    _SOCKET_TIMEOUT_S,
    blocking_read_timeout_s,
    create_redis_client,
)
from app.infrastructure.config import load_settings
from app.workers.bootstrap import (
    build_knowledge_worker_from_env,
    build_media_worker_from_env,
    build_memory_worker_from_env,
    build_relay_from_env,
)

_ROUTING = '{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}}}'


def _redis_client_of(disposables: Sequence[Disposable]) -> Redis:
    """Pick the one ``Redis`` client out of a builder's disposables list --
    the ``test_composition_root.py`` ``__self__``-identity precedent,
    restated as a lookup instead of a membership check."""
    for dispose in disposables:
        owner = getattr(dispose, "__self__", None)
        if isinstance(owner, Redis):
            return owner
    raise AssertionError("no Redis client found among the disposables")


def _read_timeout(client: Redis) -> float:
    return float(client.connection_pool.connection_kwargs["socket_timeout"])


def _connect_timeout(client: Redis) -> float:
    return float(client.connection_pool.connection_kwargs["socket_connect_timeout"])


def _fake_build_vault(settings: object) -> tuple[object, object, object]:
    return object(), object(), object()


async def _fake_bind_minio(storage: object, secrets: object, settings: object) -> None:
    return None


@pytest.fixture
def _no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``test_workers_bootstrap.py`` precedent: the real pair performs an
    AppRole login and a live Vault/MinIO read, which this suite must never
    touch."""
    monkeypatch.setattr("app.workers.bootstrap.build_vault", _fake_build_vault)
    monkeypatch.setattr("app.workers.bootstrap.bind_minio", _fake_bind_minio)


@pytest.fixture
def _booted_root(monkeypatch: pytest.MonkeyPatch) -> CompositionRoot:
    """The ``test_composition_root.py`` ``booted`` precedent, restated here
    so this file stays self-contained: a real ``from_env()`` boot with the
    two settings that have no default."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING)
    return CompositionRoot.from_env()


# --------------------------------------------------------------------------- #
# `blocking_read_timeout_s` itself                                            #
# --------------------------------------------------------------------------- #
def test_blocking_read_timeout_s_is_always_longer_than_the_block_window() -> None:
    for block_ms in (1, 200, 2000, 5000, 30_000):
        assert blocking_read_timeout_s(block_ms) > block_ms / 1000


def test_blocking_read_timeout_s_is_a_derivation_not_a_bare_second_literal() -> None:
    """Item 2's whole point: the derived value MOVES when `block_ms` moves --
    a bare constant would not."""
    assert blocking_read_timeout_s(1000) != blocking_read_timeout_s(9000)
    assert blocking_read_timeout_s(5000) == pytest.approx(6.0)


# --------------------------------------------------------------------------- #
# `create_redis_client`'s new keyword-only parameter                          #
# --------------------------------------------------------------------------- #
def test_create_redis_client_defaults_the_read_timeout_to_the_old_constant() -> None:
    """Every existing call site that does not pass `read_timeout_s` must
    behave byte-for-byte as before the split."""
    client = create_redis_client(_settings())
    assert _read_timeout(client) == _SOCKET_TIMEOUT_S == 2.0


def test_create_redis_client_accepts_an_explicit_read_timeout() -> None:
    client = create_redis_client(_settings(), read_timeout_s=9.5)
    assert _read_timeout(client) == 9.5


def _settings() -> RedisSettings:
    return RedisSettings(url="redis://127.0.0.1:6379/0")


# --------------------------------------------------------------------------- #
# Test 1: the three workers' clients AND the notify-bridge client are built  #
# with a READ timeout greater than the configured `block_ms`.                #
# --------------------------------------------------------------------------- #
async def test_knowledge_worker_redis_client_has_a_read_timeout_above_block_ms(
    _no_vault: None,
) -> None:
    settings = load_settings()
    _, _, disposables = await build_knowledge_worker_from_env()
    client = _redis_client_of(disposables)
    assert _read_timeout(client) > settings.events.consumer_block_ms / 1000


async def test_media_worker_redis_client_has_a_read_timeout_above_block_ms(
    _no_vault: None,
) -> None:
    settings = load_settings()
    _, _, disposables = await build_media_worker_from_env()
    client = _redis_client_of(disposables)
    assert _read_timeout(client) > settings.events.consumer_block_ms / 1000


def test_memory_worker_redis_client_has_a_read_timeout_above_block_ms() -> None:
    settings = load_settings()
    _, _, disposables = build_memory_worker_from_env()
    client = _redis_client_of(disposables)
    assert _read_timeout(client) > settings.events.consumer_block_ms / 1000


def test_notify_bridge_redis_client_has_a_read_timeout_above_block_ms(
    _booted_root: CompositionRoot,
) -> None:
    assert (
        _read_timeout(_booted_root.notify_redis_client)
        > _booted_root.settings.events.consumer_block_ms / 1000
    )


# --------------------------------------------------------------------------- #
# Test 2: the API's cache client and the relay's client stay at EXACTLY 2.0. #
# --------------------------------------------------------------------------- #
def test_api_cache_client_stays_at_exactly_two_seconds(_booted_root: CompositionRoot) -> None:
    """Guard #1: a "fix" must never heal the notify bridge by slowing every
    request in the platform."""
    assert _read_timeout(_booted_root.redis_client) == 2.0


def test_relay_client_stays_at_exactly_two_seconds() -> None:
    """Guard #2: the relay is the FIFTH client, named explicitly as the
    exception (stream-topology-plan.md §3's coverage rule) -- it polls with a
    short sleep and never blocks, so it must NOT be generalised onto the
    derived timeout later, e.g. once step 3 mounts a consumer-shaped object
    on top of it."""
    _, _, disposables = build_relay_from_env()
    client = _redis_client_of(disposables)
    assert _read_timeout(client) == 2.0


# --------------------------------------------------------------------------- #
# Test 3: `_CONNECT_TIMEOUT_S` stays 2.0 in all FIVE sites, no exceptions.    #
# --------------------------------------------------------------------------- #
async def test_connect_timeout_is_untouched_everywhere(
    _no_vault: None, _booted_root: CompositionRoot
) -> None:
    assert _CONNECT_TIMEOUT_S == 2.0

    _, _, knowledge_disposables = await build_knowledge_worker_from_env()
    _, _, media_disposables = await build_media_worker_from_env()
    _, _, memory_disposables = build_memory_worker_from_env()
    _, _, relay_disposables = build_relay_from_env()

    clients = [
        _redis_client_of(knowledge_disposables),
        _redis_client_of(media_disposables),
        _redis_client_of(memory_disposables),
        _redis_client_of(relay_disposables),
        _booted_root.redis_client,
        _booted_root.notify_redis_client,
    ]
    for client in clients:
        assert _connect_timeout(client) == 2.0


# --------------------------------------------------------------------------- #
# Test 4: moving `CONSUMER_BLOCK_MS` moves the derived timeout with it, in   #
# all four sites -- the drift guard that makes the fix durable.              #
# --------------------------------------------------------------------------- #
async def test_changing_consumer_block_ms_moves_the_derived_timeout_everywhere(
    monkeypatch: pytest.MonkeyPatch, _no_vault: None
) -> None:
    monkeypatch.setenv("CONSUMER_BLOCK_MS", "9000")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING)
    expected = blocking_read_timeout_s(9000)
    assert expected != blocking_read_timeout_s(5000)  # sanity: the default moved

    _, _, knowledge_disposables = await build_knowledge_worker_from_env()
    _, _, media_disposables = await build_media_worker_from_env()
    _, _, memory_disposables = build_memory_worker_from_env()
    root = CompositionRoot.from_env()

    assert _read_timeout(_redis_client_of(knowledge_disposables)) == expected
    assert _read_timeout(_redis_client_of(media_disposables)) == expected
    assert _read_timeout(_redis_client_of(memory_disposables)) == expected
    assert _read_timeout(root.notify_redis_client) == expected
    # The two exceptions must still be untouched by the SAME env change.
    assert _read_timeout(root.redis_client) == 2.0
