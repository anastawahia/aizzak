"""ASGI tests for the Credentials router (6.1-و-2).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory credentials stack (``support_credentials``) — the
same single-instance wiring the Composition Root builds, minus Postgres and
Vault. What these pin, against 03 §1/§2 and 06 §3:

* **the secret never comes back** — not in the create response, not in the
  listing, not anywhere in either body's raw text, while the plaintext IS
  proven to have reached the encryptor and the stored ciphertext is proven
  not to contain it;
* the listing's scoping: another tenant's rows and PLATFORM rows are both
  absent, revoked rows are present (revocation is a status, not a deletion);
* ``scope='platform'`` refused with 403 and nothing stored — a real scope
  that is not a tenant's to create;
* the catalog codes: ``credentials.provider_unknown``/422 for an unknown
  provider, ``common.conflict``/409 for a second active key;
* ``DELETE`` revoking rather than erasing, idempotently, and 404 for a
  credential this tenant cannot see.
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
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.credentials.domain.value_objects import CredentialScope, CredentialStatus
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import CredentialsStack, build_credentials, seed_credential
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_FILES_MEDIA = build_files_media()
_WORKSPACE_USAGE = build_workspace_usage()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_W2 = "018f0000-0000-7000-8000-0000000000w2"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"
_RAW_SECRET = "sk-super-secret-9876"


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


def _make_app() -> tuple[FastAPI, CredentialsStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_credentials()
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
        credentials=stack.credentials,
        knowledge=_KNOWLEDGE.knowledge,
        integrations=_INTEGRATIONS.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
    )
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    return app, stack


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_body(
    *,
    provider: str = "openai",
    scope: str = "user",
    secret: str = _RAW_SECRET,
    label: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {"provider": provider, "scope": scope, "secret": secret}
    if label is not None:
        body["label"] = label
    return body


# --------------------------------------------------------------------------- #
# auth                                                                        #
# --------------------------------------------------------------------------- #
def test_every_route_refuses_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        assert client.get("/api/v1/credentials").status_code == 401
        assert client.post("/api/v1/credentials", json=_create_body()).status_code == 401
        assert client.delete("/api/v1/credentials/whatever").status_code == 401


# --------------------------------------------------------------------------- #
# POST — the secret goes in and never comes back                              #
# --------------------------------------------------------------------------- #
def test_create_stores_the_key_encrypted_and_never_echoes_it() -> None:
    """The one assertion this whole router exists to make good on: 03 §2's
    «لا يُعاد السرّ أبداً». Checked against the RAW response text, not the
    parsed model, so a stray field could not slip past a key lookup."""
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/credentials", json=_create_body(), headers=_auth())
    assert response.status_code == 201
    assert _RAW_SECRET not in response.text

    body = response.json()
    assert body["provider"] == "openai"
    assert body["scope"] == "user"
    assert body["status"] == "active"
    assert body["label"] == "****9876"  # the masked hint, never the key

    # It really was encrypted — the plaintext reached Transit, and what was
    # stored does not contain it.
    assert stack.secrets.encrypted == [_RAW_SECRET.encode("utf-8")]
    stored = stack.repository.rows[body["id"]]
    assert _RAW_SECRET not in stored.ciphertext_ref.ciphertext
    assert stored.ciphertext_ref.key_name == "tenant-secrets"
    assert stored.workspace_id == _W1
    assert stored.created_by == _U1  # from the principal, not the body


def test_create_uses_the_given_label_over_the_mask() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/credentials", json=_create_body(label="prod key"), headers=_auth()
        )
    assert response.json()["label"] == "prod key"
    assert stack.repository.rows[response.json()["id"]].label == "prod key"


def test_create_refuses_platform_scope_with_403_and_stores_nothing() -> None:
    """A real scope, just not a tenant's to write (INV-C1). 403 «not yours»
    rather than 422 «not a scope» — and crucially nothing is persisted."""
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/credentials", json=_create_body(scope="platform"), headers=_auth()
        )
    assert response.status_code == 403
    assert response.json()["code"] == "authz.forbidden"
    assert stack.repository.rows == {}
    assert stack.secrets.encrypted == []  # the secret never even reached Vault


def test_create_rejects_an_unknown_provider_with_the_catalog_code() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/credentials", json=_create_body(provider="hal9000"), headers=_auth()
        )
    assert response.status_code == 422
    assert response.json()["code"] == "credentials.provider_unknown"
    assert stack.repository.rows == {}


def test_create_rejects_an_empty_secret_at_the_dto() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/credentials", json=_create_body(secret=""), headers=_auth())
    assert response.status_code == 422
    assert stack.repository.rows == {}


def test_create_refuses_a_second_active_key_for_the_same_provider() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/credentials", json=_create_body(), headers=_auth()).status_code
            == 201
        )
        response = client.post("/api/v1/credentials", json=_create_body(), headers=_auth())
    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"
    assert len(stack.repository.rows) == 1


def test_a_revoked_key_does_not_block_a_replacement() -> None:
    """The duplicate rule is about ACTIVE keys — rotation has to work."""
    app, stack = _make_app()
    with TestClient(app) as client:
        first = client.post("/api/v1/credentials", json=_create_body(), headers=_auth()).json()
        assert (
            client.delete(f"/api/v1/credentials/{first['id']}", headers=_auth()).status_code == 204
        )
        second = client.post("/api/v1/credentials", json=_create_body(), headers=_auth())
    assert second.status_code == 201
    assert second.json()["id"] != first["id"]
    assert len(stack.repository.rows) == 2


# --------------------------------------------------------------------------- #
# GET — metadata only, this tenant only                                       #
# --------------------------------------------------------------------------- #
def test_listing_is_empty_in_the_api_04_envelope() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/credentials", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_listing_reports_metadata_and_no_secret() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        created = client.post("/api/v1/credentials", json=_create_body(), headers=_auth()).json()
        response = client.get("/api/v1/credentials", headers=_auth())
    assert _RAW_SECRET not in response.text
    stored = stack.repository.rows[created["id"]]
    assert stored.ciphertext_ref.ciphertext not in response.text  # not the ciphertext either
    assert response.json() == {
        "data": [
            {
                "id": created["id"],
                "provider": "openai",
                "scope": "user",
                "label": "****9876",
                "status": "active",
                "created_at": created["created_at"],
            }
        ],
        "meta": {"next_cursor": None, "limit": 1},
    }


def test_listing_excludes_other_tenants_and_platform_rows() -> None:
    """Two absences, one assertion. The foreign row is RLS's job; the
    platform row is this listing's own (``CredentialRepository.list``): it is
    visible to resolution but is not the workspace's to manage."""
    app, stack = _make_app()
    stack.repository.rows["mine"] = seed_credential(credential_id="mine", workspace_id=_W1)
    stack.repository.rows["theirs"] = seed_credential(
        credential_id="theirs", workspace_id=_W2, provider="gemini"
    )
    stack.repository.rows["platform"] = seed_credential(
        credential_id="platform",
        workspace_id=None,
        provider="claude",
        scope=CredentialScope.PLATFORM,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/credentials", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == ["mine"]


def test_listing_includes_revoked_rows() -> None:
    """Revocation is a status, not a deletion: hiding the row would make
    ``DELETE`` look lossy and leave a client unable to explain the silence."""
    app, stack = _make_app()
    stack.repository.rows["dead"] = seed_credential(
        credential_id="dead", workspace_id=_W1, status=CredentialStatus.REVOKED
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/credentials", headers=_auth())
    assert [(row["id"], row["status"]) for row in response.json()["data"]] == [("dead", "revoked")]


def test_listing_is_newest_first() -> None:
    app, stack = _make_app()
    for credential_id in ("a-old", "b-mid", "c-new"):
        stack.repository.rows[credential_id] = seed_credential(
            credential_id=credential_id, workspace_id=_W1, provider="openai"
        )
    with TestClient(app) as client:
        response = client.get("/api/v1/credentials", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == ["c-new", "b-mid", "a-old"]


# --------------------------------------------------------------------------- #
# DELETE — revoke, don't erase                                                #
# --------------------------------------------------------------------------- #
def test_delete_revokes_the_row_rather_than_removing_it() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        created = client.post("/api/v1/credentials", json=_create_body(), headers=_auth()).json()
        response = client.delete(f"/api/v1/credentials/{created['id']}", headers=_auth())
    assert response.status_code == 204
    assert response.content == b""
    stored = stack.repository.rows[created["id"]]  # still there
    assert stored.status is CredentialStatus.REVOKED


def test_delete_is_idempotent() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        created = client.post("/api/v1/credentials", json=_create_body(), headers=_auth()).json()
        first = client.delete(f"/api/v1/credentials/{created['id']}", headers=_auth())
        second = client.delete(f"/api/v1/credentials/{created['id']}", headers=_auth())
    assert (first.status_code, second.status_code) == (204, 204)


def test_delete_of_another_tenants_credential_is_404() -> None:
    app, stack = _make_app()
    stack.repository.rows["theirs"] = seed_credential(credential_id="theirs", workspace_id=_W2)
    with TestClient(app) as client:
        response = client.delete("/api/v1/credentials/theirs", headers=_auth())
    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"
    # Untouched — a 404 that had already flipped a foreign row's status would
    # be a cross-tenant write dressed up as a refusal.
    assert stack.repository.rows["theirs"].status is CredentialStatus.ACTIVE


def test_delete_of_an_unknown_credential_is_404() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.delete("/api/v1/credentials/nope", headers=_auth())
    assert response.status_code == 404
