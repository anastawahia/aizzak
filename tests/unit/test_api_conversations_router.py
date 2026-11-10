"""ASGI tests for the Conversations router (6.1-ج-2/ج-3).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``, real
conversations use-cases, and a real ``AgentOrchestrator`` — only the store
(in-memory) and the authenticator are fakes. What they pin, against 03 §1/§2:

* the six CRUD/read routes, including the ``API-04`` envelope on both
  collections, the soft-delete semantics (204, idempotent, then invisible),
  and the rename's present-but-null contract for clearing a title;
* ``POST /conversations/{id}/messages`` — the turn actually RUNS the thread's
  own agent and BOTH messages land in the thread (user then assistant), with
  the reply returned as JSON or streamed as SSE;
* ``DELETE /conversations/{id}/messages/{message_id}`` — the turn leaves the
  transcript while its row and its ``seq`` stay (INV-CV3), the path itself
  refuses a message quoted against another thread, and a repeat is another
  204;
* ``GET/POST /conversations/{id}/files`` + ``DELETE …/files/{file_id}`` — the
  thread's retrieval SCOPE: pinning is idempotent and refuses an unreadable
  file, un-pinning destroys nothing, a pin never crosses threads, and what the
  API pins is what the orchestrator's inbound port reads back;
* the router-level bearer gate.

The in-test ``_EchoAgent`` declares no ``chat`` capability, so no LLM is ever
resolved — the whole conversation path is exercised without a provider.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import ClassVar

import pytest
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
from tests.unit.support_conversations import (
    ConversationsStack,
    StubActiveSpaces,
    build_conversations,
)
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
# Spaces plan step 12: the space every thread here is opened in, and the only
# one the seam below treats as live.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
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


_CITING_METADATA = AgentMetadata(
    key="citing",
    name="Citing",
    version="1.0.0",
    description="Answers and lists its sources (th-1).",
    capabilities=frozenset(),
    required_permissions=frozenset({"agents:invoke"}),
)

_DOC = "0190aaaa-0000-7000-8000-000000000001"
_CHUNK_1 = "0190bbbb-0000-7000-8000-000000000001"
_CHUNK_2 = "0190bbbb-0000-7000-8000-000000000002"
_ARABIC_NAME = "دليل الصيانة ٢٠٢٦.pdf"
_TWO_CITATIONS: list[dict[str, object]] = [
    {"document_id": _DOC, "file_name": _ARABIC_NAME, "page": 12, "chunk_id": _CHUNK_1, "rank": 1},
    {"document_id": _DOC, "file_name": None, "page": None, "chunk_id": _CHUNK_2, "rank": 2},
]


class _CitingAgent(BaseAgent):
    """A RAG-shaped agent: its `final` carries ``payload`` beside the text."""

    metadata = _CITING_METADATA
    # `None` = leave the key off entirely.
    citations: ClassVar[list[dict[str, object]] | None] = None

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "the answer"})
        data: dict[str, object] = {"text": "the answer"}
        if type(self).citations is not None:
            data["citations"] = type(self).citations
        yield AgentEvent(type="final", data=data)


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
    registry.register(_CITING_METADATA, _CitingAgent)
    conversations = build_conversations(spaces=StubActiveSpaces(live={_SPACE}))
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
        "/api/v1/conversations",
        headers=_auth(),
        json={"space_id": _SPACE, "agent_key": agent_key, "title": title},
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
        "/api/v1/conversations",
        headers=_auth(),
        json={"space_id": _SPACE, "agent_key": "ECHO", "title": "First"},
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


def test_create_conversation_with_the_same_idempotency_key_replays_the_first_thread() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    headers = {**_auth(), "Idempotency-Key": "create-1"}
    body = {"space_id": _SPACE, "agent_key": "echo", "title": "First"}

    first = client.post("/api/v1/conversations", headers=headers, json=body)
    second = client.post("/api/v1/conversations", headers=headers, json=body)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    listed = client.get(
        "/api/v1/conversations",
        headers=_auth(),
        params={"agent_key": "echo", "space_id": _SPACE},
    ).json()["data"]
    assert [c["id"] for c in listed] == [first.json()["id"]]


def test_create_conversation_reusing_a_key_with_another_body_is_409() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    headers = {**_auth(), "Idempotency-Key": "create-1"}
    client.post(
        "/api/v1/conversations", headers=headers, json={"space_id": _SPACE, "agent_key": "echo"}
    )
    response = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"space_id": _SPACE, "agent_key": "echo", "title": "Other"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"


def test_create_conversation_rejects_a_blank_agent_key() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/conversations", headers=_auth(), json={"space_id": _SPACE, "agent_key": ""}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# GET /conversations                                                           #
# --------------------------------------------------------------------------- #
def test_list_conversations_wraps_in_the_page_envelope() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    _create(client)
    _create(client, title="Second")

    response = client.get(
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo", "space_id": _SPACE}
    )

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
        "/api/v1/conversations",
        headers=_auth(),
        params={"agent_key": "echo", "space_id": _SPACE, "limit": 1},
    ).json()
    assert [row["id"] for row in first["data"]] == [newer_id]
    assert first["meta"]["next_cursor"] is not None

    second = client.get(
        "/api/v1/conversations",
        headers=_auth(),
        params={
            "agent_key": "echo",
            "space_id": _SPACE,
            "limit": 1,
            "cursor": first["meta"]["next_cursor"],
        },
    ).json()
    assert [row["id"] for row in second["data"]] == [older_id]
    assert second["meta"]["next_cursor"] is None


def test_list_conversations_rejects_a_limit_above_the_contract_ceiling() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get(
        "/api/v1/conversations",
        headers=_auth(),
        params={"agent_key": "echo", "space_id": _SPACE, "limit": 101},
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
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo", "space_id": _SPACE}
    ).json()
    assert listed["data"] == []


# --------------------------------------------------------------------------- #
# PATCH /conversations/{id}                                                    #
# --------------------------------------------------------------------------- #
def test_rename_returns_the_renamed_thread_and_the_list_agrees() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, title="First")

    response = client.patch(
        f"/api/v1/conversations/{conversation_id}", headers=_auth(), json={"title": "Renamed"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed"
    assert body["id"] == conversation_id
    # Bare resource, same as every other single-conversation route.
    assert "data" not in body
    assert "version" not in body
    # Persisted, not merely echoed.
    listed = client.get(
        "/api/v1/conversations", headers=_auth(), params={"agent_key": "echo", "space_id": _SPACE}
    ).json()
    assert [row["title"] for row in listed["data"]] == ["Renamed"]


def test_rename_to_null_clears_the_title() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, title="First")
    response = client.patch(
        f"/api/v1/conversations/{conversation_id}", headers=_auth(), json={"title": None}
    )
    assert response.status_code == 200
    assert response.json()["title"] is None


def test_rename_without_a_title_field_is_422() -> None:
    """`title` is required-to-be-present: an empty body must not be read as
    "clear it". Present-and-null is the only way to clear."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.patch(f"/api/v1/conversations/{conversation_id}", headers=_auth(), json={})
    assert response.status_code == 422


