"""ASGI tests for the Integrations router — connectors + connections
(6.1-و-4-1).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory integrations stack (``support_integrations``). What
these pin, against 03 §1/§2 and 06 §9:

* **no token material crosses the wire, ever** — no response body on any of
  the five routes contains a ciphertext, and ``ConnectionOut`` has neither a
  ``token_ref`` nor a ``last_error`` field;
* **the redirect URI comes from configuration, not from the request** — the
  connector records what it was handed, and a body that tries to supply its
  own redirect is simply not part of the contract;
* an unconfigured deployment (no connector adapters — production today)
  answers ``GET /connectors`` with an empty collection and refuses every
  ``POST /connections`` with ``integrations.connector_unknown``/422;
* the listing shows EVERY status, newest first, this tenant only — a
  ``pending`` row is the answer to "did my handshake start?";
* 404 for an unknown connection AND for another tenant's — indistinguishable
  on purpose;
* ``DELETE`` revokes rather than erases, drops the stored token, and is
  idempotent.
"""

from __future__ import annotations

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
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.integrations.application.use_cases import ConnectorDescriptor
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

_C1 = "018f0000-0000-7000-8000-0000000000c1"
_C2 = "018f0000-0000-7000-8000-0000000000c2"
_C3 = "018f0000-0000-7000-8000-0000000000c3"


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


def _make_app(
    *,
    catalog: Sequence[ConnectorDescriptor] = (),
    connector: RecordingConnector | None = None,
) -> tuple[FastAPI, IntegrationsStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_integrations(catalog=catalog, connector=connector)
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


# --------------------------------------------------------------------------- #
# auth                                                                         #
# --------------------------------------------------------------------------- #
def test_every_route_refuses_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        base = "/api/v1/integrations"
        assert client.get(f"{base}/connectors").status_code == 401
        assert client.get(f"{base}/connections").status_code == 401
        assert (
            client.post(f"{base}/connections", json={"connector_key": "github"}).status_code == 401
        )
        assert client.post(f"{base}/connections/{_C1}/authorize").status_code == 401
        assert client.delete(f"{base}/connections/{_C1}").status_code == 401


# --------------------------------------------------------------------------- #
# GET /connectors — the catalog, empty being a legitimate answer                #
# --------------------------------------------------------------------------- #
def test_connector_catalog_is_empty_while_no_connector_adapter_exists() -> None:
    """Production's shape today. An empty collection is the truth, and it is
    what makes `connector_unknown` on POST an explicable refusal rather than
    a mystery."""
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connectors", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_connector_catalog_shape_is_exactly_the_spec() -> None:
    catalog = (ConnectorDescriptor(key="github", name="GitHub", scopes=("repo", "read:user")),)
    app, _stack = _make_app(catalog=catalog)
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connectors", headers=_auth())
    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "key": "github",
            "name": "GitHub",
            "scopes": ["repo", "read:user"],
            "auth_type": "oauth2",
        }
    ]


# --------------------------------------------------------------------------- #
# GET /connections                                                             #
# --------------------------------------------------------------------------- #
def test_listing_is_an_empty_envelope_when_the_workspace_has_no_connections() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connections", headers=_auth())
    assert response.status_code == 200
    # `meta.limit` is the page size asked for — this listing paginates
    # (6.3-ب), so an empty workspace still reports the contract's default.
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 20}}


def test_connections_page_newest_first_and_refuse_a_malformed_cursor() -> None:
    """``openapi.yaml`` declared ``limit``/``cursor`` on ``listConnections``
    from the start; only the implementation was unpaginated (6.3-ب).

    The ceiling this listing was documented to have does not exist:
    ``Limits.max_connectors`` counts CONNECTED rows only, so every abandoned
    handshake and failed exchange accumulates here under no cap at all.
    """
    app, stack = _make_app()
    ids = [new_uuid7() for _ in range(3)]
    for connection_id in ids:
        stack.repository.rows[connection_id] = seed_connection(
            connection_id=connection_id, workspace_id=_W1
        )

    with TestClient(app) as client:
        first = client.get("/api/v1/integrations/connections?limit=2", headers=_auth()).json()
        cursor = first["meta"]["next_cursor"]
        second = client.get(
            f"/api/v1/integrations/connections?limit=2&cursor={cursor}", headers=_auth()
        ).json()
        malformed = client.get("/api/v1/integrations/connections?cursor=!!!!", headers=_auth())

    assert [row["id"] for row in first["data"]] == [ids[2], ids[1]]
    assert [row["id"] for row in second["data"]] == [ids[0]]
    assert second["meta"]["next_cursor"] is None
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "common.invalid_cursor"


