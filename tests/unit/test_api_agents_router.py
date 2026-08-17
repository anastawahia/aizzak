"""ASGI tests for the Agents router (``app/api/v1/routers/agents.py``, 6.1-b).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with a real ``AgentOrchestrator`` + ``InMemoryAgentRegistry`` holding one tiny
in-test agent. What these pin, against 03 §1/§2/§3.1:

* ``GET /agents`` — the catalog in the ``API-04`` ``Page`` envelope
  (``next_cursor: null``, ``limit`` = count), manifests rendered as
  ``AgentOut`` with sorted permission/capability lists;
* ``GET /agents/{key}`` — one manifest bare; an unknown key ⇒ 404
  ``agent.unknown`` (03 §4's own code since 6.2, on both the read and the
  invoke path);
* ``POST /agents/{key}/invoke`` — the orchestrator's event stream as SSE
  (``event: token`` … ``event: final``), a pre-flight unknown-agent failure as
  a real 404 (not a broken stream);
* auth — the router-level bearer gate answers 401 ``auth.missing_token`` before
  any handler runs.

The in-test ``_EchoAgent`` declares NO ``chat`` capability, so the orchestrator
resolves no LLM for it and the fake resolver is never consulted on the happy
path — the router is exercised end to end without a provider.
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
# Spaces plan step 12 -- an invoke that opens a fresh thread names its space,
# and the seam behind it is a real one that can refuse an id it never had.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"

_ECHO_METADATA = AgentMetadata(
    key="echo",
    name="Echo",
    version="1.2.3",
    description="Echoes its input as one token then a final.",
    capabilities=frozenset(),  # no "chat" ⇒ the orchestrator resolves no LLM
    required_permissions=frozenset({"agents:invoke", "agents:read"}),
)


class _EchoAgent(BaseAgent):
    metadata = _ECHO_METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        text = req.input.get("text")
        payload = text if isinstance(text, str) else ""
        yield AgentEvent(type="token", data={"delta": payload})
        # `stream` is echoed so a test can pin that the router propagates the
        # request's flag into the AgentRequest (nothing downstream branches on
        # it yet, so this is the only observable of that propagation).
        yield AgentEvent(type="final", data={"text": payload, "stream": req.stream})


class _FakeLLM:
    """A no-op provider. Only its ``provider`` label is read (by the metering
    wrapper); ``complete``/``stream`` are never reached, because the sole test
    that resolves one targets an unknown agent that 404s at ``registry.create``
    before the agent runs."""

    provider = "fake"


class _FakeResolver:
    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        # Only reached for an UNKNOWN agent (no manifest ⇒ no capability skip);
        # a known no-chat agent never gets here.
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
        raise AssertionError("not exercised")


def _make_app_with_store(*, with_echo: bool = True) -> tuple[FastAPI, ConversationsStack]:
    registry = InMemoryAgentRegistry()
    # `_SPACE` is the ONE live space, so a body naming it opens a thread and a
    # body naming anything else is a 404 from the seam rather than a silent
    # accept — which is what makes `space_id` on the wire mean something.
    conversations = build_conversations(spaces=StubActiveSpaces(live={_SPACE}))
    if with_echo:
        registry.register(_ECHO_METADATA, _EchoAgent)
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),
            # 6.1-ج-3: invoke now opens a thread and writes both turns, so the
            # router tests run against a REAL persistence path, not a stub.
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
    )
    return app, conversations


def _make_app(*, with_echo: bool = True) -> FastAPI:
    app, _ = _make_app_with_store(with_echo=with_echo)
    return app


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# GET /agents                                                                 #
# --------------------------------------------------------------------------- #
def test_list_agents_wraps_in_page_envelope() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/agents", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {"next_cursor": None, "limit": 1}
    assert [a["key"] for a in body["data"]] == ["echo"]
    echo = body["data"][0]
    assert echo["name"] == "Echo"
    assert echo["version"] == "1.2.3"
    # frozensets rendered as SORTED lists (deterministic wire order)
    assert echo["capabilities"] == []
    assert echo["required_permissions"] == ["agents:invoke", "agents:read"]


def test_list_agents_empty_registry_is_empty_page() -> None:
    client = TestClient(_make_app(with_echo=False))
    response = client.get("/api/v1/agents", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_list_agents_requires_auth() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/agents")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "auth.missing_token"


# --------------------------------------------------------------------------- #
# GET /agents/{key}                                                           #
# --------------------------------------------------------------------------- #
def test_get_agent_returns_bare_manifest() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/v1/agents/echo", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    # A single resource is UNWRAPPED — no data/meta envelope (03 §0).
    assert "data" not in body and "meta" not in body
    assert body["key"] == "echo"
    assert body["required_permissions"] == ["agents:invoke", "agents:read"]


def test_get_unknown_agent_is_404_agent_unknown() -> None:
    """6.2 gave 03 §4's `agent.unknown` its site, so a client can tell "no
    such agent" from "no such conversation" on the same 404."""
    client = TestClient(_make_app())
    response = client.get("/api/v1/agents/nope", headers=_auth())
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "agent.unknown"
    assert body["correlation_id"]


