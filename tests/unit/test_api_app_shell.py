"""ASGI tests for the API application shell (``app/api/main.py``, 6.1-a).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with fakes. What these pin, against 03 §0/§1/§4:

* ``/health`` (liveness, 200, no auth) and ``/health/ready`` (503 until the
  lifespan marks the replica ready, 200 after);
* the correlation id — generated when absent, echoed when the client sends one,
  present on every response and every error body (03 §0);
* the RFC 9457 problem responses — an ``AppError`` carrying its own code/status,
  a missing bearer (``auth.missing_token``), a bad token (``auth.invalid_token``
  from the fake authenticator), a request-validation 422 with ``errors[]``, and
  an unexpected crash reduced to ``common.internal`` with no leak;
* (6.2-ب) that Starlette's OWN failures — an unroutable path, a wrong verb —
  are problems too, and that the generated OpenAPI documents the error body it
  actually sends;
* that the WebSocket endpoint (5.3-ج) is mounted at ``/api/v1/ws``.

Probe routes are added to the returned app IN THE TEST (never in production
code) so the auth → context → error pipeline can be exercised end-to-end before
any real resource router exists.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api import main as main_module
from app.api.main import (
    CORRELATION_HEADER,
    PROBLEM_MEDIA_TYPE,
    create_app,
)
from app.api.v1.dependencies import ApiServices, Context, Principal
from app.api.v1.dto.problem import ProblemDetails
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.lifecycle import Disposable
from app.framework.errors import NotFoundError, RateLimitedError, UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.infrastructure.monitoring.vault_health import VaultProbe
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

# This suite never exercises the files/media routes; ApiServices simply
# requires the fields (6.1-هـ-3), so one shared in-memory stack suffices.
_FILES_MEDIA = build_files_media()
_CREDENTIALS = build_credentials()
_WORKSPACE_USAGE = build_workspace_usage()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"


class _FakeLLM:
    provider = "fake"

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("not exercised")

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        raise AssertionError("not exercised")

    def supports(self, capability: str) -> bool:
        return True


class _FakeResolver:
    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        return _FakeLLM(), ResolvedProvider(provider="fake", model="fake-model", api_key="k")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"member"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"member"}))


class _Body(BaseModel):
    name: str


def _make_app(
    *,
    startup: tuple[Callable[[], Coroutine[Any, Any, None]], ...] = (),
    background: tuple[Callable[[], Coroutine[Any, Any, None]], ...] = (),
    shutdown: tuple[Disposable, ...] = (),
) -> FastAPI:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),
            conversations=conversations.service,
            authorization=build_authorization(),
        )
    )
    services = ApiServices(
        settings=Settings(),
        orchestrator=orchestrator,
        hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
        agents=registry,
        conversations=conversations.use_cases,
        workflows=InMemoryWorkflowRegistry(),
        files=_FILES_MEDIA.files,
        media=_FILES_MEDIA.media,
        workspace=_WORKSPACE_USAGE.workspace,
        usage=_WORKSPACE_USAGE.usage,
        credentials=_CREDENTIALS.credentials,
        knowledge=_KNOWLEDGE.knowledge,
        integrations=_INTEGRATIONS.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
    )
    app = create_app(
        services,
        http_authenticator=_FakeAuth(),
        ws_authenticator=_FakeWsAuth(),
        startup=startup,
        background=background,
        shutdown=shutdown,
    )

    @app.get("/api/v1/_ctx")
    async def _ctx(ctx: Context) -> dict[str, object]:
        return {
            "workspace_id": ctx.workspace_id,
            "user_id": ctx.user_id,
            "correlation_id": ctx.correlation_id,
            "roles": sorted(ctx.roles),
        }

    @app.get("/api/v1/_notfound")
    async def _notfound(ctx: Context) -> dict[str, str]:
        raise NotFoundError("no such thing")

    @app.get("/api/v1/_boom")
    async def _boom(ctx: Context) -> dict[str, str]:
        raise ValueError("secret detail 42")

    @app.get("/api/v1/_throttled")
    async def _throttled(ctx: Context) -> dict[str, str]:
        # What the orchestrator raises on a usage denial once the enforcement
        # adapter supplied a reset (3.79).
        raise RateLimitedError(
            "usage limit reached", code="usage.quota_exceeded", retry_after_s=1800
        )

    @app.get("/api/v1/_throttled_unknown")
    async def _throttled_unknown(ctx: Context) -> dict[str, str]:
        raise RateLimitedError("usage limit reached", code="usage.quota_exceeded")

    @app.post("/api/v1/_validate")
    async def _validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/api/v1/_teapot")
    async def _teapot() -> dict[str, str]:
        # A status the handler's map does not carry. Nothing in `src/app`
        # raises `HTTPException` at all, so this can only be reached from a
        # test — which is exactly what makes the fallback worth pinning.
        raise StarletteHTTPException(status_code=418, detail="short and stout")

    return app


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Health                                                                      #
# --------------------------------------------------------------------------- #
def test_health_is_ok_without_auth() -> None:
    client = TestClient(_make_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers[CORRELATION_HEADER]


def test_ready_gates_on_state_flag() -> None:
    app = _make_app()
    client = TestClient(app)
    app.state.ready = False
    assert client.get("/health/ready").status_code == 503
    app.state.ready = True
    assert client.get("/health/ready").status_code == 200


def test_lifespan_toggles_ready() -> None:
    app = _make_app()
    assert app.state.ready is False
    with TestClient(app):
        assert app.state.ready is True
    assert app.state.ready is False


def test_startup_hooks_complete_before_tasks_and_before_ready() -> None:
    """6.1-هـ-1: the lifespan ORDER is the contract — every startup hook
    finishes before any background task starts and before ``ready`` flips, so
    neither a task nor a routed request can observe half-finished wiring (in
    production: `connect_storage` completing before the first file request)."""
    events: list[str] = []
    ready_when_hook_ran: list[bool] = []
    app: FastAPI | None = None

    async def hook_one() -> None:
        assert app is not None
        ready_when_hook_ran.append(bool(app.state.ready))
        events.append("hook-1")

    async def hook_two() -> None:
        events.append("hook-2")

    async def task() -> None:
        events.append("task-started")
        await asyncio.sleep(3600)  # cancelled at shutdown

    app = _make_app(startup=(hook_one, hook_two), background=(task,))
    with TestClient(app):
        pass

    assert events[:2] == ["hook-1", "hook-2"]  # hooks first, in order
    assert events[2:] == ["task-started"]  # the task only after both
    assert ready_when_hook_ran == [False]  # ready had not flipped yet


def test_a_failing_startup_hook_aborts_boot() -> None:
    """Fail-fast: a hook that raises (Vault down at `connect_storage`, in
    production) must abort startup — the app never reports ready and the
    background tasks never start — rather than serve half-wired."""
    started: list[str] = []

    async def bad_hook() -> None:
        raise RuntimeError("vault is down")

    async def task() -> None:
        started.append("task")

    app = _make_app(startup=(bad_hook,), background=(task,))
    with pytest.raises(RuntimeError, match="vault is down"), TestClient(app):
        pass

    assert app.state.ready is False
    assert started == []


# --------------------------------------------------------------------------- #
# Shutdown (3.79)                                                             #
# --------------------------------------------------------------------------- #
def test_shutdown_disposes_after_ready_flips_and_tasks_are_reaped() -> None:
    """3.79: the teardown ORDER is the contract — ``ready`` goes false, the
    background tasks are cancelled AND reaped, and only THEN are the clients
    disposed. Closing the notify bridge's Redis out from under a task still
    awaiting on it is exactly the graceful-drain failure this ordering
    prevents."""
    events: list[str] = []
    ready_at_dispose: list[bool] = []
    app: FastAPI | None = None

    async def task() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("task-reaped")
            raise

    async def close_client() -> None:
        assert app is not None
        ready_at_dispose.append(bool(app.state.ready))
        events.append("disposed")

    app = _make_app(background=(task,), shutdown=(close_client,))
    with TestClient(app):
        pass

    assert events == ["task-reaped", "disposed"]
    assert ready_at_dispose == [False]


def test_shutdown_disposes_even_when_boot_aborts() -> None:
    """The disposal shares the task-cancellation ``finally``, so it is on the
    aborted-boot path too: a startup hook that raises still gives back the
    pools ``from_env`` had already opened, instead of leaking them out of a
    process about to exit non-zero."""
    disposed: list[str] = []

    async def bad_hook() -> None:
        raise RuntimeError("vault is down")

    async def close_client() -> None:
        disposed.append("engine")

    app = _make_app(startup=(bad_hook,), shutdown=(close_client,))
    with pytest.raises(RuntimeError, match="vault is down"), TestClient(app):
        pass

    assert disposed == ["engine"]


# --------------------------------------------------------------------------- #
# Background task death visibility (stream-topology-plan.md §3, item 5) --    #
# logging ONLY, no management (see `_log_background_task_death`'s docstring). #
# --------------------------------------------------------------------------- #
def test_a_background_task_that_dies_with_an_exception_leaves_an_error_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§1-أ-2's silent death gets its one line here: the exception text must
    land in the log once the task's own done-callback has run -- before this
    step nothing did (the ``finally`` in ``_make_lifespan`` swallows
    ``BaseException`` and never looks at a task again until shutdown)."""

    async def task() -> None:
        raise RuntimeError("bridge exploded")

    app = _make_app(background=(task,))
    with caplog.at_level(logging.ERROR), TestClient(app):
        time.sleep(0.2)  # let the task raise and its done-callback run

    matches = [
        record
        for record in caplog.records
        if record.name == "app.api.main" and record.levelno >= logging.ERROR
    ]
    assert matches
    assert matches[0].getMessage() == "api.background_task_died"
    assert "bridge exploded" in caplog.text