def test_listing_shape_is_exactly_the_spec_and_carries_no_token() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connections", headers=_auth())
    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": _C1,
            "connector_key": "github",
            "display_name": "GitHub",
            "status": "connected",
            "scopes": ["repo"],
            "expires_at": "2026-05-06T08:08:09Z",
            "created_at": "2026-05-06T07:08:09Z",
        }
    ]


def test_no_ciphertext_or_failure_reason_ever_reaches_the_client() -> None:
    """INV-I1 at the wire: the seeded row holds a `CipherRef` and an error
    string, and `ConnectionOut` has a field for neither."""
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    stack.repository.rows[_C2] = seed_connection(
        connection_id=_C2,
        workspace_id=_W1,
        connector_key="slack",
        status=ConnectionStatus.ERROR,
        last_error="invalid_grant: the authorization code has expired",
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connections", headers=_auth())
    assert "vault:" not in response.text
    assert "invalid_grant" not in response.text
    for row in response.json()["data"]:
        assert "token_ref" not in row
        assert "last_error" not in row


def test_listing_shows_every_lifecycle_status() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(
        connection_id=_C1,
        workspace_id=_W1,
        connector_key="github",
        status=ConnectionStatus.PENDING,
    )
    stack.repository.rows[_C2] = seed_connection(
        connection_id=_C2,
        workspace_id=_W1,
        connector_key="slack",
        status=ConnectionStatus.REVOKED,
    )
    stack.repository.rows[_C3] = seed_connection(
        connection_id=_C3,
        workspace_id=_W1,
        connector_key="gmail",
        status=ConnectionStatus.ERROR,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connections", headers=_auth())
    assert {row["status"] for row in response.json()["data"]} == {"pending", "revoked", "error"}


def test_listing_is_newest_first_and_excludes_other_tenants() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    stack.repository.rows[_C2] = seed_connection(
        connection_id=_C2, workspace_id=_W1, connector_key="slack"
    )
    stack.repository.rows[_C3] = seed_connection(connection_id=_C3, workspace_id=_W2)
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/connections", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == [_C2, _C1]


# --------------------------------------------------------------------------- #
# POST /connections                                                            #
# --------------------------------------------------------------------------- #
def test_creating_a_connection_is_refused_while_no_connector_is_configured() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/connections", json={"connector_key": "github"}, headers=_auth()
        )
    assert response.status_code == 422
    assert response.json()["code"] == "integrations.connector_unknown"
    assert stack.repository.rows == {}


def test_creating_a_connection_returns_201_and_a_pending_row() -> None:
    app, stack = _make_app(connector=RecordingConnector())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/connections",
            json={"connector_key": "github", "scopes": ["repo"]},
            headers=_auth(),
        )
    assert response.status_code == 201
    body = response.json()
    assert body["connector_key"] == "github"
    assert body["status"] == "pending"
    assert body["scopes"] == ["repo"]
    assert body["expires_at"] is None
    # The row exists, in this workspace, and the response is the bare
    # resource — no envelope on a single resource (API-04).
    assert "data" not in body
    assert stack.repository.rows[body["id"]].workspace_id == _W1


def test_the_redirect_uri_comes_from_configuration_not_from_the_request() -> None:
    """The open-redirect property: whatever a client sends, the connector is
    handed the platform's own callback URL."""
    app, stack = _make_app(connector=RecordingConnector())
    with TestClient(app) as client:
        client.post(
            "/api/v1/integrations/connections",
            json={
                "connector_key": "github",
                "redirect_uri": "https://evil.test/steal",
            },
            headers=_auth(),
        )
    assert [call[0] for call in stack.connector.calls] == [REDIRECT_URI]


