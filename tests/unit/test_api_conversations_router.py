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
from typing import Any, ClassVar

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import PROBLEM_MEDIA_TYPE, create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.idempotent_turn import (
    IdempotentTurnLedger,
    TurnHandle,
    claim_turn,
    content_hash,
    stale_after_s,
)
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, RateLimitedError, UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.conversations.ports.inbound import FallbackRef
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


# th-3 -- one entry per agent run, so a test can prove a replay ran nothing.
_RUNS: list[str] = []


@pytest.fixture(autouse=True)
def _reset_runs() -> None:
    _RUNS.clear()


class _EchoAgent(BaseAgent):
    metadata = _ECHO_METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        _RUNS.append("echo")
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
        _RUNS.append("citing")
        yield AgentEvent(type="token", data={"delta": "the answer"})
        data: dict[str, object] = {"text": "the answer"}
        if type(self).citations is not None:
            data["citations"] = type(self).citations
        yield AgentEvent(type="final", data=data)


_HALF_METADATA = AgentMetadata(
    key="half",
    name="Half",
    version="1.0.0",
    description="Streams some Arabic text and then fails (th-2).",
    capabilities=frozenset(),
    required_permissions=frozenset({"agents:invoke"}),
)
_SLOW_METADATA = AgentMetadata(
    key="slow",
    name="Slow",
    version="1.0.0",
    description="Streams a token and then stalls past the cap (th-2).",
    capabilities=frozenset(),
    required_permissions=frozenset({"agents:invoke"}),
)


class _HalfAgent(BaseAgent):
    metadata = _HALF_METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        _RUNS.append("half")
        yield AgentEvent(type="token", data={"delta": "الجواب هو"})
        raise ConflictError("the provider went away")


class _SlowAgent(BaseAgent):
    metadata = _SLOW_METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "slow answer"})
        await asyncio.sleep(3)
        yield AgentEvent(type="final", data={"text": "unreachable"})


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


def _make_app(
    *,
    stream_max_duration_s: float | None = None,
    store: InMemoryIdempotencyStore | None = None,
) -> tuple[FastAPI, ConversationsStack]:
    """The app plus the store behind it — tests assert on the wire AND on what
    was actually persisted, which is the whole point of 6.1-ج-3."""
    registry = InMemoryAgentRegistry()
    registry.register(_ECHO_METADATA, _EchoAgent)
    registry.register(_CITING_METADATA, _CitingAgent)
    registry.register(_HALF_METADATA, _HalfAgent)
    registry.register(_SLOW_METADATA, _SlowAgent)
    conversations = build_conversations(spaces=StubActiveSpaces(live={_SPACE}))
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),
            conversations=conversations.service,
            stream_max_duration_s=stream_max_duration_s,
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
        idempotency=store if store is not None else InMemoryIdempotencyStore(),
    )
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    # th-3: the orchestrator, for the tests that drive a turn without the wire.
    app.state.test_orchestrator = orchestrator
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


def test_a_deleted_assistant_message_leaves_history_and_takes_its_citations_with_it(
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
    # AC-2.8: neither the message nor any of its sources is still readable
    raw = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth()).text
    assert _ARABIC_NAME not in raw
    assert all(m["role"] == "user" for m in _messages(client, conversation_id)["data"])  # type: ignore[attr-defined]


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


def test_no_token_is_401_on_the_messages_route() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/conversations/x/messages")

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# ث-2 — status and fallback stored with the assistant message and returned     #
# --------------------------------------------------------------------------- #
_FALLBACK_WIRE = {
    "from": {"provider": "openai", "model": "gpt-4.1-mini"},
    "to": {"provider": "ollama", "model": "gemma3:1b"},
}
_FALLBACK_REF = FallbackRef("openai", "gpt-4.1-mini", "ollama", "gemma3:1b")


def _assistant_of(client: TestClient, conversation_id: str) -> dict[str, object]:
    listed = _messages(client, conversation_id)["data"]
    return [m for m in listed if m["role"] == "assistant"][-1]  # type: ignore[attr-defined,no-any-return]


def test_a_streamed_answer_is_complete_in_history() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    response = _ask(client, conversation_id, stream=True)

    final = _final_frame(response.text)  # type: ignore[attr-defined]
    assistant = _assistant_of(client, conversation_id)
    assert assistant["id"] == final["message_id"]
    assert assistant["status"] == "complete"
    assert assistant["fallback"] is None
    # the frame gains nothing (FR-9)
    assert "status" not in final
    assert "message_status" not in final


def test_a_turn_cut_by_the_time_cap_is_partial_in_history() -> None:
    app, _ = _make_app(stream_max_duration_s=0.05)
    client = TestClient(app)
    conversation_id = _create(client, agent_key="slow")

    _ask(client, conversation_id, stream=True)

    assistant = _assistant_of(client, conversation_id)
    assert assistant["status"] == "partial"
    assert assistant["fallback"] is None
    assert assistant["content"]["text"].startswith("slow answer")  # type: ignore[index]


