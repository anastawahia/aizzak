"""The ASGI application — assembly of the ``/api/v1`` surface (Phase 6.1-a).

``create_app`` is a FACTORY, not a module-level ``app``: every collaborator it
mounts (the orchestrator, the hub, the authenticators) is process-wide state
the Composition Root owns, and a hidden module global would make two isolated
test apps impossible. It takes an ``ApiServices`` bundle plus the two auth
seams and returns a wired ``FastAPI``; ``create_production_app`` is the thin
adapter that builds those from ``CompositionRoot.from_env()`` for uvicorn.

What 6.1-a assembles (later 6.1 sub-steps add the resource routers on top):

* **Correlation id** — one ``X-Correlation-Id`` per request (echoed if the
  client sent one, generated otherwise), stamped on ``request.state`` before
  any route runs and returned on every response and every error body (03 §0).
* **RFC 9457 problem responses** — the exception handlers that turn an
  ``AppError`` (the shared 10 §5 hierarchy), a request-validation failure, and
  an unexpected crash into ``application/problem+json``, reusing the
  ``api.errors`` builders 5.3-ب already shipped. 6.2 OWNS the full error model
  (the complete 03 §4 catalog, richer ``errors[]``); 6.1-a registers the
  minimum a delegating router needs so a raised ``AppError`` is already a
  correct problem response, and marks the boundary.
* **Health** — ``/health`` + ``/health/ready`` at the root, unauthenticated.
* **The WebSocket endpoint** — ``/api/v1/ws``, mounting the router 5.3-ج built
  and tested in isolation into the composed app for the first time.
* **The notification bridge** — started as a lifespan background task from the
  ``background`` factories a deployment passes (the ``cg.notify.<host>.<pid>``
  per-process consumer, in production — one group per OS process since
  §3.81, not one shared group; see ``composition_root.py``'s
  ``build_notification_consumer``), cancelled on shutdown. Its own consumer
  group is destroyed on a clean exit (``teardown_notify_bridge``) and any
  orphan a dead sibling left behind is swept at boot
  (``sweep_stale_notify_groups``).

The auth SEAMS (``http_authenticator`` / ``ws_authenticator``) stay seams —
``create_app`` never builds a verifier — but since **6.4-أ** the production
wiring passes a real one: a single ``ApiAuthenticator`` over the 2.7 Firebase
adapter, given to both parameters. The placeholders that used to refuse every
request are gone; there is no longer any state in which this app boots
authenticated routes it cannot serve.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.errors import problem, problem_from_error
from app.api.health import health_router
from app.api.metrics import metrics_router
from app.api.middleware.auth import ApiAuthenticator
from app.api.v1.dependencies import ApiServices, HttpAuthenticator
from app.api.v1.dto.problem import ProblemDetails
from app.api.v1.routers.admin import router as admin_router
from app.api.v1.routers.agents import router as agents_router
from app.api.v1.routers.conversations import router as conversations_router
from app.api.v1.routers.credentials import router as credentials_router
from app.api.v1.routers.files import router as files_router
from app.api.v1.routers.integrations import router as integrations_router
from app.api.v1.routers.integrations_public import router as integrations_public_router
from app.api.v1.routers.knowledge import router as knowledge_router
from app.api.v1.routers.me import router as me_router
from app.api.v1.routers.media import router as media_router
from app.api.v1.routers.models import router as models_router
from app.api.v1.routers.usage import router as usage_router
from app.api.v1.routers.workflows import router as workflows_router
from app.api.v1.routers.workspace import router as workspace_router
from app.api.v1.websocket.streaming import WsAuthenticator, create_ws_router
from app.framework.auth.revocation import SessionRevocationList
from app.framework.di.composition_root import CompositionRoot
from app.framework.di.lifecycle import Disposable, dispose_all
from app.framework.errors import ERROR_CATALOG, AppError, RateLimitedError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.ports.metrics_source import MetricsSource
from app.framework.ports.vault_health import VaultHealth
from app.framework.types import Json

_logger = get_logger(__name__)

# 03 §0: the correlation header, returned on every response and error body.
CORRELATION_HEADER = "X-Correlation-Id"
# RFC 9457's media type (03 §4). Every problem body carries it.
PROBLEM_MEDIA_TYPE = "application/problem+json"

# The `default` response every operation declares (6.2-ب). FastAPI renders a
# `model` under `application/json`, so `_retype_problem_responses` moves it to
# the media type the handlers actually send — see that function for why the
# fix belongs there and not here.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
}

# One long-running coroutine the app owns for its lifetime (the notify bridge,
# in production). A factory so it is (re)created inside the running loop, never
# a bare coroutine captured before startup.
BackgroundTask = Callable[[], Coroutine[Any, Any, None]]

# One awaited-to-completion wiring step run INSIDE the lifespan, before the
# background tasks start and before `ready` flips (6.1-هـ-1: the Composition
# Root's async `connect_storage` is the first). Distinct from `BackgroundTask`
# on purpose: a hook FINISHES before the app serves, a task runs FOR AS LONG AS
# the app serves. A hook that raises aborts startup — fail-fast, so a replica
# that could not complete its wiring never enters the load balancer.
StartupHook = Callable[[], Awaitable[None]]

# The teardown counterpart (3.79): one thunk per raw client the process owns,
# run in the lifespan's `finally`. Deliberately the SAME `Disposable` the
# worker entrypoints already close (`framework/di/lifecycle.py`) rather than an
# API-shaped variant — a server and a worker leak a connection pool the same
# way. The API's list comes from `CompositionRoot.disposables()`, the one
# object that holds every client alongside the adapter wrapping it.


def create_app(
    services: ApiServices,
    *,
    http_authenticator: HttpAuthenticator,
    ws_authenticator: WsAuthenticator,
    background: Sequence[BackgroundTask] = (),
    startup: Sequence[StartupHook] = (),
    shutdown: Sequence[Disposable] = (),
    # P1-3 (docs/p1-hardening-plan.md §3 step 10). `None` by default so every
    # pre-existing `create_app(services, http_authenticator=..., ...)` call
    # site (the 6.1 router tests) keeps building exactly as before; `/metrics`
    # itself answers 503 rather than crashing when this is unset (`api/
    # metrics.py`'s own guard). `create_production_app` always wires a real
    # `SqlRedisMetricsSource`.
    metrics_source: MetricsSource | None = None,
    # ن-10 (docs/log/3.94.md) — the third `/metrics` signal. `None` by default
    # for the SAME reason as `metrics_source` above, with one extra: an
    # unwired probe must render NOTHING rather than `0`, so a test app cannot
    # look like a dead Vault credential (`api/metrics.py`'s own guard).
    # `create_production_app` always wires the real `VaultProbe`.
    vault_health: VaultHealth | None = None,
    revocations: SessionRevocationList | None = None,
) -> FastAPI:
    """Assemble the ASGI app around already-built collaborators."""
    app = FastAPI(
        title="AIZZAK Platform API",
        version="v1",
        lifespan=_make_lifespan(startup, background, shutdown),
        # The interactive docs default on; a deployment can gate them in Phase
        # 7. Nothing here depends on them.
        #
        # 6.2-ب: `default: Problem` on EVERY operation, exactly as
        # `openapi.yaml` declares it. Applied here rather than per-router so a
        # router added later cannot forget it — the error contract is the
        # app's, not each router's.
        responses=PROBLEM_RESPONSES,
    )
    app.state.services = services
    app.state.http_authenticator = http_authenticator
    app.state.metrics_source = metrics_source
    app.state.vault_health = vault_health
    app.state.revocations = revocations
    app.state.ready = False

    _install_correlation_middleware(app)
    _install_problem_handlers(app)
    _install_problem_media_type(app)

    # Health + metrics at the ROOT (unversioned, unauthenticated); everything
    # else under the configured prefix so the wire path is `/api/v1/...` (03
    # §1). `/metrics` is additionally blocked at the nginx edge (P1-3's own
    # mzalaq #2 -- see `api/metrics.py`'s module docstring) -- reachable here
    # only from inside the trust boundary a real scraper sits in.
    app.include_router(health_router)
    app.include_router(metrics_router)
    prefix = services.settings.api_prefix
    app.include_router(agents_router, prefix=prefix)
    app.include_router(models_router, prefix=prefix)
    app.include_router(admin_router, prefix=prefix)
    app.include_router(conversations_router, prefix=prefix)
    app.include_router(workflows_router, prefix=prefix)
    app.include_router(files_router, prefix=prefix)
    app.include_router(media_router, prefix=prefix)
    app.include_router(me_router, prefix=prefix)
    app.include_router(workspace_router, prefix=prefix)
    app.include_router(usage_router, prefix=prefix)
    app.include_router(credentials_router, prefix=prefix)
    app.include_router(knowledge_router, prefix=prefix)
    # The PUBLIC callback first, then the authenticated routes (6.1-و-4-2).
    # Order is belt and braces rather than necessity — `/connections/oauth/
    # callback` matches no authenticated path, since 03 defines no
    # `GET /connections/{id}` — but a literal path registered before any
    # parameterised sibling can never be shadowed by a later one.
    app.include_router(integrations_public_router, prefix=prefix)
    app.include_router(integrations_router, prefix=prefix)
    app.include_router(
        create_ws_router(
            authenticator=ws_authenticator,
            orchestrator=services.orchestrator,
            hub=services.hub,
            limits=services.settings.limits,
            authorization=services.authorization,
        ),
        prefix=prefix,
    )

    return app


def _make_lifespan(
    startup: Sequence[StartupHook],
    background: Sequence[BackgroundTask],
    shutdown: Sequence[Disposable],
) -> Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]:
    """A lifespan that awaits the startup hooks, starts the background tasks,
    marks the app ready, and on exit marks it not-ready, cancels those tasks and
    disposes every resource the process owns.

    The ORDER is the contract (6.1-هـ-1): hooks complete before any background
    task starts and before ``ready`` flips, so a task or a routed request never
    observes half-finished wiring; a hook that raises aborts startup entirely.
    ``ready`` gates ``/health/ready`` (503 until this runs, 503 again the moment
    shutdown begins) so a rolling deploy never routes to a half-started or
    draining replica.

    Teardown is the mirror image and its order is equally load-bearing (3.79):
    ``ready`` flips false, then the background tasks are cancelled AND reaped,
    and only then are the resources disposed — closing the notify bridge's
    Redis client out from under a task still awaiting on it would turn a
    graceful drain into a connection error on the way out. ``dispose_all``
    isolates each failure, so one client refusing to close never strands the
    ones behind it.

    The disposal sits in the SAME ``finally`` as the task cancellation, which
    puts it on the aborted-boot path too: a startup hook that raises (Vault
    down at ``connect_storage``) still gives back the engine/Redis/HTTP pools
    that ``from_env`` had already opened, instead of leaking them out of a
    process that is about to exit non-zero.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        try:
            for hook in startup:
                await hook()
            tasks = [asyncio.create_task(factory()) for factory in background]
            for task in tasks:
                task.add_done_callback(_log_background_task_death)
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(BaseException):
                    await task
            await dispose_all(shutdown)

    return lifespan


