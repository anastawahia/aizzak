"""Boot tests for the Composition Root (``framework/di/composition_root.py``).

Until Phase 4.7-b-2 this module had NO test at all — ``from_env()`` was never
executed anywhere, which was tolerable only while it built lazily-connecting
adapters and nothing else. Now it also wires the application layer (provider
resolver, agent registry, orchestrator), so a wiring mistake would otherwise
first surface at deploy time. These tests boot it for real.

No network: every adapter this root constructs connects lazily (SQLAlchemy
engine, redis, qdrant, hvac, httpx clients), so a full ``from_env()`` touches
nothing external. The one genuinely eager side effect is ``PluginLoader``'s
filesystem scan + importlib of ``app.agents`` — which is exactly what we want
asserted.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Sequence

import pytest

from app.agents.orchestrator import AgentOrchestrator
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.composition_root import (
    CompositionRoot,
    _pid_is_alive,
    _RoutedEmbeddingResolver,
    _sweep_stale_notify_groups,
)
from app.framework.di.lifecycle import dispose_all
from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LLMProvider
from app.framework.providers.resolver import ResolvedProvider
from app.infrastructure.config import load_settings
from app.infrastructure.storage.minio_storage import MinioStorage
from app.modules.files.application.use_cases import FileUseCases
from app.modules.knowledge.application.use_cases import KnowledgeRetrievalService
from app.modules.media.application.use_cases import GetJobStatus, MediaUseCases
from app.modules.spaces.application.use_cases import SpacesQueryService, SpaceUseCases

_ROUTING = '{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}}}'

# 2.10 -- the routing table WITH an embedding route, for the tests that
# actually resolve one end to end (the plain `_ROUTING` above deliberately
# carries none, proving the wiring still boots -- see
# `test_unwired_seams_are_absent_rather_than_faked`'s replacement below).
_ROUTING_WITH_EMBEDDING = (
    '{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}},'
    '"embedding":{"default":{"provider":"embedding-local",'
    '"model":"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"}}}'
)


@pytest.fixture
def booted(monkeypatch: pytest.MonkeyPatch) -> CompositionRoot:
    """A real ``from_env()`` boot with the two settings that have no default:
    ``FIREBASE_PROJECT_ID`` (boot-mandatory since 2.7) and a routing table."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING)
    return CompositionRoot.from_env()


