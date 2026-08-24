"""ASGI tests for the Spaces router (``docs/spaces-backend-plan.md`` step 12).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app``, with
ONE space store behind every face — the module's own use-cases, the
``ActiveSpaces`` seam ``files``/``conversations`` prove ids through, and the
``SpaceLock`` the quota holds. That single-instance wiring is the point of
this suite as much as any individual assertion: a space created over HTTP has
to be a space the very next upload can file into, and separate fakes would let
those two agree by luck.

What is pinned here, against §3.7:

* the four routes and their statuses, including the ``201``/``409`` a
  duplicate name produces and the read/write asymmetry a deleted space gets;
* **the three counters**, which are the reason this router touches three
  modules: ``bytes_used``/``file_count`` are sums over ``files``,
  ``conversation_count`` is a count over ``conversations``, and a space that
  owns nothing must read ``0`` rather than vanish — the ``GROUP BY`` returns
  no row for it;
* **the axis end to end**: create a space, upload into it, open a thread in
  it, and see all three numbers move — then delete the space and see the
  files, the threads and the stored objects go with it;
* the mandatory ``?space_id=`` on ``GET /files``, from the side that hands
  out the id;
* both cross-module services failing CLOSED while unwired (plan step 13 binds
  them), rather than a router quietly answering as if a space store existed.
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
from app.framework.di.space_deletion import DeleteSpaceService
from app.framework.errors import UnauthorizedError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.types import Uuid
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.conversations.application.use_cases import PurgeSpaceConversations
from app.modules.files.application.use_cases import PurgeSpaceFiles
from app.modules.spaces.application.use_cases import DeleteSpace
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import ConversationsStack, build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import FilesMediaStack, build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_spaces import SpacesStack, build_spaces
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_CREDENTIALS = build_credentials()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"
_AUTH = {"Authorization": f"Bearer {_GOOD}"}


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


# `owner`, the `test_api_files_media_router` convention: `spaces:write` is
# deliberately not a member's (`RoleCatalog` says why at length), and role
# sensitivity is `test_api_rbac.py`'s subject, pinned there for every
# operation and every role at once.
class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        raise AssertionError("not exercised")


class _RecordingKnowledgePurge:
    """The cascade's knowledge third, recorded rather than run.

    `knowledge` has no in-memory stack in this suite and needs none: what the
    router owes is that the CASCADE is what `DELETE` reaches, and the
    knowledge step's own behaviour is pinned where it lives
    (`test_space_deletion.py`, and live in `test_space_cascade_live.py`).
    """

    def __init__(self) -> None:
        self.calls: list[Uuid] = []

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        self.calls.append(space_id)
        return 0


def _make_app(
    *, wired: bool = True
) -> tuple[FastAPI, SpacesStack, FilesMediaStack, ConversationsStack, _RecordingKnowledgePurge]:
    spaces = build_spaces()
    # ONE store behind all three: the space `POST /spaces` mints is the space
    # `POST /files` proves, locks and charges, and the space `POST
    # /conversations` files its thread under.
    files_media = build_files_media(spaces=spaces.gateway)
    conversations = build_conversations(spaces=spaces.query)
    knowledge_purge = _RecordingKnowledgePurge()
    deletion = DeleteSpaceService(
        DeleteSpace(spaces.repository),
        knowledge=knowledge_purge,
        files=PurgeSpaceFiles(files_media.file_repository, files_media.storage),
        conversations=PurgeSpaceConversations(conversations.repository),
    )
    services = ApiServices(
        settings=Settings(),
        orchestrator=AgentOrchestrator(
            OrchestratorDependencies(
                agents=InMemoryAgentRegistry(),
                executor=AgentLifecycleExecutor(),
                providers=_FakeResolver(),
                conversations=conversations.service,
                authorization=build_authorization(),
            )
        ),
        hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
        agents=InMemoryAgentRegistry(),
        conversations=conversations.use_cases,
        workflows=InMemoryWorkflowRegistry(),
        files=files_media.files,
        # Always wired, unlike the three space-shaped fields below: the file
        # cascade has nothing to do with `spaces` being configured, and the
        # unwired case is exercised where it belongs (the files router).
        file_deletion=files_media.file_deletion,
        media=files_media.media,
        workspace=build_workspace_usage().workspace,
        usage=build_workspace_usage().usage,
        credentials=_CREDENTIALS.credentials,
        knowledge=_KNOWLEDGE.knowledge,
        integrations=_INTEGRATIONS.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
        spaces=spaces.use_cases if wired else None,
        space_deletion=deletion if wired else None,
        space_quota=files_media.space_quota if wired else None,
    )
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    return app, spaces, files_media, conversations, knowledge_purge


def _create(client: TestClient, name: str = "Research") -> dict[str, object]:
    response = client.post("/api/v1/spaces", json={"name": name}, headers=_AUTH)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _upload(client: TestClient, space_id: str, *, name: str, size: int) -> str:
    response = client.post(
        "/api/v1/files",
        json={
            "space_id": space_id,
            "name": name,
            "content_type": "application/pdf",
            "size_bytes": size,
        },
        headers=_AUTH,
    )
    assert response.status_code == 201, response.text
    file_id: str = response.json()["file_id"]
    return file_id


# --------------------------------------------------------------------------- #
# auth                                                                         #
# --------------------------------------------------------------------------- #
def test_every_space_route_requires_a_bearer() -> None:
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    assert client.get("/api/v1/spaces").status_code == 401
    assert client.post("/api/v1/spaces", json={"name": "x"}).status_code == 401
    assert client.patch("/api/v1/spaces/whatever", json={"name": "x"}).status_code == 401
    assert client.delete("/api/v1/spaces/whatever").status_code == 401


# --------------------------------------------------------------------------- #
# POST /spaces                                                                 #
# --------------------------------------------------------------------------- #
def test_creating_a_space_returns_201_and_an_empty_one() -> None:
    """The counters are zeros WITHOUT a query (router docstring): a row
    inserted one statement ago owns nothing, and that is a definition rather
    than an assumption."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)

    body = _create(client, "Research")

    assert body["name"] == "Research"
    assert (body["bytes_used"], body["file_count"], body["conversation_count"]) == (0, 0, 0)
    assert body["created_at"]
    # A single resource is not wrapped, and never leaks the optimistic lock.
    assert "data" not in body
    assert "version" not in body
    assert "deleted_at" not in body


