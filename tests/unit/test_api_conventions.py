"""The API conventions are a MIRROR of the contract — enforced, not asserted in
prose (Phase 6.3-ج · API-02…05 · DD-06 · AC-07).

``docs/design/openapi.yaml`` is the binding machine-readable contract, and
``AC-07`` is conformance to it. But nothing compared it to what the
application actually generates, and 6.3-ب is what that cost: the contract had
declared ``limit``/``cursor`` on **five** operations since Phase 3 while the
implementation carried three, and the two missing ones sat behind code
comments written as though the question were still open. The default page
size had drifted the same way — ``default: 20`` in the contract, ``50`` in
every router — so a client generated from the document paged in 20s and was
answered 50. Neither is the kind of mistake review catches; both are the kind
a diff catches.

So this module loads both descriptions and compares them, and checks the
conventions that no document can state about itself:

* **API-02** — every property and every parameter name is ``snake_case``;
* **API-03** — the paginated operations are EXACTLY the ones the contract
  declares (both directions), and ``limit``'s bounds and default are the
  contract's;
* **API-04** — the page-returning operations are exactly the ones the
  contract wraps (both directions), every envelope is ``data`` + ``meta``
  with both required, and NO operation returns a bare array;
* **API-05** — every path is under ``/api/v1`` except the deliberately
  unversioned health probes (which the contract marks with their own
  ``servers`` override — a load-balancer probe must not chase API versions),
  and every timestamp on the wire is UTC with a trailing ``Z`` (DAT-06),
  which the contract encodes as ``pattern: Z$`` on all ten of its
  ``date-time`` fields;
* **SEC-01** (6.4-أ) — the bearer scheme is PUBLISHED, and the operations that
  require it are exactly the ones the contract requires it on, both ways. This
  arrived with the verifier because it is the same class of defect the rest of
  this module exists for: alpha bound a plain header parameter instead of a
  security scheme, so its published document had no scheme at all and marked
  the header optional (``refs/auth-firebase.md`` §9) — a contract lie no
  request can expose, since the server behaves correctly either way.

The contract states authentication GLOBALLY (``security:`` at the document
root) and switches it off per operation; FastAPI can only state it per
operation. So the comparison is over EFFECTIVE security — what a client is
told about one operation after inheritance is resolved — which is the only
form of the question that means anything to a generated client anyway.

Operations are matched on ``(path, verb)`` with path parameters erased:
``/files/{id}`` in the contract and ``/files/{file_id}`` in the router are the
same URL to a client, and the parameter's NAME is not part of the wire.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.dto.pagination import DEFAULT_LIMIT
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import InMemorySpaces, build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_W1 = "018f0000-0000-7000-8000-0000000000w1"
# Spaces plan step 12 — `POST /files` names its space now, and the seam
# behind it treats only this id as live.
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
_U1 = "018f0000-0000-7000-8000-0000000000u1"

_VERBS = frozenset({"get", "post", "put", "patch", "delete"})
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_PATH_PARAM = re.compile(r"\{[^}]+\}")

# The `(path, verb)` pair, with path parameters erased.
Operation = tuple[str, str]


# --------------------------------------------------------------------------- #
# The two descriptions                                                        #
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


class _FakeAuth:
    async def authenticate(self, token: str) -> Principal:
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        return WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


def _build_app() -> FastAPI:
    """The REAL router set, with no probe routes bolted on.

    Deliberately not reusing another suite's app factory: this module's whole
    claim is about what the application publishes, so a test-only route in the
    document would make the comparison meaningless.
    """
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    files_media = build_files_media(spaces=InMemorySpaces(active={_SPACE}))
    workspace_usage = build_workspace_usage()
    return create_app(
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
            space_quota=files_media.space_quota,
            media=files_media.media,
            workspace=workspace_usage.workspace,
            usage=workspace_usage.usage,
            credentials=build_credentials().credentials,
            knowledge=build_knowledge().knowledge,
            integrations=build_integrations().integrations,
            authorization=build_authorization(),
            idempotency=InMemoryIdempotencyStore(),
        ),
        http_authenticator=_FakeAuth(),
        ws_authenticator=_FakeWsAuth(),
    )


_APP = _build_app()
LIVE: dict[str, Any] = _APP.openapi()
SPEC: dict[str, Any] = yaml.safe_load(
    (pathlib.Path(__file__).parents[2] / "docs/design/openapi.yaml").read_text(encoding="utf-8")
)


# --------------------------------------------------------------------------- #
# Walking the two documents                                                   #
# --------------------------------------------------------------------------- #
def _spec_prefix(path_item: dict[str, Any]) -> str:
    """The contract's server prefix for one path.

    Global ``servers: /api/v1``, unless the path overrides it — which
    ``/health`` and ``/health/ready`` do, on purpose: the probes a load
    balancer calls are unversioned (03 §1), and the contract says so with its
    own ``servers: [{url: /}]``.
    """
    override = path_item.get("servers")
    if override:
        url = str(override[0]["url"])
        return "" if url == "/" else url.rstrip("/")
    return "/api/v1"


def _operations(doc: dict[str, Any], *, spec: bool) -> Iterator[tuple[Operation, dict[str, Any]]]:
    for path, path_item in doc["paths"].items():
        prefix = _spec_prefix(path_item) if spec else ""
        for verb, operation in path_item.items():
            if verb not in _VERBS:
                continue
            yield (_PATH_PARAM.sub("{}", prefix + path), verb), operation


def _parameter_names(doc: dict[str, Any], path_item: dict[str, Any], verb: str) -> set[str]:
    """Every parameter on one operation, path-level and operation-level, with
    the contract's ``$ref``\\ s resolved."""
    names: set[str] = set()
    for declared in list(path_item.get("parameters", ())) + list(
        path_item[verb].get("parameters", ())
    ):
        parameter = declared
        if "$ref" in declared:
            key = str(declared["$ref"]).rsplit("/", 1)[-1]
            parameter = doc["components"]["parameters"][key]
        names.add(str(parameter["name"]))
    return names