def test_a_background_task_cancelled_at_clean_shutdown_leaves_no_error_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real guard, in the plan's own words: cancellation at shutdown is
    the ORDINARY outcome of every clean stop, and must produce ZERO error
    lines -- otherwise the new line becomes noise every operator learns to
    ignore, which destroys the visibility item 5 exists to buy."""

    async def task() -> None:
        await asyncio.sleep(3600)  # cancelled at shutdown; never dies on its own

    app = _make_app(background=(task,))
    with caplog.at_level(logging.ERROR), TestClient(app):
        pass

    assert not [
        record
        for record in caplog.records
        if record.name == "app.api.main" and record.levelno >= logging.ERROR
    ]


# --------------------------------------------------------------------------- #
# Correlation id                                                              #
# --------------------------------------------------------------------------- #
def test_correlation_generated_when_absent() -> None:
    client = TestClient(_make_app())
    response = client.get("/health")
    assert response.headers[CORRELATION_HEADER]


def test_correlation_echoed_when_supplied() -> None:
    client = TestClient(_make_app())
    supplied = "trace-abc-123"
    response = client.get("/health", headers={CORRELATION_HEADER: supplied})
    assert response.headers[CORRELATION_HEADER] == supplied


def test_correlation_flows_into_context() -> None:
    client = TestClient(_make_app())
    supplied = "trace-xyz-999"
    response = client.get("/api/v1/_ctx", headers={**_auth(), CORRELATION_HEADER: supplied})
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == _W1
    assert body["correlation_id"] == supplied
    assert body["roles"] == ["member"]


# --------------------------------------------------------------------------- #
# Auth seam                                                                   #
# --------------------------------------------------------------------------- #
def test_missing_bearer_is_401_problem() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/_ctx")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    body = response.json()
    assert body["code"] == "auth.missing_token"
    assert body["status"] == 401
    assert body["correlation_id"]


def test_bad_token_is_401_invalid_token() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/_ctx", headers=_auth("nope"))
    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"


def test_non_bearer_scheme_is_missing_token() -> None:
    # A present-but-non-bearer Authorization header is treated as no credential,
    # not a bad one: the scheme check, not just header-presence, drives it.
    client = TestClient(_make_app())
    response = client.get("/api/v1/_ctx", headers={"Authorization": "Basic Zm9v"})
    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


def test_bearer_token_is_stripped() -> None:
    # Surrounding whitespace after the scheme is trimmed before the token
    # reaches the authenticator, so a padded header still authenticates.
    client = TestClient(_make_app())
    response = client.get("/api/v1/_ctx", headers={"Authorization": f"Bearer  {_GOOD}  "})
    assert response.status_code == 200
    assert response.json()["workspace_id"] == _W1


# --------------------------------------------------------------------------- #
# Problem responses                                                           #
# --------------------------------------------------------------------------- #
def test_app_error_maps_to_problem() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/_notfound", headers=_auth())
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    body = response.json()
    assert body["code"] == "common.not_found"
    assert body["type"] == "https://errors.platform/common.not_found"
    assert body["instance"] == "/api/v1/_notfound"
    assert body["correlation_id"] == response.headers[CORRELATION_HEADER]


def test_a_429_with_a_computed_reset_carries_retry_after() -> None:
    """3.79: 03 §4 names the header, and it now has a producer — a usage
    denial whose binding limit has a period boundary. The body is unchanged;
    the header is pure addition."""
    client = TestClient(_make_app())
    response = client.get("/api/v1/_throttled", headers=_auth())

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1800"
    assert response.json()["code"] == "usage.quota_exceeded"
    # The correlation header still rides along; the new one did not displace it.
    assert response.headers[CORRELATION_HEADER]


def test_a_429_without_a_computed_reset_carries_no_retry_after() -> None:
    """No header at all rather than a plausible-looking number — the exact
    reason v1 shipped without one."""
    client = TestClient(_make_app())
    response = client.get("/api/v1/_throttled_unknown", headers=_auth())

    assert response.status_code == 429
    assert "Retry-After" not in response.headers


def test_other_problems_are_untouched_by_the_retry_after_path() -> None:
    """The header is selected by ERROR TYPE, not by status: nothing else may
    start emitting it."""
    client = TestClient(_make_app())

    assert "Retry-After" not in client.get("/api/v1/_notfound", headers=_auth()).headers
    assert "Retry-After" not in client.post("/api/v1/_validate", json={}).headers


def test_validation_error_is_422_with_fields() -> None:
    client = TestClient(_make_app())
    response = client.post("/api/v1/_validate", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "common.validation_error"
    assert body["errors"]
    assert body["errors"][0]["field"] == "name"


def test_unexpected_error_is_500_without_leak() -> None:
    # raise_server_exceptions=False so the ASGI 500 handler is exercised rather
    # than the exception re-raised into the test.
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/api/v1/_boom", headers=_auth())
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "common.internal"
    assert body["status"] == 500
    assert "secret detail" not in response.text


# --------------------------------------------------------------------------- #
# Starlette's own failures are problems too (6.2-ب)                           #
# --------------------------------------------------------------------------- #
def test_an_unroutable_path_is_a_problem_not_starlettes_detail_json() -> None:
    """Before 6.2-ب this answered ``{"detail": "Not Found"}`` as
    ``application/json`` — the first response most clients ever see from a
    typo'd URL was the one response that was not a problem (DD-05 says ALL
    errors), and no router test could catch it because no router is involved.
    """
    client = TestClient(_make_app())
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    body = response.json()
    assert body["code"] == "common.not_found"
    assert body["instance"] == "/api/v1/does-not-exist"
    assert body["correlation_id"] == response.headers[CORRELATION_HEADER]


def test_a_wrong_verb_is_405_method_not_allowed() -> None:
    """The path exists, the verb does not — a different problem from "no such
    resource", and 03 §4 gained the entry rather than let it borrow a 404."""
    client = TestClient(_make_app())
    response = client.post("/health")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "common.method_not_allowed"


