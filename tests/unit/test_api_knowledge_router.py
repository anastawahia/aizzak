"""ASGI tests for the Knowledge router (6.1-و-3).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory knowledge stack (``support_knowledge``). What these
pin, against 03 §1/§2 and 06 §7:

* **the 503 is a route, not an absence** — with ``search=None`` (the
  production shape while no embedding adapter exists) ``POST /search``
  answers 503 ``knowledge.search_unavailable``, never FastAPI's 404 for an
  unregistered path;
* the same route works completely once a retrieval face IS present: ``query``
  and ``k`` arrive verbatim, the chunks come back in the API-04 envelope;
* ``k``'s bounds — the spec's ``le=50`` and the ``ge=1`` floor added with it;
* the listing shows EVERY lifecycle status (a ``pending`` document is the
  answer to "did my upload get picked up?"), newest first, this tenant only;
* ``DocumentOut`` carries no ``error`` field, so an indexing failure's
  internal reason never reaches a tenant;
* 404 for an unknown document AND for another tenant's — indistinguishable
  on purpose;
* **nothing is indexed until ``POST /knowledge/documents`` asks for it** — a
  completed upload registers no document, and the route that does refuses a
  file that is not ready and a file that already has one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

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
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.knowledge.domain.value_objects import IndexStatus
from app.modules.knowledge.ports.retrieval import RetrievedChunk
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import (
    SEED_SPACE,
    KnowledgeStack,
    RecordingRetrieval,
    build_knowledge,
    seed_document,
)
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_FILES_MEDIA = build_files_media()
_WORKSPACE_USAGE = build_workspace_usage()
_CREDENTIALS = build_credentials()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_W2 = "018f0000-0000-7000-8000-0000000000w2"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"


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


def _make_app(*, retrieval: RecordingRetrieval | None = None) -> tuple[FastAPI, KnowledgeStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_knowledge(retrieval=retrieval)
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
        knowledge=stack.knowledge,
        integrations=_INTEGRATIONS.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
    )
    app = create_app(services, http_authenticator=_FakeAuth(), ws_authenticator=_FakeWsAuth())
    return app, stack


def _auth(token: str = _GOOD) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# auth                                                                        #
# --------------------------------------------------------------------------- #
def test_every_route_refuses_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        assert client.post("/api/v1/knowledge/search", json={"query": "x"}).status_code == 401
        assert client.get("/api/v1/knowledge/documents").status_code == 401
        assert client.get("/api/v1/knowledge/documents/whatever").status_code == 401


# --------------------------------------------------------------------------- #
# POST /search — the visible gap, and the working route behind it              #
# --------------------------------------------------------------------------- #
def test_search_is_registered_and_answers_503_while_unwired() -> None:
    """The whole reason the route is built now rather than later: a 404 would
    say "no such capability" about one the contract defines and the code
    implements. 503 says the true thing."""
    app, stack = _make_app()  # search=None — the production shape
    assert stack.knowledge.search is None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "how do refunds work", "space_id": SEED_SPACE},
            headers=_auth(),
        )
    assert response.status_code == 503
    assert response.json()["code"] == "knowledge.search_unavailable"


def test_search_returns_chunks_in_the_envelope_when_retrieval_is_present() -> None:
    retrieval = RecordingRetrieval(
        chunks=[
            RetrievedChunk(
                document_id="d1",
                chunk_id="c1",
                text="first",
                score=0.9,
                file_name="refund-policy.pdf",
                page_number=3,
                section="Executive Summary",
            ),
            RetrievedChunk(document_id="d1", chunk_id="c2", text="second", score=0.4),
        ]
    )
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "refunds", "k": 2, "space_id": SEED_SPACE},
            headers=_auth(),
        )
    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "document_id": "d1",
                "chunk_id": "c1",
                "text": "first",
                "score": 0.9,
                # retrieval plan §3.1/§3.9 (س-19, P-18) — the three citation
                # fields carry the REAL values the port handed the router,
                # not merely present-but-null (§6 risk 1's test rule).
                "file_name": "refund-policy.pdf",
                "page_number": 3,
                "section": "Executive Summary",
            },
            {
                "document_id": "d1",
                "chunk_id": "c2",
                "text": "second",
                "score": 0.4,
                # No citation metadata on this chunk (e.g. a pre-P-18 point,
                # or a parser that never emitted one) degrades honestly to
                # `null` on the wire rather than being omitted.
                "file_name": None,
                "page_number": None,
                "section": None,
            },
        ],
        "meta": {"next_cursor": None, "limit": 2},
    }


def test_search_passes_query_and_k_through_verbatim() -> None:
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge/search",
            json={"query": "  spaced  ", "k": 7, "space_id": SEED_SPACE},
            headers=_auth(),
        )
    assert retrieval.calls == [("  spaced  ", 7)]


def test_search_passes_the_bodys_space_down_verbatim() -> None:
    """س-32 (owner decision 2026-08-26). This test asserted the opposite until
    then — ``space_id`` was not on ``KnowledgeSearchIn`` and the route passed
    ``None`` deliberately, the ONE caller in the system that searched every
    space at once. The body carries it now, and the route neither invents nor
    interprets it."""
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge/search",
            json={"query": "q", "space_id": "space-research"},
            headers=_auth(),
        )
    assert retrieval.spaces == ["space-research"]


def test_search_without_a_space_is_422_before_retrieval_is_reached() -> None:
    """The wire half of the guard: a body with no ``space_id`` is refused by
    the DTO, so nothing is embedded and the module's own
    ``require_space_scope`` never has to fire for a request that came through
    this route. A blank one is the same refusal — ``min_length=1`` — because
    ``" "`` would otherwise reach the filter and match nothing."""
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/knowledge/search", json={"query": "q"}, headers=_auth()
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/knowledge/search",
                json={"query": "q", "space_id": ""},
                headers=_auth(),
            ).status_code
            == 422
        )
    assert retrieval.calls == []


def test_search_defaults_k_to_five() -> None:
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge/search", json={"query": "q", "space_id": SEED_SPACE}, headers=_auth()
        )
    assert retrieval.calls == [("q", 5)]


def test_search_refuses_k_outside_its_bounds_before_reaching_retrieval() -> None:
    """422 for both ends, and the refusal is the DTO's — nothing is embedded,
    nothing is asked of the vector store."""
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/knowledge/search",
                json={"query": "q", "k": 0, "space_id": SEED_SPACE},
                headers=_auth(),
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/knowledge/search",
                json={"query": "q", "k": 51, "space_id": SEED_SPACE},
                headers=_auth(),
            ).status_code
            == 422
        )
    assert retrieval.calls == []


def test_search_refuses_an_empty_query() -> None:
    retrieval = RecordingRetrieval()
    app, _stack = _make_app(retrieval=retrieval)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/search", json={"query": "", "space_id": SEED_SPACE}, headers=_auth()
        )
    assert response.status_code == 422
    assert retrieval.calls == []


def test_search_returning_nothing_is_an_empty_envelope_not_an_error() -> None:
    """An indexed corpus that simply does not match is a 200 with no rows —
    the reason ``knowledge.not_indexed``/409 is NOT forced onto this route."""
    app, _stack = _make_app(retrieval=RecordingRetrieval())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/search", json={"query": "q", "space_id": SEED_SPACE}, headers=_auth()
        )
    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


# --------------------------------------------------------------------------- #
# GET /documents                                                              #
# --------------------------------------------------------------------------- #
def test_listing_is_empty_in_the_envelope_when_nothing_is_registered() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert response.status_code == 200
    # `meta.limit` is the page size ASKED FOR (6.3-أ), so an empty corpus still
    # reports the default — unlike `POST /search` above, whose bound is `k`.
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 20}}


def test_listing_returns_the_documented_shape() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1, file_id="f1", chunk_count=4)
    stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert response.json() == {
        "data": [
            {
                "id": "d1",
                "file_id": "f1",
                "status": "indexed",
                "chunk_count": 4,
                "created_at": "2026-04-05T06:07:08Z",
            }
        ],
        "meta": {"next_cursor": None, "limit": 20},
    }


def test_listing_includes_every_lifecycle_status() -> None:
    """A ``pending`` row is the answer to "did my upload get picked up?" and a
    ``failed`` row is why a search never finds it. Filtering to ``indexed``
    would leave both questions unanswerable."""
    app, stack = _make_app()
    for index, status in enumerate(
        (IndexStatus.PENDING, IndexStatus.INDEXING, IndexStatus.INDEXED, IndexStatus.FAILED)
    ):
        doc = seed_document(document_id=f"d{index}", workspace_id=_W1, status=status)
        stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert {row["status"] for row in response.json()["data"]} == {
        "pending",
        "indexing",
        "indexed",
        "failed",
    }


def test_the_corpus_pages_newest_first() -> None:
    """The one collection in this router that pages (6.3-ب).

    A corpus grows by a row per completed upload with no ceiling anywhere in
    the design — unlike ``POST /search``, bounded by its own ``k``. Newest
    first, because a client asking "did my upload get picked up?" is asking
    about the row it just created.

    Seeded with real UUIDv7 ids rather than the ``"d1"`` labels the other
    tests use: the cursor a page hands back IS a keyset id, and the codec
    refuses one that is not a UUID (6.3-أ).
    """
    app, stack = _make_app()
    ids = [new_uuid7() for _ in range(3)]
    for document_id in ids:
        doc = seed_document(document_id=document_id, workspace_id=_W1)
        stack.repository.rows[doc.id] = doc

    with TestClient(app) as client:
        first = client.get(
            f"/api/v1/knowledge/documents?space_id={SEED_SPACE}&limit=2", headers=_auth()
        ).json()
        cursor = first["meta"]["next_cursor"]
        second = client.get(
            f"/api/v1/knowledge/documents?space_id={SEED_SPACE}&limit=2&cursor={cursor}",
            headers=_auth(),
        ).json()

    assert [row["id"] for row in first["data"]] == [ids[2], ids[1]]
    assert cursor is not None
    assert [row["id"] for row in second["data"]] == [ids[0]]
    assert second["meta"]["next_cursor"] is None


def test_the_corpus_refuses_a_malformed_cursor() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/knowledge/documents?space_id={SEED_SPACE}&cursor=!!!!", headers=_auth()
        )
    assert response.status_code == 422
    assert response.json()["code"] == "common.invalid_cursor"


def test_the_published_search_body_requires_a_space() -> None:
    """س-32 on the WIRE, not only in the handler.

    The isolation is a published fact about this endpoint: a client reading the
    schema must be able to see that a search names a space, and a future edit
    that defaulted the field would be a silent return to searching everything.
    Read off the live app's own OpenAPI, which is generated from the DTO — so
    this fails on the DTO, not on the document that describes it.
    """
    app, _stack = _make_app()
    schema = app.openapi()["components"]["schemas"]["KnowledgeSearchIn"]

    assert "space_id" in schema["required"]
    assert "query" in schema["required"]
    # `k` stays optional, and the asymmetry IS the decision: a missing `k` asks
    # for the deployment's result-set size, a missing space asked for every
    # space in the workspace.
    assert "k" not in schema["required"]


def test_search_stays_unpaginated() -> None:
    """``k`` is the bound, and "the next 5 most relevant" is not a thing a
    cursor can mean — so the envelope carries ``next_cursor: null`` and no
    ``limit``/``cursor`` parameters exist on the route at all."""
    app, _stack = _make_app()
    parameters = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/api/v1/knowledge/search"]["post"].get(
            "parameters", []
        )
    }
    assert not parameters & {"limit", "cursor"}


def test_listing_never_leaks_a_failure_reason() -> None:
    """``DocumentOut`` has no ``error`` field: a parser's message is an
    internal diagnostic, not a tenant-facing payload."""
    app, stack = _make_app()
    doc = seed_document(
        document_id="d1",
        workspace_id=_W1,
        status=IndexStatus.FAILED,
        error="poppler crashed on /srv/uploads/x.pdf",
    )
    stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert "poppler" not in response.text
    assert "error" not in response.json()["data"][0]


def test_listing_excludes_another_tenants_documents() -> None:
    app, stack = _make_app()
    for doc in (
        seed_document(document_id="d1", workspace_id=_W1),
        seed_document(document_id="d2", workspace_id=_W2),
    ):
        stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == ["d1"]


def test_listing_returns_only_the_named_spaces_documents() -> None:
    """Spaces plan step 12, and the inversion of what this test asserted at
    step 8.

    ``?space_id=`` is mandatory now, so the listing shows ONE space — and the
    two rows it must not show are both here on purpose: a document in another
    space, and a document with no space at all. The second is §5-أ made
    visible: everything indexed before the plan carries no space and is
    reachable from no listing until it is re-indexed. Answering "or has no
    space" would have leaked every workspace's pre-plan corpus into every
    space created after it.
    """
    app, stack = _make_app()
    for doc in (
        seed_document(document_id="d1", workspace_id=_W1),
        seed_document(document_id="d2", workspace_id=_W1, space_id="space-research"),
        seed_document(document_id="d3", workspace_id=_W1, space_id=None),
    ):
        stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == ["d1"]


def test_listing_without_a_space_is_422_not_the_whole_workspace() -> None:
    """The narrowing is mandatory, not defaulted: a client that forgets it
    gets a 422, never the workspace-wide corpus the route used to answer."""
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1)
    stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge/documents", headers=_auth())
    assert response.status_code == 422


def test_listing_is_newest_first() -> None:
    app, stack = _make_app()
    for doc_id in ("d1", "d3", "d2"):
        doc = seed_document(document_id=doc_id, workspace_id=_W1)
        stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert [row["id"] for row in response.json()["data"]] == ["d3", "d2", "d1"]


# --------------------------------------------------------------------------- #
# GET /documents/{id}                                                         #
# --------------------------------------------------------------------------- #
def test_reading_one_document_returns_it_bare() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1, file_id="f9", chunk_count=2)
    stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge/documents/d1", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {
        "id": "d1",
        "file_id": "f9",
        "status": "indexed",
        "chunk_count": 2,
        "created_at": "2026-04-05T06:07:08Z",
    }


def test_reading_an_unknown_document_is_404() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge/documents/nope", headers=_auth())
    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


def test_reading_another_tenants_document_is_404_not_403() -> None:
    """403 would confirm the id exists. The repository's tenant filter makes
    the two cases indistinguishable, and the answer is the same."""
    app, stack = _make_app()
    doc = seed_document(document_id="d2", workspace_id=_W2)
    stack.repository.rows[doc.id] = doc
    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge/documents/d2", headers=_auth())
    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


# --------------------------------------------------------------------------- #
# POST /knowledge/documents — manual indexing                                 #
# --------------------------------------------------------------------------- #
def test_indexing_an_uploaded_file_answers_202_with_a_pending_document() -> None:
    """202 and not 201: the document is registered, and a worker will embed
    it. 201 would promise a corpus entry that answers searches, which this one
    does not until the pipeline has run."""
    app, stack = _make_app()
    stack.files.add("f1")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=_auth()
        )
    assert response.status_code == 202
    body = response.json()
    assert body["file_id"] == "f1"
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    # The document really landed, filed under the file's own space, so the
    # listing that space's panel reads shows it immediately.
    (row,) = list(stack.repository.rows.values())
    assert row.space_id == SEED_SPACE
    assert stack.outbox.event_types == ["knowledge.document.registered.v1"]


def test_uploading_a_file_indexes_nothing_by_itself() -> None:
    """The whole point of the route above. Nothing in this suite uploads —
    but nothing anywhere registers a document either, and the corpus a
    freshly built stack starts with is the proof a client relies on: the
    file panel shows an un-indexed file, not one quietly being embedded."""
    app, stack = _make_app()
    stack.files.add("f1")  # a completed upload, and nothing more
    with TestClient(app) as client:
        listing = client.get(f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth())
    assert listing.status_code == 200
    assert listing.json()["data"] == []
    assert stack.outbox.event_types == []


def test_indexing_a_file_that_is_not_ready_is_404() -> None:
    """The "only after the upload completes" precondition, enforced. The seam
    answers ``None`` for a file still uploading, quarantined, deleted, unknown
    or another tenant's, and all five are the same 404 on purpose."""
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=_auth()
        )
    assert response.status_code == 404
    assert stack.repository.rows == {}


