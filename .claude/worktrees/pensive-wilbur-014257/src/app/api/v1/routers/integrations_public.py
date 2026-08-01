"""The PUBLIC integrations route — the OAuth callback (03-api-spec §1 ·
06 §9 ``CompleteOAuth``) — Phase 6.1-و-4-2.

One route, in its own module for one reason: **it is unauthenticated.** The
و-4-1 router carries a router-level ``Depends(current_principal)`` and every
one of its handlers takes a ``ctx``; this one can do neither, because the
caller is a third-party provider's redirect landing in a user's browser with
no ``Authorization`` header to offer. Putting it on that router with a
per-route opt-out would make "authenticated" a property you have to read five
decorators to confirm. Two routers, one rule each.

**Tenant identity comes from the ``state`` binding and from nothing else.**
``BeginConnection`` wrote ``{workspace_id, connection_id, connector_key,
created_by, redirect_uri}`` into the cache under an unguessable 256-bit
single-use key with a 600 s TTL; ``CompleteOAuth`` reads that record, deletes
it, and builds its own ``ExecutionContext`` from it. Nothing in the query
string names a workspace, a connection, or a user — which is the alpha fix
(``state == user_id``, refs §5-3): there, a forged callback naming any user
completed a handshake on their behalf. Here the only two inputs are ``code``
and ``state``, and an unknown ``state`` is refused **before any database is
touched**.

**The 401-shaped hole this does not open.** An unauthenticated route that
answers with a ``ConnectionOut`` looks like a leak until you ask who can
reach it: only a caller presenting a live, unguessable, single-use state —
i.e. the browser that this workspace's own member just sent to the provider.
A guessed state is a 422 and a replayed one is a 422, because the first use
deleted it. The row it returns is the one that state *is about*, so the
response tells its reader nothing they did not just cause.

**Errors, as the catalog now has them (6.2).** A provider exchange that fails
is ``integrations.oauth_failed``/502 (and marks the row ``error`` on the way
out, so ``GET /connections`` can explain the silence); a connector missing
from this deployment's map is ``integrations.connector_unknown``/422. An
unknown/expired/replayed state is ``integrations.oauth_state_invalid``/422 —
the code §3.66 asked for and could not invent. The two-code split it also
flagged is gone: ``ValidationError`` now carries ``common.validation_error``,
so an *empty* ``state`` (stopped by FastAPI) and a *wrong* one (stopped by
the use-case) no longer answer under two names for the same 422 — and the
wrong one is now more specific than either, which is the point of the entry.

**No event is published.** ``CompleteOAuth`` returns ``ConnectionEstablished``
and this route drops it, exactly as و-4-1 drops ``ConnectionRevoked``:
integrations events carry no promotion asterisk in 04 §5, so there is no
outbox row to write.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.dependencies import Services
from app.api.v1.dto.integrations import ConnectionOut
from app.api.v1.routers.integrations import to_connection_out

# No `dependencies=[Depends(current_principal)]` — the whole point of the
# separate module. The prefix matches the authenticated router's so the wire
# path is one namespace; `/connections/oauth/callback` cannot collide with
# `/connections/{id}` because no GET-by-id route exists (and 03 defines none).
router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/connections/oauth/callback")
async def oauth_callback(
    services: Services,
    code: Annotated[str, Query(min_length=1)],
    state: Annotated[str, Query(min_length=1)],
) -> ConnectionOut:
    """Exchange the provider's authorization ``code`` and mark the connection
    connected (200 + the bare resource).

    Both parameters are required by 03's OpenAPI, and both are declared
    non-empty here: an empty ``state`` could never match a cache key anyway,
    but refusing it as a *shape* error keeps the lookup path free of inputs
    that are not even candidates.

    The tokens the exchange returns never reach this layer: ``CompleteOAuth``
    encrypts them through Transit and stores a ``CipherRef`` (INV-I1), and
    ``ConnectionOut`` has no field that could carry one.
    """
    connection, _events = await services.integrations.complete.execute(state=state, code=code)
    return to_connection_out(connection)
