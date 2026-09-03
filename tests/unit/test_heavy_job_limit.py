"""The heavy-job ceiling — capacity-plan step 1.3 (07 §4: 30 job/min per user).

Three kinds of claim, and they are separated because each can be false while
the other two are true:

* **the POLICY** — one bucket, the right scope, the right window, the right
  ceiling, and a refusal that names the ceiling without naming the tenant.
  Over ``InMemoryRateLimiter``, which models the Lua script's three declared
  properties; the script itself is proven against a real server in
  ``tests/integration/test_rate_limiter_live.py``;
* **the PLACEMENT** — the guard sits on exactly the operations that answer
  **202**, read off the finished application in BOTH directions. That is the
  claim four decorators cannot make about themselves: a fifth queueing route
  written next year is either guarded or it fails this file on the day it is
  added;
* **the BEHAVIOUR through the real routes** — the 31st submission answers the
  wire contract's 429, and *nothing reached the queue*. That second half is
  1.3's acceptance criterion in as many words ("ولا يُقبَل عملٌ في المجرى بعد
  الرفض"), and it is the reason the guard is a route dependency rather than a
  line inside a handler: an assertion that the media repository and the outbox
  are both untouched can only pass if the refusal happened before either was
  written.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.middleware.heavy_jobs import heavy_job
from app.api.middleware.rate_limit import HEAVY_SCOPE, ApiRateLimiter, HeavyJobRateLimiter
from app.api.middleware.rbac import PermissionGuard
from app.api.v1.dependencies import ApiServices, Principal
from app.api.v1.websocket.streaming import WsPrincipal
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, RateLimitedError, UnauthorizedError, ValidationError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Limits, Settings
from app.framework.streaming import ConnectionHub
from app.framework.workflows import InMemoryWorkflowRegistry
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import FilesMediaStack, InMemorySpaces, build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_rate_limit import InMemoryRateLimiter
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_WORKSPACE_USAGE = build_workspace_usage()
_CREDENTIALS = build_credentials()
_KNOWLEDGE = build_knowledge()
_INTEGRATIONS = build_integrations()

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_SPACE = "018f0000-0000-7000-8000-0000000000sp"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_U2 = "018f0000-0000-7000-8000-0000000000u2"
_GOOD = "good"

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# The policy                                                                   #
# --------------------------------------------------------------------------- #
def _limiter(
    per_min: int = 30,
) -> tuple[HeavyJobRateLimiter, InMemoryRateLimiter]:
    fake = InMemoryRateLimiter()
    return HeavyJobRateLimiter(fake, jobs_per_min=per_min), fake


async def _submit(limiter: HeavyJobRateLimiter, times: int, *, user: str = _U1) -> None:
    for _ in range(times):
        await limiter.check(user_id=user)


async def test_one_submission_consumes_from_exactly_one_bucket() -> None:
    """1.3 is ONE ceiling, not 1.2's pair with a third name. A workspace-wide
    job ceiling is a number 07 §4 does not declare, and inventing one here
    would put an unsigned limit into the enforcement path."""
    limiter, fake = _limiter()

    await limiter.check(user_id=_U1)

    (buckets,) = fake.calls
    assert [bucket.scope for bucket in buckets] == [HEAVY_SCOPE]


async def test_the_bucket_carries_the_minute_window_and_the_declared_ceiling() -> None:
    """07 §4 writes "30 job/min", so both halves of it have to arrive: a
    ceiling of 30 over a window of anything else is a different limit."""
    limiter, fake = _limiter()

    await limiter.check(user_id=_U1)

    (bucket,) = fake.calls[0]
    assert (bucket.limit, bucket.window_s) == (30, 60)


async def test_the_ceiling_is_07s_own_number_by_default() -> None:
    """Not a literal repeated in a test: the wiring reads
    `Limits.heavy_jobs_per_min`, and this is the assertion that the number in
    the design document is the number in force."""
    assert Limits().heavy_jobs_per_min == 30


async def test_a_users_job_budget_is_not_their_request_budget() -> None:
    """The two ceilings are the same person's, and they must not be the same
    bucket: thirty uploads should not consume a quarter of somebody's 120
    requests, and 120 reads should not lock them out of uploading."""
    fake = InMemoryRateLimiter()
    heavy = HeavyJobRateLimiter(fake, jobs_per_min=1)
    requests = ApiRateLimiter(fake, user_per_min=1, workspace_per_min=1)

    await heavy.check(user_id=_U1)

    # The heavy bucket is full; the request bucket has never been touched.
    assert fake.count(f"{HEAVY_SCOPE}:{_U1}") == 1
    assert fake.count(f"user:{_U1}") == 0
    await requests.check(user_id=_U1, workspace_id=_W1)


async def test_two_users_do_not_share_a_job_budget() -> None:
    """Per USER is the row 07 §4 writes. A shared bucket would let one
    colleague's bulk import refuse everybody else's single upload."""
    limiter, _fake = _limiter(per_min=1)
    await limiter.check(user_id=_U1)

    await limiter.check(user_id=_U2)


# --------------------------------------------------------------------------- #
# The refusal                                                                  #
# --------------------------------------------------------------------------- #
async def test_the_thirty_first_job_in_a_minute_is_refused() -> None:
    """1.3's acceptance criterion at the DECLARED ceiling, not at a small
    synthetic one: "30 job/min" has been in 07 §4 since it was written and
    this is the first assertion that anything reads it."""
    limiter, _fake = _limiter()
    await _submit(limiter, 30)

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_U1)

    assert refusal.value.retry_after_s >= 1


async def test_a_refusal_names_the_ceiling_and_no_identifier() -> None:
    """1.2's rule, unchanged: the caller learns which of ITS limits bound, and
    nothing about the platform's remaining room or anyone else's id."""
    limiter, _fake = _limiter(per_min=1)
    await limiter.check(user_id=_U1)

    with pytest.raises(RateLimitedError) as refusal:
        await limiter.check(user_id=_U1)

    detail = str(refusal.value)
    assert "heavy job" in detail
    assert _U1 not in detail
    assert "1" not in detail


async def test_a_window_that_has_rolled_admits_again() -> None:
    """A sliding log, so the budget returns entry by entry rather than all at
    once on a calendar boundary."""
    limiter, fake = _limiter(per_min=1)
    await limiter.check(user_id=_U1)

    fake.now_ms += 60_001

    await limiter.check(user_id=_U1)


# --------------------------------------------------------------------------- #
# Failure policy                                                               #
# --------------------------------------------------------------------------- #
async def test_a_store_outage_admits_the_job_rather_than_refusing_it() -> None:
    """⚠️ Fail OPEN, the same direction 1.2 chose and for a sharper reason: a
    Redis outage that refused every upload and every index request would BE
    the outage this ceiling exists to prevent."""
    limiter, fake = _limiter(per_min=1)
    fake.failure = AppError("redis is unreachable", code="common.internal")

    await _submit(limiter, 5)


async def test_the_outage_does_not_leak_the_stores_error_to_the_caller() -> None:
    """A limiter that could not decide is invisible to the client by design;
    what makes it visible to the OPERATOR is the `unavailable` counter."""
    limiter, fake = _limiter()
    fake.failure = AppError("redis is unreachable", code="common.internal")

    await limiter.check(user_id=_U1)


# --------------------------------------------------------------------------- #
# Construction                                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("per_min", [0, -1])
def test_a_non_positive_ceiling_is_refused_at_construction(per_min: int) -> None:
    """Zero is a WIRING decision, never a ceiling: `API_RATE_LIMIT_ENABLED=
    false` builds no limiter at all, while a zero accepted here would refuse
    every job in the platform with a perfectly well-formed 429."""
    with pytest.raises(ValidationError):
        HeavyJobRateLimiter(InMemoryRateLimiter(), jobs_per_min=per_min)


def test_the_ceiling_is_readable_back() -> None:
    limiter, _fake = _limiter(per_min=7)
    assert limiter.jobs_per_min == 7


# --------------------------------------------------------------------------- #
# The application under test                                                   #
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
    """One token per role, so the 403 path is reachable without a second app."""

    def __init__(self, role: str = "owner") -> None:
        self._role = role

    async def authenticate(self, token: str) -> Principal:
        if token != _GOOD:
            raise UnauthorizedError("bad token", code="auth.invalid_token")
        return Principal(workspace_id=_W1, user_id=_U1, roles=frozenset({self._role}))


class _FakeWsAuth:
    async def authenticate(self, token: str) -> WsPrincipal:
        return WsPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset({"owner"}))


def _make_app(
    *,
    heavy_job_limiter: HeavyJobRateLimiter | None = None,
    role: str = "owner",
) -> tuple[FastAPI, FilesMediaStack]:
    registry = InMemoryAgentRegistry()
    conversations = build_conversations()
    stack = build_files_media(spaces=InMemorySpaces(active={_SPACE}))
    services = ApiServices(
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
        files=stack.files,
        file_deletion=stack.file_deletion,
        file_replacement=stack.file_replacement,
        media=stack.media,
        space_quota=stack.space_quota,
        workspace=_WORKSPACE_USAGE.workspace,
        usage=_WORKSPACE_USAGE.usage,
        credentials=_CREDENTIALS.credentials,
        knowledge=_KNOWLEDGE.knowledge,
        integrations=_INTEGRATIONS.integrations,
        authorization=build_authorization(),
        idempotency=InMemoryIdempotencyStore(),
        # capacity-plan 1.3. `None` by default, so every suite written before
        # it keeps asserting the unlimited behaviour it was written against.
        heavy_job_limiter=heavy_job_limiter,
    )
    app = create_app(services, http_authenticator=_FakeAuth(role), ws_authenticator=_FakeWsAuth())
    return app, stack


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_GOOD}"}


def _post_job(client: TestClient) -> int:
    """One media submission, as a status code. The route tests below count
    submissions rather than inspect them, and 07 §4's ceiling is thirty."""
    return client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth()).status_code