@pytest.fixture
def booted_with_embedding_routing(monkeypatch: pytest.MonkeyPatch) -> CompositionRoot:
    """The ``booted`` precedent, with an ``embedding`` route configured too
    -- what actually lets ``resolve_embedding`` succeed end to end (D-16
    fail-closed: an unconfigured namespace, the ``booted`` fixture's own
    routing, boots fine but has no route for a call to find)."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING_WITH_EMBEDDING)
    return CompositionRoot.from_env()


def test_from_env_boots_and_builds_the_orchestrator(booted: CompositionRoot) -> None:
    assert isinstance(booted.orchestrator, AgentOrchestrator)


def test_boot_registers_every_shipped_agent_with_zero_isolations(
    booted: CompositionRoot,
) -> None:
    """The AC-04 add-side, now proven through the REAL Composition Root rather
    than a hand-built registry: a broken plugin would be isolated into
    ``failures`` instead of registered, so asserting both halves is what makes
    this a real check."""
    assert booted.plugin_report.failures == ()
    assert set(booted.plugin_report.loaded) == {
        "rag_agent",
        "data_analysis_agent",
        "file_editing_agent",
        "image_agent",
        "video_agent",
    }
    assert {m.key for m in booted.agent_registry.list()} == set(booted.plugin_report.loaded)


def test_boot_fails_loudly_on_a_provider_route_naming_an_unbuilt_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2.9's boot-time strictness, now reachable for the first time because
    something finally CONSTRUCTS the resolver at boot. Routing to a provider
    whose adapter this root never built is an operator error that must fail at
    startup — not become a 500 on request N."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv(
        "PROVIDER_ROUTING",
        '{"llm":{"default":{"provider":"gemini","model":"gemini-2.5-pro"}}}',
    )

    with pytest.raises(Exception) as excinfo:
        CompositionRoot.from_env()

    assert "gemini" in str(excinfo.value)


async def test_ollama_resolves_keyless_without_touching_the_credential_store(
    booted: CompositionRoot,
) -> None:
    """``keyless_providers={"ollama"}`` is Composition-Root code (2.9 decision
    6), and dropping it is silent at boot: the resolver would simply start
    demanding a credential for the one provider that has no auth at all,
    sending every local-model request through ``CredentialResolver`` into
    Postgres and failing there.

    Hermetic despite touching the real resolver, and deliberately so: keyless
    resolution is defined as the path that never reaches the key resolver, so
    if this test ever needs a database it has already failed. That is exactly
    what the assertion below encodes — this ran with no DB configured, and an
    ``api_key`` of ``""`` is the proof the credential path was skipped.

    (Added after the 4.7-b-2 mutation battery reported this mutation as
    SURVIVED — the boot tests never resolved anything, so nothing hermetic
    covered it.)
    """
    ctx = ExecutionContext(
        workspace_id="018f0000-0000-7000-8000-000000000001",
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset(),
    )

    provider, resolved = await booted.provider_resolver.resolve_llm(ctx, capability="anything")

    assert resolved.provider == "ollama"
    assert resolved.api_key == ""
    assert provider.provider == "ollama"


async def test_embedding_local_resolves_keyless_through_the_real_provider_resolver(
    booted_with_embedding_routing: CompositionRoot,
) -> None:
    """2.10's own version of the ``ollama`` proof above:
    ``keyless_providers`` also names ``"embedding-local"``, and the SAME
    real ``ExternalEmbeddingProvider`` this root builds is what the resolver
    hands back -- hermetic for the identical reason (no DB configured, and
    ``api_key == ""`` is the proof the credential path was skipped)."""
    ctx = ExecutionContext(
        workspace_id="018f0000-0000-7000-8000-000000000001",
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset(),
    )

    provider, resolved = await booted_with_embedding_routing.provider_resolver.resolve_embedding(
        ctx
    )

    assert resolved.provider == "embedding-local"
    assert resolved.api_key == ""
    assert provider.provider == "embedding-local"


def test_unwired_seams_are_absent_rather_than_faked(booted: CompositionRoot) -> None:
    """A seam that is still unwired must be ``None`` — an agent needing one then
    fails with a clean 500 instead of receiving a stub that silently answers
    wrongly. This test is the tripwire that goes RED when a seam lands, forcing
    the docs to be updated with the code: it fired as designed for ``media`` in
    4.7-d-2 and for ``knowledge`` in 2.10 (see the assertion below, no longer
    ``None``); the two remaining asserts are what the Phase-6 lifespan and an
    Exa key will each trip in turn.

    It fired for ``storage`` in 6.1-هـ-1 exactly as designed: the seam is now
    the root's late-binding ``StorageHandle`` — the SAME object, so binding it
    at startup lights the agents up with no orchestrator rebuild — and is
    still honestly UNBOUND straight out of ``from_env`` (the async Vault read
    happens in ``connect_storage``, tested below)."""
    deps = booted.orchestrator._deps

    assert deps.files is not None  # wired in 4.7-b-2
    assert deps.media is not None  # wired in 4.7-d-2
    # 4.7-c-2: both usage inbound ports reach the ORCHESTRATOR (INV-U4 — never
    # `AgentDependencies`, so no agent can call `usage` even by accident).
    assert deps.usage_enforcement is not None
    assert deps.usage_capture is not None
    assert deps.workflows is not None  # wired in 4.7-e-1
    assert deps.conversations is not None  # wired in 4.7-e-1 (D-12)
    assert deps.knowledge is not None  # wired in 2.10: KnowledgeRetrievalService
    assert deps.knowledge is booted.knowledge.search  # the SAME instance, two seams
    assert deps.storage is booted.storage  # 6.1-هـ-1: the handle, shared
    assert booted.storage.is_bound is False  # bound at startup, not boot
    assert deps.web_search is None  # blocked: no Exa key


async def test_disposables_covers_every_raw_client_this_root_owns(
    booted: CompositionRoot,
) -> None:
    """3.79: the tripwire for a NEW client added to the root without a teardown
    thunk. Comparing bound methods is not possible (each attribute access makes
    a fresh object), so the list is checked by ``__self__`` identity — which is
    exactly the property that matters: is THIS client's close in the list.

    The two MinIO clients are absent by design (minio-py exposes no close at
    all); ``vault_client`` is present through a ``to_thread`` closure rather
    than a bound method, since hvac is synchronous — hence the two-part
    assertion instead of one set comparison.
    """
    thunks = booted.disposables()
    owners = {id(getattr(t, "__self__", None)) for t in thunks}

    for client in (
        booted.engine,
        booted.redis_client,
        # stream-topology-plan.md §3, item 4 — the notify bridge's SECOND
        # Redis client: exactly the "new client added without a teardown
        # thunk" shape this tripwire exists to catch.
        booted.notify_redis_client,
        booted.qdrant_client,
        booted.firebase_http,
        booted.ollama_http,
        booted.openai_http,
        booted.embedding_http,
    ):
        assert id(client) in owners

    # Vault: the one non-bound-method entry, offloaded because hvac is sync.
    assert any(getattr(t, "__name__", "") == "_close_vault" for t in thunks)


async def test_disposing_the_root_actually_closes_its_clients(
    booted: CompositionRoot,
) -> None:
    """Hermetic end to end: nothing here ever connected, and disposal of a
    never-connected client is a no-op on the wire — but the httpx pools flip to
    closed, which is the observable proof the thunks are the real ones rather
    than a list of look-alikes."""
    await dispose_all(booted.disposables())

    assert booted.firebase_http.is_closed
    assert booted.ollama_http.is_closed
    assert booted.openai_http.is_closed
    assert booted.embedding_http.is_closed


def test_the_stream_deadline_is_wired_from_limits(booted: CompositionRoot) -> None:
    """5.3-أ: the total stream cap reaches the orchestrator from ``Limits``.
    ``None`` here would silently mean UNCAPPED in production — exactly the
    bare-bundle default that must not leak through ``from_env``."""
    deps = booted.orchestrator._deps

    assert deps.stream_max_duration_s == float(booted.settings.limits.stream_max_duration_s)
    assert deps.stream_max_duration_s == 600.0


def test_the_abandoned_build_bound_is_wired_from_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ب-9 (خطة السيناريوهات §6): the request path is where an abandoned
    build's key is released, and the staleness bound it measures against is
    `Limits.summarize_job_max_duration_s` — the same number the worker ends a
    too-long build with. That identity is the whole argument for the
    derivation: a job that passed the longest build allowed and is still not
    terminal is one no live worker was going to finish, and this is the one
    place the two are joined.

    The setting is moved off the application layer's own default, for the
    reason ب-6's sibling test moves its own: with both sitting at 1,800 the
    assertion would pass just as happily for a root that had stopped passing
    it at all.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING)
    base = load_settings()
    raised = base.model_copy(
        update={"limits": base.limits.model_copy(update={"summarize_job_max_duration_s": 999})}
    )
    monkeypatch.setattr("app.framework.di.composition_root.load_settings", lambda: raised)

    root = CompositionRoot.from_env()

    request = root.knowledge.request_summary._request  # type: ignore[attr-defined]
    assert raised.limits.summarize_job_max_duration_s != base.limits.summarize_job_max_duration_s
    assert request._max_build_duration_s == 999


def test_the_workspace_build_ceiling_is_wired_from_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ب-10 (خطة السيناريوهات §7): the SECOND number this use case takes, and
    the second it takes as a scalar rather than as `Limits`.

    س-24 is why: the knowledge module's application and domain layers import
    no `Settings` at all, and a test parses their trees to say so. So each
    configured quantity crosses as its own argument, and each needs its own
    proof that the root still passes it — a default of three on this side and
    three on the other would let a root that had dropped the argument pass
    happily.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("PROVIDER_ROUTING", _ROUTING)
    base = load_settings()
    raised = base.model_copy(
        update={
            "limits": base.limits.model_copy(update={"max_active_summary_jobs_per_workspace": 17})
        }
    )
    monkeypatch.setattr("app.framework.di.composition_root.load_settings", lambda: raised)

    root = CompositionRoot.from_env()

    request = root.knowledge.request_summary._request  # type: ignore[attr-defined]
    assert raised.limits.max_active_summary_jobs_per_workspace != (
        base.limits.max_active_summary_jobs_per_workspace
    )
    assert request._max_active_jobs == 17


def test_the_files_and_media_bundles_are_wired_over_the_shared_handle(
    booted: CompositionRoot,
) -> None:
    """6.1-هـ-2: the API-facing bundles exist on the root, and the files
    presigned faces hold the SAME storage handle the orchestrator does — so
    `connect_storage`'s single bind lights agents and files up together. The
    media bundle carries `GetJobStatus`'s first-ever construction."""
    assert isinstance(booted.files, FileUseCases)
    assert booted.files.transfers._storage is booted.storage
    assert isinstance(booted.media, MediaUseCases)
    assert isinstance(booted.media.get_status, GetJobStatus)
    # One atomic service behind both media faces (agents + API).
    assert booted.media.requests is booted.orchestrator._deps.media


