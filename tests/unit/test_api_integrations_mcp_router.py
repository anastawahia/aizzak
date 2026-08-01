"""ASGI tests for the Integrations router — MCP servers + the tool catalog
(6.1-و-4-3).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory integrations stack (``support_integrations``). What
these pin, against 03 §1/§2 and 06 §9:

* **``auth`` goes in and never comes out** — the plaintext reaches Transit
  (the recording provider's log proves it) and no response field on any of
  these routes can carry it back;
* **``DELETE`` disables rather than erases, and the disabling is
  reversible** — the row stays listed as ``disabled``, and re-registering
  its name revives *that* row, id and all. That last assertion is the whole
  reason ``McpServer`` grew a lifecycle: without the revive, a delete would
  burn the workspace-unique name permanently;
* the listing shows every status, newest first, this tenant only;
* INV-I2 at the wire: a ``stdio`` transport is refused as
  ``integrations.mcp_transport_unsupported``/422;
* **``GET /tools`` can name a tool and cannot run one** — the route set this
  router exposes contains no invocation path at all, and the field it reads
  is typed to the reading half of the port;
* ``GET /tools`` answers 503 ``integrations.tools_unavailable`` while no
  connector/MCP adapter exists — *not* an empty list, which would be a lie
  rather than the true empty answer ``GET /connectors`` gives.
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
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.settings.settings import Limits
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.integrations.application.use_cases import ConnectorDescriptor
from app.modules.integrations.domain.value_objects import CipherRef, McpStatus
from app.modules.integrations.ports.inbound import DiscoveredTool
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import (
    IntegrationsStack,
    StubToolDiscovery,
    build_integrations,
    seed_mcp_server,
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

_S1 = "018f0000-0000-7000-8000-0000000000s1"
_S2 = "018f0000-0000-7000-8000-0000000000s2"
_S3 = "018f0000-0000-7000-8000-0000000000s3"

_BASE = "/api/v1/integrations"


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
    tools: StubToolDiscovery | None = None,
    limits: Limits | None = None,
) -> tuple[FastAPI, IntegrationsStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_integrations(catalog=catalog, tools=tools, limits=limits)
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
def test_every_mcp_route_refuses_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        assert client.get(f"{_BASE}/mcp-servers").status_code == 401
        assert client.post(f"{_BASE}/mcp-servers", json={}).status_code == 401
        assert client.delete(f"{_BASE}/mcp-servers/{_S1}").status_code == 401
        assert client.get(f"{_BASE}/tools").status_code == 401


# --------------------------------------------------------------------------- #
# GET /mcp-servers                                                             #
# --------------------------------------------------------------------------- #
def test_listing_shows_every_status_newest_first_this_tenant_only() -> None:
    """`disabled` is the status DELETE produces, so hiding it would make a
    deleted server invisible while its name stays reserved."""
    app, stack = _make_app()
    for row in (
        seed_mcp_server(server_id=_S1, workspace_id=_W1, name="alpha"),
        seed_mcp_server(server_id=_S2, workspace_id=_W1, name="beta", status=McpStatus.DISABLED),
        seed_mcp_server(server_id=_S3, workspace_id=_W2, name="other"),
    ):
        stack.servers.rows[row.id] = row
    with TestClient(app) as client:
        response = client.get(f"{_BASE}/mcp-servers", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body["data"]] == ["beta", "alpha"]
    assert [row["status"] for row in body["data"]] == ["disabled", "active"]
    assert body["meta"] == {"next_cursor": None, "limit": 2}


def test_listed_server_carries_no_auth_material() -> None:
    app, stack = _make_app()
    stack.servers.rows[_S1] = seed_mcp_server(
        server_id=_S1,
        workspace_id=_W1,
        auth_ref=CipherRef("vault:v1:c2VjcmV0", "tenant-secrets"),
    )
    with TestClient(app) as client:
        response = client.get(f"{_BASE}/mcp-servers", headers=_auth())
    assert response.status_code == 200
    assert set(response.json()["data"][0]) == {
        "id",
        "name",
        "endpoint_url",
        "transport",
        "status",
        "created_at",
    }
    assert "vault:v1" not in response.text


# --------------------------------------------------------------------------- #
# POST /mcp-servers                                                            #
# --------------------------------------------------------------------------- #
def test_registering_a_server_returns_201_and_the_row() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={
                "name": "notes",
                "endpoint_url": "https://mcp.example.test/sse",
                "transport": "sse",
            },
            headers=_auth(),
        )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "notes"
    assert body["endpoint_url"] == "https://mcp.example.test/sse"
    assert body["transport"] == "sse"
    assert body["status"] == "active"
    assert stack.servers.rows[body["id"]].workspace_id == _W1


def test_auth_material_is_encrypted_and_never_returned() -> None:
    """INV-I1 at the wire: the plaintext reaches Transit and nothing else."""
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={
                "name": "notes",
                "endpoint_url": "https://mcp.example.test/sse",
                "auth": {"header": "Bearer hunter2"},
            },
            headers=_auth(),
        )
    assert response.status_code == 201
    assert stack.secrets.encrypted == [b'{"header": "Bearer hunter2"}']
    assert "hunter2" not in response.text
    assert "auth" not in response.json()
    stored = stack.servers.rows[response.json()["id"]]
    assert stored.auth_ref is not None and stored.auth_ref.key_name == "tenant-secrets"


def test_stdio_transport_is_refused_by_name() -> None:
    """INV-I2 — remote transports only in v1; stdio belongs to `sandbox`."""
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "local", "endpoint_url": "https://x.test", "transport": "stdio"},
            headers=_auth(),
        )
    assert response.status_code == 422
    assert response.json()["code"] == "integrations.mcp_transport_unsupported"


def test_non_remote_url_is_refused() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "local", "endpoint_url": "stdio://local"},
            headers=_auth(),
        )
    assert response.status_code == 422


def test_a_name_already_held_by_an_active_server_conflicts() -> None:
    app, stack = _make_app()
    stack.servers.rows[_S1] = seed_mcp_server(server_id=_S1, workspace_id=_W1, name="notes")
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "notes", "endpoint_url": "https://other.test/sse"},
            headers=_auth(),
        )
    assert response.status_code == 409


def test_a_name_shadowing_a_connector_key_is_refused() -> None:
    """Both become the `<prefix>` of a tool handle; a collision would make
    `<prefix>.<tool>` routing ambiguous."""
    app, _stack = _make_app(catalog=(ConnectorDescriptor(key="github", name="GitHub", scopes=()),))
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "github", "endpoint_url": "https://x.test/sse"},
            headers=_auth(),
        )
    assert response.status_code == 422


def test_the_workspace_cap_is_enforced() -> None:
    app, stack = _make_app(limits=Limits(max_mcp_servers=1))
    stack.servers.rows[_S1] = seed_mcp_server(server_id=_S1, workspace_id=_W1, name="alpha")
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "beta", "endpoint_url": "https://x.test/sse"},
            headers=_auth(),
        )
    assert response.status_code == 409
    assert response.json()["code"] == "integrations.too_many"


# --------------------------------------------------------------------------- #
# DELETE /mcp-servers/{id} — disable, and the revive that makes it reversible   #
# --------------------------------------------------------------------------- #
def test_delete_disables_and_keeps_the_row_listed() -> None:
    app, stack = _make_app()
    stack.servers.rows[_S1] = seed_mcp_server(
        server_id=_S1,
        workspace_id=_W1,
        auth_ref=CipherRef("vault:v1:c2VjcmV0", "tenant-secrets"),
    )
    with TestClient(app) as client:
        assert client.delete(f"{_BASE}/mcp-servers/{_S1}", headers=_auth()).status_code == 204
        listing = client.get(f"{_BASE}/mcp-servers", headers=_auth()).json()
    assert [row["status"] for row in listing["data"]] == ["disabled"]
    # The auth material survives — it is what makes re-registration a revive
    # rather than a re-entry of every setting (`McpServer.disable`).
    assert stack.servers.rows[_S1].auth_ref is not None


def test_delete_is_idempotent() -> None:
    app, stack = _make_app()
    stack.servers.rows[_S1] = seed_mcp_server(
        server_id=_S1, workspace_id=_W1, status=McpStatus.DISABLED
    )
    with TestClient(app) as client:
        assert client.delete(f"{_BASE}/mcp-servers/{_S1}", headers=_auth()).status_code == 204
        assert client.delete(f"{_BASE}/mcp-servers/{_S1}", headers=_auth()).status_code == 204
    assert stack.servers.rows[_S1].status is McpStatus.DISABLED


def test_deleting_an_unknown_or_another_tenants_server_is_404() -> None:
    """Indistinguishable on purpose — a 403 would confirm the id exists."""
    app, stack = _make_app()
    stack.servers.rows[_S3] = seed_mcp_server(server_id=_S3, workspace_id=_W2)
    with TestClient(app) as client:
        assert client.delete(f"{_BASE}/mcp-servers/{_S1}", headers=_auth()).status_code == 404
        assert client.delete(f"{_BASE}/mcp-servers/{_S3}", headers=_auth()).status_code == 404
    assert stack.servers.rows[_S3].status is McpStatus.ACTIVE


def test_re_registering_a_disabled_name_revives_the_same_row() -> None:
    """The point of the whole lifecycle pair: without this, DELETE would burn
    the workspace-unique name forever, since 03 defines no re-enable route."""
    app, stack = _make_app()
    with TestClient(app) as client:
        created = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "notes", "endpoint_url": "https://first.test/sse"},
            headers=_auth(),
        ).json()
        assert (
            client.delete(f"{_BASE}/mcp-servers/{created['id']}", headers=_auth()).status_code
            == 204
        )
        again = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "notes", "endpoint_url": "https://second.test/sse"},
            headers=_auth(),
        )
        listing = client.get(f"{_BASE}/mcp-servers", headers=_auth()).json()
    assert again.status_code == 201
    assert again.json()["id"] == created["id"]  # same server, same identity
    assert again.json()["status"] == "active"
    assert again.json()["endpoint_url"] == "https://second.test/sse"
    assert len(listing["data"]) == 1  # revived, not duplicated
    assert len(stack.servers.rows) == 1


def test_the_cap_does_not_block_reviving_a_disabled_server() -> None:
    """A disabled row already holds its name; reviving it adds no capacity."""
    app, stack = _make_app(limits=Limits(max_mcp_servers=1))
    stack.servers.rows[_S1] = seed_mcp_server(
        server_id=_S1, workspace_id=_W1, name="notes", status=McpStatus.DISABLED
    )
    with TestClient(app) as client:
        response = client.post(
            f"{_BASE}/mcp-servers",
            json={"name": "notes", "endpoint_url": "https://again.test/sse"},
            headers=_auth(),
        )
    assert response.status_code == 201
    assert response.json()["id"] == _S1


# --------------------------------------------------------------------------- #
# GET /tools                                                                   #
# --------------------------------------------------------------------------- #
def test_tools_is_503_while_no_connector_or_mcp_adapter_exists() -> None:
    """Production's shape today — and *not* an empty list: a workspace with a
    live MCP server does have tools this deployment cannot go and discover."""
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get(f"{_BASE}/tools", headers=_auth())
    assert response.status_code == 503
    assert response.json()["code"] == "integrations.tools_unavailable"


def test_tools_renders_the_discovered_catalog() -> None:
    discovery = StubToolDiscovery(
        tools=[
            DiscoveredTool(
                name="github.create_issue",
                description="Open an issue",
                parameters={"type": "object"},
                source="connector:018f0000-0000-7000-8000-0000000000c1",
            ),
            DiscoveredTool(
                name="notes.search",
                description="Search notes",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                source="mcp:notes",
            ),
        ]
    )
    app, _stack = _make_app(tools=discovery)
    with TestClient(app) as client:
        response = client.get(f"{_BASE}/tools", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [
        {
            "name": "github.create_issue",
            "description": "Open an issue",
            "parameters": {"type": "object"},
            "source": "connector:018f0000-0000-7000-8000-0000000000c1",
        },
        {
            "name": "notes.search",
            "description": "Search notes",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            "source": "mcp:notes",
        },
    ]
    assert body["meta"] == {"next_cursor": None, "limit": 2}
    assert discovery.calls == 1


def test_the_parameters_schema_is_passed_through_untouched() -> None:
    """Rewriting a third party's schema here is how a generated call ends up
    not matching what the tool actually accepts."""
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string", "minLength": 1}},
        "required": ["q"],
        "additionalProperties": False,
    }
    discovery = StubToolDiscovery(
        tools=[
            DiscoveredTool(
                name="notes.search", description="", parameters=schema, source="mcp:notes"
            )
        ]
    )
    app, _stack = _make_app(tools=discovery)
    with TestClient(app) as client:
        response = client.get(f"{_BASE}/tools", headers=_auth())
    assert response.json()["data"][0]["parameters"] == schema


def test_the_router_exposes_no_way_to_invoke_a_tool() -> None:
    """INV-I4 as a route-set assertion: the API can NAME a tool and cannot
    run one. `invoke_tool` is the agent runtime's through the `ToolCatalog`
    inbound port, and the bundle field this router reads is typed to the
    reading half only (`ToolDiscovery`)."""
    app, _stack = _make_app()
    paths = {path for path in app.openapi()["paths"] if path.startswith(_BASE)}
    assert paths == {
        f"{_BASE}/connectors",
        f"{_BASE}/connections",
        f"{_BASE}/connections/{{connection_id}}",
        f"{_BASE}/connections/{{connection_id}}/authorize",
        f"{_BASE}/connections/oauth/callback",
        f"{_BASE}/mcp-servers",
        f"{_BASE}/mcp-servers/{{server_id}}",
        f"{_BASE}/tools",
    }
