"""ASGI tests for the PUBLIC OAuth callback (6.1-و-4-2).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``, wired
with the shared in-memory integrations stack. The route under test is the one
route of the whole ``/api/v1`` surface that answers **without** an
``Authorization`` header, so what these pin is mostly about what it does NOT
accept:

* it is genuinely reachable unauthenticated — and that is a property, not an
  accident, so it is asserted directly;
* **tenant identity comes only from the server-side ``state`` binding**: a
  query string that names a workspace, a user or a connection changes
  nothing (the alpha ``state == user_id`` CSRF fix);
* a forged state is refused *before any row is read*, and a replayed one is
  refused because the first use deleted it;
* the exchange rides the redirect URI that was STORED at handshake time, not
  one the caller supplies;
* the provider's tokens are encrypted on the way to storage and appear in no
  response body;
* a provider failure is a 502 that leaves an explicable ``error`` row behind.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.integrations.domain.value_objects import ConnectionStatus
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import (
    REDIRECT_URI,
    IntegrationsStack,
    RecordingConnector,
    build_integrations,
    seed_connection,
)
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_FILES_MEDIA = build_files_media()
_WORKSPACE_USAGE = build_workspace_usage()
_CREDENTIALS = build_credentials()
_KNOWLEDGE = build_knowledge()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_W2 = "018f0000-0000-7000-8000-0000000000w2"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"

_CALLBACK = "/api/v1/integrations/connections/oauth/callback"
_C2 = "018f0000-0000-7000-8000-0000000000c2"


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
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


def _make_app(*, connector: RecordingConnector | None = None) -> tuple[FastAPI, IntegrationsStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_integrations(connector=connector)
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
        integrations=stack.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
    )
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    return app, stack


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _begin(client: TestClient, stack: IntegrationsStack) -> tuple[str, str]:
    """Start a real handshake through the authenticated route and return
    ``(connection_id, state)`` — the state read out of the cache the way the
    provider would read it out of the authorize URL."""
    created = client.post(
        "/api/v1/integrations/connections",
        json={"connector_key": "github", "scopes": ["repo"]},
        headers=_auth(),
    )
    assert created.status_code == 201, created.text
    (key,) = stack.cache.values
    return created.json()["id"], key.rsplit(":", 1)[1]


# --------------------------------------------------------------------------- #
# it is public — the property, asserted                                        #
# --------------------------------------------------------------------------- #
def test_the_callback_answers_without_any_authorization_header() -> None:
    """The whole reason this route lives in its own module. A third-party
    redirect arrives in a browser with no bearer token; a 401 here would break
    every OAuth handshake. The refusal it *does* give is about the state — and
    since 6.2 it says so by name."""
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get(_CALLBACK, params={"code": "c", "state": "forged"})
    assert response.status_code == 422
    assert response.json()["code"] == "integrations.oauth_state_invalid"


def test_a_bearer_token_is_neither_required_nor_consulted() -> None:
    """Not merely optional: a *bad* token does not turn a valid callback into
    a 401, because no authenticator runs on this path at all."""
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        _connection_id, state = _begin(client, stack)
        response = client.get(
            _CALLBACK, params={"code": "c", "state": state}, headers=_auth("garbage")
        )
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# the happy path                                                               #
# --------------------------------------------------------------------------- #
def test_a_completed_handshake_returns_the_connected_row() -> None:
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        connection_id, state = _begin(client, stack)
        assert stack.repository.rows[connection_id].status is ConnectionStatus.PENDING
        response = client.get(_CALLBACK, params={"code": "the-code", "state": state})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == connection_id
    assert body["connector_key"] == "github"
    assert body["status"] == "connected"
    assert body["scopes"] == ["repo"]
    assert body["expires_at"] is not None
    assert stack.repository.rows[connection_id].status is ConnectionStatus.CONNECTED


def test_the_exchange_uses_the_redirect_uri_stored_at_handshake_time() -> None:
    """Not one the caller supplies. The provider requires the exchange's
    redirect to match the authorize step's; taking it from the query string
    would hand that choice to whoever holds the state."""
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        _connection_id, state = _begin(client, stack)
        client.get(
            _CALLBACK,
            params={"code": "the-code", "state": state, "redirect_uri": "https://evil.test/steal"},
        )
    assert stack.connector.exchanges == [("the-code", REDIRECT_URI)]


# --------------------------------------------------------------------------- #
# tenant identity comes from the state binding and nothing else                #
# --------------------------------------------------------------------------- #
def test_query_parameters_naming_a_tenant_are_ignored_entirely() -> None:
    """The alpha CSRF fix, pinned: ``state`` was that user's id there, so a
    forged callback naming any user completed a handshake on their behalf.
    Here the workspace, the user and the connection all come from the stored
    binding, and extra parameters are simply not read."""
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    other = seed_connection(connection_id=_C2, workspace_id=_W2, status=ConnectionStatus.PENDING)
    stack.repository.rows[_C2] = other

    with TestClient(app) as client:
        connection_id, state = _begin(client, stack)
        response = client.get(
            _CALLBACK,
            params={
                "code": "c",
                "state": state,
                "workspace_id": _W2,
                "connection_id": _C2,
                "user_id": "someone-else",
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == connection_id
    # The other tenant's pending row was never touched.
    assert stack.repository.rows[_C2].status is ConnectionStatus.PENDING


def test_a_forged_state_is_refused_before_any_row_is_read() -> None:
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        connection_id, _state = _begin(client, stack)
        response = client.get(_CALLBACK, params={"code": "c", "state": "not-a-real-state"})

    assert response.status_code == 422
    assert response.json()["code"] == "integrations.oauth_state_invalid"
    assert stack.repository.rows[connection_id].status is ConnectionStatus.PENDING
    assert stack.connector.exchanges == []


def test_a_state_is_single_use() -> None:
    """A replayed callback loses because the first use deleted the binding —
    the property that makes an intercepted redirect URL worthless."""
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        _connection_id, state = _begin(client, stack)
        first = client.get(_CALLBACK, params={"code": "c", "state": state})
        second = client.get(_CALLBACK, params={"code": "c", "state": state})

    assert first.status_code == 200
    assert second.status_code == 422
    assert stack.cache.values == {}
    assert len(stack.connector.exchanges) == 1


def test_both_parameters_are_required_and_non_empty() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        assert client.get(_CALLBACK).status_code == 422
        assert client.get(_CALLBACK, params={"code": "c"}).status_code == 422
        assert client.get(_CALLBACK, params={"state": "s"}).status_code == 422
        empty = client.get(_CALLBACK, params={"code": "c", "state": ""})
    assert empty.status_code == 422
    # A MISSING/blank parameter is a shape error (`common.validation_error`);
    # a well-shaped state that matches nothing is `integrations.
    # oauth_state_invalid` above. Two different problems, two codes — 6.2 kept
    # the distinction §3.66 flagged and made it mean something, instead of
    # having one 422 answer under two names for no reason a client can see.
    assert empty.json()["code"] == "common.validation_error"


# --------------------------------------------------------------------------- #
# the tokens                                                                   #
# --------------------------------------------------------------------------- #
def test_the_provider_tokens_are_encrypted_and_never_reach_the_wire() -> None:
    connector = RecordingConnector()
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        connection_id, state = _begin(client, stack)
        response = client.get(_CALLBACK, params={"code": "c", "state": state})

    # Everything the provider returned went through `encrypt` (INV-I1)...
    assert stack.secrets.encrypted == [b'{"access_token": "at", "refresh_token": "rt"}']
    stored = stack.repository.rows[connection_id].token_ref
    assert stored is not None
    assert stored.key_name == "tenant-secrets"
    # ...and none of it — neither plaintext nor ciphertext — is in the answer.
    assert "access_token" not in response.text
    assert "refresh_token" not in response.text
    assert "vault:" not in response.text
    assert set(response.json()) == {
        "id",
        "connector_key",
        "display_name",
        "status",
        "scopes",
        "expires_at",
        "created_at",
    }


# --------------------------------------------------------------------------- #
# failure paths                                                                #
# --------------------------------------------------------------------------- #
def test_a_provider_failure_is_a_502_that_leaves_an_explicable_row() -> None:
    connector = RecordingConnector(exchange_error="invalid_grant")
    app, stack = _make_app(connector=connector)
    with TestClient(app) as client:
        connection_id, state = _begin(client, stack)
        response = client.get(_CALLBACK, params={"code": "stale", "state": state})

    assert response.status_code == 502
    assert response.json()["code"] == "integrations.oauth_failed"
    assert "invalid_grant" not in response.text  # the provider's words stay in the log
    row = stack.repository.rows[connection_id]
    assert row.status is ConnectionStatus.ERROR
    assert row.last_error is not None
    assert row.token_ref is None


def test_a_connector_missing_from_this_deployment_is_named_as_such() -> None:
    """A state minted while an adapter existed, consumed after it was removed
    (or, today, a state for a connector this deployment never built). The
    callback re-checks the map rather than trusting the binding."""
    app, stack = _make_app()
    stack.repository.rows[_C2] = seed_connection(
        connection_id=_C2, workspace_id=_W1, status=ConnectionStatus.PENDING
    )
    stack.cache.values["integrations:oauth:state:s1"] = json.dumps(
        {
            "workspace_id": _W1,
            "connection_id": _C2,
            "connector_key": "github",
            "created_by": _U1,
            "redirect_uri": REDIRECT_URI,
        }
    ).encode()

    with TestClient(app) as client:
        response = client.get(_CALLBACK, params={"code": "c", "state": "s1"})

    assert response.status_code == 422
    assert response.json()["code"] == "integrations.connector_unknown"
    assert stack.repository.rows[_C2].status is ConnectionStatus.PENDING
