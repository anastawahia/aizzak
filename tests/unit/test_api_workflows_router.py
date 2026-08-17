"""ASGI tests for the Workflows router (6.1-د-2).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``, a real
``AgentOrchestrator``, a real ``SequentialWorkflowEngine`` and a real
``InMemoryWorkflowRegistry`` — only the conversations store and the
authenticator are fakes. What they pin, against `03 §1/§2`:

* ``GET /workflows`` — the catalog in the ``API-04`` envelope, and the honest
  empty page the production catalog actually returns today (§3.42);
* ``POST /workflows/{key}/run`` — both shapes, the pre-flight 404 for an
  unknown key, and the run's transcript landing in its own D-12 thread;
* ``GET /workflows/runs/{id}`` — the run read back AS its conversation,
  including the two refusals (not a run · not a workflow thread) and the
  deliberately conservative status derivation.

The in-test agents declare no ``chat`` capability, so no LLM is ever resolved:
the whole workflow path is exercised without a provider.
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
from app.framework.workflows import (
    InMemoryWorkflowRegistry,
    WorkflowDefinition,
    WorkflowStep,
)
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
# Spaces plan step 12 -- a run always opens its own D-12 thread, so
# `WorkflowRunIn.space_id` is required with no `None` case at all.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"
_AUTH = {"Authorization": f"Bearer {_GOOD}"}


def _metadata(key: str) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=key.title(),
        version="1.0.0",
        description="test agent",
        capabilities=frozenset(),  # no "chat" ⇒ no LLM resolved
        required_permissions=frozenset(),
    )


class _StepA(BaseAgent):
    metadata = _metadata("step_a")

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="final", data={"text": f"a:{req.input.get('text', '')}"})


class _StepB(_StepA):
    metadata = _metadata("step_b")

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="final", data={"text": "b:done"})


class _Exploding(BaseAgent):
    metadata = _metadata("boom")

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="token", data={"delta": "…"})
        raise RuntimeError("step blew up")


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


class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"member"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        raise AssertionError("not exercised")


def _definition(
    *agent_keys: str, key: str = "wf", name: str = "Test Pipeline"
) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key,
        name=name,
        steps=tuple(WorkflowStep(agent_key=k, input_map={}) for k in agent_keys),
    )


def _make_app(
    *,
    definitions: tuple[WorkflowDefinition, ...] = (),
    conversations: ConversationsStack | None = None,
) -> tuple[FastAPI, ConversationsStack]:
    """The app plus the store behind it — the run's transcript is asserted on
    the wire AND in what was actually persisted.

    ``conversations`` can be supplied so a SECOND app can be built over the
    SAME store with a different catalog: that is how a run whose definition was
    retired is exercised.
    """
    agents = InMemoryAgentRegistry()
    for agent_cls in (_StepA, _StepB, _Exploding):
        agents.register(agent_cls.metadata, agent_cls)
    workflows = InMemoryWorkflowRegistry()
    for definition in definitions:
        workflows.register(definition)
    stack = (
        conversations
        if conversations is not None
        else build_conversations(spaces=StubActiveSpaces(live={_SPACE}))
    )
    orchestrator = AgentOrchestrator(
        OrchestratorDependencies(
            agents=agents,
            executor=AgentLifecycleExecutor(),
            providers=_FakeResolver(),
            workflows=workflows,
            conversations=stack.service,
            authorization=build_authorization(),
        )
    )
    services = ApiServices(
        settings=Settings(),
        orchestrator=orchestrator,
        hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
        agents=agents,
        conversations=stack.use_cases,
        workflows=workflows,
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
    return app, stack


# --------------------------------------------------------------------------- #
# GET /workflows — the catalog                                                #
# --------------------------------------------------------------------------- #
def test_the_catalog_is_wrapped_in_the_api_04_envelope() -> None:
    app, _ = _make_app(definitions=(_definition("step_a", "step_b"),))

    with TestClient(app) as client:
        response = client.get("/api/v1/workflows", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {"next_cursor": None, "limit": 1}
    # `steps` is the ordered agent keys — the only part of a step a client can
    # act on; `input_map` is internal plumbing and stays internal.
    assert body["data"] == [{"key": "wf", "name": "Test Pipeline", "steps": ["step_a", "step_b"]}]


def test_an_empty_catalog_lists_nothing_rather_than_a_placeholder() -> None:
    """The PRODUCTION state today (§3.42): the example workflow does not wire
    up, so the catalog is empty by a product decision — and the endpoint says
    so plainly instead of inventing an entry to look alive."""
    app, _ = _make_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/workflows", headers=_AUTH)

    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_the_router_refuses_an_unauthenticated_request() -> None:
    """``GET /workflows`` builds no ``ExecutionContext``, so the router-level
    bearer dependency is the ONLY thing standing between it and the world."""
    app, _ = _make_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/workflows")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


# --------------------------------------------------------------------------- #
# POST /workflows/{key}/run                                                   #
# --------------------------------------------------------------------------- #
def test_running_an_unknown_workflow_is_a_pre_flight_404() -> None:
    """The registry's own ``workflow.unknown`` — the catalog gives "no such
    workflow" its own code so a client can tell it from "no such run"."""
    app, stack = _make_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/nope/run", json={"space_id": _SPACE, "input": {}}, headers=_AUTH
        )

    assert response.status_code == 404
    assert response.json()["code"] == "workflow.unknown"
    # Pre-flight means pre-flight: no thread was opened for a run that never
    # started.
    assert stack.repository.rows == {}


