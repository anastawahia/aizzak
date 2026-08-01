"""Protocol tests for the interactive WebSocket endpoint
(``api/v1/websocket/streaming.py``, 5.3-ج) — hermetic, over Starlette's
TestClient against a REAL ``AgentOrchestrator`` driving real (test) agents.

What these pin, against 03 §3.2 clause by clause: auth verified BEFORE accept
for a query token (bad ⇒ no socket at all) and the first-message ``auth``
alternative; ``ping``/``pong``; ``invoke`` streaming the contract's flattened
shapes with ``conversation_id`` on every event; a pre-flight failure arriving
as an ``error`` problem WITHOUT costing the connection; ``cancel`` stopping a
running stream; the 64KB close (1009); the per-user connection cap; the
``notification`` push routed by workspace; and hub cleanup on disconnect.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.v1.websocket.streaming import WsPrincipal, create_ws_router
from app.framework.agent_runtime.base_agent import (
    AgentEvent,
    AgentRequest,
    BaseAgent,
)
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Limits
from app.framework.streaming import ConnectionHub
from tests.unit.support_access import build_authorization
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

_W1 = "018f0000-0000-7000-8000-00000000w001"
_W2 = "018f0000-0000-7000-8000-00000000w002"
_U1 = "018f0000-0000-7000-8000-00000000u001"
_U2 = "018f0000-0000-7000-8000-00000000u002"

# token -> principal; anything else is refused.
_TOKENS = {
    "t-one": WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"member"})),
    "t-two": WsPrincipal(workspace_id=_W2, user_id=_U2, roles=frozenset({"member"})),
    # 6.4-ب: a viewer — the role the matrix grants `agents:read` and withholds
    # `agents:invoke` from (05 §1.3). It exists to prove the socket enforces
    # what the routes enforce.
    "t-viewer": WsPrincipal(workspace_id=_W1, user_id=_U2, roles=frozenset({"viewer"})),
}


class _MapAuthenticator:
    async def authenticate(self, token: str) -> WsPrincipal:
        principal = _TOKENS.get(token)
        if principal is None:
            raise UnauthorizedError("bad token")
        return principal


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
    def __init__(self) -> None:
        self.llm = _FakeLLM()

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        return self.llm, ResolvedProvider(provider="fake", model="fake-model", api_key="k")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


def _metadata(key: str) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=key,
        version="1.0.0",
        description="test agent",
        capabilities=frozenset({"chat"}),
        required_permissions=frozenset(),
    )


class _Streamer(BaseAgent):
    metadata = _metadata("streamer")

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "مرحب"})
        yield AgentEvent(type="final", data={"content": {"text": "مرحبا"}})


class _Staller(BaseAgent):
    metadata = _metadata("staller")

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "a"})
        # Short enough that the cancel test can PROVE the run died: were the
        # cancel a no-op, this final would surface within the test's window.
        await asyncio.sleep(0.5)
        yield AgentEvent(type="final", data={})


def _build(
    *,
    cap: int = 5,
    limits: Limits | None = None,
) -> tuple[FastAPI, ConnectionHub]:
    registry = InMemoryAgentRegistry()
    registry.register(_Streamer.metadata, _Streamer)
    registry.register(_Staller.metadata, _Staller)
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=registry,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),  # type: ignore[arg-type]
            authorization=build_authorization(),
        )
    )
    hub = ConnectionHub(max_connections_per_user=cap, registry=InMemoryWsConnectionRegistry())
    used_limits = limits if limits is not None else Limits()
    app = FastAPI()
    app.include_router(
        create_ws_router(
            authenticator=_MapAuthenticator(),
            orchestrator=orchestrator,
            hub=hub,
            limits=used_limits,
            authorization=build_authorization(),
        ),
        prefix="/api/v1",
    )

    # Test-only trigger so a notification can be pushed FROM THE APP'S OWN
    # LOOP while the sync TestClient holds open sockets (5.3-د wires the real
    # producer — the cg.notify bridge — into this same hub method).
    @app.post("/test/notify/{workspace_id}")
    async def _notify(workspace_id: str) -> dict[str, bool]:
        await hub.notify(
            workspace_id, "knowledge.document.indexed.v1", {"document_id": "d1", "chunk_count": 3}
        )
        return {"ok": True}

    return app, hub


# --------------------------------------------------------------------------- #
# Handshake (03 §3.2: auth before accept)                                     #
# --------------------------------------------------------------------------- #
def test_a_valid_query_token_opens_the_socket() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_a_bad_query_token_is_refused_before_accept() -> None:
    """The upgrade itself is rejected — the client never holds a socket, which
    is exactly what "يتحقّق قبل القبول" buys."""
    app, _ = _build()
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/v1/ws?token=bogus"),
    ):
        pass


def test_first_message_auth_admits_the_tokenless_handshake() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": "t-one"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_a_first_message_that_is_not_auth_closes_1008() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_a_bad_token_in_the_auth_message_closes_1008() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": "bogus"})
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 1008


# --------------------------------------------------------------------------- #
# The invoke stream (contract shapes)                                         #
# --------------------------------------------------------------------------- #
def test_invoke_streams_the_flattened_contract_shapes() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json(
            {"type": "invoke", "agent_key": "streamer", "conversation_id": "c-1", "input": {}}
        )

        assert ws.receive_json() == {"type": "token", "conversation_id": "c-1", "delta": "مرحب"}
        final = ws.receive_json()

    assert final["type"] == "final"
    assert final["conversation_id"] == "c-1"
    assert final["content"] == {"text": "مرحبا"}


def test_a_preflight_failure_is_an_error_message_not_a_dead_socket() -> None:
    """Unknown agent ⇒ the orchestrator raises pre-flight; over a socket that
    becomes an in-band problem — and the CONNECTION keeps working (unlike
    HTTP, one bad invoke must not cost the client its channel)."""
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json({"type": "invoke", "agent_key": "nope", "conversation_id": "c-2", "input": {}})

        message = ws.receive_json()
        assert message["type"] == "error"
        assert message["conversation_id"] == "c-2"
        assert message["problem"]["status"] == 404
        assert message["problem"]["code"] == "agent.unknown"
        assert message["problem"]["correlation_id"]

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_cancel_stops_a_running_stream() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json(
            {"type": "invoke", "agent_key": "staller", "conversation_id": "c-3", "input": {}}
        )
        assert ws.receive_json()["type"] == "token"  # the run is live mid-stream

        ws.send_json({"type": "cancel", "conversation_id": "c-3"})
        ws.send_json({"type": "ping"})

        # The VERY NEXT frame is the pong: the cancelled run emitted nothing
        # further (no `final`), yet the connection is fully alive.
        assert ws.receive_json() == {"type": "pong"}

        # And the run is genuinely DEAD, not merely quiet: wait out the
        # staller's own delay — a survived run's `final` would now precede
        # this second pong on the wire.
        time.sleep(0.8)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_malformed_json_and_unknown_types_answer_with_validation_problems() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_text("{not json")
        first = ws.receive_json()
        ws.send_json({"type": "mystery"})
        second = ws.receive_json()

    for message in (first, second):
        assert message["type"] == "error"
        assert message["problem"]["code"] == "common.validation_error"
        assert message["problem"]["status"] == 422
        # 6.2-ب: a protocol-level refusal happens before any `ExecutionContext`
        # exists, so there is no run correlation id to borrow — but the field
        # is REQUIRED by `components.schemas.Problem` and 03 §4 says every
        # error carries one, so the endpoint mints one per problem. The two
        # refusals get DIFFERENT ids: the point of the field is that an
        # operator can find THIS event in the log.
        assert message["problem"]["correlation_id"]
    assert first["problem"]["correlation_id"] != second["problem"]["correlation_id"]


def test_invoke_with_missing_fields_is_a_validation_problem() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json({"type": "invoke", "conversation_id": "c-4"})  # no agent_key/input
        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["conversation_id"] == "c-4"
    assert message["problem"]["code"] == "common.validation_error"


# --------------------------------------------------------------------------- #
# Limits (07 §4)                                                              #
# --------------------------------------------------------------------------- #
def test_an_oversize_message_closes_1009() -> None:
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_text("x" * (Limits().ws_message_bytes + 1))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 1009


def test_the_connection_cap_refuses_the_next_socket_with_1008() -> None:
    app, _ = _build(cap=1)
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as first:
        first.send_json({"type": "ping"})
        assert first.receive_json() == {"type": "pong"}

        with (
            client.websocket_connect("/api/v1/ws?token=t-one") as second,
            pytest.raises(WebSocketDisconnect) as excinfo,
        ):
            second.receive_json()
        assert excinfo.value.code == 1008


def test_disconnect_frees_the_user_slot() -> None:
    app, hub = _build(cap=1)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws?token=t-one") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        # The endpoint's finally has run by the time the context exits.
        assert hub.user_connection_count(_U1) == 0
        # And the slot is genuinely reusable.
        with client.websocket_connect("/api/v1/ws?token=t-one") as again:
            again.send_json({"type": "ping"})
            assert again.receive_json() == {"type": "pong"}


# --------------------------------------------------------------------------- #
# Notifications (03 §3.2's bridge outlet — the hub's routing over real WS)    #
# --------------------------------------------------------------------------- #
def test_notifications_reach_only_the_events_workspace() -> None:
    app, _ = _build()
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/v1/ws?token=t-one") as mine,
        client.websocket_connect("/api/v1/ws?token=t-two") as theirs,
    ):
        assert client.post(f"/test/notify/{_W1}").status_code == 200

        assert mine.receive_json() == {
            "type": "notification",
            "event": "knowledge.document.indexed.v1",
            "data": {"document_id": "d1", "chunk_count": 3},
        }
        # The other workspace's socket got NOTHING queued before this pong.
        theirs.send_json({"type": "ping"})
        assert theirs.receive_json() == {"type": "pong"}


# --------------------------------------------------------------------------- #
# Authorization over the socket (6.4-ب)                                       #
# --------------------------------------------------------------------------- #
def test_a_viewer_cannot_invoke_over_the_socket() -> None:
    """The bypass this step closes.

    RBAC guards are FastAPI dependencies and a WebSocket has no route, so
    until 6.4-ب every permission enforced on ``POST /agents/{key}/invoke`` was
    enforced on nothing at all here — a client that could open a socket could
    run any agent whatever its role. The refusal is an in-band problem, not a
    close: the same rule as every other pre-flight failure over this
    transport, since one refused invoke must not cost the client its channel.
    """
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-viewer") as ws:
        ws.send_json(
            {"type": "invoke", "agent_key": "streamer", "conversation_id": "c-9", "input": {}}
        )

        message = ws.receive_json()
        assert message["type"] == "error"
        assert message["conversation_id"] == "c-9"
        assert message["problem"]["code"] == "authz.forbidden"
        assert message["problem"]["detail"] == "missing permission: agents:invoke"

        # The connection survives it.
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_a_member_still_invokes_over_the_socket() -> None:
    """The other direction — a guard that refused everyone would pass the test
    above and break the endpoint."""
    app, _ = _build()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws?token=t-one") as ws:
        ws.send_json(
            {"type": "invoke", "agent_key": "streamer", "conversation_id": "c-10", "input": {}}
        )
        assert ws.receive_json()["type"] == "token"
