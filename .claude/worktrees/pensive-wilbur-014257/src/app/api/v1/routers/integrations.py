"""The Integrations router — ``/api/v1/integrations`` (03-api-spec §1 ·
06 §9) — Phase 6.1-و-4-1 (connectors + connections), completed by و-4-3
(MCP servers + the tool catalog).

Nine routes over ``IntegrationsUseCases``:

* ``GET  /integrations/connectors`` — the deployment's connector catalog
  (API-04 envelope, ``next_cursor: null``);
* ``GET  /integrations/connections`` — this workspace's connections, every
  status;
* ``POST /integrations/connections`` — begin a handshake (201, the pending
  row);
* ``POST /integrations/connections/{id}/authorize`` — the URL to redirect
  the user to;
* ``DELETE /integrations/connections/{id}`` — revoke it (204, idempotent);
* ``GET  /integrations/mcp-servers`` — the registered servers, every status;
* ``POST /integrations/mcp-servers`` — register one (201);
* ``DELETE /integrations/mcp-servers/{id}`` — disable it (204, idempotent);
* ``GET  /integrations/tools`` — the workspace's discovered tool catalog.

The *public* OAuth callback is the one route not here: it lives in
``routers.integrations_public`` because it is unauthenticated, and every
route in this file is authenticated (و-4-2).

**``GET /tools`` names tools; it cannot run one.** The bundle field it reads
is typed ``ToolDiscovery``, a Protocol with ``list_tools`` and nothing else,
so the object underneath (``WorkspaceToolCatalog``, which does carry
``invoke_tool``) is narrowed at the type before this layer ever sees it.
Invocation stays the agent runtime's through the ``ToolCatalog`` inbound
port (INV-I4) — see ``IntegrationsUseCases``' docstring.

**``GET /tools`` is built, wired, and 503 on this deployment** — the
``POST /knowledge/search`` shape (§3.64) reached for a second time and for
the same kind of reason: ``WorkspaceToolCatalog`` needs a ``ConnectorToolset``
and an ``MCPClient``, and both adapters are still empty files. Unlike the
empty *connector catalog* — where "this deployment offers no connectors" is a
state the contract models and an empty list is the truth — an empty tool list
here would be a lie: a workspace with a registered, reachable MCP server does
have tools, and this deployment simply cannot go and look. So 503
``integrations.tools_unavailable`` rather than ``200 {"data": []}``.

**``DELETE /mcp-servers/{id}`` disables, it does not erase** — the same
promise ``DELETE /connections/{id}`` makes. It is reversible without a
re-enable route 03 does not define, because re-registering the name revives
the row (``McpServer.disable``); the alternative was a DELETE that silently
burned the name forever.

**Nothing here returns token material.** The DTOs name their fields
explicitly (see ``dto/integrations``) and the bundle this router reads holds
no decrypting face at all — ``invoke_tool``, the one place a decrypted access
token is used, is the agent runtime's through ``ToolCatalog`` and is not
reachable from any request shape (``IntegrationsUseCases``' docstring). A
client can create, authorize and destroy a connection; it can never read one
back, nor spend one.

**An empty catalog is the honest answer while no connector adapter exists.**
``infrastructure/integrations/oauth_connector.py`` is an empty file (a Phase-2
scheduling gap, like the embedding adapter §3.64 met), so the Composition Root
passes an empty catalog and an empty provider map. ``GET /connectors`` then
returns an empty collection and every ``POST /connections`` is refused with
``integrations.connector_unknown``/422 — which is *true*, not a stand-in:
the key genuinely is not in this deployment's catalog, and the catalog route
says so first. No 503 seam is needed here, unlike ``POST /knowledge/search``,
because "no connectors configured" is a state the contract already models.

**DELETE revokes, it does not erase** (the credentials precedent, §3.63):
``RevokeConnection`` flips status and drops ``token_ref``, so the row stays
and ``GET`` can still explain why a connector went quiet. 204 either way.

**Internal events, dropped here on purpose.** ``ConnectionRevoked`` and its
siblings carry no promotion asterisk in 04 §5 — integrations events never
cross a stream in v1 — so there is no outbox row to write and no unit of work
to wrap this in.

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0). The owner/admin guard these deserve — a member should not be
connecting a workspace's Google account, nor pointing it at an MCP server of
their choosing — is 6.4's, like every other router's.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.integrations import (
    AuthorizeOut,
    ConnectionCreateIn,
    ConnectionOut,
    ConnectorOut,
    McpServerCreateIn,
    McpServerOut,
    ToolOut,
)
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission
from app.modules.integrations.application.use_cases import ConnectorDescriptor
from app.modules.integrations.domain.entities import Connection, McpServer

# Folded into `ERROR_CATALOG` by 6.2 (with `knowledge.search_unavailable`,
# minted for the same reason in §3.64), which is where the 503 now comes from
# — this raise passes a code and no status.
_TOOLS_UNAVAILABLE = "integrations.tools_unavailable"

# Belt and braces, knowingly (the §3.56 precedent): every route builds `ctx`.
router = APIRouter(
    prefix="/integrations", tags=["integrations"], dependencies=[Depends(current_principal)]
)


def _to_connector_out(descriptor: ConnectorDescriptor) -> ConnectorOut:
    return ConnectorOut(
        key=descriptor.key,
        name=descriptor.name,
        scopes=list(descriptor.scopes),
        auth_type=descriptor.auth_type,
    )


def to_connection_out(connection: Connection) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id,
        connector_key=connection.connector_key.value,
        display_name=connection.display_name,
        status=connection.status.value,
        scopes=list(connection.scopes),
        expires_at=connection.expires_at,
        created_at=connection.created_at,
    )


@router.get("/connectors", dependencies=[Depends(require(Permission.INTEGRATIONS_READ))])
async def list_connectors(services: Services, ctx: Context) -> Page[ConnectorOut]:
    """The connectors this deployment offers — configuration, not state.

    Bounded and unpaginated: a hand-built catalog of adapters, not a growing
    collection. Wrapped anyway (API-04 admits no exception).
    """
    connectors = await services.integrations.list_connectors.execute(ctx)
    data = [_to_connector_out(descriptor) for descriptor in connectors]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.get("/connections", dependencies=[Depends(require(Permission.INTEGRATIONS_READ))])
async def list_connections(
    services: Services, ctx: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> Page[ConnectionOut]:
    """The workspace's connections, newest first, all four statuses —
    cursor-paginated (6.3-ب), as ``openapi.yaml`` always declared.

    A ``pending`` row (a handshake the user never finished) and an ``error``
    row (an exchange that failed) are both visible on purpose — see
    ``ConnectionRepository.list`` — and that is exactly what makes the
    collection unbounded: ``Limits.max_connectors`` counts CONNECTED rows
    only, so handshake debris accumulates under no cap at all.
    """
    page = await services.integrations.list_connections.execute(ctx, limit=limit, cursor=cursor)
    return Page(
        data=[to_connection_out(connection) for connection in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.post(
    "/connections", status_code=201, dependencies=[Depends(require(Permission.INTEGRATIONS_MANAGE))]
)
async def create_connection(
    body: ConnectionCreateIn, services: Services, ctx: Context
) -> ConnectionOut:
    """Begin a connector's OAuth handshake (201 + the bare pending row).

    Nothing is connected yet: the row is ``pending`` until the user completes
    the provider's consent screen and the callback exchanges the code. The
    URL to send them to is ``POST /connections/{id}/authorize``'s to return
    — 03's OpenAPI makes this response the bare resource, so the two steps
    stay separable (a client may create now and redirect later).

    An unknown connector surfaces as ``integrations.connector_unknown``/422,
    a workspace already at ``Limits.max_connectors`` as
    ``integrations.too_many``/409.
    """
    connection = await services.integrations.start.execute(
        ctx, connector_key=body.connector_key, scopes=body.scopes
    )
    return to_connection_out(connection)


@router.post(
    "/connections/{connection_id}/authorize",
    dependencies=[Depends(require(Permission.INTEGRATIONS_MANAGE))],
)
async def authorize_connection(
    connection_id: str, services: Services, ctx: Context
) -> AuthorizeOut:
    """The provider authorize URL for an existing connection, plus its
    one-time ``state``.

    No body: the connector and scopes are the row's, not this call's.
    Unknown or another tenant's id ⇒ 404 (the §3.55 read precedent).
    """
    initiation = await services.integrations.authorize.execute(ctx, connection_id=connection_id)
    return AuthorizeOut(authorize_url=initiation.authorize_url, state=initiation.state)


@router.delete(
    "/connections/{connection_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.INTEGRATIONS_MANAGE))],
)
async def revoke_connection(connection_id: str, services: Services, ctx: Context) -> None:
    """Revoke a connection and drop its stored token (204, no body).

    Unknown or another tenant's ⇒ 404; already revoked ⇒ 204 again, since
    ``RevokeConnection`` is idempotent by contract and emits no second event.
    """
    await services.integrations.revoke.execute(ctx, connection_id=connection_id)


def _to_mcp_server_out(server: McpServer) -> McpServerOut:
    return McpServerOut(
        id=server.id,
        name=server.name.value,
        endpoint_url=server.endpoint.url,
        transport=server.endpoint.transport,
        status=server.status.value,
        created_at=server.created_at,
    )


@router.get("/mcp-servers", dependencies=[Depends(require(Permission.INTEGRATIONS_READ))])
async def list_mcp_servers(services: Services, ctx: Context) -> Page[McpServerOut]:
    """The workspace's registered MCP servers, newest first, every status.

    Bounded by ``Limits.max_mcp_servers``; a ``disabled`` row stays visible
    because deleting a server is what creates one, and its name stays
    reserved until it is re-registered (``McpServerRepository.list``).
    """
    servers = await services.integrations.list_mcp_servers.execute(ctx)
    data = [_to_mcp_server_out(server) for server in servers]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.post(
    "/mcp-servers", status_code=201, dependencies=[Depends(require(Permission.INTEGRATIONS_MANAGE))]
)
async def register_mcp_server(
    body: McpServerCreateIn, services: Services, ctx: Context
) -> McpServerOut:
    """Register a remote MCP server (201 + the row).

    ``auth`` goes in and never comes back: it is encrypted through Transit
    into an ``auth_ref`` the response has no field for (INV-I1).

    A non-remote transport ⇒ ``integrations.mcp_transport_unsupported``/422
    (INV-I2 — ``stdio`` is the reserved ``sandbox`` module's, not v1's); a
    name already taken by an *active* server ⇒ 409, while one held by a
    disabled/errored row revives that row and answers 201 with its original
    id; a name colliding with a connector key ⇒ 422, since both become the
    ``<prefix>`` of a tool handle; a workspace at ``Limits.max_mcp_servers``
    ⇒ ``integrations.too_many``/409.
    """
    server, _events = await services.integrations.register_mcp_server.execute(
        ctx,
        name=body.name,
        endpoint_url=body.endpoint_url,
        transport=body.transport,
        auth=body.auth,
    )
    return _to_mcp_server_out(server)


@router.delete(
    "/mcp-servers/{server_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.INTEGRATIONS_MANAGE))],
)
async def disable_mcp_server(server_id: str, services: Services, ctx: Context) -> None:
    """Disable a registered MCP server (204, no body).

    Its tools leave the catalog and it stops being routable, but the row
    survives — ``GET`` still lists it, and re-registering the name brings it
    back. Unknown or another tenant's ⇒ 404; already disabled ⇒ 204 again.
    """
    await services.integrations.disable_mcp_server.execute(ctx, server_id=server_id)


@router.get("/tools", dependencies=[Depends(require(Permission.INTEGRATIONS_READ))])
async def list_tools(services: Services, ctx: Context) -> Page[ToolOut]:
    """Every tool this workspace can reach — connector tools plus tools
    discovered live on its active MCP servers.

    Wrapped with ``next_cursor: null`` and bounded by
    ``Limits.max_discovered_tools``: the catalog is assembled per request
    from live discovery, so there is no stable ordering for a cursor to
    resume from — a paginated view of it would be paginating a different
    list on every page.

    Discovery is best-effort by design: an unreachable MCP server is skipped
    rather than failing the whole catalog, so this route can answer 200 with
    a partial list. 503 while no connector/MCP adapter exists at all (module
    docstring) — the difference between "one source is quiet" and "this
    deployment cannot look".
    """
    discovery = services.integrations.tools
    if discovery is None:
        raise AppError(
            "tool discovery is not available on this deployment", code=_TOOLS_UNAVAILABLE
        )
    tools = await discovery.list_tools(ctx)
    data = [
        ToolOut(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            source=tool.source,
        )
        for tool in tools
    ]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))