def test_indexing_a_file_twice_is_409() -> None:
    """Two live documents over one file make every search answer from it
    twice, forever. Rebuilding is ``POST /knowledge/reindex``, which destroys
    what it supersedes; this route only ever starts one that does not exist."""
    app, stack = _make_app()
    stack.files.add("f1")
    with TestClient(app) as client:
        first = client.post("/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=_auth())
        second = client.post("/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=_auth())
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "common.conflict"
    assert len(stack.repository.rows) == 1


def test_indexing_the_same_file_under_one_idempotency_key_registers_one_document() -> None:
    """A retried POST must not buy a second document and a second bill for the
    same embeddings. The 409 above catches the double CLICK; the header
    catches the retry whose first attempt committed after the client gave up."""
    app, stack = _make_app()
    stack.files.add("f1")
    headers = {**_auth(), "Idempotency-Key": "key-1"}
    with TestClient(app) as client:
        first = client.post("/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=headers)
        replay = client.post("/api/v1/knowledge/documents", json={"file_id": "f1"}, headers=headers)
    assert (first.status_code, replay.status_code) == (202, 202)
    assert first.json() == replay.json()
    assert len(stack.repository.rows) == 1


def test_the_index_body_requires_a_file_id() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        missing = client.post("/api/v1/knowledge/documents", json={}, headers=_auth())
        blank = client.post("/api/v1/knowledge/documents", json={"file_id": ""}, headers=_auth())
    assert (missing.status_code, blank.status_code) == (422, 422)


# --------------------------------------------------------------------------- #
# POST /reindex + GET/POST /reindex/{id} (BE-RAG-007/008)                     #
# --------------------------------------------------------------------------- #
def test_reindexing_answers_202_with_the_job() -> None:
    """202 and not 201: the rebuild has been ACCEPTED, and a worker will do
    it. 201 would promise a finished index that does not exist yet."""
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1, file_id="f1")
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth()
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert (body["total"], body["finished"], body["percent"]) == (1, 0, 0)
    assert body["current_file_id"] is None
    assert body["cancelled_at"] is None
    (item,) = body["items"]
    assert item["source_document_id"] == "d1"
    assert item["file_id"] == "f1"
    assert item["status"] == "pending"


def test_the_reindexed_file_disappears_from_the_corpus_until_the_worker_runs() -> None:
    """The cost the contract states out loud: the old document is destroyed
    up front, so the file answers nothing until the rebuild lands. The listing
    is where a client can see that, which is why it is asserted here and not
    only in the module tests."""
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1, file_id="f1", chunk_count=9)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        client.post("/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth())
        listing = client.get(
            f"/api/v1/knowledge/documents?space_id={SEED_SPACE}", headers=_auth()
        ).json()
    (row,) = listing["data"]
    assert row["id"] != "d1"
    assert (row["status"], row["chunk_count"]) == ("pending", 0)


def test_a_job_reports_progress_read_from_its_documents() -> None:
    app, stack = _make_app()
    for name in ("d1", "d2"):
        doc = seed_document(document_id=name, workspace_id=_W1, file_id=f"file-{name}")
        stack.repository.rows[doc.id] = doc
        stack.files.add(doc.file_id)
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1", "d2"]}, headers=_auth()
        ).json()
        first, second = (item["document_id"] for item in job["items"])
        stack.repository.rows[first] = seed_document(
            document_id=first, workspace_id=_W1, status=IndexStatus.INDEXED
        )
        stack.repository.rows[second] = seed_document(
            document_id=second, workspace_id=_W1, file_id="file-d2", status=IndexStatus.INDEXING
        )
        response = client.get(f"/api/v1/knowledge/reindex/{job['id']}", headers=_auth())
    body = response.json()
    assert (body["status"], body["finished"], body["percent"]) == ("running", 1, 50)
    assert body["current_file_id"] == "file-d2"


def test_cancelling_answers_200_with_the_cancelled_job() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth()
        ).json()
        response = client.post(f"/api/v1/knowledge/reindex/{job['id']}/cancel", headers=_auth())
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert body["items"][0]["status"] == "failed"