def test_a_collected_run_answers_with_its_thread_and_status() -> None:
    app, stack = _make_app(definitions=(_definition("step_a", "step_b"),))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": _SPACE, "input": {"text": "hi"}},
            headers=_AUTH,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    # v1: the run IS its conversation, so the two ids are one id — not a
    # fabricated identifier that `GET /runs/{id}` could never resolve.
    assert body["run_id"] == body["conversation_id"]
    # And the transcript really is in that thread: the input, then one turn
    # per completed step (6.1-د-1).
    messages = stack.repository.messages[body["conversation_id"]]
    assert [message.role.value for message in messages] == ["user", "assistant", "assistant"]
    assert [message.content.text for message in messages] == ["hi", "a:hi", "b:done"]


def test_a_failed_run_answers_200_with_the_failure_in_its_status() -> None:
    """Unlike ``invoke_once``, a failed run is NOT a problem response: the DTO
    has a ``status`` field to carry the outcome, and the caller still needs the
    thread holding the steps that completed — and were billed."""
    app, stack = _make_app(definitions=(_definition("step_a", "boom"),))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/wf/run", json={"space_id": _SPACE, "input": {}}, headers=_AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    messages = stack.repository.messages[body["conversation_id"]]
    assert [message.role.value for message in messages] == ["user", "assistant"]


def test_a_run_streams_sse_when_the_body_asks_for_it() -> None:
    app, _ = _make_app(definitions=(_definition("step_a"),))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": _SPACE, "input": {"text": "x"}, "stream": True},
            headers=_AUTH,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: final" in response.text
    assert "a:x" in response.text


def test_a_run_streams_on_the_accept_header_alone() -> None:
    """`03 §3.1` keys SSE off ``Accept``; honouring only the body flag would
    make a correct client of that contract wrong."""
    app, _ = _make_app(definitions=(_definition("step_a"),))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": _SPACE, "input": {"text": "x"}},
            headers={**_AUTH, "Accept": "text/event-stream"},
        )

    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: final" in response.text


# --------------------------------------------------------------------------- #
# Idempotency-Key (3.79)                                                       #
# --------------------------------------------------------------------------- #
def test_a_repeated_collected_run_replays_the_first_run_instead_of_starting_a_second() -> None:
    """``openapi.yaml`` declares the header on ``runWorkflow`` and this router
    did not mention it at all until 3.79 — on the operation billed PER STEP.
    Same key + same body ⇒ the first ``WorkflowRunOut`` (same ``run_id``), and
    the store holds one conversation, not two."""
    app, stack = _make_app(definitions=(_definition("step_a"),))
    headers = {**_AUTH, "Idempotency-Key": "wf-1"}
    body = {"space_id": _SPACE, "input": {"text": "hi"}}

    with TestClient(app) as client:
        first = client.post("/api/v1/workflows/wf/run", json=body, headers=headers)
        second = client.post("/api/v1/workflows/wf/run", json=body, headers=headers)

    assert first.status_code == 200
    assert second.json() == first.json()
    assert len(stack.repository.rows) == 1


def test_a_repeated_run_with_a_different_input_is_a_conflict() -> None:
    app, stack = _make_app(definitions=(_definition("step_a"),))
    headers = {**_AUTH, "Idempotency-Key": "wf-2"}

    with TestClient(app) as client:
        client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": _SPACE, "input": {"text": "a"}},
            headers=headers,
        )
        response = client.post(
            "/api/v1/workflows/wf/run",
            json={"space_id": _SPACE, "input": {"text": "b"}},
            headers=headers,
        )

    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"
    assert len(stack.repository.rows) == 1


