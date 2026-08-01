"""ASGI tests for the Conversations router (6.1-ج-2/ج-3).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``, real
conversations use-cases, and a real ``AgentOrchestrator`` — only the store
(in-memory) and the authenticator are fakes. What they pin, against 03 §1/§2:

* the five CRUD/read routes, including the ``API-04`` envelope on both
  collections and the soft-delete semantics (204, idempotent, then invisible);
* ``POST /conversations/{id}/messages`` — the turn actually RUNS the thread's
  own agent and BOTH messages land in the thread (user then assistant), with
  the reply returned as JSON or streamed as SSE;
* the router-level bearer gate.

The in-test ``_EchoAgent`` declares no ``chat`` capability, so no LLM is ever
resolved — the whole conversation path is exercised without a provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import PROBLEM_MEDIA_TYPE, create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import ConversationsStack, build_conversations
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

_ECHO_METADATA = AgentMetadata(
    key="echo",
    name="Echo",
    version="1.0.0",
    description="Echoes its input.",
    capabilities=frozenset(),  # no "chat" ⇒ no LLM resolved
    required_permissions=frozenset({"agents:invoke"}),
)


class _EchoAgent(BaseAgent):
    metadata = _ECHO_METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        text = req.input.get("text")
        payload = text if isinstance(text, str) else ""
        yield AgentEvent(type="token", data={"delta": payload})
        yield AgentEvent(type="final", data={"text": f"echo: {payload}"})


class _FakeLLM:
    provider = "fake"


class _FakeResolver:
    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        return _FakeLLM(), ResolvedProvider(provider="fake", model="fake-model", api_key="k")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


# 6.4-ب: `owner`, not `member`. These suites exercise the ROUTER — its
# delegation, its statuses, its bodies — and several of the routes they
# cover require a management permission a member does not hold
# (`conversations:delete`, `files:delete`, `workspace:manage`,
# `usage:manage`, 05 §1.3). Role sensitivity itself is `test_api_rbac.py`'s
# subject, tested there over every operation and every role at once;
# duplicating it here would leave the same claim in two places to drift.
class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        raise AssertionError("not exercised")


def _make_app() -> tuple[FastAPI, ConversationsStack]:
    """The app plus the store behind it — tests assert on the wire AND on what
    was actually persisted, which is the whole point of 6.1-ج-3."""
    registry = InMemoryAgentRegistry()
    registry.register(_ECHO_METADATA, _EchoAgent)
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
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    return app, conversations


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, *, agent_key: str = "echo", title: str | None = "First") -> str:
    response = client.post(
        "/api/v1/conversations", headers=_auth(), json={"agent_key": agent_key, "title": title}
    )
    assert response.status_code == 201
    conversation_id: str = response.json()["id"]
    return conversation_id


# --------------------------------------------------------------------------- #
# POST /conversations                                                          #
# --------------------------------------------------------------------------- #
def test_create_conversation_returns_201_and_a_bare_resource() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/conversations", headers=_auth(), json={"agent_key": "ECHO", "title": "First"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["agent_key"] == "echo"  # normalised by the domain value object
    assert body["kind"] == "agent"
    assert body["title"] == "First"
    assert body["created_at"]
    # A single resource is NOT wrapped, and never leaks the optimistic lock.
    assert "data" not in body
    assert "version" not in body


def test_create_conversation_rejects_a_blank_agent_key() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post("/api/v1/conversations", headers=_auth(), json={"agent_key": ""})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# GET /conversations                                                           #
# --------------------------------------------------------------------------- #
def test_list_conversations_wraps_in_the_page_envelope() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    _create(client)
    _create(client, title="Second")

    response = client.get("/api/v1/conversations", headers=_auth(), params={"agent_key": "echo"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["meta"] == {"next_cursor": None, "limit": 20}


def test_list_conversations_requires_the_agent_key_query() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/api/v1/conversations", headers=_auth())
    assert response.status_code == 422


def test_list_conversations_pages_with_an_opaque_cursor() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    older_id = _create(client)
    newer_id = _create(client)

    # NEWEST FIRST (6.3-ب): the thread just opened is the first row, and the
    # cursor walks BACKWARDS through the workspace's history.
    first = client.get(
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo", "limit": 1}
    ).json()
    assert [row["id"] for row in first["data"]] == [newer_id]
    assert first["meta"]["next_cursor"] is not None

    second = client.get(
        "/api/v1/conversations",
        headers=_auth(),
        params={"agent_key": "echo", "limit": 1, "cursor": first["meta"]["next_cursor"]},
    ).json()
    assert [row["id"] for row in second["data"]] == [older_id]
    assert second["meta"]["next_cursor"] is None


def test_list_conversations_rejects_a_limit_above_the_contract_ceiling() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get(
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo", "limit": 101}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# GET / DELETE /conversations/{id}                                             #
# --------------------------------------------------------------------------- #
def test_get_conversation_returns_the_thread() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id


def test_get_unknown_conversation_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/api/v1/conversations/missing", headers=_auth())
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "common.not_found"


def test_delete_is_204_idempotent_and_hides_the_thread_afterwards() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    first = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    second = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())

    assert first.status_code == second.status_code == 204
    assert first.content == b""
    # Soft-deleted ⇒ absent from reads (both the single resource and the list).
    assert (
        client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth()).status_code == 404
    )
    listed = client.get(
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo"}
    ).json()
    assert listed["data"] == []


# --------------------------------------------------------------------------- #
# GET /conversations/{id}/messages                                             #
# --------------------------------------------------------------------------- #
def test_list_messages_of_a_fresh_thread_is_an_empty_page() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    body = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth()).json()
    assert body == {"data": [], "meta": {"next_cursor": None, "limit": 20}}


def test_list_messages_of_an_unknown_thread_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/api/v1/conversations/missing/messages", headers=_auth())
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# POST /conversations/{id}/messages — the turn                                 #
# --------------------------------------------------------------------------- #
def test_post_message_runs_the_agent_and_persists_both_turns() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hello"}},
    )

    assert response.status_code == 200
    reply = response.json()
    assert reply["role"] == "assistant"
    assert reply["content"] == {"text": "echo: hello", "attachments": []}
    assert reply["seq"] == 2  # the user's turn took seq 1

    # Both turns are really in the thread, in order.
    listed = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth()).json()
    assert [(m["role"], m["content"]) for m in listed["data"]] == [
        ("user", {"text": "hello", "attachments": []}),
        ("assistant", {"text": "echo: hello", "attachments": []}),
    ]
    assert len(stack.repository.messages[conversation_id]) == 2


def test_post_message_streams_sse_when_asked() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hi"}, "stream": True},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "event: token" in text
    assert "event: final" in text
    # 03 §3.1: the final frame carries the persisted message id + usage.
    assert '"message_id"' in text
    assert '"usage"' in text


def test_post_message_to_an_unknown_thread_is_404_before_anything_runs() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/conversations/missing/messages", headers=_auth(), json={"content": {"text": "x"}}
    )
    assert response.status_code == 404
    assert stack.repository.messages == {}


def test_post_message_to_a_deleted_thread_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "x"}},
    )
    assert response.status_code == 404


def test_post_message_runs_the_threads_own_agent_not_a_requested_one() -> None:
    # A thread threaded under an unregistered agent cannot run: the router takes
    # the key from the CONVERSATION, so a client cannot redirect the turn.
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="ghost")
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        # `agent_key` smuggled INSIDE the content the agent will read: content is
        # caller-controlled data, and no routing decision may come out of it.
        json={"content": {"text": "x", "agent_key": "echo"}, "agent_key": "echo"},
    )
    assert response.status_code == 404
    # `agent.unknown`, not `common.not_found` (6.2): the thread was found, the
    # agent it names was not — and the code now says which.
    assert response.json()["code"] == "agent.unknown"


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
def test_routes_require_a_bearer_token() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    assert client.get("/api/v1/conversations?agent_key=echo").status_code == 401
    assert client.post("/api/v1/conversations", json={"agent_key": "echo"}).status_code == 401
    assert client.get("/api/v1/conversations/x").status_code == 401
    assert client.delete("/api/v1/conversations/x").status_code == 401
    assert client.get("/api/v1/conversations/x/messages").status_code == 401
    assert client.post("/api/v1/conversations/x/messages", json={"content": {}}).status_code == 401
