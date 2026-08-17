"""ASGI tests for the Files and Media routers (6.1-هـ-3).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory files/media stack (``support_files_media``) — the
same single-instance wiring the Composition Root builds, minus Postgres/MinIO.
What these pin, against 03 §1/§2:

* the whole upload lifecycle over the wire: register (201 + presigned PUT for
  the minted key) → complete (200, ``ready``, presigned GET, optional body) →
  list/get (API-04 envelope; ready-only ``download_url``) → delete (204,
  idempotent, then an honest 404);
* the limit refusals as their catalog codes (413/415);
* the rename face (BE-RAG-006): the extension policy over the wire, the whole
  ``FileOut`` coming back, the new name visible to the list and the read, and
  422/409/404 landing where they belong;
* a present-but-not-ready file read as a BODY with its status, not a problem;
* the media queue: POST answering **202** with the full job face
  (``result_file_id``/``error`` null at queue time), its ``MediaRequested``
  event actually appended, INV-MJ3 refusals, and the GET round-trip.
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
from app.framework.pagination import encode_seq_cursor
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import (
    FilesMediaStack,
    InMemorySpaces,
    build_files_media,
)
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_WORKSPACE_USAGE = build_workspace_usage()
_CREDENTIALS = build_credentials()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
# Spaces plan step 12: every upload names its space, every listing is scoped
# to one, and the seam behind both treats only this id as live.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"
_SHA = "a" * 64


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
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


def _make_app() -> tuple[FastAPI, FilesMediaStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_files_media(spaces=InMemorySpaces(active={_SPACE}))
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
        files=stack.files,
        media=stack.media,
        space_quota=stack.space_quota,
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


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }
    body.update(overrides)
    response = client.post("/api/v1/files", json=body, headers=_auth())
    assert response.status_code == 201, response.text
    out: dict[str, object] = response.json()
    return out


# --------------------------------------------------------------------------- #
# Files — auth                                                                 #
# --------------------------------------------------------------------------- #
def test_files_requires_a_bearer() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/files")

    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


# --------------------------------------------------------------------------- #
# Files — register                                                             #
# --------------------------------------------------------------------------- #
def test_register_answers_201_with_a_presigned_put_for_the_minted_key() -> None:
    app, stack = _make_app()
    client = TestClient(app)

    out = _register(client)

    (key, ttl, content_type) = stack.storage.presigned_puts[0]
    assert out["file_id"] == stack.file_repository.rows[str(out["file_id"])].id
    assert key == stack.file_repository.rows[str(out["file_id"])].storage_key.value
    assert out["upload_url"] == f"https://put/{key}"
    assert (ttl, content_type) == (900, "application/pdf")
    assert out["expires_in"] == 900


def test_register_refusals_speak_the_catalog() -> None:
    """The use-case's own codes carry through the problem handlers: an
    unlisted MIME is 415 `files.unsupported_type`, an oversized upload 413
    `files.too_large` — and neither leaves a row or a presigned URL behind."""
    app, stack = _make_app()
    client = TestClient(app)

    unsupported = client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "x.bin",
            "content_type": "application/x-msdownload",
            "size_bytes": 10,
        },
        headers=_auth(),
    )
    too_large = client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10**9,
        },
        headers=_auth(),
    )

    assert (unsupported.status_code, unsupported.json()["code"]) == (
        415,
        "files.unsupported_type",
    )
    assert (too_large.status_code, too_large.json()["code"]) == (413, "files.too_large")
    assert stack.file_repository.rows == {}
    assert stack.storage.presigned_puts == []


# --------------------------------------------------------------------------- #
# Files — complete                                                             #
# --------------------------------------------------------------------------- #
def test_complete_answers_the_ready_file_with_its_download_url() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    response = client.post(
        f"/api/v1/files/{file_id}/complete", json={"checksum": _SHA}, headers=_auth()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == file_id
    assert body["status"] == "ready"
    assert str(body["download_url"]).startswith("https://get/")
    assert body["name"] == "report.pdf"
    assert body["size_bytes"] == 2048
    assert body["created_at"]
    # The checksum the client sent is the checksum stored — a router that
    # silently dropped it would still answer this exact 200 otherwise.
    stored = stack.file_repository.rows[str(file_id)].checksum
    assert stored is not None
    assert stored.value == _SHA


def test_complete_accepts_an_absent_body() -> None:
    """openapi.yaml: the complete requestBody is `required: false` — a client
    that cannot hash its upload completes with no body at all, and the file is
    honestly `ready` (checksum None below the wire, §3.60)."""
    app, stack = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    response = client.post(f"/api/v1/files/{file_id}/complete", headers=_auth())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert stack.file_repository.rows[str(file_id)].checksum is None


def test_complete_unknown_is_404_and_twice_is_409() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]
    assert client.post(f"/api/v1/files/{file_id}/complete", headers=_auth()).status_code == 200

    missing = client.post("/api/v1/files/no-such-file/complete", headers=_auth())
    again = client.post(f"/api/v1/files/{file_id}/complete", headers=_auth())

    assert missing.status_code == 404
    assert again.status_code == 409


# --------------------------------------------------------------------------- #
# Files — list / get                                                           #
# --------------------------------------------------------------------------- #
def test_list_wraps_files_in_the_api04_envelope_with_ready_only_urls() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    pending_id = _register(client)["file_id"]
    ready_id = _register(client, name="other.pdf")["file_id"]
    client.post(f"/api/v1/files/{ready_id}/complete", headers=_auth())

    response = client.get(f"/api/v1/files?space_id={_SPACE}", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "meta"}
    assert body["meta"] == {"next_cursor": None, "limit": 20}
    by_id = {row["id"]: row for row in body["data"]}
    assert by_id[str(pending_id)]["download_url"] is None
    assert str(by_id[str(ready_id)]["download_url"]).startswith("https://get/")


def test_the_cursor_round_trips_through_the_envelope() -> None:
    """``meta.next_cursor`` is a real position, and spending it returns the
    REST of the collection exactly once (API-03).

    The first end-to-end proof of a non-null ``next_cursor``: until 6.3 every
    in-memory repository answered ``None``, so the routers' ``next_cursor=
    page.next_cursor`` was a line no test could distinguish from ``None``.
    """
    app, _ = _make_app()
    client = TestClient(app)
    ids = [_register(client, name=f"f{n}.pdf")["file_id"] for n in range(5)]

    first = client.get(f"/api/v1/files?space_id={_SPACE}&limit=2", headers=_auth()).json()
    assert first["meta"]["limit"] == 2
    cursor = first["meta"]["next_cursor"]
    assert cursor is not None
    assert str(cursor) not in str(first["data"][-1]["id"])  # opaque, not the raw id

    second = client.get(
        f"/api/v1/files?space_id={_SPACE}&limit=2&cursor={cursor}", headers=_auth()
    ).json()
    third = client.get(
        f"/api/v1/files?space_id={_SPACE}&limit=2&cursor={second['meta']['next_cursor']}",
        headers=_auth(),
    ).json()

    seen = [row["id"] for page in (first, second, third) for row in page["data"]]
    assert seen == sorted(ids, reverse=True)  # every row once, newest first (6.3-ب)
    assert third["meta"]["next_cursor"] is None  # last page
    # `meta.limit` is the page SIZE ASKED FOR, not the count returned — the
    # last page carries one row and still reports the limit it was given.
    assert (len(third["data"]), third["meta"]["limit"]) == (1, 2)


def test_a_malformed_cursor_is_a_422_problem_not_a_500() -> None:
    """``common.invalid_cursor`` reaching the wire for the first time.

    ``"!!!!"`` is the exact input the pre-6.3 lenient decoder turned into the
    empty string — which then arrived at Postgres as ``id > ''``. The catalog
    has carried this code since 6.2 with no reachable trigger.
    """
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get(f"/api/v1/files?space_id={_SPACE}&cursor=!!!!", headers=_auth())

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "common.invalid_cursor"


def test_a_cursor_minted_for_another_collection_is_refused() -> None:
    """A ``seq`` cursor from ``GET /conversations/{id}/messages`` spent on
    ``GET /files`` — well-formed base64, wrong keyset."""
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get(
        f"/api/v1/files?space_id={_SPACE}&cursor={encode_seq_cursor(42)}", headers=_auth()
    )

    assert response.status_code == 422
    assert response.json()["code"] == "common.invalid_cursor"


def test_a_present_but_not_ready_file_is_a_body_not_a_problem() -> None:
    """The §3.58 status-is-a-field logic: the resource exists, its state is a
    field — refusing the read would turn a truthful state into an error."""
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    response = client.get(f"/api/v1/files/{file_id}", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["download_url"] is None


def test_get_unknown_file_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/files/no-such-file", headers=_auth())

    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


# --------------------------------------------------------------------------- #
# Files — delete                                                               #
# --------------------------------------------------------------------------- #
def test_delete_is_204_idempotent_and_the_file_then_reads_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    first = client.delete(f"/api/v1/files/{file_id}", headers=_auth())
    read_back = client.get(f"/api/v1/files/{file_id}", headers=_auth())
    second = client.delete(f"/api/v1/files/{file_id}", headers=_auth())

    assert (first.status_code, first.content) == (204, b"")
    assert read_back.status_code == 404  # the §3.55 read precedent
    assert second.status_code == 204  # a retried lost 204 is a 204, not a 409


# --------------------------------------------------------------------------- #
# Files — rename (BE-RAG-006)                                                  #
# --------------------------------------------------------------------------- #
def test_rename_returns_the_whole_file_with_the_new_name() -> None:
    """The response is a full `FileOut`, so the client replaces the row it
    holds instead of patching a name into a stale one — and everything that
    describes the BYTES must come back untouched."""
    app, _ = _make_app()
    client = TestClient(app)
    registered = _register(client)

    response = client.patch(
        f"/api/v1/files/{registered['file_id']}", json={"name": "Q1 summary.pdf"}, headers=_auth()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Q1 summary.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["size_bytes"] == 2048


def test_rename_without_an_extension_inherits_the_current_one() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    response = client.patch(
        f"/api/v1/files/{file_id}", json={"name": "Q1 summary"}, headers=_auth()
    )

    assert response.json()["name"] == "Q1 summary.pdf"


def test_rename_to_another_extension_is_422_and_changes_nothing() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    response = client.patch(f"/api/v1/files/{file_id}", json={"name": "x.exe"}, headers=_auth())
    read_back = client.get(f"/api/v1/files/{file_id}", headers=_auth())

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"
    assert read_back.json()["name"] == "report.pdf"


def test_the_renamed_name_is_what_the_list_and_the_read_both_show() -> None:
    """A rename nobody else can see is not a rename."""
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]

    client.patch(f"/api/v1/files/{file_id}", json={"name": "renamed.pdf"}, headers=_auth())
    listed = client.get(f"/api/v1/files?space_id={_SPACE}", headers=_auth()).json()["data"]
    read = client.get(f"/api/v1/files/{file_id}", headers=_auth()).json()

    assert [row["name"] for row in listed if row["id"] == file_id] == ["renamed.pdf"]
    assert read["name"] == "renamed.pdf"


def test_rename_of_a_deleted_file_is_409_not_404() -> None:
    """A write against a soft-deleted resource is a conflict — the read face's
    404 belongs to reads."""
    app, _ = _make_app()
    client = TestClient(app)
    file_id = _register(client)["file_id"]
    client.delete(f"/api/v1/files/{file_id}", headers=_auth())

    response = client.patch(f"/api/v1/files/{file_id}", json={"name": "late.pdf"}, headers=_auth())

    assert response.status_code == 409


def test_rename_of_an_unknown_file_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.patch("/api/v1/files/no-such-file", json={"name": "x.pdf"}, headers=_auth())

    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


def test_rename_requires_a_bearer() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.patch("/api/v1/files/anything", json={"name": "x.pdf"})

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Media — queue                                                                 #
# --------------------------------------------------------------------------- #
def _job_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": "image",
        "prompt": "a cat",
        "agent_key": "image-agent",
        "params": {"width": 512, "height": 512},
    }
    body.update(overrides)
    return body


def test_media_requires_a_bearer() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.post("/api/v1/media/jobs", json=_job_body())

    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


def test_queueing_a_job_answers_202_with_the_full_face_and_its_event() -> None:
    """202, not 201: the POST queues work, it does not create a finished
    resource — `result_file_id`/`error` are null until the Phase-5 worker
    fills them. The `MediaRequested` event must actually land in the outbox:
    a job with no event is invisible to that worker forever."""
    app, stack = _make_app()
    client = TestClient(app)

    response = client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth())

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "image"
    assert body["status"] == "queued"
    assert body["result_file_id"] is None
    assert body["error"] is None
    assert body["created_at"]
    (_, records) = stack.outbox.calls[0]
    assert [r.event_type for r in records] == ["media.job.requested.v1"]
    assert records[0].aggregate_id == body["id"]


def test_a_kind_outside_the_contract_never_reaches_the_use_case() -> None:
    app, stack = _make_app()
    client = TestClient(app)

    response = client.post("/api/v1/media/jobs", json=_job_body(kind="audio"), headers=_auth())

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"
    assert stack.media_repository.rows == {}


def test_out_of_bounds_params_are_the_catalog_422() -> None:
    """INV-MJ3 through the wire: the configured ceiling refuses BEFORE anything
    is queued, with the stable `media.invalid_params` code."""
    app, stack = _make_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/media/jobs",
        json=_job_body(params={"width": 99_999, "height": 512}),
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "media.invalid_params"
    assert stack.media_repository.rows == {}
    assert stack.outbox.calls == []


def test_a_queued_job_reads_back_as_itself() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    queued = client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth()).json()

    response = client.get(f"/api/v1/media/jobs/{queued['id']}", headers=_auth())

    assert response.status_code == 200
    assert response.json() == queued  # the 202 face IS the stored face


def test_an_unknown_job_is_404() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.get("/api/v1/media/jobs/no-such-job", headers=_auth())

    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


# --------------------------------------------------------------------------- #
# Idempotency-Key (3.79)                                                       #
# --------------------------------------------------------------------------- #
def _idem(key: str) -> dict[str, str]:
    return {**_auth(), "Idempotency-Key": key}


def test_registering_twice_with_one_key_creates_one_file() -> None:
    """The promise 03 §0 made and the header could not keep until 3.79: a
    retried create must not burn a second slot against the workspace's file
    quota, and the second answer must be the FIRST one — same file id, same
    presigned URL — rather than a fresh resource the client never asked for."""
    app, stack = _make_app()
    client = TestClient(app)
    body = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }

    first = client.post("/api/v1/files", json=body, headers=_idem("k-1"))
    second = client.post("/api/v1/files", json=body, headers=_idem("k-1"))

    assert first.status_code == 201
    assert second.status_code == 201  # the route's own status, replayed
    assert second.json() == first.json()
    assert len(stack.file_repository.rows) == 1


def test_the_same_key_with_a_different_body_is_a_conflict() -> None:
    """A key is a uniqueness claim. Reusing it for different content is
    ``common.conflict``/409 — the catalog's existing code (03 §4), not one
    invented for this feature."""
    app, stack = _make_app()
    client = TestClient(app)

    client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        headers=_idem("k-2"),
    )
    response = client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "b.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        headers=_idem("k-2"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"
    assert len(stack.file_repository.rows) == 1  # the second body created nothing


def test_different_keys_create_different_files() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    body = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }

    first = client.post("/api/v1/files", json=body, headers=_idem("k-3"))
    second = client.post("/api/v1/files", json=body, headers=_idem("k-4"))

    assert first.json()["file_id"] != second.json()["file_id"]
    assert len(stack.file_repository.rows) == 2


def test_without_the_header_nothing_is_deduplicated_and_the_store_is_untouched() -> None:
    """The header is OPTIONAL (``openapi.yaml``): absent, the route behaves
    exactly as it did before 3.79 — including spending no database round
    trip on a ledger the caller never opted into."""
    app, stack = _make_app()
    client = TestClient(app)
    body = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }

    client.post("/api/v1/files", json=body, headers=_auth())
    client.post("/api/v1/files", json=body, headers=_auth())

    assert len(stack.file_repository.rows) == 2
    store = app.state.services.idempotency
    assert store.claims == []


def test_a_blank_key_is_a_422_not_a_silent_pass() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    body = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }

    response = client.post("/api/v1/files", json=body, headers=_idem("   "))

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"


def test_queueing_a_media_job_twice_with_one_key_queues_one_job() -> None:
    """The most expensive duplicate in the API: a second job is generated and
    billed. Same key + same body ⇒ the first ``MediaJobOut``, so the client
    polls the job that exists."""
    app, stack = _make_app()
    client = TestClient(app)

    first = client.post("/api/v1/media/jobs", json=_job_body(), headers=_idem("m-1"))
    second = client.post("/api/v1/media/jobs", json=_job_body(), headers=_idem("m-1"))

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(stack.media_repository.rows) == 1


def test_the_media_and_files_ledgers_do_not_collide_on_one_key() -> None:
    """Keys are scoped by ENDPOINT as well as workspace: the same client
    counter used on two different creates must not make the second one replay
    the first one's body."""
    app, stack = _make_app()
    client = TestClient(app)

    file_out = client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 2048,
        },
        headers=_idem("shared"),
    )
    job_out = client.post("/api/v1/media/jobs", json=_job_body(), headers=_idem("shared"))

    assert file_out.status_code == 201
    assert job_out.status_code == 202
    assert len(stack.file_repository.rows) == 1
    assert len(stack.media_repository.rows) == 1


def test_a_failed_create_releases_its_key_so_the_retry_can_proceed() -> None:
    """Without ``release``, one transient failure would make that key a 409
    forever — turning a temporary error into a permanent one on a billable
    path. The 413 below is a real refusal from the use-case's own limit
    check, not a synthetic exception."""
    app, stack = _make_app()
    client = TestClient(app)
    too_big = {
        "space_id": _SPACE,
        "name": "big.pdf",
        "content_type": "application/pdf",
        "size_bytes": 10**9,
    }
    ok = {
        "space_id": _SPACE,
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
    }

    failed = client.post("/api/v1/files", json=too_big, headers=_idem("k-5"))
    assert failed.status_code == 413

    # The SAME key, now with a body that works: the claim was released, so this
    # is a first attempt rather than a conflict.
    retried = client.post("/api/v1/files", json=ok, headers=_idem("k-5"))
    assert retried.status_code == 201
    assert len(stack.file_repository.rows) == 1