# --------------------------------------------------------------------------- #
# POST /agents/{key}/invoke                                                    #
# --------------------------------------------------------------------------- #
def test_invoke_streams_sse_frames() -> None:
    client = TestClient(_make_app())
    response = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "مرحبا"}, "stream": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    # UTF-8 verbatim (ensure_ascii=False), token then final, contract framing.
    assert "event: token" in text
    assert 'data: {"delta":"مرحبا"}' in text
    assert "event: final" in text
    # token precedes final on the wire.
    assert text.index("event: token") < text.index("event: final")


def test_invoke_streams_on_the_accept_header_alone() -> None:
    # 03 §3.1 keys SSE off `Accept`, 03 §2 off the body flag: either alone is
    # enough, so a client of one contract is never wrong under the other.
    client = TestClient(_make_app())
    response = client.post(
        "/api/v1/agents/echo/invoke",
        headers={**_auth(), "Accept": "text/event-stream"},
        json={"space_id": _SPACE, "input": {"text": "x"}},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_invoke_propagates_stream_flag() -> None:
    # The router must carry the request's `stream` into the AgentRequest; the
    # echo agent mirrors it back in its final frame, in both directions. The
    # `false` case is streamed via `Accept` so the frame is observable while
    # `body.stream` stays false.
    client = TestClient(_make_app())
    on = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "x"}, "stream": True},
    )
    assert '"stream":true' in on.text
    off = client.post(
        "/api/v1/agents/echo/invoke",
        headers={**_auth(), "Accept": "text/event-stream"},
        json={"space_id": _SPACE, "input": {"text": "x"}},
    )
    assert '"stream":false' in off.text


def test_invoke_unknown_agent_is_preflight_404() -> None:
    # The unknown key fails pre-flight (registry.create), so the status is still
    # open and the app answers a real 404 — never a 200 SSE body.
    client = TestClient(_make_app())
    response = client.post(
        "/api/v1/agents/nope/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "x"}},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "agent.unknown"


def test_invoke_requires_auth() -> None:
    client = TestClient(_make_app())
    response = client.post(
        "/api/v1/agents/echo/invoke", json={"space_id": _SPACE, "input": {"text": "x"}}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


def test_invoke_missing_input_is_422() -> None:
    client = TestClient(_make_app())
    response = client.post("/api/v1/agents/echo/invoke", headers=_auth(), json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "common.validation_error"
    assert any(err["field"] == "input" for err in body["errors"])


# --------------------------------------------------------------------------- #
# POST /agents/{key}/invoke — the collected reply (6.1-ج-3)                    #
# --------------------------------------------------------------------------- #
def test_invoke_without_stream_returns_the_persisted_turn() -> None:
    app, stack = _make_app_with_store()
    client = TestClient(app)

    response = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    # Every field is read from something that actually happened: the thread was
    # opened, both turns were written, the split comes from the meter.
    conversation_id = body["conversation_id"]
    assert conversation_id in stack.repository.rows
    assert body["message"]["role"] == "assistant"
    assert body["message"]["seq"] == 2
    assert body["message"]["content"] == {"text": "hello", "attachments": []}
    assert body["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
    stored = stack.repository.messages[conversation_id]
    assert [(m.role.value, m.content.text) for m in stored] == [
        ("user", "hello"),
        ("assistant", "hello"),
    ]


def test_invoke_continues_the_thread_it_is_given() -> None:
    app, stack = _make_app_with_store()
    client = TestClient(app)
    opened = client.post(
        "/api/v1/conversations", headers=_auth(), json={"space_id": _SPACE, "agent_key": "echo"}
    ).json()

    first = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "a"}, "conversation_id": opened["id"]},
    ).json()
    second = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "b"}, "conversation_id": opened["id"]},
    ).json()

    # No new thread per call, and `seq` keeps climbing across turns.
    assert first["conversation_id"] == second["conversation_id"] == opened["id"]
    assert [first["message"]["seq"], second["message"]["seq"]] == [2, 4]
    assert len(stack.repository.rows) == 1


def test_invoke_with_an_unknown_conversation_is_a_preflight_404() -> None:
    app, stack = _make_app_with_store()
    client = TestClient(app)
    response = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "x"}, "conversation_id": "missing"},
    )
    assert response.status_code == 404
    # Nothing ran and nothing was written.
    assert stack.repository.messages == {}


def test_streamed_final_frame_carries_the_persisted_message_and_usage() -> None:
    # 03 §3.1's final frame: the platform's `message_id`/`content`/`usage` are
    # ADDED beside the agent's own keys, not instead of them.
    app, _ = _make_app_with_store()
    client = TestClient(app)
    text = client.post(
        "/api/v1/agents/echo/invoke",
        headers=_auth(),
        json={"space_id": _SPACE, "input": {"text": "hi"}, "stream": True},
    ).text
    assert '"message_id"' in text
    assert '"prompt_tokens"' in text
    assert '"stream":true' in text  # the agent's own key survived the enrichment


def test_invoke_of_an_unknown_agent_leaves_no_orphan_thread() -> None:
    # Order matters: the agent is created (404s) BEFORE the thread is opened, so
    # a bad key must not leave a conversation and a user message behind.
    app, stack = _make_app_with_store()
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/agents/nope/invoke",
            headers=_auth(),
            json={"space_id": _SPACE, "input": {"text": "x"}},
        ).status_code
        == 404
    )
    assert stack.repository.rows == {}
    assert stack.repository.messages == {}
