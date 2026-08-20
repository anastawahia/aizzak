"""P1-6 (``docs/p1-hardening-plan.md`` §3 step 13 · ``docs/log/3.92.md``): the
FIRST measured latency SLO on this platform beyond the embedding hop (§3.83).

**The row chosen (not this file's to re-litigate — the plan fixed it):**
``07-nfr-slo.md`` §2's «قراءة متزامنة (GET مورد)» — p50 40ms / p95 150ms /
p99 300ms — measured on the real production path of
``GET /api/v1/conversations/{conversation_id}``: routing → the auth
dependency → RBAC (`api.middleware.rbac.require`) → ``GetConversation`` →
``SqlConversationRepository`` → a tenant-scoped session (RLS
``SET LOCAL app.workspace_id``) → PgBouncer → PostgreSQL. No external
provider sits on this path (07 §2's own carve-out for LLM/media latency), it
is deterministic and repeatable (unlike a first-token measurement), and it is
the busiest row in the table.

**Methodology, copied from §3.83 on purpose (so the two numbers stay
comparable):** warm-up requests are run and DISCARDED, THEN a declared
sample count is measured with ``time.perf_counter()``, sorted, and percentiles
are taken by ``int(n * p)`` indexing into the sorted list. This file adds one
thing §3.83 did not need: a declared CONCURRENCY (target 8 in-flight
requests). ``docs/log/3.92.md`` §13 and ``07-nfr-slo.md`` §2 record the
accepted concurrency sweep: this single-process ASGI harness stays within the
40ms p50 budget through 8, while 16 saturates its one event loop and measures
the harness's queue rather than the multi-worker live deployment. Per-request
round trips to a real database are still exercised concurrently, unlike a
single sequential probe.

**The ONE substitution the task's own mandate names**: the Firebase
``HttpAuthenticator`` seam (``api/v1/dependencies.py``) is replaced with
``_FixedAuthenticator`` returning a ``Principal`` for a real, freshly seeded
workspace/user/conversation -- a real Google-signed Firebase ID token for
``aizzak-local-dev`` cannot be minted in this environment (§3.83 §4, restated
here because it is still true). Every other collaborator on the measured
path -- ``ConversationUseCases``, ``AuthorizationService.is_allowed`` (a pure
function over the roles ``current_principal`` hands back — no I/O, see
``api/middleware/rbac.py``'s own docstring), ``SqlConversationRepository``,
``TenantSessionFactory`` -- is the production object, built by
``CompositionRoot.from_env()`` exactly as ``create_production_app()`` builds
it.

**TWO further deviations, forced by what this step discovered live and NOT
by convenience — both declared here, not buried:**

1. **The live Vault AppRole cannot currently authenticate at all.**
   Reproduced with the literal production adapter (``hvac.Client.auth
   .approle.login`` -- ``infrastructure/secrets/vault_secrets.py``'s own
   call), against the live stack's ``vault:8200``, using the EXACT
   ``VAULT_ROLE_ID``/``VAULT_SECRET_ID`` the running ``aizzak-app-1``
   container was given: every attempt (3/3) fails with
   ``InternalServerError: failed to determine alias name from login
   request`` -- a Vault-side identity/alias fault, not a credential typo (a
   deliberately wrong role/secret_id gets the expected, DIFFERENT 400
   "invalid role or secret ID"). The root token recorded in the ``vault-init``
   volume at bootstrap time is itself invalid (``token lookup-self`` → 403),
   so there is no admin path available to diagnose or repair this from
   outside Vault, and restarting/reconfiguring the live Vault container is
   out of this step's hard constraints regardless. Since
   ``CompositionRoot.from_env()`` performs this login UNCONDITIONALLY at
   construction (``create_vault_client``, before a single conversations-module
   object is even built) whenever ``VAULT_SECRET_ID`` is non-empty, this
   harness runs with ``VAULT_SECRET_ID`` deliberately BLANK -- the
   documented "not using AppRole" path (``create_approle_relogin``'s own
   docstring: "``None`` when either half of the AppRole pair is absent").
   ``CompositionRoot.from_env()`` then builds an unauthenticated
   ``hvac.Client`` (never touched, since the measured path never calls
   ``SecretsProvider``) instead of crashing. This is a live-infrastructure
   fault this step SURFACES, not one it is scoped to fix (§0 rule 1) — see
   ``docs/log/3.92.md`` for the full reproduction and ``docs/p1-hardening
   -plan.md`` §5-ب's new note.
2. **``connect_storage`` is omitted from ``startup=``, and the notify bridge
   is omitted from ``background=``.** The first is a direct consequence of
   (1) -- MinIO's credentials come from a Vault KV read this process cannot
   perform. The second is a hard-constraint safety decision independent of
   Vault: ``create_production_app``'s background task joins a REAL Redis
   Streams consumer group over the SAME production ``stream.knowledge``/
   ``stream.media`` this stack actually serves traffic on
   (``composition_root.py::build_notification_consumer``) -- a throwaway
   measurement container has no business creating or acking against a real
   consumer group on those streams, so it is never started here. Neither
   omission touches the measured path: ``GET /conversations/{id}`` calls no
   storage face and publishes to no stream.

**What this means for the number below:** it is real, production-code,
production-database latency -- routed exactly as a live client's request
would be, over the same PgBouncer, against the same Postgres, through the
same RLS policies -- with the request's OWN identity resolution swapped for
a fixed one (unavoidable) and two inert, off-path startup/background hooks
removed (unavoidable given (1), and a deliberate safety call given the
Streams risk). Nothing on the measured span itself is faked.

**How to actually run this** (never under the bare gate-5 ``pytest``, which
skips it — see the ``skipif`` below):

    OWNER_DSN="postgresql+asyncpg://aizzak_owner:<PASSWORD>@postgres:5432/aizzak"
    docker run --rm --network aizzak_default \\
        -v "$(pwd)/src:/app/src:ro" \\
        -v "$(pwd)/tests:/app/tests:ro" \\
        --env-file <file with aizzak-app-1's inherited env, VAULT_SECRET_ID
                    blanked> \\
        -e RUN_P1_6_LOAD_TEST=1 \\
        -e LOAD_TEST_OWNER_DATABASE_URL="$OWNER_DSN" \\
        aizzak/app:dev \\
        sh -c 'pip install --no-cache-dir --quiet \\
                   --target=/tmp/pydeps "pytest>=8.3" "prometheus-client>=0.20" &&
               PYTHONPATH="/tmp/pydeps:$PYTHONPATH" \\
               python /app/tests/integration/test_conversation_get_slo_live.py'

(the ``__main__`` block at the bottom runs the exact same coroutine the
pytest test calls. Two packages, not one: ``pytest`` is only imported for
``pytestmark``/``pytest.mark.anyio``, but ``prometheus-client`` is a HARD
import of ``api/main.py`` itself (``api/metrics.py``, P1-3 step 10) that the
running image predates — measured live: `git log --since=2026-07-26 --
pyproject.toml` shows step 10 added it AFTER this image's build timestamp,
which the task's own "no dependency churn" fact did not catch (it checked a
``requirements.txt`` this repository does not have). ``--target=`` rather
than a normal install: the image's ``/opt/venv`` is owned by root and the
process runs as the unprivileged ``aizzak`` user (the ``Dockerfile``'s own
``USER aizzak``), so a plain ``pip install`` fails with a permission error.
Neither install touches the persisted image — both die with the ``--rm``
container. A machine with the full dev venv AND network access to the
compose stack can instead run ``pytest tests/integration/
test_conversation_get_slo_live.py -m live_stack_slo -s`` directly.)

Seeding/cleanup: one workspace + one user + one conversation + five messages,
every row tagged with a distinctive ``p1-step13-<uuid7>`` string, written and
torn down through the OWNER role (``aizzak_owner``) because these tables are
RLS-subject (``FORCE ROW LEVEL SECURITY`` — even the owner needs
``SET LOCAL app.workspace_id`` first, exactly like step 12's live probe). The
teardown re-counts this seed's IDs in all four tables afterwards and the test
asserts they are back to zero. Deliberately scoped predicates make the proof
composable with a non-empty development database: pre-existing workspaces are
preserved and cannot make this test fail or be mistaken for this file's
residue.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.api.main import create_app
from app.api.v1.dependencies import ApiServices, Principal
from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.composition_root import CompositionRoot
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.framework.types import Uuid
from app.infrastructure.persistence.database import create_engine, create_sessionmaker
from app.infrastructure.persistence.rls import TenantSessionFactory

# ---------------------------------------------------------------------------
# The gate: skip unless BOTH an explicit opt-in and the live stack are real.
# ---------------------------------------------------------------------------
_ENABLE_VAR = "RUN_P1_6_LOAD_TEST"
_OWNER_DSN_VAR = "LOAD_TEST_OWNER_DATABASE_URL"
_PROBE_TIMEOUT_S = 1.5


def _tcp_reachable(host: str, port: int, timeout_s: float = _PROBE_TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


async def _select_one(dsn: str) -> None:
    engine = create_engine(DatabaseSettings(url=dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def _handshake_failure(dsn: str) -> str | None:
    """``None`` ⇒ this DSN's role really can log in and query. Anything else
    ⇒ the driver's own sentence explaining why it cannot.

    A port check is not a credential check: an open port with the wrong
    password is not "unreachable", and the whole integration suite once
    ERRORED 392 times on exactly that gap
    (``rag-retrieval-plan-review.md §10``, cured for the ``live_*`` fixtures
    in ``tests/integration/conftest.py``). This gate is the same shape, so it
    gets the same cure: connect + ``SELECT 1`` through the app's own
    ``create_engine``, and any failure becomes a stated skip reason instead
    of a seeding traceback.
    """
    try:
        asyncio.run(_select_one(dsn))
    # Deliberately broad: whatever a real connection attempt raises, the
    # answer to "can this run" is no, and the reason is worth printing.
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _skip_reason() -> str | None:
    """``None`` ⇒ run for real. Anything else ⇒ the exact reason this
    process is not driving real traffic at the live stack."""
    if not os.environ.get(_ENABLE_VAR):
        return (
            f"set {_ENABLE_VAR}=1 to run this against the live docker-compose stack "
            "(never auto-run — see this module's own docstring for the exact "
            "docker run invocation, docs/log/3.92.md)"
        )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return "DATABASE_URL is not set — this must run inside the aizzak_default network"
    url = make_url(database_url)
    if url.host is None or url.port is None or not _tcp_reachable(url.host, url.port):
        return f"{url.host}:{url.port} unreachable — run this inside the aizzak_default network"
    owner_dsn = os.environ.get(_OWNER_DSN_VAR)
    if not owner_dsn:
        return f"{_OWNER_DSN_VAR} is not set — the owner role is needed to seed/clean up"
    for label, dsn in (("DATABASE_URL", database_url), (_OWNER_DSN_VAR, owner_dsn)):
        failure = _handshake_failure(dsn)
        if failure is not None:
            return f"{label} did not complete a handshake — {failure}"
    return None


_SKIP_REASON = _skip_reason()

pytestmark = [pytest.mark.live_stack_slo]


@pytest.fixture(autouse=True)
def _require_live_stack() -> None:
    """The ``live_*`` idiom's own fixture-based skip (the ``live_db``/
    ``live_redis`` precedent in ``tests/integration/conftest.py``), not a
    static ``skipif`` — a multi-line ``pytestmark`` list defeats the
    single-line regex ``tests/unit/test_ci_workflow_truthfulness.py`` uses to
    prove every integration module is ``live_*``-gated."""
    if _SKIP_REASON is not None:
        pytest.skip(_SKIP_REASON)


# ---------------------------------------------------------------------------
# Load shape (07-nfr-slo.md §2's "قراءة متزامنة" row; §3.83's sampling method).
# ---------------------------------------------------------------------------
_WARMUP_REQUESTS = 50
_MEASURED_REQUESTS = 1000
# The accepted §3.92 §13 / 07-nfr-slo §2 boundary for this one-process ASGI
# harness. 16 measures event-loop saturation, not the live two-worker app.
_CONCURRENCY = 8

_BUDGET_P50_S = 0.040
_BUDGET_P95_S = 0.150
_BUDGET_P99_S = 0.300

_MESSAGE_COUNT = 5


@dataclass(frozen=True, slots=True)
class _Seed:
    """The one tagged workspace/user/conversation this file owns end to end."""

    tag: str
    workspace_id: Uuid
    user_id: Uuid
    conversation_id: Uuid


@dataclass(frozen=True, slots=True)
class _FixedAuthenticator:
    """The ONE substitution the task's mandate names (module docstring) —
    structurally satisfies ``HttpAuthenticator``/``WsAuthenticator``
    (``api/v1/dependencies.py``): a bearer token maps to a FIXED, real
    ``Principal`` rather than a verified Firebase one."""

    principal: Principal

    async def authenticate(self, token: str) -> Principal:
        # `token` is intentionally ignored -- this seam always answers with
        # the same, fixed `Principal` (module docstring's ONE substitution).
        return self.principal


@dataclass(frozen=True, slots=True)
class LoadReport:
    samples: int
    concurrency: int
    warmup: int
    wall_s: float
    p50_s: float
    p95_s: float
    p99_s: float
    min_s: float
    max_s: float
    latencies_s: list[float] = field(repr=False)

    @property
    def throughput_rps(self) -> float:
        return self.samples / self.wall_s if self.wall_s > 0 else float("inf")

    def render(self) -> str:
        budgets_ok = (
            self.p50_s <= _BUDGET_P50_S,
            self.p95_s <= _BUDGET_P95_S,
            self.p99_s <= _BUDGET_P99_S,
        )
        verdict = "PASS" if all(budgets_ok) else "FAIL"
        return (
            "GET /api/v1/conversations/{id} — P1-6 SLO "
            "(docs/design/07-nfr-slo.md §2, 'قراءة متزامنة (GET مورد)')\n"
            f"  samples={self.samples} concurrency={self.concurrency} warmup={self.warmup} "
            f"wall={self.wall_s:.3f}s throughput={self.throughput_rps:.1f} req/s\n"
            f"  p50={self.p50_s * 1000:.2f}ms (budget 40ms)\n"
            f"  p95={self.p95_s * 1000:.2f}ms (budget 150ms)\n"
            f"  p99={self.p99_s * 1000:.2f}ms (budget 300ms)\n"
            f"  min={self.min_s * 1000:.2f}ms max={self.max_s * 1000:.2f}ms\n"
            f"  verdict={verdict}"
        )


def _percentile(sorted_samples: list[float], p: float) -> float:
    """§3.83's own indexing convention (``int(n * p)`` into an ascending
    sorted sample list), kept identical so the two numbers are comparable."""
    n = len(sorted_samples)
    index = min(int(n * p), n - 1)
    return sorted_samples[index]


# ---------------------------------------------------------------------------
# Seeding / cleanup — through the OWNER role, RLS-aware (module docstring).
# ---------------------------------------------------------------------------
def _ctx(workspace_id: Uuid) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id=None, correlation_id=new_uuid7(), roles=frozenset()
    )


async def seed_conversation(owner_dsn: str) -> _Seed:
    tag = f"p1-step13-{new_uuid7()}"
    workspace_id = new_uuid7()
    user_id = new_uuid7()
    conversation_id = new_uuid7()
    now = utc_now()

    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        # workspace.workspaces carries NO RLS (01-data-model §2.1, R2) — a
        # plain owner-role insert, no `app.workspace_id` to set.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO workspace.workspaces "
                    "(id, owner_user_id, name, status, created_at, updated_at, version) "
                    "VALUES (:id, :owner, :name, 'active', :now, :now, 1)"
                ),
                {"id": workspace_id, "owner": user_id, "name": tag, "now": now},
            )

        sessionmaker = create_sessionmaker(engine)
        tenant_session = TenantSessionFactory(sessionmaker)
        ctx = _ctx(workspace_id)
        async with tenant_session(ctx) as session:
            await session.execute(
                text(
                    "INSERT INTO workspace.users "
                    "(id, workspace_id, firebase_uid, email, display_name, status, "
                    " created_at, updated_at, version) "
                    "VALUES (:id, :ws, :uid, :email, :name, 'active', :now, :now, 1)"
                ),
                {
                    "id": user_id,
                    "ws": workspace_id,
                    "uid": tag,
                    "email": f"{tag}@example.invalid",
                    "name": tag,
                    "now": now,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO conversations.conversations "
                    "(id, workspace_id, agent_key, kind, title, created_by, "
                    " created_at, updated_at, version) "
                    "VALUES (:id, :ws, 'load-test-p1-13', 'agent', :title, :creator, :now, :now, 1)"
                ),
                {
                    "id": conversation_id,
                    "ws": workspace_id,
                    "title": tag,
                    "creator": user_id,
                    "now": now,
                },
            )
            for seq in range(1, _MESSAGE_COUNT + 1):
                content = json.dumps({"text": f"{tag} message {seq}", "attachments": []})
                await session.execute(
                    text(
                        "INSERT INTO conversations.messages "
                        "(id, conversation_id, workspace_id, role, content, token_count, "
                        " seq, created_at) "
                        "VALUES (:id, :conv, :ws, :role, CAST(:content AS jsonb), :tok, :seq, :now)"
                    ),
                    {
                        "id": new_uuid7(),
                        "conv": conversation_id,
                        "ws": workspace_id,
                        "role": "user" if seq % 2 else "assistant",
                        "content": content,
                        "tok": 12,
                        "seq": seq,
                        "now": now,
                    },
                )
    finally:
        await engine.dispose()
    return _Seed(
        tag=tag, workspace_id=workspace_id, user_id=user_id, conversation_id=conversation_id
    )


async def cleanup_conversation(owner_dsn: str, seed: _Seed) -> dict[str, int]:
    """Delete every seeded row and prove this seed's four predicates are zero.

    The live development database need not be globally empty. Existing rows
    belong to its operator and are neither residue nor ours to delete.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        sessionmaker = create_sessionmaker(engine)
        tenant_session = TenantSessionFactory(sessionmaker)
        ctx = _ctx(seed.workspace_id)
        async with tenant_session(ctx) as session:
            await session.execute(
                text("DELETE FROM conversations.messages WHERE conversation_id = :conv"),
                {"conv": seed.conversation_id},
            )
            await session.execute(
                text("DELETE FROM conversations.conversations WHERE id = :id"),
                {"id": seed.conversation_id},
            )
            await session.execute(
                text("DELETE FROM workspace.users WHERE id = :id"), {"id": seed.user_id}
            )
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM workspace.workspaces WHERE id = :id"), {"id": seed.workspace_id}
            )
            proof = {
                "workspace.workspaces": (
                    await conn.execute(
                        text("SELECT count(*) FROM workspace.workspaces WHERE id = :id"),
                        {"id": seed.workspace_id},
                    )
                ).scalar_one(),
                "workspace.users": (
                    await conn.execute(
                        text("SELECT count(*) FROM workspace.users WHERE id = :id"),
                        {"id": seed.user_id},
                    )
                ).scalar_one(),
                "conversations.conversations": (
                    await conn.execute(
                        text("SELECT count(*) FROM conversations.conversations WHERE id = :id"),
                        {"id": seed.conversation_id},
                    )
                ).scalar_one(),
                "conversations.messages": (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM conversations.messages "
                            "WHERE conversation_id = :id"
                        ),
                        {"id": seed.conversation_id},
                    )
                ).scalar_one(),
            }
    finally:
        await engine.dispose()
    return proof