def test_rename_never_repoints_the_thread_at_another_agent() -> None:
    """`agent_key` is not in the patch DTO, so naming one changes nothing —
    a thread is threaded per (workspace, agent) by 06 §4."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        headers=_auth(),
        json={"title": "Renamed", "agent_key": "other"},
    )
    assert response.status_code == 200
    assert response.json()["agent_key"] == "echo"


def test_rename_of_an_unknown_conversation_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.patch(
        "/api/v1/conversations/missing", headers=_auth(), json={"title": "Renamed"}
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "common.not_found"


def test_rename_of_a_soft_deleted_conversation_is_a_conflict() -> None:
    """A WRITE against a deleted thread is refused (409), not disguised as a
    missing resource — the asymmetry `ListMessages` documents."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    deleted = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert deleted.status_code == 204

    response = client.patch(
        f"/api/v1/conversations/{conversation_id}", headers=_auth(), json={"title": "Renamed"}
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


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
    assert (
        client.post(
            "/api/v1/conversations", json={"space_id": _SPACE, "agent_key": "echo"}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/conversations/x").status_code == 401
    assert client.delete("/api/v1/conversations/x").status_code == 401
    assert client.get("/api/v1/conversations/x/messages").status_code == 401
    assert client.post("/api/v1/conversations/x/messages", json={"content": {}}).status_code == 401
    assert client.delete("/api/v1/conversations/x/messages/y").status_code == 401


# --------------------------------------------------------------------------- #
# PUT /conversations/{id}/model (BE-RAG-003)                                  #
# --------------------------------------------------------------------------- #
def test_pinning_a_configured_route_returns_the_thread_carrying_it() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.put(
        f"/api/v1/conversations/{conversation_id}/model",
        headers=_auth(),
        json={"route": "rag_agent"},
    )
    assert response.status_code == 200
    assert response.json()["model_route"] == "rag_agent"
    # And the READ agrees, so the pin is stored rather than echoed back.
    read = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert read.json()["model_route"] == "rag_agent"


def test_a_fresh_thread_reports_no_pin() -> None:
    """Null is the shipped default, and it means "route by agent key" — the
    behaviour every thread had before the column existed."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/conversations",
        headers=_auth(),
        json={"space_id": _SPACE, "agent_key": "echo", "title": "T"},
    )
    assert response.json()["model_route"] is None


def test_pinning_to_null_unpins() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    url = f"/api/v1/conversations/{conversation_id}/model"
    client.put(url, headers=_auth(), json={"route": "default"})
    response = client.put(url, headers=_auth(), json={"route": None})
    assert response.status_code == 200
    assert response.json()["model_route"] is None


def test_a_body_without_a_route_field_is_422() -> None:
    """Required-to-be-present, exactly like the rename's title: an optional
    field would make "unpin" and "leave it alone" the same request."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.put(
        f"/api/v1/conversations/{conversation_id}/model", headers=_auth(), json={}
    )
    assert response.status_code == 422


def test_pinning_an_unconfigured_route_is_422() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.put(
        f"/api/v1/conversations/{conversation_id}/model",
        headers=_auth(),
        json={"route": "gpt-4o"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


def test_pinning_an_unknown_conversation_is_404() -> None:
    app, _ = _make_app()
    response = TestClient(app).put(
        "/api/v1/conversations/018f0000-0000-7000-8000-00000000dead/model",
        headers=_auth(),
        json={"route": "default"},
    )
    assert response.status_code == 404


def test_pinning_a_soft_deleted_conversation_is_a_conflict() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    deleted = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert deleted.status_code == 204
    response = client.put(
        f"/api/v1/conversations/{conversation_id}/model",
        headers=_auth(),
        json={"route": "default"},
    )
    assert response.status_code == 409


def test_a_pin_never_changes_the_threads_agent() -> None:
    """The route decides WHICH MODEL answers; the agent is still the thread's
    own (06 §4 threads per (workspace, agent))."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    body = client.put(
        f"/api/v1/conversations/{conversation_id}/model",
        headers=_auth(),
        json={"route": "rag_agent"},
    ).json()
    assert body["agent_key"] == "echo"


def test_the_pin_route_requires_a_bearer_token() -> None:
    client = TestClient(_make_app()[0])
    response = client.put("/api/v1/conversations/x/model", json={"route": None})
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# DELETE /conversations/{id}/messages/{message_id} (BE-RAG-004)               #
# --------------------------------------------------------------------------- #
def _turn(client: TestClient, conversation_id: str) -> list[dict[str, object]]:
    """One round trip through the thread, then its two persisted turns."""
    posted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hello"}},
    )
    assert posted.status_code == 200
    listed = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth())
    data: list[dict[str, object]] = listed.json()["data"]
    return data


def test_deleting_a_message_hides_it_from_the_transcript() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    user_message, assistant_message = _turn(client, conversation_id)

    response = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{user_message['id']}", headers=_auth()
    )

    assert response.status_code == 204
    assert not response.content
    remaining = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_auth()
    ).json()
    assert [message["id"] for message in remaining["data"]] == [assistant_message["id"]]
    # SOFT: the row is still there, carrying its `deleted_at` — a hard delete
    # would take `seq` 1 with it and let the next turn reuse it (INV-CV3).
    stored = stack.repository.messages[conversation_id]
    assert len(stored) == 2
    assert stored[0].deleted_at is not None


def test_a_deleted_messages_seq_is_never_reused() -> None:
    """The transcript keeps a GAP rather than renumbering — INV-CV1's counter
    is `MAX(seq)`, which a soft-delete deliberately does not move."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    user_message, _assistant = _turn(client, conversation_id)
    client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{user_message['id']}", headers=_auth()
    )

    after = _turn(client, conversation_id)

    assert [message["seq"] for message in after] == [2, 3, 4]


def test_deleting_the_same_message_twice_is_still_204() -> None:
    """A client retrying a lost 204 gets a 204, like the thread's own delete."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    message_id = _turn(client, conversation_id)[0]["id"]
    url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}"
    assert client.delete(url, headers=_auth()).status_code == 204
    assert client.delete(url, headers=_auth()).status_code == 204


def test_deleting_a_message_through_the_wrong_thread_is_404() -> None:
    """The path IS the ownership check: a real message id quoted against
    another thread must not delete anything."""
    app, stack = _make_app()
    client = TestClient(app)
    owner = _create(client, title="Owner")
    other = _create(client, title="Other")
    message_id = _turn(client, owner)[0]["id"]

    response = client.delete(
        f"/api/v1/conversations/{other}/messages/{message_id}", headers=_auth()
    )

    assert response.status_code == 404
    assert stack.repository.messages[owner][0].deleted_at is None


def test_deleting_an_unknown_message_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    response = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/018f0000-0000-7000-8000-00000000dead",
        headers=_auth(),
    )
    assert response.status_code == 404


def test_deleting_a_message_of_an_unknown_thread_is_404() -> None:
    app, _ = _make_app()
    response = TestClient(app).delete(
        "/api/v1/conversations/018f0000-0000-7000-8000-00000000dead/messages/x", headers=_auth()
    )
    assert response.status_code == 404


def test_deleting_a_message_of_a_soft_deleted_thread_is_a_conflict() -> None:
    """409, not 404 — the same read/write asymmetry the rename and the pin
    answer with: the thread refuses the write, it does not deny existing."""
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    message_id = _turn(client, conversation_id)[0]["id"]
    dropped = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert dropped.status_code == 204
    response = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}", headers=_auth()
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


# --------------------------------------------------------------------------- #
# GET/POST /conversations/{id}/files · DELETE .../files/{file_id} (BE-RAG-005) #
# --------------------------------------------------------------------------- #
def _scope_ctx() -> ExecutionContext:
    """The same tenant the fake authenticator resolves — the inbound port is
    tenant-scoped, so reading the scope under a different workspace would
    prove nothing about what the API just wrote."""
    return ExecutionContext(
        workspace_id=_W1, user_id=_U1, correlation_id="corr", roles=frozenset({"owner"})
    )


def _pin(client: TestClient, conversation_id: str, file_id: str) -> object:
    return client.post(
        f"/api/v1/conversations/{conversation_id}/files",
        headers=_auth(),
        json={"file_id": file_id},
    )


def _pinned(client: TestClient, conversation_id: str) -> list[str]:
    listed = client.get(f"/api/v1/conversations/{conversation_id}/files", headers=_auth())
    assert listed.status_code == 200
    data: list[dict[str, object]] = listed.json()["data"]
    return [str(row["file_id"]) for row in data]


def test_pinning_a_file_returns_201_and_then_lists_it() -> None:
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = _SPACE
    client = TestClient(app)
    conversation_id = _create(client)

    response = _pin(client, conversation_id, "file-1")

    assert response.status_code == 201
    body = response.json()
    assert body["file_id"] == "file-1"
    # A REFERENCE, not the file: no name, size or status crosses — those stay
    # on `GET /files`, which the client joins by id.
    assert set(body) == {"file_id", "pinned_at"}
    assert _pinned(client, conversation_id) == ["file-1"]


def test_an_unpinned_thread_lists_an_empty_scope_not_a_404() -> None:
    """Empty is the DEFAULT state, not a missing one: an un-pinned thread
    searches the whole workspace corpus, and the UI must be able to say "0
    pinned" without treating it as an error."""
    app, _stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    listed = client.get(f"/api/v1/conversations/{conversation_id}/files", headers=_auth())

    assert listed.status_code == 200
    assert listed.json()["data"] == []
    # The page envelope is the API-04 one, with a cursor that is always null:
    # the pinned set is bounded, so there is never a second page.
    assert listed.json()["meta"]["next_cursor"] is None


def test_pinning_an_unreadable_file_is_a_422_and_stores_nothing() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    response = _pin(client, conversation_id, "never-uploaded")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert _pinned(client, conversation_id) == []
    assert stack.repository.pins.get(conversation_id, []) == []


def test_pinning_a_file_from_another_space_is_a_409_on_the_wire() -> None:
    """Spaces plan §3.5, end to end: 409 (not the 422 an unreadable file
    gets), the catalogued code, and nothing stored.

    Since step 12 this is the REAL shape of the rule rather than a stand-in
    for it: the thread is opened in ``_SPACE`` (``ConversationCreateIn`` now
    carries the id) and the file lives in a different one, so two real spaces
    are being compared — not a space against the ``None`` every thread used
    to have."""
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = "018f0000-0000-7000-8000-000000000501"  # NOT _SPACE
    client = TestClient(app)
    conversation_id = _create(client)

    response = _pin(client, conversation_id, "file-1")

    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "spaces.cross_space_pin"
    assert _pinned(client, conversation_id) == []
    assert stack.repository.pins.get(conversation_id, []) == []


def test_pinning_the_same_file_twice_is_still_201_and_still_one_pin() -> None:
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = _SPACE
    client = TestClient(app)
    conversation_id = _create(client)

    first = _pin(client, conversation_id, "file-1")
    second = _pin(client, conversation_id, "file-1")

    assert (first.status_code, second.status_code) == (201, 201)
    # The ORIGINAL timestamp comes back, so a retry cannot reorder the list.
    assert second.json()["pinned_at"] == first.json()["pinned_at"]
    assert _pinned(client, conversation_id) == ["file-1"]


def test_unpinning_removes_only_that_pin_and_repeats_are_still_204() -> None:
    app, stack = _make_app()
    stack.files.ready.update({"file-1", "file-2"})
    stack.files.spaces.update({"file-1": _SPACE, "file-2": _SPACE})
    client = TestClient(app)
    conversation_id = _create(client)
    _pin(client, conversation_id, "file-1")
    _pin(client, conversation_id, "file-2")

    first = client.delete(f"/api/v1/conversations/{conversation_id}/files/file-1", headers=_auth())
    second = client.delete(f"/api/v1/conversations/{conversation_id}/files/file-1", headers=_auth())

    assert (first.status_code, second.status_code) == (204, 204)
    assert _pinned(client, conversation_id) == ["file-2"]


def test_a_pin_is_confined_to_its_own_thread() -> None:
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = _SPACE
    client = TestClient(app)
    first_thread = _create(client)
    second_thread = _create(client, title="Second")
    _pin(client, first_thread, "file-1")

    assert _pinned(client, second_thread) == []
    # Un-pinning from the wrong thread is a no-op, not a cross-thread removal.
    assert (
        client.delete(
            f"/api/v1/conversations/{second_thread}/files/file-1", headers=_auth()
        ).status_code
        == 204
    )
    assert _pinned(client, first_thread) == ["file-1"]


def test_pinning_and_unpinning_on_a_soft_deleted_thread_are_409() -> None:
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = _SPACE
    client = TestClient(app)
    conversation_id = _create(client)
    _pin(client, conversation_id, "file-1")
    deleted = client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    assert deleted.status_code == 204

    pinned = _pin(client, conversation_id, "file-1")
    unpinned = client.delete(
        f"/api/v1/conversations/{conversation_id}/files/file-1", headers=_auth()
    )

    assert pinned.status_code == 409
    assert unpinned.status_code == 409
    assert pinned.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    # The READ still works on a deleted thread — see `ListConversationFiles`.
    assert _pinned(client, conversation_id) == ["file-1"]


def test_the_scope_of_an_unknown_thread_is_a_404() -> None:
    app, stack = _make_app()
    stack.files.ready.add("file-1")
    stack.files.spaces["file-1"] = _SPACE
    client = TestClient(app)

    assert client.get("/api/v1/conversations/nope/files", headers=_auth()).status_code == 404
    assert _pin(client, "nope", "file-1").status_code == 404
    assert (
        client.delete("/api/v1/conversations/nope/files/file-1", headers=_auth()).status_code == 404
    )


def test_the_scope_routes_require_a_bearer_token() -> None:
    client = TestClient(_make_app()[0])
    assert client.get("/api/v1/conversations/x/files").status_code == 401
    assert client.post("/api/v1/conversations/x/files", json={"file_id": "f"}).status_code == 401
    assert client.delete("/api/v1/conversations/x/files/f").status_code == 401


def test_what_the_api_pins_is_what_the_orchestrator_reads_as_the_scope() -> None:
    """The two faces of the module share one store (`_build_conversations`),
    so a pin made over HTTP is visible to the inbound port the orchestrator
    resolves the retrieval scope through — without that, the pin would be a
    row nothing acts on."""
    app, stack = _make_app()
    stack.files.ready.update({"file-1", "file-2"})
    stack.files.spaces.update({"file-1": _SPACE, "file-2": _SPACE})
    client = TestClient(app)
    conversation_id = _create(client)
    _pin(client, conversation_id, "file-1")
    _pin(client, conversation_id, "file-2")

    scope = asyncio.run(stack.service.pinned_files(_scope_ctx(), conversation_id))

    assert scope == ("file-1", "file-2")


def test_an_unpinned_thread_reads_as_an_empty_scope_which_means_unscoped() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    assert asyncio.run(stack.service.pinned_files(_scope_ctx(), conversation_id)) == ()
    # And an unknown thread degrades the same way rather than raising: the
    # read-ahead must not take over the reporting the write does properly.
    assert asyncio.run(stack.service.pinned_files(_scope_ctx(), "nope")) == ()


# --------------------------------------------------------------------------- #
# ث-1 — citations stored with the assistant message and returned               #
# --------------------------------------------------------------------------- #
def _ask(client: TestClient, conversation_id: str, *, stream: bool = False) -> object:
    return client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "what does the manual say?"}, "stream": stream},
    )


