"""Request-scoped wiring for the ``/api/v1`` routers (Phase 6.1-a).

Two jobs, kept apart from the routers themselves so a router stays a thin
delegate (FR-100: "no business logic in the API layer"):

* **``ApiServices``** — the PROCESS-WIDE collaborators a router delegates to,
  built once by the Composition Root and read off ``app.state`` per request.
  It is deliberately a small, explicit bundle rather than the whole
  ``CompositionRoot``: the API layer may not import ``app.infrastructure``
  (import-linter contract 6), and naming exactly what the routers touch keeps
  ``create_app`` constructible in a test with a handful of fakes instead of a
  22-field container. It grows ADDITIVELY, one field per 6.1 sub-step — the
  ``OrchestratorDependencies`` precedent — starting here with only what the
  shell (6.1-a) needs.

* **The authentication SEAM.** ``HttpAuthenticator`` maps a bearer token to a
  ``Principal``; ``current_context`` turns that principal plus the request's
  correlation id into the ``ExecutionContext`` every delegate call needs. The
  real implementation landed in **6.4-أ** — ``api/middleware/auth.py``'s
  ``ApiAuthenticator``: local Firebase JWT verification (the 2.7 adapter) + JIT
  provisioning + fresh role resolution. It is still injected through this seam
  rather than imported, which is what lets every hermetic router test run
  without a Firebase project: a fake authenticator returns a fixed principal.

The catalog codes raised here (``auth.missing_token``) are 03 §4's, not
``UnauthorizedError``'s generic default — the API layer speaks the wire
catalog even before 6.2 formalised the error model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents.orchestrator import AgentOrchestrator
from app.framework.agent_runtime.registry import AgentRegistry
from app.framework.auth.revocation import SessionRevocationList
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_deletion import DeleteFileService
from app.framework.di.file_replacement import ReplaceNamesakesService
from app.framework.di.space_deletion import DeleteSpaceService
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.errors import UnauthorizedError
from app.framework.ports.idempotency_store import IdempotencyStore
from app.framework.ports.system_stats import SystemStatsSource
from app.framework.providers.catalog import ModelCatalog
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.types import Uuid
from app.framework.workflows.registry import WorkflowRegistry
from app.modules.access.ports.inbound import AuthorizationService
from app.modules.admin.application.providers import PlatformProviderUseCases
from app.modules.admin.application.users import PlatformAdminUseCases
from app.modules.conversations.application.use_cases import ConversationUseCases
from app.modules.credentials.application.use_cases import CredentialUseCases
from app.modules.files.application.use_cases import FileUseCases, RegisteredUpload
from app.modules.files.domain.entities import File
from app.modules.integrations.application.use_cases import IntegrationsUseCases
from app.modules.knowledge.application.use_cases import KnowledgeUseCases
from app.modules.media.application.use_cases import MediaUseCases
from app.modules.spaces.application.use_cases import SpaceUseCases
from app.modules.usage.application.use_cases import UsageUseCases
from app.modules.workspace.application.use_cases import RecordUserPresence, WorkspaceUseCases

# 03 §0: "Authorization: Bearer <Firebase ID Token>" — declared as an OpenAPI
# security scheme (6.4-أ), not read off the raw header, so the generated
# document carries `components.securitySchemes.firebaseBearer` and marks every
# authenticated operation with it, exactly as the binding `openapi.yaml` does.
# This is the one alpha pattern `refs/auth-firebase.md` §9 rules out by name:
# alpha bound a plain `Header(default=None)`, so its published document had NO
# security scheme at all and showed the header as optional — a contract lie
# (AC-07) that costs nothing to avoid and cannot be spotted by reading a router.
#
# `auto_error=False` keeps the ERROR ours: FastAPI's own auto-error raises a
# bare `HTTPException(403)` with a `{"detail": ...}` body, which would be both
# the wrong status (401, 03 §4) and the wrong media type (RFC 9457). The class
# is used for what it is good at — extraction and documentation — and for
# nothing else. The scheme MATCHES case-insensitively (RFC 7235) and returns
# `None` for any other scheme, which is precisely the old prefix test.
_bearer_scheme = HTTPBearer(
    scheme_name="firebaseBearer",
    bearerFormat="JWT",
    description="Firebase ID token, verified locally against cached JWKS (D-25).",
    auto_error=False,
)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a request speaks for, once authenticated — the resolved identity an
    ``ExecutionContext`` is built from.

    **One type for both transports since 6.4-أ.** 5.3-ج and 6.1-a each declared
    their own carrier of the same three facts (workspace, user, roles), split
    only so 6.1-a would not touch already-shipped WS code; ``WsPrincipal`` is
    now an alias of this class. That is what lets ONE ``ApiAuthenticator``
    satisfy both seams — and it has to be one object, not merely one class,
    because the 2.7 adapter's JWKS cache lives on the instance.
    """

    workspace_id: Uuid
    user_id: Uuid
    roles: frozenset[str] = frozenset()
    # These two facts are intentionally available only after token validation.
    # `/me/logout` needs the Firebase subject to revoke the actual credential,
    # and its expiry to bound the denylist entry to the token's remaining life.
    firebase_uid: str = ""
    token_expires_at: int | None = None