def test_a_blank_name_is_422_with_the_domains_own_reason() -> None:
    """``SpaceName`` owns the rule, not a Pydantic constraint — so the reason
    on the wire is the one the domain gives."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)

    response = client.post("/api/v1/spaces", json={"name": "   "}, headers=_AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"


def test_a_duplicate_name_is_a_409_from_the_index_and_case_is_folded() -> None:
    """``ux_spaces_ws_name`` is partial and folds case, and the adapter turns
    its ``23505`` into ``spaces.duplicate_name`` — never a read-then-insert,
    which would answer the same question one round trip earlier and the wrong
    answer under a race."""
    app, spaces, _, _, _ = _make_app()
    client = TestClient(app)
    _create(client, "Research")

    response = client.post("/api/v1/spaces", json={"name": "research"}, headers=_AUTH)

    assert response.status_code == 409
    assert response.json()["code"] == "spaces.duplicate_name"
    assert len(spaces.repository.rows) == 1


# --------------------------------------------------------------------------- #
# GET /spaces                                                                  #
# --------------------------------------------------------------------------- #
def test_listing_wraps_in_the_page_envelope_newest_first() -> None:
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    older = _create(client, "Older")
    newer = _create(client, "Newer")

    response = client.get("/api/v1/spaces", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["data"]] == [newer["id"], older["id"]]
    assert body["meta"] == {"next_cursor": None, "limit": 20}


def test_listing_pages_with_an_opaque_cursor() -> None:
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    older = _create(client, "Older")
    newer = _create(client, "Newer")

    first = client.get("/api/v1/spaces?limit=1", headers=_AUTH).json()
    assert [row["id"] for row in first["data"]] == [newer["id"]]
    cursor = first["meta"]["next_cursor"]
    assert cursor is not None

    second = client.get(f"/api/v1/spaces?limit=1&cursor={cursor}", headers=_AUTH).json()
    assert [row["id"] for row in second["data"]] == [older["id"]]
    assert second["meta"]["next_cursor"] is None


def test_an_empty_space_reads_zero_rather_than_disappearing() -> None:
    """The ``GROUP BY`` returns no row for a space that owns nothing, so the
    router's default is what puts the ``0`` on the wire. Without it the field
    would be missing — or the response would fail to validate."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    _create(client, "Empty")

    (row,) = client.get("/api/v1/spaces", headers=_AUTH).json()["data"]

    assert (row["bytes_used"], row["file_count"], row["conversation_count"]) == (0, 0, 0)