def test_the_spaces_bundle_is_wired_and_shares_its_mark_with_the_cascade(
    booted: CompositionRoot,
) -> None:
    """``spaces-backend-plan.md`` step 13: the module's bundle exists on the
    root — the ``files``/``media`` precedent — and the cascade marks with the
    bundle's OWN ``DeleteSpace`` rather than a second one.

    The identity is the point, not the presence. Two ``DeleteSpace`` instances
    over the same repository behave identically today and would type-check
    forever, so nothing else in the suite could notice them diverging: the
    mark's idempotence rule (re-deleting emits no event, which is what makes an
    interrupted cascade resumable, §3.6) would then live in two places free to
    be changed in one.
    """
    assert isinstance(booted.spaces, SpaceUseCases)
    assert booted.space_deletion._mark is booted.spaces.delete
    # One store behind every face of the module (the `_build_conversations`
    # one-repository precedent): the space `POST /spaces` mints is the one
    # `GET /spaces` pages and the one the cascade marks.
    store = booted.spaces.create._spaces
    assert booted.spaces.list._spaces is store
    assert booted.spaces.rename._spaces is store
    assert booted.spaces.get._spaces is store
    assert booted.spaces.delete._spaces is store
    # And the quota locks THAT store's rows — the lock and the mark must
    # refuse/observe the same row, or a space could be emptied while an upload
    # is being admitted into it.
    assert booted.space_quota._spaces is store