def test_one_key_on_two_different_workflows_does_not_collide() -> None:
    """The endpoint half of the scope includes the workflow ``key``, so a
    client counter reused across two workflows runs both."""
    app, stack = _make_app(
        definitions=(_definition("step_a"), _definition("step_a", key="wf2", name="Second"))
    )
    headers = {**_AUTH, "Idempotency-Key": "shared"}

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/workflows/wf/run", json={"space_id": _SPACE, "input": {}}, headers=headers
        )
        second = client.post(
            "/api/v1/workflows/wf2/run", json={"space_id": _SPACE, "input": {}}, headers=headers
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run_id"] != second.json()["run_id"]
    assert len(stack.repository.rows) == 2


def test_the_streamed_answer_is_outside_the_ledger_by_design() -> None:
    """A stated LIMIT, not an omission (``routers/workflows.py``): an SSE
    stream is consumed as it is produced, so there is no response body to
    store and no honest way to replay one. The key must therefore neither be
    claimed nor block the stream — a client asking for events gets events."""
    app, _ = _make_app(definitions=(_definition("step_a"),))
    headers = {**_AUTH, "Idempotency-Key": "wf-3"}
    body = {"space_id": _SPACE, "input": {"text": "x"}, "stream": True}

    with TestClient(app) as client:
        first = client.post("/api/v1/workflows/wf/run", json=body, headers=headers)
        second = client.post("/api/v1/workflows/wf/run", json=body, headers=headers)

    assert first.headers["content-type"].startswith("text/event-stream")
    assert second.headers["content-type"].startswith("text/event-stream")
    assert "event: final" in second.text
    assert app.state.services.idempotency.claims == []


# --------------------------------------------------------------------------- #
# GET /workflows/runs/{id}                                                    #
# --------------------------------------------------------------------------- #
def test_a_finished_run_reads_back_as_completed() -> None:
    app, _ = _make_app(definitions=(_definition("step_a", "step_b"),))

    with TestClient(app) as client:
        run_id = client.post(
            "/api/v1/workflows/wf/run", json={"space_id": _SPACE, "input": {}}, headers=_AUTH
        ).json()["run_id"]
        response = client.get(f"/api/v1/workflows/runs/{run_id}", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "conversation_id": run_id, "status": "completed"}


def test_a_partial_run_reads_back_as_unknown_rather_than_a_guess() -> None:
    """One of two steps completed. Storage cannot tell "still running" from
    "failed" from "abandoned" — nothing stores a run's state — so the read says
    ``unknown`` instead of dressing a guess as a fact."""
    app, _ = _make_app(definitions=(_definition("step_a", "boom"),))

    with TestClient(app) as client:
        run_id = client.post(
            "/api/v1/workflows/wf/run", json={"space_id": _SPACE, "input": {}}, headers=_AUTH
        ).json()["run_id"]
        response = client.get(f"/api/v1/workflows/runs/{run_id}", headers=_AUTH)

    assert response.json()["status"] == "unknown"


def test_a_retired_definition_leaves_its_old_runs_readable() -> None:
    """A workflow removed from the catalog since the run happened must not turn
    reading that run into a 404 about the definition — hence the lenient scan
    rather than ``registry.get``."""
    app, stack = _make_app(definitions=(_definition("step_a"),))
    with TestClient(app) as client:
        run_id = client.post(
            "/api/v1/workflows/wf/run", json={"space_id": _SPACE, "input": {}}, headers=_AUTH
        ).json()["run_id"]

    retired, _ = _make_app(conversations=stack)  # same store, empty catalog
    with TestClient(retired) as client:
        response = client.get(f"/api/v1/workflows/runs/{run_id}", headers=_AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_an_agent_conversation_is_not_a_workflow_run() -> None:
    """A perfectly valid resource — just not a run. Answering with a run-shaped
    body would be a lie about what the id names."""
    app, _ = _make_app(definitions=(_definition("step_a"),))

    with TestClient(app) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={"space_id": _SPACE, "agent_key": "step_a"}, headers=_AUTH
        ).json()["id"]
        response = client.get(f"/api/v1/workflows/runs/{conversation_id}", headers=_AUTH)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


def test_an_unknown_run_is_a_404() -> None:
    app, _ = _make_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/workflows/runs/018f0000-0000-7000-8000-00000000dead", headers=_AUTH
        )

    assert response.status_code == 404