class HttpAuthenticator(Protocol):
    """Bearer token → ``Principal``, or raise an ``AppError`` (any subclass is
    treated as a refusal ⇒ the RFC 9457 handler maps it to 401/403).

    Implemented by ``api.middleware.auth.ApiAuthenticator`` (6.4-أ): Firebase
    JWT verification + JIT provisioning + fresh role resolution. Tests wire a
    fake that returns a fixed principal, and the WebSocket endpoint's
    ``WsAuthenticator`` is this same protocol under its 5.3-ج name.
    """

    async def authenticate(self, token: str) -> Principal: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    """The process-wide collaborators the ``/api/v1`` routers delegate to.

    Built once by the Composition Root and stored on ``app.state`` by
    ``create_app``. Grows one field per 6.1 sub-step (agents/conversations →
    workflows → files/media → the rest); 6.1-a needed only the three the shell
    and the mounted WebSocket endpoint touch. **6.1-b adds ``agents``** — the
    registry the Agents router reads for ``GET /agents`` and ``GET
    /agents/{key}`` (the orchestrator only RUNS one agent; listing the catalog
    is the registry's own job, reached without going through it).
    **6.1-ج-2 adds ``conversations``** — the module's use-cases, which the
    router calls directly (`10 §3`); the orchestrator's own conversation writes
    go through its narrow inbound port instead, so the two never share a seam.
    **6.1-د-2 adds ``workflows``** — the static D-09 catalog, for the same
    reason ``agents`` is here: the orchestrator RUNS a workflow, it does not
    enumerate the catalog, and reading a run's step count is a catalog
    question too. **6.1-هـ-3 adds ``files`` and ``media``** — each module's own
    bundle (§3.60), one field per module (the ``conversations`` precedent);
    the files bundle's presigned faces hold the root's ``StorageHandle``, so
    they are live from the startup lifespan onward (§3.59). **6.1-و-1 adds
    ``workspace`` and ``usage``** — each the module's own API-facing bundle,
    holding ONLY the client-reachable use-cases: no provisioning under
    ``workspace`` (that is the auth path's), and no enforcement/capture under
    ``usage`` (those are the orchestrator's internal ports, INV-U4). What a
    bundle omits is as load-bearing as what it carries — the API layer cannot
    reach a face this dataclass does not name. **6.1-و-2 adds ``credentials``**
    on exactly that principle: the bundle holds list/add/revoke and NOT
    ``ResolveCredential``, the only face that decrypts (INV-C2), so a stored
    secret is unreachable from this layer by construction rather than by a
    router remembering not to ask. **6.1-و-3 adds ``knowledge``** — the two
    document reads plus the retrieval INBOUND port, and neither ingestion
    face: a request cannot mint a document or re-drive a worker's pipeline.
    Its ``search`` is typed optional and is ``None`` in production today, so
    the missing embedding adapter is a fact this bundle STATES rather than an
    absence a client would meet as a 404. **6.1-و-4-1 adds ``integrations``**
    — the connector catalog and the connection lifecycle, and *not*
    ``invoke_tool``: the face that spends a decrypted OAuth token belongs to
    the agent runtime's inbound port, and leaving it out of this dataclass is
    what makes it unreachable from any request shape. **6.1-و-4-2** adds no
    field: the public OAuth callback reads the SAME ``integrations`` bundle,
    which now carries ``complete``. Note what that means for the rule above —
    membership here governs what the API layer *can* reach, and the
    authenticated/unauthenticated split is the ROUTER's, not this bundle's.
    ``complete`` is reachable only from ``routers.integrations_public``, and
    it is safe there because it takes no ``ExecutionContext`` from anyone: it
    derives one from a server-side state binding.
    """

    settings: Settings
    orchestrator: AgentOrchestrator
    hub: ConnectionHub
    agents: AgentRegistry
    conversations: ConversationUseCases
    workflows: WorkflowRegistry
    files: FileUseCases
    media: MediaUseCases
    workspace: WorkspaceUseCases
    usage: UsageUseCases
    credentials: CredentialUseCases
    knowledge: KnowledgeUseCases
    integrations: IntegrationsUseCases
    # 6.4-ب — the ONE face of `access` the API layer holds, and it is the read
    # half: `is_allowed` decides, `roles_of` reads. Neither can grant a role —
    # `AssignRole` is not on this protocol at all, so no route, guarded or not,
    # can widen its own caller's permissions. The guards call `is_allowed` and
    # nothing else, since the roles were already resolved by the authentication
    # path onto `ExecutionContext.roles`.
    authorization: AuthorizationService
    # 3.79 — the `Idempotency-Key` ledger (`api/v1/idempotency.py`). NOT a
    # module bundle: it is platform infrastructure serving three endpoints
    # owned by three different modules, so it sits here for the same reason
    # `platform.idempotency_keys` sits outside every module schema. The API
    # layer is its only caller by construction — nothing below this layer knows
    # a request had a header at all.
    idempotency: IdempotencyStore
    # `spaces-backend-plan.md` step 12 — the two faces `/api/v1/spaces` needs,
    # and they are two rather than one because they come from opposite sides
    # of the boundary. `spaces` is the MODULE's own bundle (create/rename/list
    # — the `FileUseCases` precedent); `space_deletion` is the cross-module
    # cascade (§3.6), which belongs to no module at all and lives at the
    # Composition Root. Folding the cascade into `SpaceUseCases` would have
    # put a service that touches `knowledge`, Qdrant, `files`, MinIO and
    # `conversations` inside a module forbidden to know any of them exist.
    #
    # Both optional and both fail closed, the `admin`/`models` precedent:
    # plan step 12 is this layer, plan step 13 is the wiring. `DeleteSpace`
    # (the mark, step 1 of the cascade) is reached THROUGH `space_deletion`
    # and is deliberately not reachable on its own — a route that could mark a
    # space deleted without running the other six steps would be a route that
    # hides a workspace's data instead of erasing it.
    spaces: SpaceUseCases | None = None
    space_deletion: DeleteSpaceService | None = None
    # The file cascade (`framework/di/file_deletion.py`) — `space_deletion`'s
    # sibling, and here for the identical reason: `DELETE /files/{id}` has to
    # empty the file's corpus as well as mark its row, which spans `files`,
    # `knowledge` and Qdrant — three things no single module bundle may hold.
    # `FileUseCases.delete` is still the mark, and is still reachable on the
    # bundle, because the bundle is what the cascade itself calls; the ROUTE
    # goes through this field so no request can mark a file deleted while
    # leaving its points answering searches, which is the whole defect being
    # repaired.
    #
    # Optional and fails closed, the `space_deletion` precedent: hermetic test
    # applications that predate the cascade keep composing, and a route that
    # reaches an unwired one is a 500 rather than a silent half-deletion.
    file_deletion: DeleteFileService | None = None
    # س-29 rule 1 (`framework/di/file_replacement.py`) — `file_deletion`'s
    # sibling on the WRITE side, and here for the same three-way reason:
    # completing an upload (or renaming a file) under a name its space already
    # holds destroys the older file's corpus as well as its row.
    #
    # `FileUseCases.complete`/`.rename` stay reachable on the bundle because
    # this service is what calls them; both ROUTES go through this field, so
    # no request can leave a space holding two files with one name, both
    # indexed, answering the same question twice.
    #
    # Optional and FAILS CLOSED — the `space_quota` rule, not the
    # `file_deletion` one, because the difference matters here: these two
    # routes worked before the cascade existed, so a fallback to the bundle
    # would be available and would silently skip the replacement. That is the
    # silence this check exists to break.
    file_replacement: ReplaceNamesakesService[File] | None = None
    # The third space-shaped field, and the only one an EXISTING route needs:
    # `POST /files` registers through it instead of through
    # `files.transfers.register`, so the 1 GiB ceiling (§3.3) is consulted
    # under the space's row lock. Generic in its result, and the parameter is
    # `RegisteredUpload` — the concrete type the Composition Root's binding
    # infers — so this bundle keeps the exact shape the router returns.
    space_quota: SpaceQuotaService[RegisteredUpload] | None = None
    # Optional only for small hermetic applications that predate the platform
    # directory. Production wiring always supplies it; the route fails closed
    # if a test application accidentally reaches the admin surface unwired.
    admin: PlatformAdminUseCases | None = None
    # A browser heartbeat records only the authenticated caller under the
    # caller's tenant RLS context. Optional preserves older hermetic apps;
    # production always wires it and the route fails closed when absent.
    presence: RecordUserPresence | None = None
    # The account-status route must revoke an existing Firebase subject after
    # its state transition commits. Optional preserves small hermetic apps;
    # production always supplies the shared, process-wide list.
    session_revocations: SessionRevocationList | None = None
    # The D-16 routing table, read-only (02 §3.5.1). Typed as the NARROW
    # catalogue port and never as `ProviderResolver`: the resolver's
    # `ResolvedProvider` carries a decrypted `api_key`, so holding the wide
    # port here would undo — for provider keys — exactly what leaving
    # `ResolveCredential` off the `credentials` bundle achieves for stored
    # ones. Optional preserves the hermetic applications that predate the
    # route; production always wires it and `GET /models` fails closed.
    models: ModelCatalog | None = None
    # BE-ADM-007 — the System Monitor tab's host telemetry. On this bundle
    # rather than beside `metrics_source` on `app.state` (where `/metrics`
    # keeps its own source) because this one is reached through the versioned,
    # authenticated router and its permission guard, not from an unversioned
    # endpoint the edge blocks outright. Optional preserves the hermetic
    # applications that predate the route; production always wires it and the
    # route fails closed when absent.
    system_stats: SystemStatsSource | None = None
    # BE-ADM-010/011/012 — the Service Providers tab. A SEPARATE bundle from
    # `admin` rather than four more fields on it: that one is built over the
    # user directory's sessions and answers questions about people, while this
    # one joins the boot-time routing table to the platform's credentials, and
    # a test application can wire either without the other. Optional for the
    # same reason as its neighbours, and the routes fail closed when absent.
    providers: PlatformProviderUseCases | None = None