def _messages(client: TestClient, conversation_id: str, query: str = "") -> dict[str, object]:
    response = client.get(
        f"/api/v1/conversations/{conversation_id}/messages{query}", headers=_auth()
    )
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def _final_frame(sse_text: str) -> dict[str, object]:
    lines = sse_text.splitlines()
    index = lines.index("event: final")
    frame: dict[str, object] = json.loads(lines[index + 1].removeprefix("data: "))
    return frame


def test_a_streamed_answer_returns_the_same_citations_in_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")

    response = _ask(client, conversation_id, stream=True)

    final = _final_frame(response.text)  # type: ignore[attr-defined]
    listed = _messages(client, conversation_id)["data"]
    (assistant,) = [m for m in listed if m["role"] == "assistant"]  # type: ignore[attr-defined]
    assert assistant["id"] == final["message_id"]
    assert assistant["citations"] == final["citations"] == _TWO_CITATIONS


def test_the_arabic_file_name_is_sent_as_utf8_not_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")
    _ask(client, conversation_id)

    response = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth())

    assert _ARABIC_NAME.encode("utf-8") in response.content
    assert b"\\u" not in response.content


def test_the_user_message_has_the_citations_key_with_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")
    _ask(client, conversation_id)

    user, assistant = _messages(client, conversation_id)["data"]  # type: ignore[misc]

    assert "citations" in user
    assert user["citations"] is None
    assert assistant["citations"] == _TWO_CITATIONS