def test_an_unmapped_http_status_degrades_to_internal() -> None:
    """A status the map does not carry means a framework path we do not model.
    It answers the catalogued ``common.internal``/500 rather than a code
    invented from the status on the spot — and the ORIGINAL status is dropped
    on purpose, because a body claiming ``common.internal`` with a 418 beside
    it would break the catalog's own status column."""
    client = TestClient(_make_app())
    response = client.get("/api/v1/_teapot")

    assert response.status_code == 500
    assert response.json()["code"] == "common.internal"


def test_every_problem_body_validates_against_the_documented_model() -> None:
    """The wire body and ``ProblemDetails`` (what OpenAPI publishes) are two
    descriptions of one thing; this is the seam where they could drift."""
    client = TestClient(_make_app())

    for response in (
        client.get("/api/v1/does-not-exist"),
        client.post("/health"),
        client.get("/api/v1/_notfound", headers=_auth()),
        client.post("/api/v1/_validate", json={}),
    ):
        ProblemDetails.model_validate(response.json())


# --------------------------------------------------------------------------- #
# OpenAPI declares the error contract (6.2-ب · AC-07)                         #
# --------------------------------------------------------------------------- #
def test_every_operation_declares_the_problem_default() -> None:
    """`openapi.yaml` attaches ``default: Problem`` to every operation; the
    GENERATED document said nothing about errors at all, so a client built
    from ``/openapi.json`` had no error type."""
    schema = _make_app().openapi()

    operations = [
        (path, verb, operation)
        for path, verbs in schema["paths"].items()
        for verb, operation in verbs.items()
    ]
    assert operations
    for path, verb, operation in operations:
        default = operation["responses"].get("default")
        assert default is not None, f"{verb} {path}"
        content = default["content"]
        assert set(content) == {PROBLEM_MEDIA_TYPE}, f"{verb} {path}"
        assert content[PROBLEM_MEDIA_TYPE]["schema"]["$ref"].endswith("/ProblemDetails")