def test_cancelling_a_finished_job_is_409() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth()
        ).json()
        done = job["items"][0]["document_id"]
        stack.repository.rows[done] = seed_document(
            document_id=done, workspace_id=_W1, status=IndexStatus.INDEXED
        )
        response = client.post(f"/api/v1/knowledge/reindex/{job['id']}/cancel", headers=_auth())
    assert response.status_code == 409
    assert response.json()["code"] == "common.conflict"


def test_reindexing_a_document_that_is_still_indexing_is_409() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1, status=IndexStatus.INDEXING)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth()
        )
    assert response.status_code == 409
    assert stack.repository.rows["d1"].status is IndexStatus.INDEXING


def test_reindexing_an_unknown_document_is_404() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["nope"]}, headers=_auth()
        )
    assert response.status_code == 404


def test_the_reindex_body_bounds_are_the_dtos() -> None:
    """422 before a single document is read: an empty rebuild asks for
    nothing, and an unbounded one would delete an unbounded slice of the
    corpus."""
    app, stack = _make_app()
    with TestClient(app) as client:
        empty = client.post("/api/v1/knowledge/reindex", json={"document_ids": []}, headers=_auth())
        too_many = client.post(
            "/api/v1/knowledge/reindex",
            json={"document_ids": [f"d{n}" for n in range(51)]},
            headers=_auth(),
        )
    assert (empty.status_code, too_many.status_code) == (422, 422)
    assert empty.json()["code"] == "common.validation_error"
    assert stack.repository.purged == []