def test_content_stays_text_and_attachments_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")
    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    assert set(reply["content"]) == {"text", "attachments"}
    for message in _messages(client, conversation_id)["data"]:  # type: ignore[attr-defined]
        assert set(message["content"]) == {"text", "attachments"}


def test_a_client_sent_content_citations_key_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", None)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")

    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hi", "citations": _TWO_CITATIONS}},
    )

    user, assistant = _messages(client, conversation_id)["data"]  # type: ignore[misc]
    assert user["citations"] is None
    assert user["content"] == {"text": "hi", "attachments": []}
    assert assistant["citations"] is None


def test_each_message_keeps_its_own_citations_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")
    first = [_TWO_CITATIONS[0]]
    for payload in (first, [], None):
        monkeypatch.setattr(_CitingAgent, "citations", payload)
        _ask(client, conversation_id)

    seen: list[object] = []
    cursor = ""
    while True:
        page = _messages(client, conversation_id, f"?limit=1{cursor}")
        seen.extend(m["citations"] for m in page["data"])  # type: ignore[attr-defined]
        next_cursor = page["meta"]["next_cursor"]  # type: ignore[index]
        if next_cursor is None:
            break
        cursor = f"&cursor={next_cursor}"

    assert seen == [None, first, None, [], None, None]


def test_a_non_streamed_reply_has_the_same_citations_as_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")

    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    assert reply["citations"] == _TWO_CITATIONS
    listed = _messages(client, conversation_id)["data"]
    (stored,) = [m for m in listed if m["id"] == reply["id"]]  # type: ignore[attr-defined]
    assert stored["citations"] == reply["citations"]