def test_the_problem_schema_is_published_once_in_components() -> None:
    schema = _make_app().openapi()

    problem_schema = schema["components"]["schemas"]["ProblemDetails"]
    assert set(problem_schema["required"]) == {"type", "title", "status", "code", "correlation_id"}


def test_asking_twice_neither_rebuilds_nor_re_files() -> None:
    """The media-type fix runs on FastAPI's own memoised document, in place.
    So it must be idempotent — a second call must not re-file an entry that
    is no longer there (and must not quietly hand back a second document)."""
    app = _make_app()

    first = app.openapi()
    second = app.openapi()

    assert first is second
    content = second["paths"]["/api/v1/agents"]["get"]["responses"]["default"]["content"]
    assert set(content) == {PROBLEM_MEDIA_TYPE}


# --------------------------------------------------------------------------- #
# WebSocket mount                                                             #
# --------------------------------------------------------------------------- #
def test_websocket_mounted_at_api_v1_ws() -> None:
    client = TestClient(_make_app())
    with client.websocket_connect(f"/api/v1/ws?token={_GOOD}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


# --------------------------------------------------------------------------- #
# P1-8 (step 11): the renewal loop is wired by the PRODUCTION factory         #
# --------------------------------------------------------------------------- #
def test_production_wiring_starts_and_stops_the_hubs_renewal_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A STRUCTURAL guard, not a textual one: it calls the real
    ``create_production_app`` over a real ``CompositionRoot`` and inspects the
    hook tuples that factory actually passes.

    Why it earns its place: if nobody ever calls ``hub.start_renewal``, every
    live socket's registry entry ages out after ``entry_ttl_s`` and the
    cross-process cap silently LOOSENS — with no exception, no log line and no
    other failing test anywhere. And ``stop_renewal`` must come BEFORE
    ``disposables()``, whose entries close the very Redis client the loop may
    be mid-call on (the ``teardown_notify_bridge`` precedent, §3.81).
    """
    captured: dict[str, Any] = {}

    def _capture(_services: Any, **kwargs: Any) -> FastAPI:
        captured.update(kwargs)
        return FastAPI()

    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv(
        "PROVIDER_ROUTING", '{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}}}'
    )
    monkeypatch.setattr(main_module, "create_app", _capture)

    main_module.create_production_app()

    startup = [getattr(hook, "__name__", "") for hook in captured["startup"]]
    shutdown = [getattr(thunk, "__name__", "") for thunk in captured["shutdown"]]
    assert "start_renewal" in startup
    assert "stop_renewal" in shutdown
    # Reaped before ANY client is disposed — `disposables()` closes Redis.
    assert shutdown.index("stop_renewal") < shutdown.index("dispose")


# --------------------------------------------------------------------------- #
# ن-10: the Vault liveness probe is wired by the PRODUCTION factory           #
# --------------------------------------------------------------------------- #
def test_production_wiring_passes_the_vault_health_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SAME structural shape as the renewal-loop guard above, for the same
    class of defect.

    ``create_app`` defaults ``vault_health=None`` and ``/metrics`` then omits
    the gauge entirely (deliberately — an unwired probe must not be readable
    as a dead credential). So a ``create_production_app`` that forgot to pass
    it would produce a metric that NEVER appears, an alert that can therefore
    never fire, and not one failing test anywhere else: exactly the
    silent-detection failure ن-10 exists to end (``docs/log/3.94.md``). It
    must also be the root's OWN probe, not some freshly built one, because the
    point is to answer for the credential the request path actually uses.
    """
    captured: dict[str, Any] = {}

    def _capture(_services: Any, **kwargs: Any) -> FastAPI:
        captured.update(kwargs)
        return FastAPI()

    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv(
        "PROVIDER_ROUTING", '{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}}}'
    )
    monkeypatch.setattr(main_module, "create_app", _capture)

    main_module.create_production_app()

    assert captured["vault_health"] is not None
    # The REAL adapter, not some stand-in: `VaultProbe` wraps the root's own
    # `SecretsProvider`, so this is also what pins "same client, same token,
    # same relogin closure as the request path".
    assert isinstance(captured["vault_health"], VaultProbe)