def test_a_partial_arabic_answer_is_sent_as_utf8_and_marked_partial() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="half")
    _ask(client, conversation_id, stream=True)

    response = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth())

    assert "انقطعت الإجابةُ قبل اكتمالها.".encode() in response.content
    assert b"\\u" not in response.content
    assert _assistant_of(client, conversation_id)["status"] == "partial"


def test_a_non_streamed_cut_turn_is_a_problem_but_history_keeps_the_partial() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="half")

    response = _ask(client, conversation_id)

    assert response.status_code >= 400  # type: ignore[attr-defined]
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)  # type: ignore[attr-defined]
    assert _assistant_of(client, conversation_id)["status"] == "partial"


def test_a_stream_error_frame_keeps_its_problem_keys() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="half")

    response = _ask(client, conversation_id, stream=True)

    lines = response.text.splitlines()  # type: ignore[attr-defined]
    error = json.loads(lines[lines.index("event: error") + 1].removeprefix("data: "))
    assert {"type", "title", "status", "code", "detail", "correlation_id"} <= set(error)
    assert isinstance(error["status"], int)


def test_the_user_message_has_status_and_fallback_keys_with_null() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    _ask(client, conversation_id)

    (user, assistant) = _messages(client, conversation_id)["data"]  # type: ignore[misc]

    assert user["status"] is None  # type: ignore[index]
    assert user["fallback"] is None  # type: ignore[index]
    assert "status" in user  # type: ignore[operator]
    assert "fallback" in user  # type: ignore[operator]
    assert set(user["content"]) == {"text", "attachments"}  # type: ignore[index]
    assert set(assistant["content"]) == {"text", "attachments"}  # type: ignore[index]


def test_a_client_sent_status_or_fallback_in_content_is_not_stored() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hi", "status": "partial", "fallback": _FALLBACK_WIRE}},
    )

    user = _messages(client, conversation_id)["data"][0]  # type: ignore[index]
    assert user["status"] is None  # type: ignore[index]
    assert user["fallback"] is None  # type: ignore[index]
    assert set(user["content"]) == {"text", "attachments"}  # type: ignore[index]


def test_a_non_streamed_reply_has_the_same_status_and_fallback_as_history() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)

    reply = _ask(client, conversation_id).json()  # type: ignore[attr-defined]

    assert reply["status"] == "complete"
    assert "fallback" in reply
    stored = _assistant_of(client, conversation_id)
    assert (stored["status"], stored["fallback"]) == (reply["status"], reply["fallback"])


async def test_a_fallback_is_served_with_the_from_key_and_rows_without_status_are_null() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    ctx = _scope_ctx()
    await stack.service.append(ctx, conversation_id, role="assistant", text="old")
    await stack.service.append(
        ctx, conversation_id, role="assistant", text="b", status="partial", fallback=_FALLBACK_REF
    )
    await stack.service.append(ctx, conversation_id, role="assistant", text="c", status="complete")

    seen: list[tuple[object, object]] = []
    cursor = ""
    while True:
        page = _messages(client, conversation_id, f"?limit=1{cursor}")
        seen.extend((m["status"], m["fallback"]) for m in page["data"])  # type: ignore[attr-defined]
        next_cursor = page["meta"]["next_cursor"]  # type: ignore[index]
        if next_cursor is None:
            break
        cursor = f"&cursor={next_cursor}"

    assert seen == [(None, None), ("partial", _FALLBACK_WIRE), ("complete", None)]


async def test_a_row_without_status_that_ends_with_the_cut_off_sentence_stays_null() -> None:
    """AC-3.3: nothing is inferred from the text of a row saved before the fields."""
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    await stack.service.append(
        _scope_ctx(),
        conversation_id,
        role="assistant",
        text="half an answer\n\n⚠️ انقطعت الإجابةُ قبل اكتمالها.",
    )

    page = _messages(client, conversation_id, "")

    (message,) = page["data"]  # type: ignore[index]
    assert message["status"] is None
    assert message["fallback"] is None


async def test_a_stored_fallback_never_renders_a_from_underscore_key() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    await stack.service.append(
        _scope_ctx(),
        conversation_id,
        role="assistant",
        text="b",
        status="complete",
        fallback=_FALLBACK_REF,
    )

    raw = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_auth()).text

    assert '"from_"' not in raw
    assert '"from"' in raw


def test_a_deleted_partial_message_leaves_history() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    conversation_id = _create(client, agent_key="half")
    _ask(client, conversation_id, stream=True)
    partial = _assistant_of(client, conversation_id)

    deleted = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{partial['id']}", headers=_auth()
    )

    assert deleted.status_code == 204
    assert partial["id"] not in [m["id"] for m in _messages(client, conversation_id)["data"]]  # type: ignore[attr-defined]


def test_an_unknown_thread_is_a_404_problem_without_status_or_fallback() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/conversations/missing/messages", headers=_auth())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert "fallback" not in response.json()