def _job_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": "image",
        "prompt": "a cat",
        "agent_key": "image-agent",
        "params": {"width": 512, "height": 512},
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Placement — the guard is on the 202s, and on nothing else                     #
# --------------------------------------------------------------------------- #
def _routes(app: FastAPI) -> Iterator[tuple[str, APIRoute]]:
    """Every ``APIRoute`` in the composed app, with the prefix it is mounted
    under — ``test_api_rbac.py``'s walker, for the same reason: the versioned
    routers are on a sub-application, so ``app.routes`` alone finds only the
    unprefixed probes."""
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            yield "", entry
            continue
        included = getattr(entry, "original_router", None)
        if included is None:
            continue
        for route in included.routes:
            if isinstance(route, APIRoute):
                yield entry.include_context.prefix, route


def _operations(app: FastAPI) -> Iterator[tuple[str, str, APIRoute]]:
    for prefix, route in _routes(app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, f"{prefix}{route.path}", route


def _guarded(route: APIRoute) -> bool:
    return any(dependency.call is heavy_job for dependency in route.dependant.dependencies)


def test_the_guard_is_on_exactly_the_operations_that_answer_202() -> None:
    """The rule, in both directions, read off the finished application.

    202 is this API's word for "a worker will do this later" — every route
    that queues work answers it and no route that does not. So the set of 202s
    IS the set of queue entrances, and pinning the guard to it makes the next
    queueing route's omission a failing test rather than a silent hole. Four
    decorators can be kept in step by hand exactly until they are five.
    """
    app, _stack = _make_app()

    queueing = {
        (method, path) for method, path, route in _operations(app) if route.status_code == 202
    }
    guarded = {(method, path) for method, path, route in _operations(app) if _guarded(route)}

    assert queueing == guarded
    # And the set is not accidentally empty, which would make the equality
    # above true and meaningless.
    assert len(queueing) == 4


def test_every_guarded_operation_is_charged_after_its_permission_is_checked() -> None:
    """The order is the contract (`api/middleware/heavy_jobs.py`): a caller
    that lacks the permission must be told WHICH permission, not told to slow
    down and retry something that can never succeed."""
    app, _stack = _make_app()

    for _method, path, route in _operations(app):
        if not _guarded(route):
            continue
        calls = [dependency.call for dependency in route.dependant.dependencies]
        permission = next(i for i, call in enumerate(calls) if isinstance(call, PermissionGuard))
        assert permission < calls.index(heavy_job), path


# --------------------------------------------------------------------------- #
# Behaviour through the real routes                                            #
# --------------------------------------------------------------------------- #
def test_the_thirty_first_submission_is_the_wire_contracts_429() -> None:
    """End to end at the declared ceiling: thirty media jobs are queued, the
    thirty-first is an RFC 9457 problem with an RFC 9110 header."""
    fake = InMemoryRateLimiter()
    app, _stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=30))
    client = TestClient(app)

    for _ in range(30):
        assert _post_job(client) == 202

    response = client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth())

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    body = response.json()
    assert body["code"] == "common.rate_limited"
    assert body["correlation_id"]