# ---------------------------------------------------------------------------
# The ASGI app under test — `create_app` exactly as `create_production_app`
# calls it, minus the two off-path hooks the module docstring names.
# ---------------------------------------------------------------------------
def _build_app(root: CompositionRoot, principal: Principal) -> tuple[httpx.ASGITransport, FastAPI]:
    services = ApiServices(
        settings=root.settings,
        orchestrator=root.orchestrator,
        hub=root.hub,
        agents=root.agent_registry,
        conversations=root.conversations,
        workflows=root.workflow_registry,
        files=root.files,
        media=root.media,
        workspace=root.workspace,
        usage=root.usage,
        credentials=root.credentials,
        knowledge=root.knowledge,
        integrations=root.integrations,
        authorization=root.authorization,
        idempotency=root.idempotency,
    )
    authenticator = _FixedAuthenticator(principal)
    app = create_app(
        services,
        http_authenticator=authenticator,
        ws_authenticator=authenticator,
        metrics_source=root.metrics_source,
        # Module docstring, deviation 2: no notify bridge (real Streams
        # consumer group on production stream.knowledge/stream.media), no
        # connect_storage (Vault is down for this AppRole — deviation 1).
        background=(),
        startup=(),
        shutdown=tuple(root.disposables()),
    )
    return httpx.ASGITransport(app=app), app