async def test_every_kind_of_message_keeps_content_to_text_and_attachments() -> None:
    """AC-3.5: user, complete, partial, switched and legacy rows alike -- the
    two new fields are siblings of `content`, never keys inside it."""
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    _ask(client, conversation_id)  # a user message and a complete reply
    ctx = _scope_ctx()
    await stack.service.append(ctx, conversation_id, role="assistant", text="old")
    await stack.service.append(
        ctx, conversation_id, role="assistant", text="b", status="partial", fallback=_FALLBACK_REF
    )

    data = _messages(client, conversation_id)["data"]

    assert len(data) == 4  # type: ignore[arg-type]
    for message in data:  # type: ignore[attr-defined]
        assert set(message["content"]) == {"text", "attachments"}
        assert "status" in message and "fallback" in message
        assert "citations" in message


async def test_another_workspaces_history_is_a_404_with_no_status_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6.1: a partial, switched message in workspace A is invisible to B."""
    app, stack = _make_app()
    client = TestClient(app)
    conversation_id = _create(client)
    await stack.service.append(
        _scope_ctx(),
        conversation_id,
        role="assistant",
        text="secret partial",
        status="partial",
        fallback=_FALLBACK_REF,
    )
    path = f"/api/v1/conversations/{conversation_id}/messages"
    assert client.get(path, headers=_auth()).status_code == 200  # visible to A

    async def _as_workspace_b(self: _FakeAuth, token: str) -> Principal:
        return Principal(
            workspace_id="018f0000-0000-7000-8000-0000000000w2",
            user_id="018f0000-0000-7000-8000-0000000000u2",
            roles=frozenset({"owner"}),
        )

    monkeypatch.setattr(_FakeAuth, "authenticate", _as_workspace_b)

    response = client.get(path, headers=_auth())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert "secret partial" not in response.text
    for leaked in ('status":"partial', 'fallback":{', "gemma3", "gpt-4.1"):
        assert leaked not in response.text


# --------------------------------------------------------------------------- #
# th-3 -- the `accepted` frame, reply_to and the Idempotency-Key               #
# --------------------------------------------------------------------------- #
_KEY = "retry-key-1"
_ENDPOINT = "POST /conversations/{}/messages"
_THREAD_BODY = {"text": "hello"}


def _frames(sse_text: str) -> list[tuple[str, dict[str, object]]]:
    """The (event, data) frames of an SSE body, keep-alive comments skipped."""
    frames: list[tuple[str, dict[str, object]]] = []
    for block in sse_text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        assert lines[0].startswith("event: "), block
        frames.append((lines[0].removeprefix("event: "), json.loads(lines[1][len("data: ") :])))
    return frames


def _post(
    client: TestClient,
    conversation_id: str,
    *,
    key: str | None = None,
    stream: bool = False,
    content: dict[str, object] | None = None,
    accept_sse: bool = False,
) -> Any:
    headers = _auth()
    if key is not None:
        headers["Idempotency-Key"] = key
    if accept_sse:
        headers["Accept"] = "text/event-stream"
    return client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": content if content is not None else _THREAD_BODY, "stream": stream},
    )


def _data(client: TestClient, conversation_id: str) -> list[dict[str, Any]]:
    page: dict[str, Any] = _messages(client, conversation_id, "?limit=100")
    data: list[dict[str, Any]] = page["data"]
    return data


def _row_key(conversation_id: str, key: str = _KEY, workspace: str = _W1) -> tuple[str, str, str]:
    return (workspace, _ENDPOINT.format(conversation_id), key)


def _setup(
    *, agent_key: str = "echo", stream_max_duration_s: float | None = None
) -> tuple[TestClient, str, InMemoryIdempotencyStore, ConversationsStack]:
    store = InMemoryIdempotencyStore()
    app, stack = _make_app(stream_max_duration_s=stream_max_duration_s, store=store)
    client = TestClient(app)
    return client, _create(client, agent_key=agent_key), store, stack


# -- the accepted frame -------------------------------------------------------
def test_sse_starts_with_accepted_naming_the_stored_user_message() -> None:
    client, conversation_id, _store, _ = _setup()

    response = _post(client, conversation_id, stream=True)

    frames = _frames(response.text)
    assert [name for name, _ in frames] == ["accepted", "token", "final"]
    accepted = frames[0][1]
    user = _data(client, conversation_id)[0]
    assert user["role"] == "user"
    assert accepted == {"conversation_id": conversation_id, "user_message_id": user["id"], "seq": 1}
    assert frames[-1][1]["user_message_id"] == user["id"]
    assert user["seq"] == accepted["seq"]


@pytest.mark.parametrize(
    ("setup", "expected"),
    [("unknown", 404), ("invalid", 422)],
)
def test_a_refused_request_is_a_problem_with_no_accepted_frame_and_no_user_message(
    setup: str, expected: int
) -> None:
    client, conversation_id, _store, stack = _setup()
    target = "no-such-thread" if setup == "unknown" else conversation_id
    body: dict[str, Any] = {"stream": True} if setup == "invalid" else {"content": {"text": "x"}}

    response = client.post(
        f"/api/v1/conversations/{target}/messages",
        headers={**_auth(), "Accept": "text/event-stream"},
        json={"stream": True, **body} if setup == "invalid" else {**body, "stream": True},
    )

    assert response.status_code == expected
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert "event: accepted" not in response.text
    assert stack.repository.messages.get(conversation_id, []) == []


def test_a_quota_refusal_is_a_429_with_no_accepted_frame_and_no_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, conversation_id, _store, stack = _setup()

    async def _refuse(*_args: object, **_kwargs: object) -> None:
        raise RateLimitedError("over the limit", retry_after_s=3)

    monkeypatch.setattr(AgentOrchestrator, "_admit", _refuse)

    response = _post(client, conversation_id, stream=True)

    assert response.status_code == 429
    assert "event: accepted" not in response.text
    assert stack.repository.messages.get(conversation_id, []) == []


# -- reply_to -----------------------------------------------------------------
def test_the_reply_records_the_question_it_answers_in_json_and_history() -> None:
    client, conversation_id, _store, _ = _setup()

    reply = _post(client, conversation_id).json()

    history = _data(client, conversation_id)
    user, assistant = history
    assert user["reply_to"] is None
    assert "reply_to" in user  # always on the wire
    assert reply["reply_to"] == user["id"]
    assert assistant["reply_to"] == user["id"]


def test_a_client_sent_reply_to_is_ignored() -> None:
    client, conversation_id, _store, _ = _setup()
    forged = "0190eeee-0000-7000-8000-0000000000a1"

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=_auth(),
        json={"content": {"text": "hi", "reply_to": forged}, "reply_to": forged},
    )

    assert response.status_code == 200
    user, assistant = _data(client, conversation_id)
    assert user["reply_to"] is None
    assert assistant["reply_to"] == user["id"]
    assert forged not in json.dumps(user)


def test_deleting_the_question_keeps_the_id_on_the_answer() -> None:
    client, conversation_id, _store, _ = _setup()
    reply = _post(client, conversation_id).json()
    question_id = reply["reply_to"]

    gone = client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{question_id}", headers=_auth()
    )

    assert gone.status_code == 204
    history = _data(client, conversation_id)
    assert [m["role"] for m in history] == ["assistant"]
    assert history[0]["reply_to"] == question_id


# -- no key: nothing changes --------------------------------------------------
@pytest.mark.parametrize("stream", [False, True])
def test_without_a_key_the_key_store_is_never_touched(stream: bool) -> None:
    client, conversation_id, store, _ = _setup()

    response = _post(client, conversation_id, stream=stream)

    assert response.status_code == 200
    assert store.calls == []
    if stream:
        accepted = _frames(response.text)[0]
        assert accepted[0] == "accepted"
        assert "replayed" not in accepted[1]


# -- a key: replay of a completed turn ---------------------------------------
def test_a_repeat_returns_the_same_message_and_runs_nothing() -> None:
    client, conversation_id, store, _ = _setup()
    first = _post(client, conversation_id, key=_KEY)
    runs_after_first = len(_RUNS)

    second = _post(client, conversation_id, key=_KEY)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(_RUNS) == runs_after_first == 1
    assert len(_data(client, conversation_id)) == 2  # one question, one answer
    assert store.calls == ["claim", "checkpoint", "complete", "claim"]


def test_the_same_key_with_other_content_is_a_conflict_and_runs_nothing() -> None:
    client, conversation_id, store, _ = _setup()
    _post(client, conversation_id, key=_KEY)

    response = _post(client, conversation_id, key=_KEY, content={"text": "something else"})

    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert len(_RUNS) == 1
    assert len(_data(client, conversation_id)) == 2
    assert _KEY not in response.text  # the key is never echoed (d-t13)
    assert store.calls[-1] == "claim"


def test_two_keys_with_the_same_text_are_two_turns() -> None:
    client, conversation_id, _store, _ = _setup()

    first = _post(client, conversation_id, key="k-1").json()
    second = _post(client, conversation_id, key="k-2").json()

    assert first["id"] != second["id"]
    assert len(_RUNS) == 2
    assert len(_data(client, conversation_id)) == 4


def test_the_same_key_on_another_conversation_is_a_new_turn() -> None:
    client, first_id, store, _ = _setup()
    second_id = _create(client)
    _post(client, first_id, key=_KEY)

    _post(client, second_id, key=_KEY)

    assert len(_RUNS) == 2
    assert _row_key(first_id) in store.rows
    assert _row_key(second_id) in store.rows


def test_the_scope_uses_the_canonical_conversation_id_not_the_path_text() -> None:
    client, conversation_id, store, _ = _setup()
    _post(client, conversation_id, key=_KEY)

    # the path is matched exactly; the stored scope is the id the database holds
    assert list(store.rows) == [_row_key(conversation_id)]


@pytest.mark.parametrize("key", ["", "   ", "x" * 256])
def test_an_unusable_key_is_a_422_before_any_read_or_run(key: str) -> None:
    client, conversation_id, store, stack = _setup()

    response = _post(client, conversation_id, key=key)

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"
    assert store.calls == []
    assert _RUNS == []
    assert stack.repository.messages.get(conversation_id, []) == []


def test_a_stream_then_a_json_repeat_returns_the_message_not_a_conflict() -> None:
    client, conversation_id, _store, _ = _setup()
    streamed = _post(client, conversation_id, key=_KEY, stream=True)
    final = _frames(streamed.text)[-1][1]

    repeat = _post(client, conversation_id, key=_KEY)

    assert repeat.status_code == 200
    assert repeat.json()["id"] == final["message_id"]
    assert repeat.json()["content"] == final["content"]
    assert len(_RUNS) == 1


def test_a_json_then_a_stream_repeat_replays_two_frames() -> None:
    client, conversation_id, _store, _ = _setup()
    first = _post(client, conversation_id, key=_KEY).json()

    repeat = _post(client, conversation_id, key=_KEY, stream=True)

    frames = _frames(repeat.text)
    assert [name for name, _ in frames] == ["accepted", "final"]
    assert frames[0][1]["replayed"] is True
    assert frames[0][1]["user_message_id"] == first["reply_to"]
    assert frames[1][1]["message_id"] == first["id"]
    assert len(_RUNS) == 1


def test_a_streamed_repeat_replays_the_original_frames_without_tokens_or_calls() -> None:
    client, conversation_id, store, _ = _setup()
    first = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)
    assert store.calls == ["claim", "checkpoint", "complete"]

    repeat = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)

    assert [name for name, _ in repeat] == ["accepted", "final"]
    original_accepted, original_final = first[0][1], first[-1][1]
    assert repeat[0][1] == {**original_accepted, "replayed": True}
    for field in ("message_id", "content", "user_message_id", "usage"):
        assert repeat[1][1][field] == original_final[field]
    assert store.calls == ["claim", "checkpoint", "complete", "claim"]
    assert len(_RUNS) == 1


def test_a_replayed_final_carries_the_stored_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_CitingAgent, "citations", _TWO_CITATIONS)
    client, conversation_id, _store, _ = _setup(agent_key="citing")
    first = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)[-1][1]

    repeat = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)[-1][1]

    assert repeat["citations"] == first["citations"] == _TWO_CITATIONS


def test_a_replay_after_the_answer_was_deleted_is_a_404_in_both_shapes() -> None:
    client, conversation_id, store, _ = _setup()
    reply = _post(client, conversation_id, key=_KEY).json()
    client.delete(
        f"/api/v1/conversations/{conversation_id}/messages/{reply['id']}", headers=_auth()
    )
    calls = list(store.calls)

    json_repeat = _post(client, conversation_id, key=_KEY)
    sse_repeat = _post(client, conversation_id, key=_KEY, stream=True)

    for response in (json_repeat, sse_repeat):
        assert response.status_code == 404
        assert response.json()["code"] == "common.not_found"
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        assert "echo: hello" not in response.text
        assert "event: accepted" not in response.text
    assert len(_RUNS) == 1
    assert store.calls[len(calls) :] == ["claim", "claim"]


def test_a_replay_after_the_conversation_was_deleted_is_a_404_and_touches_nothing() -> None:
    client, conversation_id, store, _ = _setup()
    _post(client, conversation_id, key=_KEY)
    client.delete(f"/api/v1/conversations/{conversation_id}", headers=_auth())
    calls = list(store.calls)

    response = _post(client, conversation_id, key=_KEY)

    assert response.status_code == 404
    assert store.calls == calls
    assert len(_RUNS) == 1


def test_a_swept_key_starts_a_new_turn() -> None:
    client, conversation_id, store, _ = _setup()
    _post(client, conversation_id, key=_KEY)
    del store.rows[_row_key(conversation_id)]  # the daily retention sweep

    _post(client, conversation_id, key=_KEY)

    assert len(_RUNS) == 2
    assert len(_data(client, conversation_id)) == 4


def test_arabic_and_very_long_content_replay_byte_for_byte() -> None:
    client, conversation_id, store, _ = _setup()
    arabic = {"text": "ما هو دليل الصيانة؟ 🙂 " + "ـ" * 10}
    long_text = {"text": "س" * 100_000}
    for key, content in (("k-ar", arabic), ("k-long", long_text)):
        first = _post(client, conversation_id, key=key, content=content)
        second = _post(client, conversation_id, key=key, content=content)
        assert second.content == first.content
        assert second.json()["content"]["text"] == f"echo: {content['text']}"
    bodies = [json.dumps(row.response_body, ensure_ascii=False) for row in store.rows.values()]
    assert all(len(body) < 512 for body in bodies)  # a handle, not the text
    assert all("echo" not in body and "س" not in body for body in bodies)


def test_the_stored_handle_carries_ids_and_counts_only_after_every_path() -> None:
    forbidden = ("text", "content", "citations", "detail", "echo", "hello")
    for agent_key in ("echo", "half"):
        client, conversation_id, store, _ = _setup(agent_key=agent_key)
        _post(client, conversation_id, key="k-json")
        _post(client, conversation_id, key="k-sse", stream=True)
        for row in store.rows.values():
            body = json.dumps(row.response_body)
            assert row.response_body is not None
            assert row.response_body["kind"] == "agent_turn"
            assert not any(word in set(row.response_body) for word in forbidden)
            assert "echo:" not in body and "the provider" not in body


# -- failures -----------------------------------------------------------------
def test_a_failure_after_the_question_is_stored_is_a_502_with_ids_and_replays_the_same() -> None:
    client, conversation_id, store, _ = _setup(agent_key="half")

    first = _post(client, conversation_id, key=_KEY)
    runs = len(_RUNS)
    second = _post(client, conversation_id, key=_KEY)

    history = _data(client, conversation_id)
    user = history[0]
    partial = history[1]
    # `_HalfAgent` raises a ConflictError, so B1's own code and status are 409:
    # the replay must hand back exactly what the first caller got, whatever it was.
    assert first.status_code == 409
    assert first.json()["code"] == "common.conflict"
    assert first.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert first.json()["user_message_id"] == user["id"]
    assert first.json()["message_id"] == partial["id"]
    assert partial["status"] == "partial"
    assert second.status_code == first.status_code
    for member in ("code", "status", "user_message_id", "message_id", "title"):
        assert second.json()[member] == first.json()[member]
    assert len(_RUNS) == runs == 1
    assert store.calls == ["claim", "checkpoint", "complete", "claim"]


def test_a_streamed_failure_replays_accepted_then_the_same_error() -> None:
    client, conversation_id, _store, _ = _setup(agent_key="half")
    first = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)
    assert [name for name, _ in first] == ["accepted", "token", "error"]
    assert "message_id" in first[-1][1]

    repeat = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)

    assert [name for name, _ in repeat] == ["accepted", "error"]
    assert repeat[0][1]["replayed"] is True
    for member in ("code", "status", "user_message_id", "message_id"):
        assert repeat[1][1][member] == first[-1][1][member]
    assert len(_RUNS) == 1


def test_a_failure_before_any_token_has_the_question_id_and_no_message_id() -> None:
    client, conversation_id, _store, _ = _setup(agent_key="halt")
    registry_app = client.app
    assert registry_app is not None

    # `half` streams one token first; this one fails at once.
    class _Halt(BaseAgent):
        metadata = AgentMetadata(
            key="halt",
            name="Halt",
            version="1.0.0",
            description="fails at once",
            capabilities=frozenset(),
            required_permissions=frozenset({"agents:invoke"}),
        )

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            _RUNS.append("halt")
            raise ConflictError("down before the first token")
            yield  # pragma: no cover

    client.app.state.test_orchestrator._deps.agents.register(_Halt.metadata, _Halt)  # type: ignore[attr-defined]

    first = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)

    assert [name for name, _ in first] == ["accepted", "error"]
    assert "message_id" not in first[-1][1]
    repeat = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)
    assert [name for name, _ in repeat] == ["accepted", "error"]
    assert repeat[1][1]["code"] == first[-1][1]["code"]
    assert "message_id" not in repeat[1][1]
    assert [m["role"] for m in _data(client, conversation_id)] == ["user"]
    assert len(_RUNS) == 1


def test_a_reply_that_could_not_be_saved_is_a_502_with_the_question_id_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, conversation_id, _store, stack = _setup()
    original = stack.repository.append_message

    async def _fail_for_assistant(ctx: ExecutionContext, message: Any) -> None:
        if message.role.value == "assistant":
            raise ConflictError("the write lost a race")
        await original(ctx, message)

    monkeypatch.setattr(stack.repository, "append_message", _fail_for_assistant)

    first = _post(client, conversation_id, key=_KEY)
    second = _post(client, conversation_id, key=_KEY)

    user = stack.repository.messages[conversation_id][0]
    assert first.status_code == 502
    assert first.json()["code"] == "agent.failed"
    assert first.json()["user_message_id"] == user.id
    assert "message_id" not in first.json()
    assert second.status_code == 502
    assert second.json()["user_message_id"] == user.id
    assert len(_RUNS) == 1


def test_a_failure_leaves_no_assistant_message_that_is_not_complete_or_partial() -> None:
    client, conversation_id, _store, _ = _setup(agent_key="half")
    _post(client, conversation_id, key=_KEY)
    _post(client, conversation_id, key=_KEY, stream=True)

    statuses = [m["status"] for m in _data(client, conversation_id) if m["role"] == "assistant"]

    assert statuses == ["partial"]
    assert all(status in {"complete", "partial", None} for status in statuses)


def test_a_failure_without_a_key_touches_no_store_and_leaves_the_question() -> None:
    client, conversation_id, store, _ = _setup(agent_key="half")

    response = _post(client, conversation_id)

    assert response.status_code == 409
    assert store.calls == []
    assert _data(client, conversation_id)[0]["role"] == "user"


def test_a_failure_before_the_question_is_stored_frees_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, conversation_id, store, _ = _setup()
    real_admit = AgentOrchestrator._admit
    attempts: list[int] = []

    async def _refuse_once(self: AgentOrchestrator, *args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise RateLimitedError("over the limit", retry_after_s=1)
        return await real_admit(self, *args, **kwargs)

    monkeypatch.setattr(AgentOrchestrator, "_admit", _refuse_once)

    refused = _post(client, conversation_id, key=_KEY)
    assert refused.status_code == 429
    assert store.calls == ["claim", "release"]
    assert store.rows == {}

    retried = _post(client, conversation_id, key=_KEY)

    assert retried.status_code == 200
    assert len(_RUNS) == 1
    assert [m["role"] for m in _data(client, conversation_id)] == ["user", "assistant"]


def test_an_unknown_agent_frees_the_key_too() -> None:
    client, conversation_id, store, _ = _setup(agent_key="ghost")

    response = _post(client, conversation_id, key=_KEY)

    assert response.status_code == 404
    assert response.json()["code"] == "agent.unknown"
    assert store.calls == ["claim", "release"]
    assert store.rows == {}


# -- an attempt that is still running, or whose server died -------------------
def _accepted_handle(conversation_id: str, user_message_id: str, seq: int = 1) -> dict[str, Any]:
    return TurnHandle(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        seq=seq,
        outcome="accepted",
    ).to_json()


async def _forge_claim(
    store: InMemoryIdempotencyStore,
    conversation_id: str,
    *,
    handle: dict[str, Any] | None,
    age_s: float,
    content: dict[str, object] | None = None,
) -> None:
    ctx = _scope_ctx()
    endpoint = _ENDPOINT.format(conversation_id)
    await store.claim(
        ctx,
        endpoint=endpoint,
        key=_KEY,
        request_hash=content_hash(content if content is not None else _THREAD_BODY),
    )
    if handle is not None:
        await store.checkpoint(ctx, endpoint=endpoint, key=_KEY, response_body=handle)
    store.set_age(_row_key(conversation_id), age_s)


async def test_a_young_unfinished_claim_is_a_409_problem_not_a_stream() -> None:
    client, conversation_id, store, _ = _setup()
    await _forge_claim(
        store, conversation_id, handle=_accepted_handle(conversation_id, "u-1"), age_s=10
    )

    for stream in (False, True):
        response = _post(client, conversation_id, key=_KEY, stream=stream)
        assert response.status_code == 409
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        assert response.json()["code"] == "common.conflict"
    assert _RUNS == []


async def test_an_old_accepted_claim_is_reported_lost_with_the_question_id() -> None:
    client, conversation_id, store, _ = _setup()
    t = Settings().limits.stream_max_duration_s + 120
    await _forge_claim(
        store, conversation_id, handle=_accepted_handle(conversation_id, "u-1", 4), age_s=t
    )

    as_json = _post(client, conversation_id, key=_KEY)
    as_sse = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)

    assert as_json.status_code == 502
    assert as_json.json()["code"] == "agent.failed"
    assert as_json.json()["user_message_id"] == "u-1"
    assert "message_id" not in as_json.json()
    assert [name for name, _ in as_sse] == ["accepted", "error"]
    assert as_sse[0][1] == {
        "conversation_id": conversation_id,
        "user_message_id": "u-1",
        "seq": 4,
        "replayed": True,
    }
    assert as_sse[1][1]["code"] == "agent.failed"
    assert _RUNS == []
    # a repeat writes nothing: only the claims
    assert store.calls == ["claim", "checkpoint", "claim", "claim"]


async def test_one_second_before_the_threshold_it_is_still_a_409() -> None:
    client, conversation_id, store, _ = _setup()
    t = Settings().limits.stream_max_duration_s + 120
    await _forge_claim(
        store, conversation_id, handle=_accepted_handle(conversation_id, "u-1"), age_s=t - 1
    )

    assert _post(client, conversation_id, key=_KEY).status_code == 409


async def test_an_old_claim_with_no_recorded_question_is_a_502_without_ids_or_stream() -> None:
    client, conversation_id, store, _ = _setup()
    t = Settings().limits.stream_max_duration_s + 120
    await _forge_claim(store, conversation_id, handle=None, age_s=t)

    response = _post(client, conversation_id, key=_KEY, stream=True)

    assert response.status_code == 502
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "agent.failed"
    assert "user_message_id" not in response.json()
    assert "event: accepted" not in response.text


async def test_a_running_turn_makes_the_repeat_a_409_and_the_first_still_completes() -> None:
    store = InMemoryIdempotencyStore()
    gate = asyncio.Event()

    class _Gated(BaseAgent):
        metadata = AgentMetadata(
            key="gated",
            name="Gated",
            version="1.0.0",
            description="waits for the test",
            capabilities=frozenset(),
            required_permissions=frozenset({"agents:invoke"}),
        )

        async def initialize(self) -> None:
            return None

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            _RUNS.append("gated")
            await gate.wait()
            yield AgentEvent(type="final", data={"text": "done"})

    app, _stack = _make_app(store=store)
    app.state.test_orchestrator._deps.agents.register(_Gated.metadata, _Gated)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        created = await client.post(
            "/api/v1/conversations",
            headers=_auth(),
            json={"space_id": _SPACE, "agent_key": "gated", "title": None},
        )
        conversation_id = created.json()["id"]
        url = f"/api/v1/conversations/{conversation_id}/messages"
        headers = {**_auth(), "Idempotency-Key": _KEY}

        first = asyncio.create_task(client.post(url, headers=headers, json={"content": {"t": 1}}))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if _row_key(conversation_id) in store.rows and (
                store.rows[_row_key(conversation_id)].response_body is not None
            ):
                break
        second = await client.post(url, headers=headers, json={"content": {"t": 1}})
        gate.set()
        done = await first

    assert second.status_code == 409
    assert done.status_code == 200
    assert _RUNS == ["gated"]


async def test_an_abandoned_stream_is_replayed_as_agent_failed_with_the_question_id() -> None:
    """AC-4.3/4.4: the reader left after a token / right after `accepted`; the
    ledger was still settled, and the repeat says so without running anything."""
    client, conversation_id, store, _ = _setup(agent_key="slow")
    orchestrator = client.app.state.test_orchestrator  # type: ignore[attr-defined]
    ctx = _scope_ctx()
    ledger = await claim_turn(
        store,
        ctx,
        conversation_id=conversation_id,
        key=_KEY,
        content=_THREAD_BODY,
        stale_after_s=stale_after_s(Settings()),
    )
    assert isinstance(ledger, IdempotentTurnLedger)
    stream = await orchestrator.invoke(
        ctx,
        "slow",
        AgentRequest(conversation_id=conversation_id, input=_THREAD_BODY),
        ledger=ledger,
    )
    accepted = await stream.__anext__()
    await stream.__anext__()  # the first token
    await stream.aclose()  # the reader left

    repeat = _frames(_post(client, conversation_id, key=_KEY, stream=True).text)

    assert [name for name, _ in repeat] == ["accepted", "error"]
    assert repeat[0][1]["user_message_id"] == accepted.data["user_message_id"]
    assert repeat[1][1]["code"] == "agent.failed"
    assert repeat[1][1]["message_id"]  # the partial that was saved
    assert len(_RUNS) == 0  # `_SlowAgent` is not counted; nothing else ran


# -- tenancy, authorisation, authentication -----------------------------------
def test_another_workspace_cannot_reach_or_replay_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client, conversation_id, store, _ = _setup()
    _post(client, conversation_id, key=_KEY)
    calls = list(store.calls)

    async def _as_workspace_b(self: _FakeAuth, token: str) -> Principal:
        return Principal(
            workspace_id="018f0000-0000-7000-8000-0000000000w2",
            user_id="018f0000-0000-7000-8000-0000000000u2",
            roles=frozenset({"owner"}),
        )

    monkeypatch.setattr(_FakeAuth, "authenticate", _as_workspace_b)

    response = _post(client, conversation_id, key=_KEY)

    assert response.status_code == 404
    assert store.calls == calls  # the key store was not touched
    assert len(_RUNS) == 1


def test_a_viewer_is_refused_and_no_row_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    client, conversation_id, store, _ = _setup()

    async def _as_viewer(self: _FakeAuth, token: str) -> Principal:
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"viewer"}))

    monkeypatch.setattr(_FakeAuth, "authenticate", _as_viewer)

    response = _post(client, conversation_id, key=_KEY)

    assert response.status_code == 403
    assert response.json()["code"] == "authz.forbidden"
    assert store.rows == {}
    assert store.calls == []


def test_a_second_user_of_the_same_workspace_replays_the_first_users_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented scope (th-3 q-12): the key does not separate users."""
    client, conversation_id, _store, _ = _setup()
    first = _post(client, conversation_id, key=_KEY).json()

    async def _as_other_user(self: _FakeAuth, token: str) -> Principal:
        return Principal(
            workspace_id=_W1,
            user_id="018f0000-0000-7000-8000-0000000000u2",
            roles=frozenset({"owner"}),
        )

    monkeypatch.setattr(_FakeAuth, "authenticate", _as_other_user)

    second = _post(client, conversation_id, key=_KEY)

    assert second.status_code == 200
    assert second.json() == first
    assert len(_RUNS) == 1


@pytest.mark.parametrize("token", ["", "bad"])
def test_no_or_a_bad_token_is_401_and_the_store_is_untouched(token: str) -> None:
    client, conversation_id, store, _ = _setup()
    headers = {"Idempotency-Key": _KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": _THREAD_BODY},
    )

    assert response.status_code == 401
    assert store.calls == []


def test_the_new_log_lines_carry_neither_the_key_nor_the_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    client, conversation_id, _store, _ = _setup(agent_key="half")
    secret_key = "super-secret-retry-key"

    _post(client, conversation_id, key=secret_key, content={"text": "my private question"})
    _post(client, conversation_id, key=secret_key, content={"text": "my private question"})

    assert secret_key not in caplog.text
    assert "my private question" not in caplog.text