def test_a_refused_submission_queues_nothing() -> None:
    """⭐ The other half of 1.3's acceptance criterion, and the reason the
    guard is a route dependency: no job row, and no outbox record, which
    together are the only way work reaches a worker. A guard inside the
    handler would have had to undo both."""
    fake = InMemoryRateLimiter()
    app, stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=1))
    client = TestClient(app)
    assert _post_job(client) == 202
    rows, events = len(stack.media_repository.rows), len(stack.outbox.calls)

    assert _post_job(client) == 429

    assert len(stack.media_repository.rows) == rows
    assert len(stack.outbox.calls) == events


def test_a_refused_submission_does_not_claim_its_idempotency_key() -> None:
    """The refusal lands before the ledger, so a client whose burst was shed
    can retry the same key the moment its window reopens — rather than meeting
    an in-flight claim from an attempt that never ran."""
    fake = InMemoryRateLimiter()
    app, _stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=1))
    client = TestClient(app)
    headers = _auth() | {"Idempotency-Key": "k-1"}
    assert _post_job(client) == 202

    refused = client.post("/api/v1/media/jobs", json=_job_body(), headers=headers)
    fake.now_ms += 60_001
    retried = client.post("/api/v1/media/jobs", json=_job_body(), headers=headers)

    assert refused.status_code == 429
    assert retried.status_code == 202, retried.text