async def _one_request(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> float:
    start = time.perf_counter()
    response = await client.get(url, headers=headers)
    elapsed = time.perf_counter() - start
    response.raise_for_status()
    return elapsed


async def _drive(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    count: int,
    concurrency: int,
) -> list[float]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = [0.0] * count

    async def _one(i: int) -> None:
        async with semaphore:
            latencies[i] = await _one_request(client, url, headers)

    await asyncio.gather(*(_one(i) for i in range(count)))
    return latencies


async def measure_conversation_get_slo() -> tuple[LoadReport, dict[str, int]]:
    """Seed → build the real app → warm up → drive the measured load →
    tear down and prove the counts are zero. Returns the report AND the
    cleanup proof so the caller (test or ``__main__``) can assert/print both."""
    owner_dsn = os.environ[_OWNER_DSN_VAR]
    seed = await seed_conversation(owner_dsn)
    try:
        root = CompositionRoot.from_env()
        principal = Principal(
            workspace_id=seed.workspace_id, user_id=seed.user_id, roles=frozenset({"owner"})
        )
        transport, app = _build_app(root, principal)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://p1-6-load-test") as client,
        ):
            prefix = root.settings.api_prefix
            url = f"{prefix}/conversations/{seed.conversation_id}"
            headers = {"Authorization": "Bearer p1-6-load-test-fixed-token"}

            for _ in range(_WARMUP_REQUESTS):
                await _one_request(client, url, headers)

            start = time.perf_counter()
            latencies = await _drive(
                client, url, headers, count=_MEASURED_REQUESTS, concurrency=_CONCURRENCY
            )
            wall_s = time.perf_counter() - start
    finally:
        proof = await cleanup_conversation(owner_dsn, seed)

    latencies.sort()
    report = LoadReport(
        samples=len(latencies),
        concurrency=_CONCURRENCY,
        warmup=_WARMUP_REQUESTS,
        wall_s=wall_s,
        p50_s=_percentile(latencies, 0.50),
        p95_s=_percentile(latencies, 0.95),
        p99_s=_percentile(latencies, 0.99),
        min_s=latencies[0],
        max_s=latencies[-1],
        latencies_s=latencies,
    )
    return report, proof


