"""ASGI tests for the Models router (``app/api/v1/routers/models.py``).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``. The
catalogue under test is the REAL ``SettingsProviderResolver`` — not a stub —
narrowed to ``ModelCatalog`` at the ``ApiServices`` boundary exactly as
production narrows it. That is the point: the route must publish the table
``resolve_llm`` would actually route against, so a fake catalogue here would
test the shape of the DTO and nothing else.

What these pin, against 03 §1/§2 and 02 §3.5.1:

* ``GET /models`` — every configured LLM route, in the ``API-04`` ``Page``
  envelope (``next_cursor: null``, ``limit`` = count), ordered by capability;
* a route whose provider has no credential is LISTED with ``available: false``
  rather than dropped — the difference between "you have no key" and "this
  platform cannot do that" has to survive to the client;
* an empty routing table is an empty page, and an UNWIRED catalogue is a 500 —
  the two must not be confusable, or a wiring bug reads as an operator choice;
* no response field carries a key, on any path;
* auth — the router-level bearer gate answers 401 before any handler runs.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import PROBLEM_MEDIA_TYPE, create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, UnauthorizedError
from app.framework.providers import ModelCatalog, SettingsProviderResolver
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.types import Json
from app.framework.workflows import InMemoryWorkflowRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_FILES_MEDIA = build_files_media()
_CREDENTIALS = build_credentials()
_WORKSPACE_USAGE = build_workspace_usage()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"

_TWO_ROUTES: Json = {
    "llm": {
        # Deliberately NOT in sorted order, so the route's ordering guarantee
        # is observable rather than accidentally satisfied by insertion order.
        "rag": {"provider": "ollama", "model": "gemma3:1b"},
        "default": {"provider": "openai", "model": "gpt-test"},
    }
}


class _FakeLLM:
    """Structurally an ``LLMProvider`` for wiring purposes only; the catalogue
    returns table rows and never touches an adapter, so nothing here is
    called."""

    def __init__(self, name: str) -> None:
        self.provider = name


class _Key:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key


class _KeyPresent:
    async def resolve(self, ctx: ExecutionContext, provider: str) -> _Key:
        return _Key("sk-must-never-reach-the-wire")


class _KeyMissing:
    """The credentials module's real no-key outcome (03 §4)."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> _Key:
        raise ConflictError(
            f"no active credential for provider {provider}",
            code="credentials.none_available",
        )


def _catalog(routing: Json, *, keyed: bool = True) -> ModelCatalog:
    return SettingsProviderResolver(
        routing=routing,
        llm_providers={"ollama": _FakeLLM("ollama"), "openai": _FakeLLM("openai")},
        embedding_providers={},
        image_providers={},
        key_resolver=_KeyPresent() if keyed else _KeyMissing(),
        keyless_providers=frozenset({"ollama"}),
    )


class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"member"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        raise AssertionError("not exercised")


def _make_app(models: ModelCatalog | None) -> FastAPI:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    services = ApiServices(
        settings=Settings(),
        orchestrator=AgentOrchestrator(
            OrchestratorDependencies(
                agents=registry,
                executor=AgentLifecycleExecutor(),
                providers=_catalog({}),
                conversations=conversations.service,
                authorization=build_authorization(),
            )
        ),
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
        models=models,
    )
    return create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# GET /models                                                                 #
# --------------------------------------------------------------------------- #
def test_list_models_returns_the_configured_routes_in_the_page_envelope() -> None:
    client = TestClient(_make_app(_catalog(_TWO_ROUTES)))
    response = client.get("/api/v1/models", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {"next_cursor": None, "limit": 2}
    # Sorted by capability, not by the config's insertion order.
    assert body["data"] == [
        {"capability": "default", "provider": "openai", "model": "gpt-test", "available": True},
        {"capability": "rag", "provider": "ollama", "model": "gemma3:1b", "available": True},
    ]


def test_a_route_without_a_credential_is_listed_unavailable_not_omitted() -> None:
    """The keyed provider loses its key; the keyless one keeps working. Both
    stay on the wire, because a UI that silently dropped the first could not
    tell the user whether to add a key or give up."""
    client = TestClient(_make_app(_catalog(_TWO_ROUTES, keyed=False)))
    body = client.get("/api/v1/models", headers=_auth()).json()
    assert [(entry["capability"], entry["available"]) for entry in body["data"]] == [
        ("default", False),
        ("rag", True),
    ]


def test_no_response_field_ever_carries_a_key() -> None:
    """The key resolver hands out a real-looking secret on every call; it must
    appear nowhere in the serialised body, under any field name."""
    client = TestClient(_make_app(_catalog(_TWO_ROUTES)))
    response = client.get("/api/v1/models", headers=_auth())
    assert "sk-must-never-reach-the-wire" not in response.text
    assert set(response.json()["data"][0]) == {"capability", "provider", "model", "available"}


def test_an_empty_routing_table_is_an_empty_page_not_an_error() -> None:
    """ "The operator configured no routes" is a real state with a real
    answer — and the one the unwired case below must not be able to mimic."""
    client = TestClient(_make_app(_catalog({})))
    response = client.get("/api/v1/models", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_an_unwired_catalog_is_an_internal_error_not_an_empty_page() -> None:
    """A hermetic application that forgot to wire the catalogue must fail
    loudly. Answering 200 with an empty list would let a wiring bug pass for
    an operator's configuration choice."""
    response = TestClient(_make_app(None)).get("/api/v1/models", headers=_auth())
    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["code"] == "common.internal"


def test_list_models_requires_a_bearer_token() -> None:
    response = TestClient(_make_app(_catalog(_TWO_ROUTES))).get("/api/v1/models")
    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


def test_a_bad_token_is_rejected_before_the_catalog_is_touched() -> None:
    client = TestClient(_make_app(_catalog(_TWO_ROUTES)))
    response = client.get("/api/v1/models", headers=_auth("bad"))
    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"
