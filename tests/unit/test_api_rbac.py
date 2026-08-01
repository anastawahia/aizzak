"""RBAC — the guards on the routes, and the map they implement (Phase 6.4-ب ·
05-rbac-config-secrets §1.3/§1.4/§4 · SEC-02 · D-24).

Three kinds of claim, and the first is the one that could not exist before:

* **the MAP** — the permission each operation demands, read off the finished
  application and compared with 05 §4 in both directions. 05 §4 publishes a
  twelve-row sample; the other twenty-seven follow its own rules, and all
  thirty-nine are pinned here, because a decorator is exactly the kind of thing
  that gets copied to a new route with the wrong permission still attached and
  no test in the world noticing (§3.72's lesson, applied to authorization);
* **coverage** — every authenticated operation carries EXACTLY one guard. Zero
  is a hole; two is an ambiguity about which one denies first;
* **behaviour** — over the real ``RoleCatalog``, a role that lacks the
  permission gets a 403 problem, a role that holds it gets through, and the
  refusal happens before the use-case is reached.

Nothing here fakes the decision. ``Authorization`` and ``RoleCatalog`` are the
real ones (05 §1.3 verbatim), so a mistake in the matrix and a mistake in a
decorator are both visible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.middleware.rbac import PermissionGuard
from app.api.v1.dependencies import ApiServices, Principal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.plugin_loader import PluginLoader
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.access.domain.value_objects import Permission, RoleCatalog, RoleName
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"

# 05 §4's map, completed. The twelve rows the design publishes are marked; the
# rest follow the same rule — a read takes `<resource>:read`, a mutation takes
# the module's write/manage permission — and are pinned here so "the same rule"
# is a fact about the code rather than a claim about it.
EXPECTED: dict[tuple[str, str], Permission] = {
    ("/api/v1/agents", "get"): Permission.AGENTS_READ,
    ("/api/v1/agents/{}", "get"): Permission.AGENTS_READ,
    ("/api/v1/agents/{}/invoke", "post"): Permission.AGENTS_INVOKE,  # 05 §4
    ("/api/v1/conversations", "get"): Permission.CONVERSATIONS_READ,
    ("/api/v1/conversations", "post"): Permission.CONVERSATIONS_WRITE,
    ("/api/v1/conversations/{}", "get"): Permission.CONVERSATIONS_READ,
    ("/api/v1/conversations/{}", "delete"): Permission.CONVERSATIONS_DELETE,
    ("/api/v1/conversations/{}/messages", "get"): Permission.CONVERSATIONS_READ,
    ("/api/v1/conversations/{}/messages", "post"): Permission.CONVERSATIONS_WRITE,  # 05 §4
    ("/api/v1/workflows", "get"): Permission.WORKFLOWS_READ,
    ("/api/v1/workflows/{}/run", "post"): Permission.WORKFLOWS_RUN,  # 05 §4
    ("/api/v1/workflows/runs/{}", "get"): Permission.WORKFLOWS_READ,
    ("/api/v1/files", "post"): Permission.FILES_WRITE,
    ("/api/v1/files", "get"): Permission.FILES_READ,
    ("/api/v1/files/{}", "get"): Permission.FILES_READ,
    ("/api/v1/files/{}", "delete"): Permission.FILES_DELETE,  # 05 §4
    ("/api/v1/files/{}/complete", "post"): Permission.FILES_WRITE,
    ("/api/v1/media/jobs", "post"): Permission.MEDIA_CREATE,  # 05 §4
    ("/api/v1/media/jobs/{}", "get"): Permission.MEDIA_READ,
    ("/api/v1/workspace", "get"): Permission.WORKSPACE_READ,
    ("/api/v1/workspace", "patch"): Permission.WORKSPACE_MANAGE,  # 05 §4
    ("/api/v1/usage", "get"): Permission.USAGE_READ,  # 05 §4
    ("/api/v1/usage/limits", "get"): Permission.USAGE_READ,
    ("/api/v1/usage/limits", "put"): Permission.USAGE_MANAGE,  # 05 §4
    ("/api/v1/credentials", "get"): Permission.CREDENTIALS_READ,
    ("/api/v1/credentials", "post"): Permission.CREDENTIALS_MANAGE,  # 05 §4
    ("/api/v1/credentials/{}", "delete"): Permission.CREDENTIALS_MANAGE,
    ("/api/v1/knowledge/search", "post"): Permission.KNOWLEDGE_READ,
    ("/api/v1/knowledge/documents", "get"): Permission.KNOWLEDGE_READ,
    ("/api/v1/knowledge/documents/{}", "get"): Permission.KNOWLEDGE_READ,
    ("/api/v1/integrations/connectors", "get"): Permission.INTEGRATIONS_READ,
    ("/api/v1/integrations/connections", "get"): Permission.INTEGRATIONS_READ,
    ("/api/v1/integrations/connections", "post"): Permission.INTEGRATIONS_MANAGE,  # 05 §4
    ("/api/v1/integrations/connections/{}", "delete"): Permission.INTEGRATIONS_MANAGE,
    ("/api/v1/integrations/connections/{}/authorize", "post"): Permission.INTEGRATIONS_MANAGE,
    ("/api/v1/integrations/mcp-servers", "get"): Permission.INTEGRATIONS_READ,
    ("/api/v1/integrations/mcp-servers", "post"): Permission.INTEGRATIONS_MANAGE,
    ("/api/v1/integrations/mcp-servers/{}", "delete"): Permission.INTEGRATIONS_MANAGE,  # 05 §4
    ("/api/v1/integrations/tools", "get"): Permission.INTEGRATIONS_READ,  # 05 §4
}

# The operations no guard may sit on: two liveness probes, one OAuth
# redirect target that carries no credential by protocol (§3.66), and the
# P1-3 metrics scrape (docs/p1-hardening-plan.md §3 step 10) -- an operator's
# scraper, not a tenant-facing route (`api/metrics.py`'s own module
# docstring), blocked at the nginx edge instead of behind a bearer token
# (mzalaq #2: it must never be reachable through the public edge at all,
# which a 401/403 body would still confirm the path exists behind).
UNGUARDED = {
    ("/health", "get"),
    ("/health/ready", "get"),
    ("/metrics", "get"),
    ("/api/v1/integrations/connections/oauth/callback", "get"),
}


# --------------------------------------------------------------------------- #
# The application                                                             #
# --------------------------------------------------------------------------- #
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


class _RoleAuth:
    """A principal whose roles are whatever the current test asked for."""

    def __init__(self) -> None:
        self.roles: frozenset[str] = frozenset({"owner"})

    async def authenticate(self, token: str) -> Principal:
        return Principal(workspace_id=_W1, user_id=_U1, roles=self.roles)


def _build_app() -> tuple[FastAPI, _RoleAuth]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    files_media = build_files_media()
    workspace_usage = build_workspace_usage(_W1)
    auth = _RoleAuth()
    app = create_app(
        ApiServices(
            settings=Settings(),
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
            idempotency=InMemoryIdempotencyStore(),
        ),
        http_authenticator=auth,
        ws_authenticator=auth,
    )
    return app, auth


_APP, _AUTH = _build_app()


def _routes(app: FastAPI) -> Iterator[tuple[str, APIRoute]]:
    """Every ``APIRoute`` in the composed app, with its mounted prefix.

    ``include_router`` does not flatten: this FastAPI keeps each inclusion as
    an ``_IncludedRouter`` holding the original router plus the prefix it was
    mounted under, so a walk of ``app.routes`` alone finds only the four
    unprefixed root routes (health, and nothing that matters here). Reading the
    real tree is what makes this suite check the application as ASSEMBLED
    rather than each router in isolation — a guard lost during inclusion would
    be invisible to a per-router look.
    """
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            yield "", entry
            continue
        included = getattr(entry, "original_router", None)
        if included is None:
            continue
        prefix = entry.include_context.prefix
        for route in included.routes:
            if isinstance(route, APIRoute):
                yield prefix, route


def _operations(app: FastAPI) -> Iterator[tuple[tuple[str, str], APIRoute]]:
    """Every routed operation as ``((path with parameters erased, verb), route)``."""
    for prefix, route in _routes(app):
        full = prefix + route.path
        path = "/".join("{}" if part.startswith("{") else part for part in full.split("/"))
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            yield (path, method.lower()), route


def _guards(route: APIRoute) -> list[Permission]:
    """The permissions the guards on one route demand.

    Reads the resolved dependency tree rather than the decorator source, so
    what is checked is what FastAPI will actually run — a guard attached to a
    router but shadowed, or one added and never resolved, would show up here
    exactly as it behaves.
    """
    return [
        dependency.call.permission
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, PermissionGuard)
    ]


# --------------------------------------------------------------------------- #
# The map                                                                     #
# --------------------------------------------------------------------------- #
def test_every_operation_demands_exactly_the_permission_the_design_assigns_it() -> None:
    """05 §4, both directions.

    Forward: no operation may demand a permission other than its own. Backward:
    no row of the map may be missing from the application — which is how a
    route added in a later phase without a guard would show up, since the
    reverse direction is the only one that can see an ABSENCE.
    """
    actual = {
        operation: guards[0] for operation, route in _operations(_APP) if (guards := _guards(route))
    }
    assert actual == EXPECTED


def test_every_authenticated_operation_carries_exactly_one_guard() -> None:
    """Zero is a hole. Two is a question — which one answers first, and what
    does a client learn from the one that did?"""
    counts = {
        operation: len(_guards(route))
        for operation, route in _operations(_APP)
        if operation not in UNGUARDED
    }
    assert set(counts.values()) == {1}, {op: n for op, n in counts.items() if n != 1}


def test_the_unauthenticated_operations_carry_no_guard_at_all() -> None:
    """A guard on the OAuth callback would be dead weight in the worst way: it
    reads roles off a context built from a state binding, not from a principal,
    so it would be deciding about an identity nobody authenticated."""
    guarded = {operation for operation, route in _operations(_APP) if _guards(route)}
    assert guarded & UNGUARDED == set()


def test_every_demanded_permission_exists_in_the_catalog() -> None:
    """Trivially true through the ``Permission`` enum — and that is the point
    being pinned. ``require("media:generate")`` would import fine and deny every
    caller forever; ``Permission.MEDIA_GENERATE`` does not exist. Two bundled
    agent manifests shipped exactly that mistake in string form (§3.73)."""
    demanded = {
        permission for _operation, route in _operations(_APP) for permission in _guards(route)
    }
    assert demanded <= set(Permission)


# --------------------------------------------------------------------------- #
# The behaviour, over the real catalog                                        #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_APP) as running:
        yield running
    _AUTH.roles = frozenset({"owner"})


def _as(role: RoleName) -> None:
    _AUTH.roles = frozenset({role.value})


def test_a_viewer_may_read_the_agent_catalog_but_not_invoke_one(client: TestClient) -> None:
    """The matrix's own distinction (05 §1.3: ``agents:read`` ✓, ``agents:invoke``
    — for a viewer), made observable on the wire."""
    _as(RoleName.VIEWER)
    assert client.get("/api/v1/agents", headers={"Authorization": "Bearer t"}).status_code == 200
    refused = client.post(
        "/api/v1/agents/echo/invoke",
        json={"input": {"text": "hi"}},
        headers={"Authorization": "Bearer t"},
    )
    assert refused.status_code == 403


def test_a_refusal_is_a_problem_that_names_the_missing_permission(client: TestClient) -> None:
    """Unlike a 401 — where the reason is withheld — a 403 answers someone we
    have identified, and the permission they lack is what they need in order to
    ask an admin for it."""
    _as(RoleName.VIEWER)
    response = client.put(
        "/api/v1/usage/limits", json={"limits": []}, headers={"Authorization": "Bearer t"}
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "authz.forbidden"
    assert body["detail"] == "missing permission: usage:manage"


def test_a_member_is_refused_the_management_routes_the_matrix_withholds(
    client: TestClient,
) -> None:
    """The four the matrix reserves for owner/admin and a member does not hold
    — the exact set that made four already-passing router suites fail the
    moment the guards went in."""
    _as(RoleName.MEMBER)
    headers = {"Authorization": "Bearer t"}
    assert client.patch("/api/v1/workspace", json={"name": "x"}, headers=headers).status_code == 403
    assert client.get("/api/v1/credentials", headers=headers).status_code == 403
    assert (
        client.delete(
            "/api/v1/files/018f0000-0000-7000-8000-00000000f001", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            "/api/v1/conversations/018f0000-0000-7000-8000-00000000c001", headers=headers
        ).status_code
        == 403
    )


def test_a_member_still_reaches_everything_the_matrix_grants(client: TestClient) -> None:
    """The other half of the same claim, and the one a too-strict guard breaks:
    a member is the ordinary user of this platform, and a permission map that
    quietly locks them out of their own files is as wrong as one that lets a
    viewer delete them."""
    _as(RoleName.MEMBER)
    headers = {"Authorization": "Bearer t"}
    assert client.get("/api/v1/files", headers=headers).status_code == 200
    threads = client.get("/api/v1/conversations?agent_key=echo", headers=headers)
    assert threads.status_code == 200
    assert client.get("/api/v1/usage/limits", headers=headers).status_code == 200
    assert client.get("/api/v1/workspace", headers=headers).status_code == 200


def test_the_guard_refuses_before_the_use_case_is_reached(client: TestClient) -> None:
    """A 403 that had already written a row would be a guard in name only. The
    workspace fake stores by whole-aggregate save, so a rename that reached it
    would be visible in the next read."""
    _as(RoleName.MEMBER)
    headers = {"Authorization": "Bearer t"}
    before = client.get("/api/v1/workspace", headers=headers).json()["name"]
    assert (
        client.patch("/api/v1/workspace", json={"name": "renamed"}, headers=headers).status_code
        == 403
    )
    _as(RoleName.OWNER)
    assert client.get("/api/v1/workspace", headers=headers).json()["name"] == before


def test_a_role_outside_the_catalog_grants_nothing(client: TestClient) -> None:
    """``is_allowed`` parses roles and drops what it cannot parse, so a token
    that somehow carried ``roles: ["superuser"]`` is a caller with no
    permissions at all — the fail-closed direction."""
    _AUTH.roles = frozenset({"superuser"})
    assert client.get("/api/v1/workspace", headers={"Authorization": "Bearer t"}).status_code == 403


def test_the_platform_admin_reaches_no_tenant_content(client: TestClient) -> None:
    """05 §1.3's deliberate shape — "دعمٌ لا اطّلاع". Worth pinning on the wire
    because it is the one row of the matrix that looks like an oversight and is
    not: a cross-tenant support role that could read conversations would make
    every workspace's content readable by a platform operator."""
    _as(RoleName.PLATFORM_ADMIN)
    headers = {"Authorization": "Bearer t"}
    assert client.get("/api/v1/workspace", headers=headers).status_code == 200
    assert client.get("/api/v1/conversations?agent_key=echo", headers=headers).status_code == 403
    assert client.get("/api/v1/files", headers=headers).status_code == 403