def _log_background_task_death(task: asyncio.Task[None]) -> None:
    """Done-callback registered on every lifespan ``background=`` task
    (stream-topology-plan.md §3, item 5) — VISIBILITY only, never
    management. This logs one line and does nothing else: no restart, no
    backoff, no ``ready`` flip, no process exit — those stay out of scope
    here by design (the plan's §6-د, acceptance criterion #10).

    ``asyncio.CancelledError`` is deliberately EXCLUDED: ``_make_lifespan``'s
    own ``finally`` cancels every one of these tasks on a normal shutdown, so
    a cancellation is the expected, intentional outcome, not a death worth a
    line. A line that fires on every ordinary stop is a line every operator
    learns to ignore — which would destroy the very visibility this callback
    exists to buy (§1-أ-2's silent failure is the one thing this closes).

    Any OTHER exception means the task's own unbounded loop
    (``consumers/engine.py``'s ``run()``, a bare ``while True``) exited
    without warning — today that has exactly one cause (§1-أ-2's timeout
    mismatch), but this callback logs whatever it is, not only that one.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    _logger.error(
        "api.background_task_died",
        extra={"task_name": task.get_name()},
        exc_info=exc,
    )


# --------------------------------------------------------------------------- #
# OpenAPI: the problem response's media type                                  #
# --------------------------------------------------------------------------- #
def _install_problem_media_type(app: FastAPI) -> None:
    """Re-file every ``default`` response under ``application/problem+json``.

    FastAPI puts a ``responses`` entry's ``model`` under ``application/json``
    and offers no per-response way to say otherwise, so the generated schema
    would document a media type the handlers never send — the one thing a
    generated client would get wrong about errors. Post-processing the
    finished document is the smallest correct fix: the schema itself, the
    ``$ref``, and the components entry all stay FastAPI's.

    No cache of its own: ``FastAPI.openapi`` already memoises into
    ``app.openapi_schema`` and hands back that same object, so the re-filing
    happens in place and exactly once. A second cache layer here would look
    load-bearing while being unreachable — which is precisely how a mutation
    survives. What the rewrite DOES need is to be idempotent, since it now
    runs on every call, and it is: the second pass finds nothing left under
    ``application/json`` and does nothing.
    """
    build = app.openapi

    def problem_typed_openapi() -> dict[str, Any]:
        schema = build()
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                default = operation.get("responses", {}).get("default")
                if default is None:
                    continue
                content = default.get("content", {})
                if "application/json" in content:
                    content[PROBLEM_MEDIA_TYPE] = content.pop("application/json")
        return schema

    app.openapi = problem_typed_openapi  # type: ignore[method-assign]


# --------------------------------------------------------------------------- #
# Correlation id                                                              #
# --------------------------------------------------------------------------- #
def _install_correlation_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or new_uuid7()
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def _correlation_of(request: Request) -> str:
    """The request's correlation id, generating a fallback if (impossibly) the
    middleware did not run — an exception handler must never itself crash on a
    missing attribute."""
    existing = getattr(request.state, "correlation_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return new_uuid7()


# --------------------------------------------------------------------------- #
# RFC 9457 problem handlers (minimal set; 6.2 owns the full model)            #
# --------------------------------------------------------------------------- #
def _problem_response(
    body: Json, status: int, correlation_id: str, *, retry_after_s: int | None = None
) -> JSONResponse:
    headers = {CORRELATION_HEADER: correlation_id}
    if retry_after_s is not None:
        # RFC 9110 §10.2.3's delay-seconds form (03 §4 names the header). Sent
        # ONLY when a real producer supplied a real number — see
        # `RateLimitedError`, which drops anything non-positive rather than
        # clamp it into a value nobody computed.
        headers["Retry-After"] = str(retry_after_s)
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


# Starlette's own router raises `HTTPException` for a path it cannot route;
# NOTHING in `src/app` raises one (6.2-ب verified this by scan), so these two
# statuses are the complete set the handler can legitimately see. An
# unmapped status therefore means a framework path we do not model — it
# degrades to `common.internal` and gets logged, rather than being dressed up
# in a code invented on the spot.
_HTTP_EXCEPTION_CODES = {
    404: "common.not_found",
    405: "common.method_not_allowed",
}


def _install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: Exception) -> Response:
        # Without this handler an unknown path answered Starlette's default
        # `{"detail": "Not Found"}` as `application/json` — a plain contract
        # break (DD-05: "كل الأخطاء"), and one no router test could catch
        # because no router is involved. The FIRST response most clients ever
        # see from a typo'd URL was the one response that was not a problem.
        assert isinstance(exc, StarletteHTTPException)
        correlation_id = _correlation_of(request)
        code = _HTTP_EXCEPTION_CODES.get(exc.status_code)
        if code is None:
            _logger.warning(
                "api.unmapped_http_exception",
                extra={
                    "path": request.url.path,
                    "status": exc.status_code,
                    "correlation_id": correlation_id,
                },
            )
            code = "common.internal"
        status = ERROR_CATALOG[code].status
        body = problem(
            code,
            status,
            detail=exc.detail if isinstance(exc.detail, str) else None,
            correlation_id=correlation_id,
            instance=request.url.path,
        )
        return _problem_response(body, status, correlation_id)

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: Exception) -> Response:
        # The pre-flight path: the error's own stable code/status (03 §4)
        # carry straight through the `api.errors` builder.
        assert isinstance(exc, AppError)
        correlation_id = _correlation_of(request)
        body = problem_from_error(exc, correlation_id=correlation_id, instance=request.url.path)
        # 3.79 — the 429's `Retry-After`. Selected by TYPE, not by status: a
        # 429 is not automatically retryable at a knowable time, and only the
        # error that carries a computed reset (`RateLimitedError`, filled from
        # `LimitDecision.retry_after_s`) can say when. Every other `AppError`
        # answers exactly as before.
        retry_after_s = exc.retry_after_s if isinstance(exc, RateLimitedError) else None
        return _problem_response(body, exc.status, correlation_id, retry_after_s=retry_after_s)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: Exception) -> Response:
        # DTO validation failed before the router body ran: 422 with the
        # per-field detail 03 §4's `errors[]` carries.
        assert isinstance(exc, RequestValidationError)
        correlation_id = _correlation_of(request)
        body = problem(
            "common.validation_error",
            422,
            detail="request validation failed",
            correlation_id=correlation_id,
            instance=request.url.path,
            errors=_validation_errors(exc),
        )
        return _problem_response(body, 422, correlation_id)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> Response:
        # 03 §4: `common.internal` hides internal detail; the specifics go to
        # the log only. Never leak `exc` into the body.
        correlation_id = _correlation_of(request)
        _logger.error(
            "api.unhandled_exception",
            extra={"path": request.url.path, "correlation_id": correlation_id},
            exc_info=exc,
        )
        body = problem(
            "common.internal",
            500,
            correlation_id=correlation_id,
            instance=request.url.path,
        )
        return _problem_response(body, 500, correlation_id)


def _validation_errors(exc: RequestValidationError) -> list[Json]:
    """FastAPI's per-error records → 03 §4's ``{field, message}`` shape.

    The ``loc`` tuple's leading ``body``/``query``/``path`` marker is dropped
    so ``field`` names the offending attribute the client sent, not FastAPI's
    internal request-part label.
    """
    out: list[Json] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body", "query", "path")]
        out.append({"field": ".".join(loc) or "body", "message": str(err.get("msg", ""))})
    return out


# --------------------------------------------------------------------------- #
# Production wiring                                                            #
# --------------------------------------------------------------------------- #
def create_production_app() -> FastAPI:
    """Build the real ASGI app from the Composition Root — uvicorn's ``--factory``
    target (08 §2's topology; Phase 7 owns the process manager).

    The notify bridge runs as a lifespan background task: its hub is the SAME
    in-process hub the WebSocket endpoint registers sessions on (5.3-د), so it
    must live in this process, not a separate worker.
    """
    root = CompositionRoot.from_env()
    session_revocations = SessionRevocationList(root.cache)
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
        presence=root.presence,
        usage=root.usage,
        credentials=root.credentials,
        knowledge=root.knowledge,
        integrations=root.integrations,
        admin=root.admin,
        session_revocations=session_revocations,
        authorization=root.authorization,
        idempotency=root.idempotency,
        # Narrowed to `ModelCatalog` at the boundary (see `ApiServices.models`):
        # the root holds the wide resolver, the API layer never does.
        models=root.model_catalog,
        # BE-ADM-007 — host telemetry for the platform-admin System Monitor.
        system_stats=root.system_stats,
        # BE-ADM-010/011/012 — the Service Providers tab.
        providers=root.providers,
    )

    async def _run_notify_bridge() -> None:
        await root.notify_consumer.run(root.notify_subscriptions)

    # 6.4-أ — ONE verifier, handed to BOTH seams. Not two instances: the 2.7
    # adapter's JWKS cache, refresh budget and lock all live on the instance, so
    # a second one would mean a socket verifying against a key set the HTTP path
    # had already retired, and twice the traffic to Google to keep them apart.
    # The two `create_app` parameters survive because they are 6.1-a's signature
    # and collapsing them would be churn with no behaviour behind it.
    # 3.79 adds the fifth collaborator: the `auth:revoked:<sub>` denylist, over
    # the SAME process-wide cache everything else uses — so an entry written by
    # `python -m app.ops.revoke` is seen by every replica sharing that Redis.
    authenticator = ApiAuthenticator(
        root.auth,
        root.provisioning,
        root.seeding,
        root.authorization,
        session_revocations,
    )

    return create_app(
        services,
        http_authenticator=authenticator,
        ws_authenticator=authenticator,
        # P1-3 (step 10) -- the real adapter the Composition Root built.
        metrics_source=root.metrics_source,
        # ن-10 -- the Vault liveness probe, over the SAME `SecretsProvider`
        # (and therefore the same client, token and relogin closure) every
        # request path already uses. Unwired here would mean a gauge that
        # never appears and an alert that can never fire, which is exactly
        # the silent-failure shape ن-10 exists to end.
        vault_health=root.vault_health,
        revocations=authenticator.revocations,
        # ت-2 — the timed cross-host sweep alongside the bridge it cleans up
        # after. A `background=` task rather than a `startup=` hook because it
        # never finishes, and one that the lifespan cancels and reaps like the
        # bridge's own, so it cannot outlive the `redis_client` it reads
        # (`sweep_orphan_notify_groups_forever`'s own docstring for the rule
        # it applies and why the startup sweep above cannot cover it).
        background=(_run_notify_bridge, root.sweep_orphan_notify_groups_forever),
        # 6.1-هـ-1 — the async half of MinIO's wiring (debt (ز)): the Vault
        # read `from_env` could not await runs here, before traffic. A failure
        # aborts boot (fail-fast), never a half-wired replica.
        # §3.81 — `sweep_stale_notify_groups` runs FIRST: every `startup=`
        # hook completes before `_run_notify_bridge` (a `background=` task)
        # ever starts, so this process's OWN `cg.notify.<host>.<pid>` group
        # cannot exist yet when the sweep inspects the host — see that
        # method's docstring for why a concurrently-booting sibling can never
        # be swept.
        # P1-8 (step 11) — `hub.start_renewal` keeps THIS process's registry
        # entries young so a live long-lived socket is never aged out of the
        # cross-process cap. It belongs in `startup=` rather than inside the
        # hub itself for the same reason the notify bridge does: a background
        # task with no owner is a task nobody cancels, and this one must be
        # reaped before `disposables()` closes the Redis client under it.
        startup=(root.sweep_stale_notify_groups, root.hub.start_renewal, root.connect_storage),
        # 3.79 — the teardown counterpart. The root is the ONLY object that can
        # write this list (see `CompositionRoot.disposables`), and this is the
        # only place it is read: a `create_app` built by a test owns no raw
        # client and so passes nothing, exactly as before.
        # §3.81 — `teardown_notify_bridge` runs FIRST, before ANY client in
        # `disposables()` closes: it must destroy this process's OWN notify
        # group over the SAME `redis_client` `disposables()` later
        # `.aclose()`\\ s, and the lifespan has already cancelled + reaped the
        # bridge's background task by the time any shutdown thunk runs
        # (`_make_lifespan`'s own ordering), so nothing is still reading the
        # group when it goes.
        # P1-8 — `hub.stop_renewal` sits alongside it, and BEFORE
        # `disposables()` for the identical reason: it cancels AND reaps a
        # loop whose in-flight call is on that same `redis_client`.
        shutdown=(root.teardown_notify_bridge, root.hub.stop_renewal, *root.disposables()),
    )