def test_both_active_space_seams_are_bound_to_one_query_instance(
    booted: CompositionRoot,
) -> None:
    """``spaces-backend-plan.md`` steps 6, 7 and 13 — the two ``ActiveSpaces``
    ports, declared independently by ``files`` and ``conversations`` (§3.1: a
    module never imports another), bound at this one call site to the SAME
    ``SpacesQueryService``.

    Two instances would be harmless while ``get_active``'s rule is one line,
    and that is exactly the failure this pins: "is this space live?" is the
    question both writers ask before they write a row that no listing would
    ever reach if the answer were wrong, and one process must not be able to
    answer it two ways.
    """
    files_seam = booted.files.transfers._register._spaces
    conversations_seam = booted.conversations.start._spaces

    assert isinstance(files_seam, SpacesQueryService)
    assert files_seam is conversations_seam


def test_the_knowledge_bundle_is_wired_with_a_real_search_service(
    booted: CompositionRoot,
) -> None:
    """2.10: ``knowledge.search`` is a REAL ``KnowledgeRetrievalService``
    (never ``None``), so ``POST /search`` no longer 503s at the composition
    level — the router's own 503 branch (``api/v1/routers/knowledge.py``)
    simply stops firing, with no change to the router itself."""
    assert isinstance(booted.knowledge.search, KnowledgeRetrievalService)
    # The identical instance also satisfies the RAG agent's `KnowledgeAccess`
    # seam (module docstring, and `test_unwired_seams_are_absent_rather_than_
    # faked` above) -- one retrieval path, reached two ways.
    assert booted.knowledge.search is booted.orchestrator._deps.knowledge


class _FakeProviderResolver:
    """A minimal ``ProviderResolver`` stub -- only ``resolve_embedding`` is
    ever called by ``_RoutedEmbeddingResolver``, so that is all this fakes."""

    def __init__(self, resolved: ResolvedProvider, provider: EmbeddingProvider) -> None:
        self._resolved = resolved
        self._provider = provider
        self.calls: list[ExecutionContext] = []

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[LLMProvider, ResolvedProvider]:
        raise AssertionError("_RoutedEmbeddingResolver never calls resolve_llm")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        self.calls.append(ctx)
        return self._provider, self._resolved


class _FakeEmbeddingProvider:
    provider = "fake-embedding"

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> object:
        raise AssertionError("not exercised")

    def dimensions(self, model: str) -> int:
        raise AssertionError("not exercised")


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="018f0000-0000-7000-8000-000000000002",
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000dd",
        roles=frozenset(),
    )