@pytest.mark.parametrize("role", list(RoleName))
def test_every_role_can_reach_something_and_no_role_can_reach_everything(role: RoleName) -> None:
    """A sanity check on the matrix itself rather than on the guards: a role
    that granted every permission in the catalog would make the other four
    decorative, and one that granted none would be unusable. Read straight off
    ``RoleCatalog`` — cheap, and it fails loudly if the matrix is ever edited
    into a shape 05 §1.3 does not describe."""
    granted = RoleCatalog[role]
    assert granted
    assert granted != set(Permission)


# --------------------------------------------------------------------------- #
# The agents' own declarations (6.4-ب)                                        #
# --------------------------------------------------------------------------- #
def test_every_shipped_manifest_declares_permissions_the_catalog_defines() -> None:
    """Read off the REAL plugin tree, and the reason this test exists at all.

    ``required_permissions`` sat in ``AgentMetadata`` since 4.1 carrying the
    comment "checked by RBAC before any run", and nothing read it — so two of
    the five shipped agents declared ``media:generate``, which 05 §1.2 has
    never contained. ``is_allowed`` denies a permission it cannot parse, which
    is the right default and a silent one: the moment enforcement went in,
    those two agents would have become uninvokable by every role including
    ``owner``, and the only symptom would have been a 403 nobody could explain.

    The check is over the loader's own scan rather than a hand-written list of
    five keys, so an agent added by dropping a folder in (AC-04) is checked the
    day it arrives.
    """
    registry = InMemoryAgentRegistry()
    PluginLoader().load_into(registry)
    catalog = {permission.value for permission in Permission}
    offenders = {
        metadata.key: sorted(set(metadata.required_permissions) - catalog)
        for metadata in registry.list()
        if set(metadata.required_permissions) - catalog
    }
    assert offenders == {}


def test_every_shipped_agent_requires_the_permission_to_be_invoked_at_all() -> None:
    """A manifest is a plugin's own declaration, and the core does not add to
    it — so an agent that forgot ``agents:invoke`` would be runnable over the
    socket by a viewer, whose transport-level check is the only other gate.
    Pinning it here keeps the five shipped agents honest and documents the
    obligation for the sixth."""
    registry = InMemoryAgentRegistry()
    PluginLoader().load_into(registry)
    manifests = registry.list()
    assert manifests, "the plugin scan found no agents at all"
    for metadata in manifests:
        assert Permission.AGENTS_INVOKE.value in metadata.required_permissions, metadata.key