def get_services(request: Request) -> ApiServices:
    """The bundle ``create_app`` stashed on ``app.state`` — the one bridge from
    a stateless router function to the process-wide wiring."""
    services: ApiServices = request.app.state.services
    return services


def get_authenticator(request: Request) -> HttpAuthenticator:
    """The token verifier ``create_app`` stashed on ``app.state`` (6.4's real
    one in production, a fake in tests)."""
    authenticator: HttpAuthenticator = request.app.state.http_authenticator
    return authenticator


def _bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    """The bearer credential the scheme extracted, or a 401.

    ``auth.missing_token`` (03 §4) for an absent, empty or non-bearer header —
    the distinction from ``auth.invalid_token`` (a present-but-bad token, the
    6.4 verifier's) is the catalog's, so it is honoured from the first step.

    ONE condition, deliberately: ``HTTPBearer`` already answers ``None`` for a
    header that is absent, carries another scheme, or carries no credential at
    all (``not (authorization and scheme and credentials)``), and HTTP itself
    strips the surrounding whitespace that could make a credential look present
    but blank. A second emptiness test here would be a guard nothing can reach
    — the §3.69/§3.70 finding, twice paid for: its mutant SURVIVED this suite,
    which is the only reliable way to learn that a line is decoration.

    The credential is passed on VERBATIM for the same reason it is opaque
    everywhere else: normalising it here would mean the verifier checks a
    signature over a different string than the client sent.
    """
    if credentials is None:
        raise UnauthorizedError("missing bearer token", code="auth.missing_token")
    return credentials.credentials


async def current_principal(
    authenticator: Annotated[HttpAuthenticator, Depends(get_authenticator)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)] = None,
) -> Principal:
    """Authenticate the request — the dependency every protected route hangs the
    ``Depends`` on (directly or through ``current_context``)."""
    return await authenticator.authenticate(_bearer_token(credentials))


def current_context(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ExecutionContext:
    """The ``ExecutionContext`` a delegate call carries: tenant identity from
    the principal, correlation id from the middleware.

    ``workspace_id`` comes from the PRINCIPAL, never the client (03 §0: "لا
    يُمرَّر من العميل") — it is what drives RLS (DD-04), so letting a request
    name its own tenant would be a tenant-isolation hole. The correlation id
    was stamped on ``request.state`` by ``correlation_middleware`` before any
    route ran, so it is always present here.
    """
    correlation_id: Uuid = request.state.correlation_id
    return ExecutionContext(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        correlation_id=correlation_id,
        roles=principal.roles,
        request_id=None,
    )


# The `Annotated` aliases routers import, so a handler signature reads
# `ctx: Context` / `services: Services` rather than repeating the `Depends`.
Services = Annotated[ApiServices, Depends(get_services)]
Context = Annotated[ExecutionContext, Depends(current_context)]