async def test_routed_embedding_resolver_delegates_and_maps_onto_resolved_embedding() -> None:
    """``_RoutedEmbeddingResolver`` (2.10) is the ONE piece of new glue code
    in this module with no other test coverage -- ``KnowledgeRetrievalService``
    itself is already proven against fakes/the real ``RetrieveContext`` in
    ``test_knowledge_module.py``; this is what proves the ADAPTATION between
    ``ProviderResolver`` and the module's ``EmbeddingResolver`` seam."""
    resolved = ResolvedProvider(provider="embedding-local", model="minilm", api_key="")
    fake_resolver = _FakeProviderResolver(resolved, _FakeEmbeddingProvider())
    glue = _RoutedEmbeddingResolver(fake_resolver)
    ctx = _ctx()

    result = await glue.resolve_embedding(ctx)

    assert result.model == "minilm"
    assert result.api_key == ""
    assert fake_resolver.calls == [ctx]


async def test_routed_embedding_resolver_drops_the_provider_half_of_the_tuple() -> None:
    """Only ``model``/``api_key`` cross the seam (``ResolvedEmbedding``'s own
    shape) -- the resolved PROVIDER instance itself is never surfaced
    through this glue class."""
    resolved = ResolvedProvider(provider="embedding-local", model="m", api_key="k")
    glue = _RoutedEmbeddingResolver(_FakeProviderResolver(resolved, _FakeEmbeddingProvider()))

    result = await glue.resolve_embedding(_ctx())

    assert not hasattr(result, "provider")


class _FakeSecrets:
    """A ``SecretsProvider`` fake that serves 05 §3's minio entry and counts
    reads — the count is what proves ``connect_storage``'s idempotence."""

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload: dict[str, object] = (
            {"access_key": "minio-ak", "secret_key": "minio-sk"} if payload is None else payload
        )
        self.reads: list[str] = []

    async def get_secret(self, path: str) -> dict[str, object]:
        self.reads.append(path)
        return self.payload

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        raise AssertionError("not exercised")

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        raise AssertionError("not exercised")


async def test_connect_storage_binds_minio_from_the_vault_secret(
    booted: CompositionRoot,
) -> None:
    """6.1-هـ-1 (debt (ز)): the async half of MinIO's wiring. The secret is
    read from 05 §3's path, the 2.4 adapter is built against the settings
    bucket, and it lands in the SAME handle the orchestrator already holds."""
    fake = _FakeSecrets()
    booted.secrets = fake

    await booted.connect_storage()

    assert fake.reads == ["secret/data/minio"]
    assert booted.storage.is_bound is True
    bound = booted.storage._provider
    assert isinstance(bound, MinioStorage)
    assert bound._bucket == booted.settings.minio.bucket


async def test_connect_storage_is_idempotent_across_lifespan_reentries(
    booted: CompositionRoot,
) -> None:
    """A test lifespan can re-enter startup on the same root; the second call
    must not re-read Vault or swap a live adapter out from under callers."""
    fake = _FakeSecrets()
    booted.secrets = fake

    await booted.connect_storage()
    first = booted.storage._provider
    await booted.connect_storage()

    assert fake.reads == ["secret/data/minio"]  # ONE read, not two
    assert booted.storage._provider is first


@pytest.mark.parametrize(
    "payload",
    [
        {"secret_key": "sk"},  # access_key absent
        {"access_key": "", "secret_key": "sk"},  # access_key empty
        {"access_key": "ak"},  # secret_key absent
        {"access_key": "ak", "secret_key": ""},  # secret_key empty
        {"access_key": 7, "secret_key": "sk"},  # access_key not a string
    ],
)
async def test_connect_storage_fails_fast_on_a_malformed_secret(
    booted: CompositionRoot, payload: dict[str, object]
) -> None:
    """The FIREBASE_PROJECT_ID/D5 precedent at startup: a malformed
    ``secret/data/minio`` aborts boot with a named complaint instead of
    booting a replica whose every file operation would 500 later."""
    booted.secrets = _FakeSecrets(payload)

    with pytest.raises(ValidationError):
        await booted.connect_storage()
    assert booted.storage.is_bound is False


def test_the_notification_bridge_is_wired_over_the_shared_hub(
    booted: CompositionRoot,
) -> None:
    """5.3-د: `from_env` builds the hub AND the notify consumer over it, so
    the API process boots with the WS endpoint's hub and the worker-result
    fan-out sharing one registry. Unwired, Phase 6 would have no bridge to
    start and notifications would never reach live sockets.

    3.81 (P0-2): the group is PER PROCESS, not the old shared `"cg.notify"`
    literal -- `from_env` runs in THIS test process, so the expected group is
    computable from this process's own hostname/pid."""
    assert booted.hub is not None
    assert {sub.stream for sub in booted.notify_subscriptions} == {
        "stream.knowledge",
        "stream.media",
    }
    expected_group = f"cg.notify.{socket.gethostname()}.{os.getpid()}"
    assert all(sub.group == expected_group for sub in booted.notify_subscriptions)
    # The hub's cap is the injected 07 §4 limit, not a bare default.
    assert booted.hub.user_connection_count("nobody") == 0