def test_an_idempotent_replay_does_not_rebuild_twice() -> None:
    """The route this matters most on in the whole API: without the ledger a
    retried POST destroys and rebuilds a second time, and the workspace pays
    for the embeddings twice."""
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    headers = {**_auth(), "Idempotency-Key": "retry-1"}
    body = {"document_ids": ["d1"]}
    with TestClient(app) as client:
        first = client.post("/api/v1/knowledge/reindex", json=body, headers=headers).json()
        second = client.post("/api/v1/knowledge/reindex", json=body, headers=headers).json()
    assert first == second
    assert stack.repository.purged == ["d1"]


def test_an_unknown_job_is_404_and_another_tenants_job_is_too() -> None:
    app, stack = _make_app()
    doc = seed_document(document_id="d1", workspace_id=_W1)
    stack.repository.rows[doc.id] = doc
    stack.files.add(doc.file_id)
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}, headers=_auth()
        ).json()
        stack.jobs.rows[job["id"]] = replace(stack.jobs.rows[job["id"]], workspace_id=_W2)
        assert client.get("/api/v1/knowledge/reindex/nope", headers=_auth()).status_code == 404
        foreign = client.get(f"/api/v1/knowledge/reindex/{job['id']}", headers=_auth())
    assert foreign.status_code == 404


def test_the_reindex_routes_refuse_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/knowledge/reindex", json={"document_ids": ["d1"]}).status_code
            == 401
        )
        assert client.get("/api/v1/knowledge/reindex/j1").status_code == 401
        assert client.post("/api/v1/knowledge/reindex/j1/cancel").status_code == 401
