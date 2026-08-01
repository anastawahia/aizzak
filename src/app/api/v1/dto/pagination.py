"""The collection envelope every ``/api/v1`` list response wears (03-api-spec
§0/§2 · API-04) — Phase 6.1-b.

03 §0's ``API-04`` is absolute: **every** endpoint that returns a collection is
wrapped in ``{data: […], meta: {next_cursor, limit}}`` — even the unpaginated,
bounded ones (``listAgents`` here, ``listWorkflows``/``listCredentials``/… to
come), which simply carry ``next_cursor: null``. A single resource is returned
bare. This module is that envelope, one place, so no router reinvents the shape.

**Two ``Page`` types on purpose.** ``app.framework.pagination.Page`` is the
INTERNAL carrier a repository returns — a flat dataclass ``(data, next_cursor,
limit)`` keyed on the keyset cursor. THIS ``Page`` is the WIRE model — a
Pydantic v2 envelope with the nested ``meta`` shape 03 §2 draws verbatim. A
list router's job is to translate the former into the latter (domain → wire),
exactly as it maps each row's aggregate into its ``*Out`` DTO.

**The REQUEST half lives here too** (6.3-أ). ``?limit=&cursor=`` is the other
side of the same contract sentence in 03 §0, and it was being re-declared in
each paginated router — two copies when 6.3 started, about to be three. One
declaration means the ceiling can only be the contract's, in one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

# 03 §0: "?limit=<=100&cursor=<opaque>", and `openapi.yaml`'s `Limit`
# parameter carries `default: 20`. BOTH numbers are the contract's — 6.1-ج
# invented a 50 for the first paginated route without checking, and every
# later route copied it, so a client generated from the contract paged in
# 20s while the server answered 50. `AC-07` is conformance to that document,
# and the number a client is TOLD it will get is the one that has to be true.
DEFAULT_LIMIT = 20

Limit = Annotated[int, Query(ge=1, le=100)]
# Deliberately UNVALIDATED at the boundary beyond "some text": the cursor is
# opaque, and only the collection that minted it knows which keyset it
# encodes. Its total decoder (`framework.pagination`) rejects a malformed one
# with `common.invalid_cursor`, so a mangled cursor is a 422 either way — but
# the check belongs with the codec, not with a route that cannot perform it.
Cursor = Annotated[str | None, Query()]


class PageMeta(BaseModel):
    """The cursor/limit descriptor beside a page's ``data`` (03 §2).

    ``next_cursor`` is ``null`` at the last page AND for a bounded, unpaginated
    collection — the client stops paging on ``null`` either way, so the two
    cases need no distinction on the wire.
    """

    next_cursor: str | None
    limit: int


class Page[T](BaseModel):
    """One page of ``T`` plus its ``meta`` — the ``API-04`` collection wrapper."""

    data: list[T]
    meta: PageMeta