# --------------------------------------------------------------------------- #
# 3.81 (P0-2): per-process notify group lifecycle                            #
# --------------------------------------------------------------------------- #
def test_pid_is_alive_is_true_for_this_very_process() -> None:
    assert _pid_is_alive(os.getpid()) is True


def test_pid_is_alive_is_false_for_a_reaped_child_process() -> None:
    """The OS's own liveness answer, not a guess: spawn a real child, wait
    for it to exit (reaped -- its pid is no longer in the process table), and
    confirm ``os.kill(pid, 0)`` -- the mechanism ``_pid_is_alive`` wraps --
    genuinely says NO for it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()

    assert _pid_is_alive(proc.pid) is False


class _FakeGroupConsumer:
    """A minimal stand-in for ``RedisStreamsConsumer`` -- only the two
    methods ``_sweep_stale_notify_groups`` calls, so this stays hermetic."""

    def __init__(self, groups: dict[str, list[str]]) -> None:
        self._groups = groups
        self.destroyed: list[tuple[str, str]] = []

    async def list_groups(self, stream: str) -> list[str]:
        return list(self._groups.get(stream, []))

    async def destroy_group(self, stream: str, group: str) -> None:
        self.destroyed.append((stream, group))
        if group in self._groups.get(stream, []):
            self._groups[stream].remove(group)


async def test_sweep_destroys_only_this_hosts_dead_pid_groups() -> None:
    """The safety argument from ``_sweep_stale_notify_groups``'s own
    docstring, exercised directly: a dead pid on THIS host is swept, a live
    pid on THIS host survives, a dead pid under a DIFFERENT host is never
    even considered, and a group that isn't shaped like a notify group at
    all (``cg.knowledge``) is left alone entirely."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_group = f"cg.notify.host-a.{proc.pid}"
    live_group = f"cg.notify.host-a.{os.getpid()}"
    other_host_group = f"cg.notify.host-b.{proc.pid}"  # dead pid, WRONG host
    unrelated_group = "cg.knowledge"  # not a notify group at all

    fake = _FakeGroupConsumer(
        {
            "stream.knowledge": [dead_group, live_group, other_host_group, unrelated_group],
            "stream.media": [],
        }
    )

    swept = await _sweep_stale_notify_groups(
        fake,  # type: ignore[arg-type]
        ["stream.knowledge", "stream.media"],
        hostname="host-a",
    )

    assert swept == [dead_group]
    assert fake.destroyed == [("stream.knowledge", dead_group)]
    # The live/other-host/unrelated groups all survive, untouched.
    assert set(fake._groups["stream.knowledge"]) == {live_group, other_host_group, unrelated_group}


async def test_sweep_destroys_nothing_when_every_candidate_is_alive() -> None:
    fake = _FakeGroupConsumer({"stream.knowledge": [f"cg.notify.host-a.{os.getpid()}"]})

    swept = await _sweep_stale_notify_groups(
        fake,  # type: ignore[arg-type]
        ["stream.knowledge"],
        hostname="host-a",
    )

    assert swept == []
    assert fake.destroyed == []


class _FakeStreamConsumer:
    """Stands in for ``StreamConsumer`` -- only ``teardown`` matters here."""

    def __init__(self) -> None:
        self.torn_down_with: list[object] = []

    async def teardown(self, subscriptions: object) -> None:
        self.torn_down_with.append(subscriptions)


async def test_teardown_notify_bridge_delegates_to_the_notify_consumers_own_teardown(
    booted: CompositionRoot,
) -> None:
    """§3.81's shutdown counterpart: destroys THIS process's own group over
    exactly the subscriptions ``from_env`` built. Verified by delegation to a
    fake rather than a live Redis round trip -- that proof lives in
    ``tests/integration/test_notify_bridge_lifecycle_live.py``."""
    fake_consumer = _FakeStreamConsumer()
    booted.notify_consumer = fake_consumer  # type: ignore[assignment]

    await booted.teardown_notify_bridge()

    assert fake_consumer.torn_down_with == [booted.notify_subscriptions]