def test_a_403_is_not_charged_to_the_job_budget() -> None:
    """A submission the platform was never going to accept must not spend a
    budget — and a caller missing `media:create` has to hear about the
    permission, since that is the only fact it can act on. 1.2 drew the same
    line between 401 and 429."""
    fake = InMemoryRateLimiter()
    app, _stack = _make_app(
        heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=30), role="viewer"
    )
    client = TestClient(app)

    response = client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth())

    assert response.status_code == 403
    assert fake.count(f"{HEAVY_SCOPE}:{_U1}") == 0


def test_a_filled_job_budget_still_serves_the_reads() -> None:
    """The ceiling bounds submissions, not the client. Polling `GET
    /media/jobs/{id}` is how a caller learns its jobs finished, and a job
    ceiling that stopped it would leave every shed client blind as well as
    refused."""
    fake = InMemoryRateLimiter()
    app, _stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=1))
    client = TestClient(app)
    queued = client.post("/api/v1/media/jobs", json=_job_body(), headers=_auth()).json()

    assert _post_job(client) == 429

    read = client.get(f"/api/v1/media/jobs/{queued['id']}", headers=_auth())
    assert read.status_code == 200


def test_an_unwired_limiter_leaves_the_queue_doors_open() -> None:
    """`API_RATE_LIMIT_ENABLED=false` builds nothing (`م-8`), and every suite
    written before 1.3 runs in exactly that shape."""
    app, _stack = _make_app()
    client = TestClient(app)

    for _ in range(35):
        assert _post_job(client) == 202


def test_a_store_outage_queues_the_job_instead_of_refusing_it() -> None:
    """Fail open, through the route this time: a Redis the limiter cannot
    reach costs throughput protection and NOT the ability to upload."""
    fake = InMemoryRateLimiter()
    fake.failure = AppError("redis is unreachable", code="common.internal")
    app, stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(fake, jobs_per_min=1))
    client = TestClient(app)

    for _ in range(3):
        assert _post_job(client) == 202
    assert len(stack.media_repository.rows) == 3
