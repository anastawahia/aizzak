"""BT23 / INC1-AC-5, AC-6, AC-8, AC-11, AC-12: the ``/api/v1/electrical`` routes on the wire.

The app is the real one (``create_app``) over the real ``DrawingsUseCases`` services on the
in-memory stack of ``support_electrical``; STG-01 is driven by the real ``InspectDocument`` over a
replayed ele-parse, so a document goes from registration to a proposal exactly as it does in
production. What is pinned here is the contract a client meets: the codes and statuses of every
refusal, the flag, the idempotency and heavy-job guards, the shapes, and that no refusal echoes
what the client sent (S-8).
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.middleware.rate_limit import HeavyJobRateLimiter
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import UnauthorizedError
from app.framework.observability.metrics import ele_documents_excluded_total
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.settings.settings import ElectricalSettings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.electrical.drawings.application.use_cases import DrawingsUseCases
from app.modules.electrical.drawings.domain.document import DocumentStatus, ExclusionReason
from app.modules.electrical.drawings.domain.pages import StageReason
from app.modules.electrical.drawings.domain.profile import (
    _TOP_KEYS,
    DeviceFunctionNumbers,
    PlantDesignation,
)
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_electrical import (
    IEC_TOKENS,
    SPACE,
    WS,
    FakeDrawingFile,
    FakePdfInspection,
    Stack,
    build_stack,
    make_summary,
    tokens_page,
)
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_rate_limit import InMemoryRateLimiter
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_GOOD = "good"
_BASE = "/api/v1/electrical"
_PDF = b"%PDF-1.7 drawing "
_RLO, _ZWSP = chr(0x202E), chr(0x200B)
_HOSTILE = f"evil{_RLO}{_ZWSP}name<script>" + "x" * 300


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
    def __init__(self) -> None:
        self.role = "owner"

    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=WS, user_id="u1", roles=frozenset({self.role}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        return WsPrincipal(workspace_id=WS, user_id="u1", roles=frozenset({"owner"}))


@dataclass
class Env:
    app: FastAPI
    client: TestClient
    stack: Stack
    auth: _FakeAuth
    store: InMemoryIdempotencyStore
    counter: int = 0

    def as_role(self, role: str) -> None:
        self.auth.role = role

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.client.get(f"{_BASE}{path}", headers=_headers(), **kwargs)

    def post(self, path: str, json: Any = None, **headers: str) -> Any:
        return self.client.post(
            f"{_BASE}{path}",
            json=json,
            headers={**_headers(), **headers} if headers else _headers(),
        )

    def put(self, path: str, json: Any) -> Any:
        return self.client.put(f"{_BASE}{path}", json=json, headers=_headers())

    def patch(self, path: str, json: Any) -> Any:
        return self.client.patch(f"{_BASE}{path}", json=json, headers=_headers())

    def project(self, **body: Any) -> dict[str, Any]:
        response = self.post("/projects", {"space_id": SPACE, "name": "Plant 7", **body})
        assert response.status_code == 201, response.text
        return response.json()  # type: ignore[no-any-return]

    def file(self, file_id: str = "f1", **changes: Any) -> FakeDrawingFile:
        self.counter += 1
        body = _PDF + file_id.encode()
        return _seed(self.stack, file_id, body, **changes)

    def register(self, project_id: str, file_id: str = "f1", **extra: Any) -> Any:
        return self.post(f"/projects/{project_id}/documents", {"file_id": file_id, **extra})

    async def run_worker(self, job_id: str, pdf: FakePdfInspection | None = None) -> None:
        self.counter += 1
        await self.stack.inspector(pdf or _pdf()).run(
            self.stack.ctx, job_id=job_id, event_id=f"e{self.counter}"
        )


def _seed(stack: Stack, file_id: str, body: bytes, **changes: Any) -> FakeDrawingFile:
    key = f"{WS}/{file_id}"
    file = FakeDrawingFile(
        file_id=file_id, storage_key=key, size_bytes=len(body), name=f"{file_id}.pdf", **changes
    )
    stack.files.add(file, workspace_id=WS)
    stack.stager.bodies[key] = body
    return file


def _pdf(pages: int = 4, tokens: tuple[str, ...] = IEC_TOKENS, **summary: Any) -> FakePdfInspection:
    return FakePdfInspection(
        make_summary(
            page_count=pages,
            **{"creator": "EPLAN Electric P8", "producer": "EPLAN", **summary},
        ),
        [tokens_page(n, tokens) for n in range(1, pages + 1)],
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_GOOD}"}


def _make_env(
    *,
    enabled: bool = True,
    wired: bool = True,
    heavy_limit: int | None = None,
    settings: ElectricalSettings | None = None,
) -> Env:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    files_media = build_files_media()
    workspace_usage = build_workspace_usage()
    electrical_settings = (settings or ElectricalSettings(queue_protection_limit=3)).model_copy(
        update={"enabled": enabled}
    )
    stack = build_stack(settings=electrical_settings)
    bundle = DrawingsUseCases(
        projects=stack.services["projects"],
        documents=stack.services["documents"],
        jobs=stack.services["jobs"],
        replacement=stack.services["replacement"],
        characterization=stack.services["characterization"],
    )
    store = InMemoryIdempotencyStore()
    auth = _FakeAuth()
    services = ApiServices(
        settings=Settings(electrical=electrical_settings),
        orchestrator=AgentOrchestrator(
            OrchestratorDependencies(
                agents=registry,
                executor=AgentLifecycleExecutor(),
                providers=_FakeResolver(),
                conversations=conversations.service,
                authorization=build_authorization(),
            )
        ),
        hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
        agents=registry,
        conversations=conversations.use_cases,
        workflows=InMemoryWorkflowRegistry(),
        files=files_media.files,
        media=files_media.media,
        workspace=workspace_usage.workspace,
        usage=workspace_usage.usage,
        credentials=build_credentials().credentials,
        knowledge=build_knowledge().knowledge,
        integrations=build_integrations().integrations,
        authorization=build_authorization(),
        idempotency=store,
        heavy_job_limiter=(
            None
            if heavy_limit is None
            else HeavyJobRateLimiter(InMemoryRateLimiter(), jobs_per_min=heavy_limit)
        ),
        electrical=bundle if wired else None,
    )
    app = create_app(services, http_authenticator=auth, ws_authenticator=_FakeWsAuth())
    return Env(app=app, client=TestClient(app), stack=stack, auth=auth, store=store)


@pytest.fixture
def env() -> Env:
    return _make_env()


def _code(response: Any) -> str:
    assert response.headers["content-type"].startswith("application/problem+json")
    return str(response.json()["code"])


# =================================================================== the flag and the wiring
_ALL_OPERATIONS = [
    ("get", "/projects?space_id=s"),
    ("post", "/projects"),
    ("get", "/projects/x"),
    ("patch", "/projects/x"),
    ("get", "/projects/x/profile"),
    ("put", "/projects/x/profile"),
    ("get", "/projects/x/documents"),
    ("post", "/projects/x/documents"),
    ("get", "/projects/x/characterization"),
    ("get", "/documents/x"),
    ("get", "/documents/x/profile"),
    ("put", "/documents/x/profile"),
    ("post", "/documents/x/profile-confirmation"),
    ("post", "/documents/x/reprocessing"),
    ("get", "/documents/x/pages"),
    ("get", "/documents/x/characterization"),
    ("get", "/ingestion-jobs/x"),
    ("post", "/ingestion-jobs/x/cancel"),
    ("post", "/ingestion-jobs/x/resume"),
    ("get", "/files/x/replacement-preview"),
]


def test_the_twenty_operations_are_all_here() -> None:
    env = _make_env()
    published = {
        (method, path)
        for path, item in env.app.openapi()["paths"].items()
        if path.startswith(_BASE)
        for method in item
    }
    assert len(published) == len(_ALL_OPERATIONS) == 20


@pytest.mark.parametrize(("method", "path"), _ALL_OPERATIONS)
def test_every_operation_answers_503_while_the_flag_is_off_even_to_a_caller_without_permission(
    method: str, path: str
) -> None:
    """INC1-D-12: the flag is checked BEFORE the permission guard. A viewer cannot manage
    anything, and still hears 'not enabled' rather than 'forbidden'."""
    env = _make_env(enabled=False)
    env.as_role("viewer")
    response = env.client.request(method, f"{_BASE}{path}", json={}, headers=_headers())
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "electrical.disabled"
    assert body["title"] == "WireTwin is not enabled on this deployment"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_anonymous_caller_is_refused_before_the_flag_is_mentioned() -> None:
    env = _make_env(enabled=False)
    response = env.client.get(f"{_BASE}/projects?space_id=s")
    assert response.status_code == 401
    bad = env.client.get(f"{_BASE}/projects?space_id=s", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["code"] == "auth.invalid_token"


def test_an_unwired_bundle_fails_closed_not_empty() -> None:
    env = _make_env(wired=False)
    response = env.get("/projects?space_id=s")
    assert response.status_code == 500 and response.json()["code"] == "common.internal"


# ========================================================================== projects (AC-5)
def test_a_project_is_created_with_the_default_profile_and_read_back(env: Env) -> None:
    created = env.project(description="main plant")
    assert created["space_id"] == SPACE and created["name"] == "Plant 7"
    assert created["description"] == "main plant"
    assert created["created_at"].endswith("Z") and created["updated_at"].endswith("Z")
    assert env.get(f"/projects/{created['id']}").json() == created
    listing = env.get(f"/projects?space_id={SPACE}").json()
    assert listing["data"] == [created] and listing["meta"] == {"next_cursor": None, "limit": 20}
    assert env.get("/projects?space_id=nowhere").json()["data"] == []  # not an existence oracle
    profile = env.get(f"/projects/{created['id']}/profile").json()
    assert profile["version"] == 1 and profile["source"] == "default"
    assert profile["body"]["identifier_convention"] == "iec_81346"
    assert len(profile["fingerprint"]) == 64


def test_the_listing_requires_a_space(env: Env) -> None:
    assert env.get("/projects").status_code == 422


def test_a_second_project_in_the_space_is_a_409_and_an_unknown_space_a_404(env: Env) -> None:
    env.project()
    second = env.post("/projects", {"space_id": SPACE, "name": "Other"})
    assert second.status_code == 409 and _code(second) == "electrical.project_exists"
    unknown = env.post("/projects", {"space_id": "nowhere", "name": "X"})
    assert unknown.status_code == 404 and _code(unknown) == "common.not_found"


def test_a_project_is_created_with_a_given_profile_as_user(env: Env) -> None:
    created = env.project(profile={"schema": 1, "identifier_convention": "cn_letter_codes"})
    profile = env.get(f"/projects/{created['id']}/profile").json()
    assert profile["source"] == "user"
    assert profile["body"]["identifier_convention"] == "cn_letter_codes"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_a_bad_project_name_is_a_422(env: Env, name: str) -> None:
    response = env.post("/projects", {"space_id": SPACE, "name": name})
    assert response.status_code == 422 and _code(response) == "common.validation_error"


def test_a_project_is_patched_and_changing_nothing_writes_nothing(env: Env) -> None:
    created = env.project()
    renamed = env.patch(f"/projects/{created['id']}", {"name": "Plant 8"}).json()
    assert renamed["name"] == "Plant 8" and renamed["description"] is None
    again = env.patch(f"/projects/{created['id']}", {"name": "Plant 8"}).json()
    assert again["updated_at"] == renamed["updated_at"]
    cleared = env.patch(f"/projects/{created['id']}", {"description": "  "}).json()
    assert cleared["description"] is None
    assert env.patch("/projects/nope", {"name": "x"}).status_code == 404


def test_the_project_profile_is_replaced_by_a_new_version(env: Env) -> None:
    created = env.project()
    replaced = env.put(
        f"/projects/{created['id']}/profile",
        {"body": {"schema": 1, "languages": ["zh"], "auto_accept_confidence": 0.5}},
    )
    assert replaced.status_code == 200
    assert replaced.json()["version"] == 2 and replaced.json()["source"] == "user"
    assert replaced.json()["body"]["languages"] == ["zh"]
    assert env.get(f"/projects/{created['id']}/profile").json()["version"] == 2
    assert env.put("/projects/nope/profile", {"body": {"schema": 1}}).status_code == 404


def test_an_invalid_profile_is_a_422_that_names_the_field_and_never_echoes_the_client(
    env: Env,
) -> None:
    """S-8 (PR-B security review): the unknown key, the unknown pattern and the unknown nested
    field are NOT reflected, in any length or script."""
    created = env.project()
    path = f"/projects/{created['id']}/profile"
    for body, field in [
        ({"schema": 1, "surprise": 1}, "body"),
        ({"schema": 1, _HOSTILE: 1}, "body"),
        ({"schema": 1, "classification": {_HOSTILE: 1}}, "classification"),
        ({"schema": 1, "wire_number_patterns": [_HOSTILE]}, "wire_number_patterns[0]"),
        ({"schema": 1, "symbol_standard": _HOSTILE}, "symbol_standard"),
        ({"schema": 1, "auto_accept_confidence": 7}, "auto_accept_confidence"),
    ]:
        response = env.put(path, {"body": body})
        assert response.status_code == 422, body
        assert _code(response) == "electrical.invalid_profile"
        detail = response.json()["detail"]
        assert detail.startswith(f"{field}: ")
        for leaked in ("evil", _RLO, _ZWSP, "<script>", "surprise", "x" * 50):
            assert leaked not in response.text
    created_with_bad = env.post(
        "/projects", {"space_id": SPACE, "name": "Y", "profile": {"schema": 1, _HOSTILE: 1}}
    )
    assert created_with_bad.status_code == 422 and "evil" not in created_with_bad.text


def test_f2_ac_16_the_new_fields_are_saved_validated_and_defaulted(env: Env) -> None:
    created = env.project()
    path = f"/projects/{created['id']}/profile"
    saved = env.put(
        path,
        {
            "body": {
                "schema": 1,
                "plant_designation": "kks",
                "device_function_numbers": "ansi",
                "ansi_suffixes": ["qz", "AB"],
            }
        },
    )
    assert saved.status_code == 200 and saved.json()["version"] == 2
    body = saved.json()["body"]
    assert body["plant_designation"] == "kks" and body["device_function_numbers"] == "ansi"
    assert body["ansi_suffixes"] == ["AB", "QZ"]
    assert env.get(path).json()["body"] == body
    # a body that does not use them is stored and returned without the three keys
    plain = env.put(path, {"body": {"schema": 1}})
    assert plain.status_code == 200 and plain.json()["version"] == 3
    assert not {"plant_designation", "device_function_numbers", "ansi_suffixes"} & set(
        plain.json()["body"]
    )
    for bad, field in [
        ({"plant_designation": _HOSTILE}, "plant_designation"),
        ({"device_function_numbers": "iec_81346"}, "device_function_numbers"),
        ({"identifier_convention": "kks"}, "identifier_convention"),
        ({"identifier_convention": "ansi"}, "identifier_convention"),
        ({"ansi_suffixes": [".*"]}, "ansi_suffixes[0]"),
        ({"ansi_suffixes": ["("]}, "ansi_suffixes[0]"),
        ({"ansi_suffixes": ["ABCD"]}, "ansi_suffixes[0]"),
        ({"ansi_suffixes": [_HOSTILE]}, "ansi_suffixes[0]"),
        ({"ansi_suffixes": ["A"] * 65}, "ansi_suffixes"),
    ]:
        response = env.put(path, {"body": {"schema": 1, **bad}})
        assert response.status_code == 422, bad
        assert _code(response) == "electrical.invalid_profile"
        assert response.json()["detail"].startswith(f"{field}: "), response.json()["detail"]
        for leaked in ("evil", _RLO, _ZWSP, "<script>", ".*", "ABCD"):
            assert leaked not in response.text
    assert env.get(path).json()["version"] == 3  # none of the refused bodies was stored


# ======================================================================= registration (AC-6)
def test_registering_a_document_answers_202_with_the_document_and_its_queued_job(
    env: Env,
) -> None:
    project = env.project()
    env.file("f1", tags={"system": "S1"})
    response = env.register(project["id"], "f1")
    assert response.status_code == 202, response.text
    body = response.json()
    document, job = body["document"], body["job"]
    assert document["project_id"] == project["id"] and document["file_id"] == "f1"
    assert document["status"] == "active" and document["profile_state"] == "none"
    assert document["file_name"] == "f1.pdf" and document["page_count"] is None
    assert document["tags"] == {"system": "S1", "equipment": None, "department": None}
    assert (
        job["status"] == "queued"
        and job["kind"] == "ingest"
        and job["document_id"] == document["id"]
    )
    assert job["queue_position"] == 1 and job["estimated_wait_s"] is None  # no history yet
    assert job["seq"] == 0 and job["attempt"] == 1 and job["stale"] is False
    assert document["latest_job"]["id"] == job["id"]
    assert env.stack.outbox.types() == ["electrical.document.registered.v1"]


def test_every_refusal_is_its_own_code_and_writes_nothing(env: Env) -> None:
    project = env.project()
    other = env.post("/projects", {"space_id": "nowhere", "name": "x"})
    assert other.status_code == 404
    refusals: list[tuple[Any, int, str]] = []
    refusals.append((env.register("nope", "f1"), 404, "common.not_found"))
    refusals.append((env.register(project["id"], "ghost"), 404, "common.not_found"))
    env.file("notpdf", content_type="image/png")
    refusals.append((env.register(project["id"], "notpdf"), 415, "electrical.not_a_pdf"))
    env.file("away", space_id="99999999-9999-7999-8999-999999999999")
    refusals.append(
        (env.register(project["id"], "away"), 422, "electrical.file_not_in_project_space")
    )
    env.file("old")
    env.stack.namesakes.set("old", replaced=["older-file"])
    refusals.append((env.register(project["id"], "old"), 409, "electrical.namesake_not_replaced"))
    refusals.append(
        (env.register(project["id"], "old", checksum="zz"), 422, "common.validation_error")
    )
    for response, status, code in refusals:
        assert (response.status_code, _code(response)) == (status, code)
    assert env.stack.documents.rows == {} and env.stack.jobs.rows == {}
    assert env.stack.outbox.types() == []
    assert "older-file" in refusals[4][0].json()["detail"]


def test_document_exists_says_an_excluded_document_still_holds_its_file(env: Env) -> None:
    """Item 5: the unique index counts every row that is not purging, an excluded one included,
    and the message must say so (the old text said only 'already a drawing document')."""
    project = env.project()
    env.file("f1")
    first = env.register(project["id"], "f1").json()
    again = env.register(project["id"], "f1")
    assert again.status_code == 409 and _code(again) == "electrical.document_exists"
    assert "active or excluded" in again.json()["detail"]
    assert "until it is purged" in again.json()["detail"]
    # an excluded document keeps the file: still 409, with the same honest text
    stored = env.stack.documents.rows[first["document"]["id"]]
    stored.exclude(ExclusionReason.FILE_UNREADABLE, stored.updated_at)
    assert stored.status is DocumentStatus.EXCLUDED
    excluded_again = env.register(project["id"], "f1")
    assert excluded_again.status_code == 409
    assert _code(excluded_again) == "electrical.document_exists"
    assert "excluded" in excluded_again.json()["detail"]


@pytest.mark.parametrize("sent", ["A" * 64, "a" * 64, "  " + "0123456789abcdef" * 4 + " "])
def test_a_checksum_in_hex_of_either_case_is_accepted(env: Env, sent: str) -> None:
    project = env.project()
    env.file("f1")
    assert env.register(project["id"], "f1", checksum=sent.strip()).status_code == 202


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, "a" * 63, "a" * 65, "a" * 200, "-" * 64])
def test_a_checksum_that_is_not_a_sha256_in_hex_is_a_422(env: Env, bad: str) -> None:
    project = env.project()
    env.file("f1")
    response = env.register(project["id"], "f1", checksum=bad)
    assert response.status_code == 422 and _code(response) == "common.validation_error"
    assert env.stack.documents.rows == {}


async def test_the_same_content_is_a_409_when_the_checksum_is_sent(env: Env) -> None:
    project = env.project()
    env.file("f1")
    first = env.register(project["id"], "f1").json()
    await env.run_worker(first["job"]["id"])
    sha = hashlib.sha256(_PDF + b"f1").hexdigest()
    # a second FILE with the same bytes
    _seed(env.stack, "f2", _PDF + b"f1")
    twin = env.register(project["id"], "f2", checksum=sha.upper())
    assert twin.status_code == 409 and _code(twin) == "electrical.duplicate_document"
    assert first["document"]["id"] in twin.json()["detail"]
    assert env.register(project["id"], "f2").status_code == 202  # without it the worker judges


def test_a_full_queue_is_a_429_with_retry_after(env: Env) -> None:
    project = env.project()
    for number in range(3):  # ELECTRICAL protection limit in this fixture: 3
        env.file(f"q{number}")
        assert env.register(project["id"], f"q{number}").status_code == 202
    env.file("q3")
    full = env.register(project["id"], "q3")
    assert full.status_code == 429 and _code(full) == "electrical.ingestion_queue_full"
    assert int(full.headers["Retry-After"]) > 0
    assert len(env.stack.documents.rows) == 3


def test_an_orphaned_document_is_excluded_at_registration_and_counted(env: Env) -> None:
    project = env.project()
    env.file("gone")
    env.file("new")
    env.register(project["id"], "gone")
    del env.stack.files._files[(WS, "gone")]  # the file became unreadable
    series = ele_documents_excluded_total.labels(reason="file_unreadable")
    before = series._value.get()

    assert env.register(project["id"], "new").status_code == 202

    assert series._value.get() == before + 1
    documents = env.get(f"/projects/{project['id']}/documents?status=excluded").json()["data"]
    assert [d["exclusion_reason"] for d in documents] == ["file_unreadable"]


def test_the_idempotency_key_replays_the_first_answer_and_creates_one_document(env: Env) -> None:
    project = env.project()
    env.file("f1")
    first = env.post(
        f"/projects/{project['id']}/documents", {"file_id": "f1"}, **{"Idempotency-Key": "k1"}
    )
    again = env.post(
        f"/projects/{project['id']}/documents", {"file_id": "f1"}, **{"Idempotency-Key": "k1"}
    )
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json()
    assert len(env.stack.documents.rows) == 1
    other = env.post(
        f"/projects/{project['id']}/documents",
        {"file_id": "f1", "checksum": "a" * 64},
        **{"Idempotency-Key": "k1"},
    )
    assert other.status_code == 409 and _code(other) == "common.conflict"


def test_the_heavy_job_guard_sits_in_front_of_the_202s_and_refuses_before_any_write() -> None:
    env = _make_env(heavy_limit=1)
    project = env.project()
    env.file("f1")
    env.file("f2")
    assert env.register(project["id"], "f1").status_code == 202
    rows = len(env.stack.documents.rows)
    refused = env.register(project["id"], "f2")
    assert refused.status_code == 429 and _code(refused) == "common.rate_limited"
    assert len(env.stack.documents.rows) == rows
    assert env.stack.outbox.types().count("electrical.document.registered.v1") == 1
    # a read is not charged
    assert env.get(f"/projects/{project['id']}").status_code == 200


# ============================================================ documents, profile, jobs (AC-8)
async def _ingested(env: Env, file_id: str = "f1") -> tuple[dict[str, Any], dict[str, Any]]:
    project = env.project()
    env.file(file_id)
    registered = env.register(project["id"], file_id).json()
    await env.run_worker(registered["job"]["id"])
    return project, registered


async def test_after_stg01_the_job_awaits_confirmation_and_the_proposal_carries_its_evidence(
    env: Env,
) -> None:
    """Item 3: ``field_confidence`` and ``basis`` come from the stored detection, and the 0.8
    symbol-standard ceiling (INC1-R-16) holds a default document for a person."""
    _project, registered = await _ingested(env)
    job = env.get(f"/ingestion-jobs/{registered['job']['id']}").json()
    assert job["status"] == "awaiting_confirmation" and job["stage"] == "classify"
    assert job["counts"] == {"succeeded": 4} and job["seq"] >= 1
    assert job["queue_position"] is None and job["stale"] is False

    profile = env.get(f"/documents/{registered['document']['id']}/profile").json()
    assert profile["state"] == "proposed"
    assert profile["effective"]["source"] == "default"  # the project's, until a person decides
    proposal = profile["proposal"]
    assert proposal["confidence"] == pytest.approx(0.8)
    assert set(proposal["field_confidence"]) == {
        "identifier_convention",
        "symbol_standard",
        "cross_reference_format",
        "plc_families",
        "languages",
        "plant_designation",  # D-36: proposed beside the device convention
        "device_function_numbers",
    }
    assert proposal["field_confidence"]["symbol_standard"] == pytest.approx(0.8)
    assert "tokens" in proposal["basis"] and "producer" in proposal["basis"]
    assert proposal["basis"]["producer"] == ["eplan"]
    assert proposal["mixed_standards"] is False
    assert proposal["body"]["identifier_convention"] == "iec_81346"


async def test_a_proposal_that_stored_no_detection_answers_empty_maps(env: Env) -> None:
    _project, registered = await _ingested(env)
    rows = env.stack.profiles.rows
    detected = next(v for v in rows if v.source.value == "detected")
    rows[rows.index(detected)] = replace(detected, detection=None)
    proposal = env.get(f"/documents/{registered['document']['id']}/profile").json()["proposal"]
    assert proposal["field_confidence"] == {} and proposal["basis"] == {}
    assert proposal["confidence"] == pytest.approx(0.8)


async def test_confirming_requeues_the_job_and_the_profile_becomes_confirmed(env: Env) -> None:
    project, registered = await _ingested(env)
    doc_id, job_id = registered["document"]["id"], registered["job"]["id"]
    reprocess_now = env.post(f"/documents/{doc_id}/reprocessing", {})
    assert (
        reprocess_now.status_code == 409
        and _code(reprocess_now) == "electrical.profile_unconfirmed"
    )
    resume_now = env.post(f"/ingestion-jobs/{job_id}/resume")
    assert resume_now.status_code == 409 and _code(resume_now) == "electrical.profile_unconfirmed"
    override = env.put(f"/documents/{doc_id}/profile", {"body": {"schema": 1}})
    assert override.status_code == 409 and _code(override) == "electrical.job_conflict"

    confirmed = env.post(f"/documents/{doc_id}/profile-confirmation")
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["id"] == job_id and confirmed.json()["status"] == "queued"
    assert env.get(f"/documents/{doc_id}/profile").json()["state"] == "confirmed"
    assert "electrical.job.requested.v1" in env.stack.outbox.types()
    nothing_waits = env.post(f"/documents/{doc_id}/profile-confirmation")
    assert nothing_waits.status_code == 409 and _code(nothing_waits) == "electrical.job_conflict"
    assert project["id"]


async def test_confirming_with_an_edited_profile_stores_it_as_confirmed(env: Env) -> None:
    _project, registered = await _ingested(env)
    doc_id = registered["document"]["id"]
    edited = env.post(
        f"/documents/{doc_id}/profile-confirmation",
        {"body": {"schema": 1, "identifier_convention": "gbt_5094"}},
    )
    assert edited.status_code == 202
    in_force = env.get(f"/documents/{doc_id}/profile").json()
    assert in_force["state"] == "confirmed" and in_force["effective"]["source"] == "confirmed"
    assert in_force["effective"]["body"]["identifier_convention"] == "gbt_5094"
    bad = env.post(
        f"/documents/{doc_id}/profile-confirmation", {"body": {"schema": 1, "x": _HOSTILE}}
    )
    assert bad.status_code in (409, 422) and "evil" not in bad.text


async def test_a_person_can_override_a_document_profile_and_it_wins(env: Env) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    doc_id = registered["document"]["id"]
    overridden = env.put(
        f"/documents/{doc_id}/profile", {"body": {"schema": 1, "languages": ["en"]}}
    )
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["state"] == "confirmed" and body["effective"]["source"] == "user"
    assert body["effective"]["body"]["languages"] == ["en"] and body["proposal"] is None
    assert env.put("/documents/nope/profile", {"body": {"schema": 1}}).status_code == 404
    assert env.put(f"/documents/{doc_id}/profile", {"body": {"schema": 2}}).status_code == 422


async def test_reprocessing_a_subset_of_pages(env: Env) -> None:
    _project, registered = await _ingested(env)
    doc_id = registered["document"]["id"]
    env.post(f"/documents/{doc_id}/profile-confirmation")
    await env.run_worker(registered["job"]["id"])  # the confirmed job runs to its end
    assert env.get(f"/ingestion-jobs/{registered['job']['id']}").json()["status"] == "succeeded"

    for pages, status in [([5], 422), ([0], 422), ([], 422), ([1, 1], 422), ([1, "a"], 422)]:
        response = env.post(f"/documents/{doc_id}/reprocessing", {"pages": pages})
        assert response.status_code == status, pages
        assert _code(response) == "common.validation_error"
    accepted = env.post(f"/documents/{doc_id}/reprocessing", {"pages": [3, 1]})
    assert accepted.status_code == 202
    job = accepted.json()
    assert job["kind"] == "reprocess" and job["pages"] == [1, 3] and job["status"] == "queued"
    busy = env.post(f"/documents/{doc_id}/reprocessing")
    assert busy.status_code == 409 and _code(busy) == "electrical.job_conflict"
    assert env.post("/documents/nope/reprocessing").status_code == 404


async def test_the_pages_are_listed_by_number_with_their_stages_and_paged(env: Env) -> None:
    _project, registered = await _ingested(env)
    doc_id = registered["document"]["id"]
    first = env.get(f"/documents/{doc_id}/pages?limit=3").json()
    assert [p["page_no"] for p in first["data"]] == [1, 2, 3]
    assert first["meta"]["limit"] == 3 and first["meta"]["next_cursor"]
    page = first["data"][0]
    assert page["kind"] == "vector_text" and page["language"] == "en" and page["needs_ocr"] is False
    assert page["stages"] == [
        {"stage": "classify", "status": "succeeded", "reason": None, "attempts": 1}
    ]
    rest = env.get(
        f"/documents/{doc_id}/pages?limit=3&cursor={first['meta']['next_cursor']}"
    ).json()
    assert [p["page_no"] for p in rest["data"]] == [4] and rest["meta"]["next_cursor"] is None
    assert env.get(f"/documents/{doc_id}/pages?limit=101").status_code == 422
    assert env.get("/documents/nope/pages").status_code == 404


async def test_a_document_and_a_project_listing_show_state_and_never_the_purging(env: Env) -> None:
    project, registered = await _ingested(env)
    doc_id = registered["document"]["id"]
    one = env.get(f"/documents/{doc_id}").json()
    assert (
        one["status"] == "active" and one["page_count"] == 4 and one["profile_state"] == "proposed"
    )
    assert one["content_sha256"] and len(one["content_sha256"]) == 64
    assert one["latest_job"]["status"] == "awaiting_confirmation"
    assert one["tags"] == {"system": None, "equipment": None, "department": None}
    listing = env.get(f"/projects/{project['id']}/documents").json()
    assert [d["id"] for d in listing["data"]] == [doc_id]
    assert listing["data"][0]["tags"] is None  # tags ride on the single read only
    assert listing["data"][0]["latest_job"]["counts"] == {}  # derived counts are not in a list
    assert env.get(f"/projects/{project['id']}/documents?status=active").json()["data"]
    assert env.get(f"/projects/{project['id']}/documents?status=excluded").json()["data"] == []
    assert env.get(f"/projects/{project['id']}/documents?status=purging").status_code == 422
    assert env.get("/projects/nope/documents").status_code == 404
    env.stack.documents.rows[doc_id].mark_purging(env.stack.clock())
    assert env.get(f"/documents/{doc_id}").status_code == 404
    assert env.get(f"/projects/{project['id']}/documents").json()["data"] == []


async def test_the_characterization_of_a_document_and_of_the_project(env: Env) -> None:
    project, registered = await _ingested(env)
    doc_id = registered["document"]["id"]
    record = env.get(f"/documents/{doc_id}/characterization").json()
    assert record["producer_family"] == "eplan" and record["page_count"] == 4
    assert record["conventions"] == {"iec_81346": 120} and record["text_kinds"] == {
        "vector_text": 4
    }
    assert record["symbol_standard_guess"] == "iec_60617" and record["ocr_needed_pages"] == 0
    assert record["document_language"] == "en" and record["mixed_standards"] is False
    assert record["pipeline_version"] and record["shared_version"] and record["rules_version"]
    report = env.get(f"/projects/{project['id']}/characterization").json()
    assert report["documents"] == 1 and report["pages"] == 4
    assert report["producers"] == {"eplan": 1} and report["conventions"] == {"iec_81346": 1}
    assert report["versions"]["shared"] == [record["shared_version"]]
    assert env.get("/documents/nope/characterization").status_code == 404
    assert env.get("/projects/nope/characterization").status_code == 404


async def test_f0_ac_3_failed_pages_reach_both_characterization_responses(env: Env) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    pdf = FakePdfInspection(
        make_summary(page_count=3, creator="EPLAN Electric P8"),
        [tokens_page(n, IEC_TOKENS) for n in range(1, 4)],
        failures={2: StageReason.PARSE_ERROR},
    )
    await env.run_worker(registered["job"]["id"], pdf)
    record = env.get(f"/documents/{registered['document']['id']}/characterization").json()
    assert record["page_count"] == 3 and record["failed_pages"] == 1
    report = env.get(f"/projects/{project['id']}/characterization").json()
    assert report["pages"] == 3 and report["failed_pages"] == 1


async def test_f0_ac_3_a_document_with_every_page_read_has_no_failed_pages(env: Env) -> None:
    project, registered = await _ingested(env)
    record = env.get(f"/documents/{registered['document']['id']}/characterization").json()
    assert record["failed_pages"] == 0
    assert env.get(f"/projects/{project['id']}/characterization").json()["failed_pages"] == 0


async def test_f0_ac_6_a_document_without_a_convention_claims_no_symbol_standard(
    env: Env,
) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    await env.run_worker(registered["job"]["id"], _pdf(tokens=()))
    record = env.get(f"/documents/{registered['document']['id']}/characterization").json()
    assert record["symbol_standard_guess"] is None and record["conventions"] == {}
    assert not any(b.startswith("convention:") for b in record["symbol_standard_basis"])
    report = env.get(f"/projects/{project['id']}/characterization").json()
    assert report["symbol_standards"] == {"unknown": 1}


async def test_a_document_not_yet_characterized_is_a_404(env: Env) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    assert env.get(f"/documents/{registered['document']['id']}/characterization").status_code == 404
    assert env.get(f"/projects/{project['id']}/characterization").json()["documents"] == 0


async def test_the_pdfs_own_metadata_arrives_escaped(env: Env) -> None:
    """R1-4: Creator and Producer are untrusted text; a bidi override or a line break in them
    reaches the client as an escape sequence."""
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    hostile = f"EPLAN{_RLO}\r\nforged{_ZWSP}"
    await env.run_worker(registered["job"]["id"], _pdf(creator=hostile, producer=hostile))
    record = env.get(f"/documents/{registered['document']['id']}/characterization").json()
    assert record["creator"] == "EPLAN\\u202e\\x0d\\x0aforged\\u200b"
    assert _RLO not in record["producer"] and "\r" not in record["producer"]


async def test_a_job_is_cancelled_once_and_a_finished_job_cannot_be(env: Env) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    job_id = registered["job"]["id"]
    cancelled = env.post(f"/ingestion-jobs/{job_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    events = len(env.stack.outbox.records)
    assert env.post(f"/ingestion-jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert len(env.stack.outbox.records) == events  # repeating it writes nothing
    assert env.post("/ingestion-jobs/nope/cancel").status_code == 404

    resumed = env.post(f"/ingestion-jobs/{job_id}/resume")
    assert resumed.status_code == 202 and resumed.json()["status"] == "queued"
    again = env.post(f"/ingestion-jobs/{job_id}/resume")
    assert again.status_code == 409 and _code(again) == "electrical.job_conflict"
    assert env.post("/ingestion-jobs/nope/resume").status_code == 404

    await env.run_worker(job_id)
    env.post(f"/documents/{registered['document']['id']}/profile-confirmation")
    await env.run_worker(job_id)
    finished = env.post(f"/ingestion-jobs/{job_id}/cancel")
    assert finished.status_code == 409 and _code(finished) == "electrical.job_conflict"


def test_a_queued_job_reports_its_position_and_an_estimate_once_there_is_history(env: Env) -> None:
    project = env.project()
    for number in range(2):
        env.file(f"p{number}")
        env.register(project["id"], f"p{number}")
    jobs = sorted(env.stack.jobs.rows.values(), key=lambda j: j.created_at)
    views = [env.get(f"/ingestion-jobs/{j.id}").json() for j in jobs]
    assert [v["queue_position"] for v in views] == [1, 2]
    assert all(v["estimated_wait_s"] is None for v in views)  # no finished run to estimate from


def test_resume_is_idempotent_on_the_same_key_and_does_not_leak_between_jobs(env: Env) -> None:
    project = env.project()
    env.file("f1")
    env.file("f2")
    a = env.register(project["id"], "f1").json()["job"]["id"]
    b = env.register(project["id"], "f2").json()["job"]["id"]
    for job in (a, b):
        env.post(f"/ingestion-jobs/{job}/cancel")
    first = env.post(f"/ingestion-jobs/{a}/resume", **{"Idempotency-Key": "same"})
    other_job = env.post(f"/ingestion-jobs/{b}/resume", **{"Idempotency-Key": "same"})
    assert first.status_code == 202
    assert (
        other_job.status_code == 409 and _code(other_job) == "common.conflict"
    )  # not job a's answer
    assert (
        env.post(f"/ingestion-jobs/{a}/resume", **{"Idempotency-Key": "same"}).json()
        == first.json()
    )


# ================================================================ the replacement preview (AC-11)
def test_the_preview_lists_what_would_be_replaced_and_kept(env: Env) -> None:
    project = env.project()
    env.file("older")
    env.file("kept", tags={"system": "S9"})
    env.file("newer")
    registered = env.register(project["id"], "older").json()
    env.stack.namesakes.set("newer", replaced=["older"], kept=["kept"])
    sha = hashlib.sha256(_PDF + b"older").hexdigest()
    stored = env.stack.documents.rows[registered["document"]["id"]]
    stored.content_sha256 = sha

    for checksum, identical in [(None, None), (sha.upper(), True), ("b" * 64, False)]:
        suffix = "" if checksum is None else f"?checksum={checksum}"
        body = env.get(f"/files/newer/replacement-preview{suffix}").json()
        assert body["file_id"] == "newer"
        (replaced,) = body["replaced"]
        assert replaced["file_id"] == "older" and replaced["name"] == "older.pdf"
        assert replaced["documents"] == [
            {
                "document_id": registered["document"]["id"],
                "project_id": project["id"],
                "status": "active",
                "content_identical": identical,
            }
        ]
        assert body["kept"] == [
            {
                "file_id": "kept",
                "name": "kept.pdf",
                "tags": {"system": "S9", "equipment": None, "department": None},
            }
        ]
    assert len(env.stack.documents.rows) == 1 and env.stack.jobs.rows  # it wrote nothing


def test_the_preview_of_an_unknown_file_is_404_and_a_bad_checksum_422_without_echo(
    env: Env,
) -> None:
    assert env.get("/files/missing/replacement-preview").status_code == 404
    bad = env.get(f"/files/newer/replacement-preview?checksum={_HOSTILE[:100]}")
    assert bad.status_code == 422 and "evil" not in bad.text
    long = env.get(f"/files/newer/replacement-preview?checksum={'a' * 200}")
    assert long.status_code == 422


# ====================================================================== permissions (AC-12)
def test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile(env: Env) -> None:
    project = env.project()
    env.file("f1")
    env.as_role("member")
    assert env.get(f"/projects/{project['id']}").status_code == 200
    assert env.get(f"/projects/{project['id']}/documents").status_code == 200
    assert env.get("/files/f1/replacement-preview").status_code == 200
    for response in (
        env.register(project["id"], "f1"),
        env.put(f"/projects/{project['id']}/profile", {"body": {"schema": 1}}),
        env.put(
            f"/projects/{project['id']}/profile",
            {"body": {"schema": 1, "plant_designation": "kks"}},  # F2-AC-17
        ),
        env.post("/projects", {"space_id": SPACE, "name": "x"}),
        env.post("/ingestion-jobs/x/cancel"),
    ):
        assert response.status_code == 403 and _code(response) == "authz.forbidden"
        assert response.json()["detail"] == "missing permission: electrical:manage"
    assert env.stack.documents.rows == {}


def test_a_viewer_reads_and_nothing_more(env: Env) -> None:
    project = env.project()
    env.as_role("viewer")
    assert env.get(f"/projects/{project['id']}/profile").status_code == 200
    assert env.get(f"/projects?space_id={SPACE}").status_code == 200
    denied = env.patch(f"/projects/{project['id']}", {"name": "x"})
    assert denied.status_code == 403
    kks = env.put(
        f"/projects/{project['id']}/profile", {"body": {"schema": 1, "plant_designation": "kks"}}
    )
    assert kks.status_code == 403 and _code(kks) == "authz.forbidden"  # F2-AC-17
    env.as_role("owner")
    assert env.get(f"/projects/{project['id']}/profile").json()["version"] == 1


# ======================================================================== the contract
_SPEC = yaml.safe_load(
    pathlib.Path(__file__)
    .parents[2]
    .joinpath("docs/design/openapi.yaml")
    .read_text(encoding="utf-8")
)


def test_the_profile_body_description_names_every_profile_field() -> None:
    """F2-AC-18: a field added to the profile without its description in ``openapi.yaml`` fails
    here, so the contract cannot lag the code."""
    description = _SPEC["components"]["schemas"]["DrawingProfileIn"]["properties"]["body"][
        "description"
    ]
    for key in _TOP_KEYS:
        if key != "schema":
            assert key in description, f"{key} is a profile field and not in the description"
    for value in (*PlantDesignation, *DeviceFunctionNumbers):
        assert value.value in description
    proposal = _SPEC["components"]["schemas"]["ProfileProposalOut"]["properties"]
    for key in ("plant_designation", "device_function_numbers"):
        assert key in proposal["field_confidence"]["description"]
    assert "systems" in proposal["basis"]["description"]


def test_every_electrical_schema_has_the_published_properties_and_required_set() -> None:
    """``openapi.yaml`` is the binding contract; a field on the wire that the document does not
    state (or the reverse) is a defect neither side's tests would find."""
    live = _make_env().app.openapi()["components"]["schemas"]
    spec = _SPEC["components"]["schemas"]
    names = [
        name
        for name in live
        if name.startswith(
            ("Electrical", "Drawing", "Effective", "Ingestion", "Profile", "Replace", "Reprocess")
        )
        or name
        in {
            "CharacterizationOut",
            "CharacterizationReportOut",
            "DocumentPageOut",
            "PageStageOut",
            "DocumentRegisterIn",
            "DocumentRegisteredOut",
            "AffectedDocumentOut",
            "KeptNamesakeOut",
            "ReplacedFileOut",
            "ReplacementPreviewOut",
        }
    ]
    assert len(names) >= 21
    for name in names:
        assert name in spec, f"{name} is on the wire and not in openapi.yaml"
        assert set(spec[name]["properties"]) == set(live[name]["properties"]), name
        assert set(spec[name].get("required", ())) == set(live[name].get("required", ())), name
