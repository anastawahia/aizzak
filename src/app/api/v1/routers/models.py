"""The Models router — ``/api/v1/models`` (03-api-spec §1 · D-16 · FR-73).

One route, a thin delegate (FR-100): ``GET /models`` publishes the configured
LLM routing table so a client can offer a real choice instead of guessing at
model names.

**What is guarded, and why by ``agents:read``.** The permission catalog is
CLOSED (05 §1.2), so this route takes an existing one rather than minting
``models:read``. ``agents:read`` is the right one on the merits, not merely the
nearest: it already answers "what can this workspace run", it is held by MEMBER
and VIEWER — the roles that would ever face a model picker — and a caller who
may read the agent catalog learns nothing new from the routes those agents
resolve against.

**No key ever reaches this module.** The bundle field is typed
``ModelCatalog``, not ``ProviderResolver``, so ``resolve_llm`` — the call whose
``ResolvedProvider`` carries a decrypted ``api_key`` (INV-C2) — is not merely
unused here but unreachable. See ``framework/providers/catalog.py``.

**Unpaginated.** The routing table is small and bounded by configuration, so
the ``API-04`` envelope carries ``next_cursor: null`` and a ``limit`` equal to
the returned count — the ``listAgents`` precedent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.models import ModelOut
from app.api.v1.dto.pagination import Page, PageMeta
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(current_principal)])


@router.get("", dependencies=[Depends(require(Permission.AGENTS_READ))])
async def list_models(services: Services, ctx: Context) -> Page[ModelOut]:
    """The configured LLM routes, with per-caller availability (03 §1).

    ``ctx`` is not decoration: availability is the user→platform credential
    lookup, so the same route can be usable for one member and not another,
    and answering it without the caller's context would report somebody else's
    access.

    An unwired catalogue is an internal error, not an empty page. Empty means
    "the operator configured no routes" — a real, actionable state — and a
    hermetic application that forgot to wire this must not be able to
    impersonate it.
    """
    if services.models is None:
        raise AppError("the model catalogue is not configured", code="common.internal")
    choices = await services.models.list_llm_models(ctx)
    data = [
        ModelOut(
            capability=choice.capability,
            provider=choice.provider,
            model=choice.model,
            available=choice.available,
        )
        for choice in choices
    ]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))