def _paginated(doc: dict[str, Any], *, spec: bool) -> set[Operation]:
    found = set()
    for path, path_item in doc["paths"].items():
        prefix = _spec_prefix(path_item) if spec else ""
        for verb in path_item:
            if verb not in _VERBS:
                continue
            if {"limit", "cursor"} <= _parameter_names(doc, path_item, verb):
                found.add((_PATH_PARAM.sub("{}", prefix + path), verb))
    return found


def _declaring(doc: dict[str, Any], parameter_name: str, *, spec: bool) -> set[Operation]:
    """Every operation that declares ``parameter_name`` — the ``_paginated``
    shape, generalised (3.79 needed the same both-directions comparison for
    one header instead of the ``limit``/``cursor`` pair)."""
    found = set()
    for path, path_item in doc["paths"].items():
        prefix = _spec_prefix(path_item) if spec else ""
        for verb in path_item:
            if verb not in _VERBS:
                continue
            if parameter_name in _parameter_names(doc, path_item, verb):
                found.add((_PATH_PARAM.sub("{}", prefix + path), verb))
    return found


def _response_schema_name(operation: dict[str, Any]) -> str | None:
    for status, response in operation.get("responses", {}).items():
        if not str(status).startswith("2"):
            continue
        schema = response.get("content", {}).get("application/json", {}).get("schema", {})
        if "$ref" in schema:
            return str(schema["$ref"]).rsplit("/", 1)[-1]
    return None


def _is_page(name: str | None) -> bool:
    # The contract names them `<T>Page`; FastAPI generates `Page_<T>_` from
    # the generic. Same envelope, two naming conventions.
    return bool(name) and (name.endswith("Page") or name.startswith("Page_"))  # type: ignore[union-attr]


def _wrapped(doc: dict[str, Any], *, spec: bool) -> set[Operation]:
    return {
        operation_id
        for operation_id, operation in _operations(doc, spec=spec)
        if _is_page(_response_schema_name(operation))
    }


# --------------------------------------------------------------------------- #
# The operation set itself                                                    #
# --------------------------------------------------------------------------- #
def test_the_application_publishes_exactly_the_operations_the_contract_declares() -> None:
    """``AC-07`` in one assertion.

    A route added without a contract entry and a contract entry never
    implemented are the same defect seen from two sides, and both are
    invisible to every other test in this suite — a router test can only ever
    check routes that exist.
    """
    spec_ops = {operation_id for operation_id, _ in _operations(SPEC, spec=True)}
    live_ops = {operation_id for operation_id, _ in _operations(LIVE, spec=False)}
    assert live_ops == spec_ops


# --------------------------------------------------------------------------- #
# API-02 — snake_case                                                         #
# --------------------------------------------------------------------------- #
def test_every_published_property_name_is_snake_case() -> None:
    offenders = [
        f"{schema}.{prop}"
        for schema, model in LIVE.get("components", {}).get("schemas", {}).items()
        for prop in (model.get("properties") or {})
        if not _SNAKE.match(prop)
    ]
    assert offenders == []


def test_the_idempotency_header_is_declared_on_exactly_the_contracts_operations() -> None:
    """3.79, and it is the same defect class 6.3-ب paid for: ``openapi.yaml``
    has declared ``Idempotency-Key`` on ``registerFile``/``createMediaJob``/
    ``runWorkflow`` since Phase 3, while the handlers named it nowhere — so
    the GENERATED document did not mention it and a client built from
    ``/openapi.json`` had no way to send it at all.

    Both directions, deliberately: forward stops a handler from advertising
    idempotency it does not implement, backward stops one of the three from
    quietly losing the header again.
    """
    assert _declaring(LIVE, "Idempotency-Key", spec=False) == _declaring(
        SPEC, "Idempotency-Key", spec=True
    )