def test_an_agent_without_citations_replies_with_null() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    assert "citations" in reply
    assert reply["citations"] is None


def test_an_agent_that_states_no_sources_replies_with_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", [])
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")

    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    assert reply["citations"] == []


def test_a_malformed_citation_list_never_fails_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = [{**_TWO_CITATIONS[0], "chunk_id": "chunk-a"}]  # not a UUID: a value rule
    monkeypatch.setattr(_CitingAgent, "citations", bad)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")

    response = _ask(client, conversation_id)

    assert response.status_code == 200  # type: ignore[attr-defined]
    assert response.json()["content"]["text"] == "the answer"  # type: ignore[attr-defined]
    assert response.json()["citations"] is None  # type: ignore[attr-defined]


def test_a_deleted_assistant_message_leaves_history_with_its_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="citing")
    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    deleted = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{reply['id']}", headers=_auth()
    )

    assert deleted.status_code == 204
    ids = [m["id"] for m in _messages(client, conversation_id)["data"]]  # type: ignore[attr-defined]
    assert reply["id"] not in ids


def test_an_unknown_thread_is_a_404_problem_without_citations() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/conversations/missing/messages", headers=_auth())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert "citations" not in response.json()


def test_a_bad_token_is_401_on_the_messages_route() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/conversations/x/messages", headers=_auth("bad"))

    assert response.status_code == 401