def test_the_counters_are_this_spaces_and_no_other_spaces() -> None:
    """The reason this router holds three bundles: every number beside a name
    is a sum over ANOTHER module's table, narrowed to this space."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    mine = _create(client, "Mine")["id"]
    theirs = _create(client, "Theirs")["id"]
    assert isinstance(mine, str) and isinstance(theirs, str)
    _upload(client, mine, name="a.pdf", size=100)
    _upload(client, mine, name="b.pdf", size=250)
    _upload(client, theirs, name="c.pdf", size=999)
    client.post(
        "/api/v1/conversations",
        json={"space_id": mine, "agent_key": "echo", "title": "T"},
        headers=_AUTH,
    )

    rows = {row["id"]: row for row in client.get("/api/v1/spaces", headers=_AUTH).json()["data"]}

    assert (rows[mine]["bytes_used"], rows[mine]["file_count"]) == (350, 2)
    assert rows[mine]["conversation_count"] == 1
    assert (rows[theirs]["bytes_used"], rows[theirs]["file_count"]) == (999, 1)
    assert rows[theirs]["conversation_count"] == 0


def test_a_soft_deleted_file_gives_its_bytes_back_to_the_listing() -> None:
    """``bytes_used`` counts ACTIVE files, matching the quota it describes —
    otherwise the number a client reads and the number the ceiling compares
    against would be two different things."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]
    assert isinstance(space_id, str)
    file_id = _upload(client, space_id, name="a.pdf", size=100)
    _upload(client, space_id, name="b.pdf", size=250)

    assert client.delete(f"/api/v1/files/{file_id}", headers=_AUTH).status_code == 204
    (row,) = client.get("/api/v1/spaces", headers=_AUTH).json()["data"]

    assert (row["bytes_used"], row["file_count"]) == (250, 1)


# --------------------------------------------------------------------------- #
# PATCH /spaces/{id}                                                           #
# --------------------------------------------------------------------------- #
def test_renaming_returns_the_renamed_space_with_its_real_counters() -> None:
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client, "Draft")["id"]
    assert isinstance(space_id, str)
    _upload(client, space_id, name="a.pdf", size=100)

    response = client.patch(f"/api/v1/spaces/{space_id}", json={"name": "Final"}, headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Final"
    # NOT the zeros `POST` may answer: a space being renamed has been around.
    assert (body["bytes_used"], body["file_count"]) == (100, 1)


def test_renaming_to_the_same_name_succeeds_and_writes_nothing() -> None:
    """``Space.rename``'s no-op rule, observable only through the store: a
    "modified at" that moves when nothing was modified is a false record."""
    app, spaces, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client, "Research")["id"]

    response = client.patch(f"/api/v1/spaces/{space_id}", json={"name": "Research"}, headers=_AUTH)

    assert response.status_code == 200
    assert spaces.repository.saved == []


def test_renaming_an_unknown_space_is_404() -> None:
    app, _, _, _, _ = _make_app()
    client = TestClient(app)

    response = client.patch("/api/v1/spaces/no-such-space", json={"name": "x"}, headers=_AUTH)

    assert response.status_code == 404


def test_renaming_a_deleted_space_is_409_not_404() -> None:
    """The read/write asymmetry: reads say "gone", writes say "deleted".
    Telling a client 404 about a row this very request could still find would
    be the less truthful of the two."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]
    client.delete(f"/api/v1/spaces/{space_id}", headers=_AUTH)

    response = client.patch(f"/api/v1/spaces/{space_id}", json={"name": "x"}, headers=_AUTH)

    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# DELETE /spaces/{id}                                                          #
# --------------------------------------------------------------------------- #
def test_deleting_a_space_takes_its_files_threads_and_objects_with_it() -> None:
    """§3.6 over the wire. The other space is here to say the cascade is
    narrowed to one — a DELETE this wide is exactly the one that must not be
    a tenant-wide sweep."""
    app, _, files_media, conversations, knowledge = _make_app()
    client = TestClient(app)
    doomed = _create(client, "Doomed")["id"]
    kept = _create(client, "Kept")["id"]
    assert isinstance(doomed, str) and isinstance(kept, str)
    _upload(client, doomed, name="a.pdf", size=100)
    _upload(client, kept, name="b.pdf", size=100)
    client.post(
        "/api/v1/conversations",
        json={"space_id": doomed, "agent_key": "echo", "title": "T"},
        headers=_AUTH,
    )

    response = client.delete(f"/api/v1/spaces/{doomed}", headers=_AUTH)

    assert response.status_code == 204
    assert response.content == b""
    assert knowledge.calls == [doomed]
    assert [row.space_id for row in files_media.file_repository.rows.values()] == [kept]
    assert conversations.repository.rows == {}
    # The stored object went too, by the key the row named (§3.6 step 5).
    assert files_media.storage.deleted
    # And the space is gone from the listing.
    assert [row["id"] for row in client.get("/api/v1/spaces", headers=_AUTH).json()["data"]] == [
        kept
    ]


def test_deleting_twice_is_another_204_and_repairs_rather_than_replays() -> None:
    """No ``Idempotency-Key`` ledger, deliberately (router docstring): the
    cascade is idempotent by construction, so a retry RE-RUNS the six steps
    after the mark — which is how a sequence that died half-way is repaired.
    A ledger would have replayed the stored answer and skipped them."""
    app, _, _, _, knowledge = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]

    first = client.delete(f"/api/v1/spaces/{space_id}", headers=_AUTH)
    second = client.delete(f"/api/v1/spaces/{space_id}", headers=_AUTH)

    assert (first.status_code, second.status_code) == (204, 204)
    assert knowledge.calls == [space_id, space_id]


def test_deleting_an_unknown_space_is_404_and_touches_nothing() -> None:
    """Step 1 is the ONLY existence check in the sequence — none of the six
    after it can tell an empty space from an absent one."""
    app, _, _, _, knowledge = _make_app()
    client = TestClient(app)

    response = client.delete("/api/v1/spaces/no-such-space", headers=_AUTH)

    assert response.status_code == 404
    assert knowledge.calls == []


# --------------------------------------------------------------------------- #
# the axis, from the side that hands out the id                                #
# --------------------------------------------------------------------------- #
def test_a_created_space_is_immediately_usable_by_the_listings_that_require_one() -> None:
    """The single-store claim, end to end: ``?space_id=`` is mandatory on
    ``GET /files`` now, and the only way a client can satisfy it is with an
    id this router minted."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]
    assert isinstance(space_id, str)
    file_id = _upload(client, space_id, name="a.pdf", size=100)

    listed = client.get(f"/api/v1/files?space_id={space_id}", headers=_AUTH).json()

    assert [row["id"] for row in listed["data"]] == [file_id]
    # And a space that was never minted lists nothing rather than 404ing: a
    # listing is not an existence oracle.
    other = client.get("/api/v1/files?space_id=no-such-space", headers=_AUTH).json()
    assert other["data"] == []