def _assert_slo_and_cleanup(report: LoadReport, proof: dict[str, int]) -> None:
    """One exit criterion shared by pytest and the documented ``__main__``."""
    assert all(count == 0 for count in proof.values()), (
        f"seeded rows were not fully cleaned up: {proof}"
    )
    assert report.p50_s <= _BUDGET_P50_S, f"p50 {report.p50_s * 1000:.2f}ms > 40ms budget"
    assert report.p95_s <= _BUDGET_P95_S, f"p95 {report.p95_s * 1000:.2f}ms > 150ms budget"
    assert report.p99_s <= _BUDGET_P99_S, f"p99 {report.p99_s * 1000:.2f}ms > 300ms budget"


@pytest.mark.anyio
async def test_conversation_get_meets_p1_6_slo_budget() -> None:
    report, proof = await measure_conversation_get_slo()
    # `-s` (pytest's own capture-disable flag) is how an operator re-running
    # this test SEES the exit criterion below -- printing it is the point.
    print(report.render())
    print(f"seed cleanup proof (must all be 0): {proof}")
    _assert_slo_and_cleanup(report, proof)


if __name__ == "__main__":
    # Runs the EXACT coroutine the pytest test calls, with no dev deps beyond
    # what the runtime image already ships (httpx is a core dependency —
    # `pyproject.toml`'s own `dependencies`, not `[dev]`) — see the module
    # docstring's docker run invocation.
    reason = _skip_reason()
    if reason is not None:
        raise SystemExit(f"refusing to run: {reason}")
    load_report, cleanup_proof = asyncio.run(measure_conversation_get_slo())
    print(load_report.render())
    print(f"seed cleanup proof (must all be 0): {cleanup_proof}")
    _assert_slo_and_cleanup(load_report, cleanup_proof)