def test_every_query_and_path_parameter_name_is_snake_case() -> None:
    """API-02's ``snake_case`` rule covers what this platform NAMES — JSON
    properties, query strings, path segments.

    HEADERS are excluded, and the exclusion is the contract's own, not a
    convenience for this test: ``openapi.yaml`` declares
    ``X-Correlation-Id`` and (3.79) ``Idempotency-Key`` in exactly that
    canonical HTTP casing, because a header name is an interoperability
    convention owned by RFC 9110 and every client library, not a field this
    API gets to rename. Filtering by ``in`` rather than by an allow-list of
    names is what keeps a FUTURE query parameter called ``Some-Thing`` caught.
    """
    offenders = [
        f"{verb.upper()} {path} ?{parameter['name']}"
        for (path, verb), operation in _operations(LIVE, spec=False)
        for parameter in operation.get("parameters", ())
        if parameter.get("in") in ("query", "path")
        if not _SNAKE.match(str(parameter["name"]))
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# API-03 — the cursor                                                         #
# --------------------------------------------------------------------------- #
def test_the_paginated_operations_are_exactly_the_ones_the_contract_declares() -> None:
    """Both directions, and this is the one that 6.3-ب paid for.

    Forward: an operation cannot page unless the contract says it does.
    Backward: an operation the contract declares paginated cannot ship
    without a cursor — which is exactly how ``listDocuments`` and
    ``listConnections`` stayed unpaginated for a phase and a half while their
    code comments treated the question as still open.
    """
    assert _paginated(LIVE, spec=False) == _paginated(SPEC, spec=True)


def test_the_limit_parameter_carries_the_contracts_bounds_and_default() -> None:
    """The number a client is TOLD it will get has to be the one it gets.

    ``DEFAULT_LIMIT`` was 50 in every router while the contract said 20, so a
    generated client paged in 20s and was answered 50 — a drift no request
    could ever reveal, because both numbers produce a valid response.
    """
    contract = SPEC["components"]["parameters"]["Limit"]["schema"]
    assert contract["default"] == DEFAULT_LIMIT
    for (path, verb), operation in _operations(LIVE, spec=False):
        for parameter in operation.get("parameters", ()):
            if parameter["name"] != "limit":
                continue
            schema = parameter["schema"]
            where = f"{verb.upper()} {path}"
            assert (where, schema["minimum"]) == (where, contract["minimum"])
            assert (where, schema["maximum"]) == (where, contract["maximum"])
            assert (where, schema["default"]) == (where, contract["default"])
            assert parameter.get("required") is False


def test_a_paginated_operation_always_returns_a_page() -> None:
    """``limit``/``cursor`` with no ``meta.next_cursor`` to spend would be a
    one-way street: the client can ask for a page and never learn there is a
    next one."""
    for operation_id, operation in _operations(LIVE, spec=False):
        if operation_id in _paginated(LIVE, spec=False):
            assert _is_page(_response_schema_name(operation)), operation_id


# --------------------------------------------------------------------------- #
# API-04 — the envelope                                                       #
# --------------------------------------------------------------------------- #
def test_the_wrapped_operations_are_exactly_the_ones_the_contract_wraps() -> None:
    assert _wrapped(LIVE, spec=False) == _wrapped(SPEC, spec=True)


def test_no_operation_returns_a_bare_array() -> None:
    """``API-04`` admits no exception, and an unwrapped array is the one shape
    that can never grow a ``meta`` without breaking every client."""
    offenders = [
        f"{verb.upper()} {path} -> {status}"
        for (path, verb), operation in _operations(LIVE, spec=False)
        for status, response in operation.get("responses", {}).items()
        if str(status).startswith("2")
        and response.get("content", {}).get("application/json", {}).get("schema", {}).get("type")
        == "array"
    ]
    assert offenders == []


def test_every_envelope_is_data_plus_meta_with_both_required() -> None:
    schemas = LIVE["components"]["schemas"]
    envelopes = [name for name in schemas if name.startswith("Page_")]
    assert envelopes, "no collection envelope in the published document at all"
    for name in envelopes:
        model = schemas[name]
        assert set(model["properties"]) == {"data", "meta"}, name
        assert set(model["required"]) == {"data", "meta"}, name
        assert model["properties"]["data"]["type"] == "array", name
    meta = schemas["PageMeta"]
    assert set(meta["properties"]) == {"next_cursor", "limit"}
    assert set(meta["required"]) == {"next_cursor", "limit"}


# --------------------------------------------------------------------------- #
# API-05 — versioning and value formats                                       #
# --------------------------------------------------------------------------- #
def test_every_path_is_versioned_except_the_health_probes() -> None:
    unversioned = {
        path
        for (path, _verb), _operation in _operations(LIVE, spec=False)
        if not path.startswith("/api/v1/")
    }
    # The probes a load balancer calls, and only those: a readiness check that
    # had to be updated for `/api/v2` would be a deployment hazard, not a
    # versioning nicety.
    assert unversioned == {"/health", "/health/ready"}


def test_the_contract_pins_utc_on_every_timestamp_it_publishes() -> None:
    """DAT-06 — and the reason the next test can be a spot check rather than
    an exhaustive one."""
    unpinned = [
        f"{schema}.{prop}"
        for schema, model in SPEC["components"]["schemas"].items()
        for prop, spec in (model.get("properties") or {}).items()
        if spec.get("format") == "date-time" and spec.get("pattern") != "Z$"
    ]
    assert unpinned == []


def test_a_timestamp_on_the_wire_ends_in_z() -> None:
    """The serialized form, not the declared one.

    An aware ``datetime`` can render as ``+00:00`` just as correctly as
    ``Z``, and the contract's ``pattern: Z$`` accepts only one of them — so
    the claim has to be checked against a real response body.
    """
    client = TestClient(_APP)
    auth = {"Authorization": "Bearer good"}
    registered = client.post(
        "/api/v1/files",
        json={
            "space_id": _SPACE,
            "name": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        headers=auth,
    )
    assert registered.status_code == 201, registered.text
    body = client.get(f"/api/v1/files/{registered.json()['file_id']}", headers=auth).json()
    assert body["created_at"].endswith("Z")


# --------------------------------------------------------------------------- #
# SEC-01 — the bearer scheme (6.4-أ)                                          #
# --------------------------------------------------------------------------- #
def _authenticated(doc: dict[str, Any], *, spec: bool) -> set[Operation]:
    """The operations that require a credential, after inheritance.

    The contract declares ``security`` once at the root and overrides it with
    ``security: []`` where a route must be reachable without one; FastAPI emits
    the requirement per operation and never a global. Resolving both to the
    same question — "is this operation authenticated?" — is what lets the two
    documents be compared at all.
    """
    inherited = doc.get("security", []) if spec else []
    return {
        operation_id
        for operation_id, operation in _operations(doc, spec=spec)
        if operation.get("security", inherited)
    }


def test_the_application_publishes_the_bearer_scheme_the_contract_names() -> None:
    """The scheme itself, under the contract's own name.

    ``firebaseBearer`` is not decoration: it is what puts the Authorize button
    in the docs and what makes a generated client send the header. alpha's
    equivalent published no scheme at all — its openapi.json had no
    ``securitySchemes`` key — and every generated client had to be told about
    the header out of band.
    """
    published = LIVE["components"]["securitySchemes"]
    declared = SPEC["components"]["securitySchemes"]
    assert set(published) == set(declared) == {"firebaseBearer"}
    for field in ("type", "scheme", "bearerFormat"):
        assert published["firebaseBearer"][field] == declared["firebaseBearer"][field]


def test_the_authenticated_operations_are_exactly_the_ones_the_contract_requires() -> None:
    """Both directions, and the backward one is the one that found a defect.

    Forward: a route cannot quietly become public. Backward: a route the
    contract marks public cannot ship behind a guard. Running it found the
    OAuth callback inheriting the global requirement in ``openapi.yaml`` while
    §3.66 had shipped it unauthenticated ON PURPOSE — a third-party redirect
    lands in a browser with no Authorization header, so the contract, not the
    code, was the thing that was wrong.
    """
    assert _authenticated(LIVE, spec=False) == _authenticated(SPEC, spec=True)


def test_the_public_operations_are_the_three_that_have_to_be() -> None:
    """Named one by one rather than counted, because "how many routes are
    reachable without a credential" is the single most consequential number in
    this document, and a set difference is what a reviewer can actually check:
    two liveness probes a load balancer calls before anyone has logged in, and
    one OAuth redirect target that cannot carry a bearer token by protocol."""
    everything = {operation_id for operation_id, _ in _operations(LIVE, spec=False)}
    assert everything - _authenticated(LIVE, spec=False) == {
        ("/health", "get"),
        ("/health/ready", "get"),
        ("/api/v1/integrations/connections/oauth/callback", "get"),
    }


@pytest.mark.parametrize("field", ["id", "file_id"])
def test_identifiers_cross_the_wire_as_text(field: str) -> None:
    """DD-06: "المعرّفات: نصوص UUID". A numeric id would be a different
    contract and a different data model."""
    document = LIVE["components"]["schemas"]["DocumentOut"]["properties"][field]
    assert document["type"] == "string"