def test_an_empty_connector_key_is_a_shape_refusal_not_a_catalog_miss() -> None:
    """422 either way, but the *code* differs and that is the point: an empty
    string is a malformed request (``common.validation_error``), not a key
    the catalog happens not to have."""
    app, stack = _make_app(connector=RecordingConnector())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/connections", json={"connector_key": ""}, headers=_auth()
        )
    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"
    assert stack.connector.calls == []
    assert stack.repository.rows == {}


# --------------------------------------------------------------------------- #
# POST /connections/{id}/authorize                                             #
# --------------------------------------------------------------------------- #
def test_authorize_returns_the_url_and_a_state_bound_server_side() -> None:
    app, stack = _make_app(connector=RecordingConnector())
    stack.repository.rows[_C1] = seed_connection(
        connection_id=_C1, workspace_id=_W1, status=ConnectionStatus.PENDING
    )
    with TestClient(app) as client:
        response = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://github.test/authorize?state=")
    # The nonce the client is handed is exactly the one the server stored.
    assert f"integrations:oauth:state:{body['state']}" in stack.cache.values


def test_authorize_mints_a_fresh_state_on_every_call() -> None:
    app, stack = _make_app(connector=RecordingConnector())
    stack.repository.rows[_C1] = seed_connection(
        connection_id=_C1, workspace_id=_W1, status=ConnectionStatus.PENDING
    )
    with TestClient(app) as client:
        first = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
        second = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
    assert first.json()["state"] != second.json()["state"]


def test_authorizing_a_live_connection_does_not_knock_it_back_to_pending() -> None:
    app, stack = _make_app(connector=RecordingConnector())
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
    assert response.status_code == 200
    assert stack.repository.rows[_C1].status is ConnectionStatus.CONNECTED


def test_authorizing_an_unknown_connection_is_404() -> None:
    app, _stack = _make_app(connector=RecordingConnector())
    with TestClient(app) as client:
        response = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


def test_authorizing_another_tenants_connection_is_404_not_403() -> None:
    """403 would confirm the id exists (the §3.55 read precedent)."""
    app, stack = _make_app(connector=RecordingConnector())
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W2)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/integrations/connections/{_C1}/authorize", headers=_auth())
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# DELETE /connections/{id}                                                     #
# --------------------------------------------------------------------------- #
def test_delete_revokes_the_row_and_drops_the_stored_token() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    with TestClient(app) as client:
        response = client.delete(f"/api/v1/integrations/connections/{_C1}", headers=_auth())
    assert response.status_code == 204
    row = stack.repository.rows[_C1]
    assert row.status is ConnectionStatus.REVOKED
    assert row.token_ref is None
    assert row.expires_at is None


def test_delete_is_idempotent_and_the_row_stays_listable() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W1)
    with TestClient(app) as client:
        assert (
            client.delete(f"/api/v1/integrations/connections/{_C1}", headers=_auth()).status_code
            == 204
        )
        assert (
            client.delete(f"/api/v1/integrations/connections/{_C1}", headers=_auth()).status_code
            == 204
        )
        listing = client.get("/api/v1/integrations/connections", headers=_auth())
    assert [row["status"] for row in listing.json()["data"]] == ["revoked"]


def test_deleting_an_unknown_connection_is_404() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.delete(f"/api/v1/integrations/connections/{_C1}", headers=_auth())
    assert response.status_code == 404


def test_deleting_another_tenants_connection_is_404() -> None:
    app, stack = _make_app()
    stack.repository.rows[_C1] = seed_connection(connection_id=_C1, workspace_id=_W2)
    with TestClient(app) as client:
        response = client.delete(f"/api/v1/integrations/connections/{_C1}", headers=_auth())
    assert response.status_code == 404
    assert stack.repository.rows[_C1].status is ConnectionStatus.CONNECTED
