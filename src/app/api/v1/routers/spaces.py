"""The Spaces router — ``/api/v1/spaces`` (``docs/spaces-backend-plan.md``
§3.7 · step 12).

Four routes, each a thin delegate (`10 §3`):

* ``GET /spaces`` — the workspace's active spaces, paginated, each with what
  it holds;
* ``POST /spaces`` — mint one (201);
* ``PATCH /spaces/{id}`` — rename it;
* ``DELETE /spaces/{id}`` — delete it AND everything in it (204).

**This router is the reason the rest of the step exists.** ``?space_id=`` is
now mandatory on ``GET /files``, ``GET /conversations`` and ``GET
/knowledge/documents``, and ``space_id`` is required in the bodies that create
a file or a thread — so without a route that hands a client a space id, every
one of those becomes unanswerable. The listing here is the entry point of the
whole axis.

**The listing composes three modules, and only this layer may.** ``ListSpaces``
returns names; ``bytes_used``/``file_count`` are sums over ``files.files`` and
``conversation_count`` is a count over ``conversations.conversations``. No
module may read another's tables (import-linter contract 4), so each answers
its own numbers (``SummariseSpaces`` / ``CountConversationsBySpace``) and this
router — the one place that legitimately holds all three bundles — puts them
side by side. Three queries per page, not three per row: both faces take the
whole page's ids at once.

**``DELETE`` carries no ``Idempotency-Key`` ledger, and that is a decision
against §3.7's table rather than an omission from it.** The cascade is
idempotent *by construction* (§3.6): every one of its seven steps is a no-op
when repeated, precisely so that a sequence which died half-way is repaired by
running it again. Putting the ledger in front of it would invert that — a
retry with the same key would replay a stored answer and SKIP the re-run,
leaving the space marked deleted and its files, points and objects behind
forever. The promise 03 §0 makes for the header ("إعادة المحاولة آمنة") is
kept here by the route itself, which is the stronger form of it.

**Both cross-module services are optional on ``ApiServices`` and the routes
fail closed** — the ``admin``/``models`` precedent. Plan step 12 is this
layer; step 13 is the Composition Root that binds them. Until then the routes
exist, are typed, and answer ``common.internal`` rather than pretending a
space store they do not have.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import ApiServices, Context, Services, current_principal
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.dto.spaces import SpaceCreateIn, SpaceOut, SpaceRenameIn
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.types import Uuid
from app.modules.access.domain.value_objects import Permission
from app.modules.files.ports.repository import SpaceFileTotals
from app.modules.spaces.application.use_cases import SpaceUseCases
from app.modules.spaces.domain.entities import Space

# The router-level bearer gate, belt and braces exactly as the files and
# conversations routers document it: every handler below also takes `ctx`,
# whose chain authenticates anyway, and the line stays so the guarantee is
# structural for the first route that would not.
router = APIRouter(prefix="/spaces", tags=["spaces"], dependencies=[Depends(current_principal)])

_UNWIRED = "space management is not configured on this deployment"

# A space that owns nothing. Returned for a space that was minted one
# statement ago (`POST`) and for one whose id matched no group in either
# `GROUP BY` — the two cases are the same fact, and neither is worth a query.
_EMPTY = SpaceFileTotals(bytes_used=0, file_count=0)


def _use_cases(services: ApiServices) -> SpaceUseCases:
    """The spaces bundle, or a fail-closed 500 (plan step 13 binds it)."""
    spaces = services.spaces
    if spaces is None:
        raise AppError(_UNWIRED, code="common.internal")
    return spaces


def _to_space_out(space: Space, totals: SpaceFileTotals, conversation_count: int) -> SpaceOut:
    return SpaceOut(
        id=space.id,
        name=space.name.value,
        bytes_used=totals.bytes_used,
        file_count=totals.file_count,
        conversation_count=conversation_count,
        created_at=space.created_at,
    )


async def _contents(
    services: ApiServices, ctx: ExecutionContext, space_ids: list[Uuid]
) -> tuple[Mapping[Uuid, SpaceFileTotals], Mapping[Uuid, int]]:
    """What each of ``space_ids`` holds — one query per module, whatever the
    page size. Both faces answer ``{}`` for an empty list without a round
    trip, so an empty page costs nothing."""
    totals = await services.files.space_totals.execute(ctx, space_ids)
    counts = await services.conversations.space_counts.execute(ctx, space_ids)
    return totals, counts


@router.get("", dependencies=[Depends(require(Permission.SPACES_READ))])
async def list_spaces(
    services: Services, ctx: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> Page[SpaceOut]:
    """The workspace's active spaces (API-04 envelope), newest first, each
    carrying what it holds.

    Soft-deleted spaces are absent — the repository's own rule — so a client
    that deleted one sees it gone from the next page whether or not the
    cascade behind it has finished. That ordering is deliberate (§3.6 step 1)
    and this listing is where a user observes it.
    """
    page = await _use_cases(services).list.execute(ctx, limit=limit, cursor=cursor)
    space_ids = [space.id for space in page.data]
    totals, counts = await _contents(services, ctx, space_ids)
    return Page(
        data=[
            _to_space_out(space, totals.get(space.id, _EMPTY), counts.get(space.id, 0))
            for space in page.data
        ],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.post("", status_code=201, dependencies=[Depends(require(Permission.SPACES_WRITE))])
async def create_space(body: SpaceCreateIn, services: Services, ctx: Context) -> SpaceOut:
    """Mint a space (201 + the bare resource).

    A name already taken in this workspace is ``spaces.duplicate_name``/409,
    raised by the unique index in the same statement that would have written
    the row — never by a read-then-insert, which would answer the same
    question one round trip earlier and the wrong answer under a race
    (``CreateSpace``).

    The counters come back as zeros WITHOUT the two queries the listing runs:
    a space that was inserted one statement ago owns no file and no thread,
    and asking the database to confirm that would be asking it to restate the
    INSERT that just returned.
    """
    space = await _use_cases(services).create.execute(ctx, name=body.name)
    return _to_space_out(space, _EMPTY, 0)


@router.patch("/{space_id}", dependencies=[Depends(require(Permission.SPACES_WRITE))])
async def rename_space(
    space_id: str, body: SpaceRenameIn, services: Services, ctx: Context
) -> SpaceOut:
    """Rename a space and return it renamed.

    Unknown ⇒ 404, soft-deleted ⇒ 409 (``RenameSpace``: reads say "gone",
    writes say "deleted" — the asymmetry the conversations router documents).
    Renaming to the name it already has succeeds and writes nothing.

    The counters ARE composed here, unlike on ``POST``: a space being renamed
    is a space that has been around, and answering ``0`` for it would be a
    lie the client would then render.
    """
    space = await _use_cases(services).rename.execute(ctx, space_id, name=body.name)
    totals, counts = await _contents(services, ctx, [space.id])
    return _to_space_out(space, totals.get(space.id, _EMPTY), counts.get(space.id, 0))


@router.delete(
    "/{space_id}", status_code=204, dependencies=[Depends(require(Permission.SPACES_WRITE))]
)
async def delete_space(space_id: str, services: Services, ctx: Context) -> None:
    """Delete a space AND everything it owns — 204, no body.

    This is the most destructive route in the API: seven steps across
    ``spaces``, ``knowledge``, Qdrant, ``files``, MinIO and ``conversations``
    (§3.6), and decision 2 gives it no undo. That is exactly why
    ``spaces:write`` is not a member's permission (``RoleCatalog`` says so at
    length): the guard on this route is, in effect, ``files:delete`` and
    ``conversations:delete`` at once, and neither is granted a member one row
    at a time.

    A space this workspace does not own is a 404 from step 1 — which is also
    the ONLY existence check in the sequence, since none of the six steps
    after it can tell an empty space from an absent one.

    Re-deleting is another 204: the mark is idempotent and each purge deletes
    nothing the second time. A client retrying a lost response gets the same
    answer, and — more usefully — a cascade that died mid-way is repaired by
    the retry rather than replayed from a ledger (module docstring).
    """
    deletion = services.space_deletion
    if deletion is None:
        raise AppError(_UNWIRED, code="common.internal")
    await deletion.delete(ctx, space_id)