def test_uploading_into_a_space_that_does_not_exist_is_404() -> None:
    """The write side of the same seam, and the asymmetry with the listing
    above: a write proves the space, because a row filed under a space that
    does not exist is invisible to every listing forever."""
    app, _, files_media, _, _ = _make_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/files",
        json={
            "space_id": "no-such-space",
            "name": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        headers=_AUTH,
    )

    assert response.status_code == 404
    assert files_media.file_repository.rows == {}
    assert files_media.storage.presigned_puts == []


def test_uploading_into_a_deleted_space_is_404_too() -> None:
    """One answer for missing, foreign and deleted alike: a caller who cannot
    write to a space has no right to learn that it exists."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]
    client.delete(f"/api/v1/spaces/{space_id}", headers=_AUTH)

    response = client.post(
        "/api/v1/files",
        json={
            "space_id": space_id,
            "name": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        headers=_AUTH,
    )

    assert response.status_code == 404


def test_the_quota_is_enforced_on_the_wire_now_that_the_route_goes_through_it() -> None:
    """Step 12's other half: ``POST /files`` registers through
    ``SpaceQuotaService`` rather than the bare registrar, so the 1 GiB
    ceiling (§3.3) is a real refusal rather than a number nothing consults."""
    app, _, _, _, _ = _make_app()
    client = TestClient(app)
    space_id = _create(client)["id"]
    assert isinstance(space_id, str)
    ceiling = Settings().limits.max_space_bytes
    per_file = Settings().limits.max_upload_bytes
    # Fill the space to within one byte of the ceiling using files that each
    # pass the PER-FILE cap — the two limits are different rules and this one
    # must be the space's.
    filled = 0
    index = 0
    while filled + per_file <= ceiling:
        _upload(client, space_id, name=f"f{index}.pdf", size=per_file)
        filled += per_file
        index += 1

    response = client.post(
        "/api/v1/files",
        json={
            "space_id": space_id,
            "name": "last.pdf",
            "content_type": "application/pdf",
            "size_bytes": ceiling - filled + 1,
        },
        headers=_AUTH,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "spaces.quota_exceeded"


# --------------------------------------------------------------------------- #
# fail-closed while step 13 is still owed                                      #
# --------------------------------------------------------------------------- #
def test_the_routes_fail_closed_while_the_services_are_unwired() -> None:
    """Plan step 12 is this layer; step 13 is the Composition Root. Until then
    the routes exist, are typed and answer ``common.internal`` — rather than
    pretending a space store this deployment does not have."""
    app, _, _, _, _ = _make_app(wired=False)
    client = TestClient(app)

    assert client.get("/api/v1/spaces", headers=_AUTH).json()["code"] == "common.internal"
    assert (
        client.post("/api/v1/spaces", json={"name": "x"}, headers=_AUTH).json()["code"]
        == "common.internal"
    )
    assert (
        client.delete("/api/v1/spaces/whatever", headers=_AUTH).json()["code"] == "common.internal"
    )
    # And the upload route with it: a fallback to the unmetered registrar
    # would have been a silent skip of the quota this step made binding.
    assert (
        client.post(
            "/api/v1/files",
            json={
                "space_id": "whatever",
                "name": "a.pdf",
                "content_type": "application/pdf",
                "size_bytes": 10,
            },
            headers=_AUTH,
        ).json()["code"]
        == "common.internal"
    )
