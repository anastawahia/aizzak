"""ASGI tests for the Workspace and Usage routers (6.1-و-1).

Hermetic, over Starlette's ``TestClient`` against a real ``create_app`` wired
with the shared in-memory workspace/usage stack (``support_workspace_usage``)
— the same single-instance wiring the Composition Root builds, minus
Postgres. What these pin, against 03 §1/§2:

* the tenant root read/renamed through ``ctx.workspace_id`` alone, with the
  rename's effect asserted on the STORED row (a router that answered a
  well-formed echo without saving would otherwise pass);
* rename's refusals: the DTO bounds (422) and the archived-workspace
  conflict (409);
* the usage summary's shape, its ``workspace_id`` echoed from the principal,
  and the ``period`` selector actually selecting;
* the limits set: the API-04 envelope, whole-set replacement (a previously
  configured limit ABSENT from a ``PUT`` is gone), server-minted ids, the
  duplicate-key 409, the ``LimitRule`` coupling 422 — and the load-bearing
  omission, that a ``GET`` reports the CONFIGURED set and never the platform
  guardrail defaults.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime

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
from app.modules.usage.domain.read_models import UsageRollup
from app.modules.usage.domain.value_objects import Period
from app.modules.workspace.domain.value_objects import WorkspaceStatus
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import (
    SEEDED_CREATED_AT,
    SEEDED_WORKSPACE_NAME,
    WorkspaceUsageStack,
    build_workspace_usage,
)

_FILES_MEDIA = build_files_media()
_CREDENTIALS = build_credentials()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_GOOD = "good"
_WILDCARD = "*"


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


def _make_app(*, seeded: bool = True) -> tuple[FastAPI, WorkspaceUsageStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_workspace_usage(_W1 if seeded else None)
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
        workspace=stack.workspace,
        usage=stack.usage,
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


def _rollup(*, agent: str, provider: str, period: Period, tokens: int, cost: int) -> UsageRollup:
    return UsageRollup(
        workspace_id=_W1,
        agent_key=agent,
        provider=provider,
        period=period,
        period_start=date(2026, 7, 1),
        tokens_sum=tokens,
        cost_micros_sum=cost,
        updated_at=datetime(2026, 7, 21, tzinfo=UTC),
    )


def _limit_body(
    *,
    scope: str = "workspace",
    scope_key: str = _WILDCARD,
    metric: str = "tokens",
    period: str = "month",
    limit_value: int = 1000,
    id: str | None = None,
) -> dict[str, object]:
    if id is not None:
        return {
            "id": id,
            "scope": scope,
            "scope_key": scope_key,
            "metric": metric,
            "period": period,
            "limit_value": limit_value,
        }
    return {
        "scope": scope,
        "scope_key": scope_key,
        "metric": metric,
        "period": period,
        "limit_value": limit_value,
    }


# --------------------------------------------------------------------------- #
# auth                                                                        #
# --------------------------------------------------------------------------- #
def test_every_route_refuses_an_unauthenticated_request() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/workspace"),
            client.patch("/api/v1/workspace", json={"name": "x"}),
            client.get("/api/v1/usage"),
            client.get("/api/v1/usage/limits"),
            client.put("/api/v1/usage/limits", json={"limits": []}),
        ]
    assert [response.status_code for response in responses] == [401] * 5
    assert {response.json()["code"] for response in responses} == {"auth.missing_token"}


# --------------------------------------------------------------------------- #
# workspace                                                                   #
# --------------------------------------------------------------------------- #
def test_get_workspace_answers_the_principals_own_tenant_root() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/workspace", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _W1
    assert body["name"] == SEEDED_WORKSPACE_NAME
    assert body["status"] == "active"
    assert body["created_at"].startswith(SEEDED_CREATED_AT.strftime("%Y-%m-%dT%H:%M:%S"))


def test_a_principal_without_a_workspace_row_reads_a_404() -> None:
    app, _stack = _make_app(seeded=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/workspace", headers=_auth())

    assert response.status_code == 404
    assert response.json()["code"] == "common.not_found"


def test_patch_saves_the_new_name_and_answers_the_saved_aggregate() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.patch("/api/v1/workspace", json={"name": "Renamed"}, headers=_auth())

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    # The STORED row moved -- an echo of the request body would pass the line
    # above and fail here.
    stored = stack.workspace_repository.rows[_W1]
    assert stored.name.value == "Renamed"
    # `updated_at` moved with it; `version` deliberately does NOT change here —
    # it is the optimistic-lock column the SQL adapter bumps on `save`, not
    # something the domain touches.
    assert stored.updated_at > stored.created_at


def test_a_blank_or_overlong_name_is_refused_and_stores_nothing() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        blank = client.patch("/api/v1/workspace", json={"name": ""}, headers=_auth())
        overlong = client.patch("/api/v1/workspace", json={"name": "x" * 81}, headers=_auth())

    assert (blank.status_code, blank.json()["code"]) == (422, "common.validation_error")
    assert (overlong.status_code, overlong.json()["code"]) == (422, "common.validation_error")
    assert stack.workspace_repository.rows[_W1].name.value == SEEDED_WORKSPACE_NAME


def test_renaming_an_archived_workspace_is_a_conflict() -> None:
    app, stack = _make_app()
    stack.workspace_repository.rows[_W1].status = WorkspaceStatus.ARCHIVED
    with TestClient(app) as client:
        response = client.patch("/api/v1/workspace", json={"name": "Renamed"}, headers=_auth())

    assert response.status_code == 409
    assert stack.workspace_repository.rows[_W1].name.value == SEEDED_WORKSPACE_NAME


# --------------------------------------------------------------------------- #
# usage summary                                                               #
# --------------------------------------------------------------------------- #
def test_the_summary_reports_totals_and_both_breakdowns() -> None:
    app, stack = _make_app()
    stack.ledger.rollups.extend(
        [
            _rollup(
                agent=_WILDCARD, provider=_WILDCARD, period=Period.MONTH, tokens=900, cost=1200
            ),
            _rollup(agent="writer", provider=_WILDCARD, period=Period.MONTH, tokens=500, cost=700),
            _rollup(agent=_WILDCARD, provider="openai", period=Period.MONTH, tokens=400, cost=500),
        ]
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/usage", headers=_auth())

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": _W1,
        "period": "month",
        "tokens": 900,
        "cost_micros": 1200,
        "by_agent": [{"key": "writer", "tokens": 500, "cost_micros": 700}],
        "by_provider": [{"key": "openai", "tokens": 400, "cost_micros": 500}],
    }


def test_the_period_query_selects_which_rollups_are_read() -> None:
    app, stack = _make_app()
    stack.ledger.rollups.extend(
        [
            _rollup(agent=_WILDCARD, provider=_WILDCARD, period=Period.MONTH, tokens=900, cost=1),
            _rollup(agent=_WILDCARD, provider=_WILDCARD, period=Period.DAY, tokens=30, cost=2),
        ]
    )
    with TestClient(app) as client:
        day = client.get("/api/v1/usage", params={"period": "day"}, headers=_auth()).json()
        month = client.get("/api/v1/usage", params={"period": "month"}, headers=_auth()).json()

    assert (day["period"], day["tokens"]) == ("day", 30)
    assert (month["period"], month["tokens"]) == ("month", 900)


def test_an_unknown_period_is_a_validation_error() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/usage", params={"period": "year"}, headers=_auth())

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"


# --------------------------------------------------------------------------- #
# usage limits                                                                #
# --------------------------------------------------------------------------- #
def test_limits_wear_the_api_04_envelope_even_when_empty() -> None:
    app, _stack = _make_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/usage/limits", headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"data": [], "meta": {"next_cursor": None, "limit": 0}}


def test_a_configured_set_is_reported_without_the_platform_defaults() -> None:
    """The load-bearing omission: ``Settings`` ships non-empty guardrail
    defaults, and enforcement falls back on them — but they are not this
    workspace's configuration, so they must not appear here (folding them in
    would make a re-``PUT`` freeze today's platform numbers into the tenant).
    """
    app, stack = _make_app()
    assert Settings().usage.default_limits, "the guardrail defaults must be non-empty to test this"
    with TestClient(app) as client:
        client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(metric="tokens", limit_value=42)]},
            headers=_auth(),
        )
        body = client.get("/api/v1/usage/limits", headers=_auth()).json()

    assert [(row["metric"], row["limit_value"]) for row in body["data"]] == [("tokens", 42)]
    assert len(stack.ledger.limits) == 1


def test_put_replaces_the_whole_set_and_mints_its_own_ids() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        first = client.put(
            "/api/v1/usage/limits",
            json={
                "limits": [
                    _limit_body(metric="tokens", limit_value=10),
                    _limit_body(metric="cost_micros", limit_value=20),
                ]
            },
            headers=_auth(),
        )
        second = client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(metric="tokens", limit_value=99, id="client-chosen")]},
            headers=_auth(),
        )

    assert first.status_code == 200
    assert first.json()["meta"] == {"next_cursor": None, "limit": 2}
    # Whole-set replacement: the `cost_micros` limit configured a moment ago
    # is GONE, not merged.
    assert [(row["metric"], row["limit_value"]) for row in second.json()["data"]] == [
        ("tokens", 99)
    ]
    assert [(row.metric.value, row.limit_value) for row in stack.ledger.limits] == [("tokens", 99)]
    # Identity is the server's: the id a client named is ignored.
    assert second.json()["data"][0]["id"] != "client-chosen"


def test_a_get_body_puts_back_unchanged() -> None:
    """The spec writes the PUT body as a list of the OUT model; that literal
    round-trip has to work, ``id`` and all."""
    app, _stack = _make_app()
    with TestClient(app) as client:
        client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(scope="agent", scope_key="writer", limit_value=7)]},
            headers=_auth(),
        )
        read_back = client.get("/api/v1/usage/limits", headers=_auth()).json()
        echoed = client.put(
            "/api/v1/usage/limits", json={"limits": read_back["data"]}, headers=_auth()
        )

    assert echoed.status_code == 200
    saved = echoed.json()["data"]
    assert [(row["scope"], row["scope_key"], row["limit_value"]) for row in saved] == [
        ("agent", "writer", 7)
    ]


def test_a_duplicate_limit_key_in_one_body_is_a_conflict() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(limit_value=1), _limit_body(limit_value=2)]},
            headers=_auth(),
        )

    assert response.status_code == 409
    assert stack.ledger.limits == []


def test_the_scope_key_coupling_is_enforced_and_stores_nothing() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        wildcard_agent = client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(scope="agent", scope_key=_WILDCARD)]},
            headers=_auth(),
        )
        named_workspace = client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(scope="workspace", scope_key="writer")]},
            headers=_auth(),
        )

    assert wildcard_agent.status_code == 422
    assert named_workspace.status_code == 422
    assert stack.ledger.limits == []


def test_an_out_of_vocabulary_metric_never_reaches_the_use_case() -> None:
    app, stack = _make_app()
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/usage/limits",
            json={"limits": [_limit_body(metric="dollars")]},
            headers=_auth(),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "common.validation_error"
    assert stack.ledger.limits == []
