"""Spaces DTOs — the wire shapes of ``/api/v1/spaces``
(``docs/spaces-backend-plan.md`` §3.7, step 12).

A space is the ownership axis inside a workspace (§1.1): every file and every
conversation belongs to exactly one, and a space's contents are what its
conversations can see. On the wire it is almost nothing — a name and three
numbers — because the aggregate itself is almost nothing (``domain/entities.py``
says why): everything that makes a space interesting is owned by other modules
and reached by filtering on its id.

**One shape for all four routes, including the three counters.** §3.7's table
marks ``bytes_used``/``file_count``/``conversation_count`` on the LISTING,
where they matter, and the temptation was to give ``POST``/``PATCH`` a
narrower body. That would hand the client two models of one resource and a
rule about which routes return which — for the sake of two queries on a
rename. The counters are therefore always present; ``POST`` fills them with
zeros without asking anyone, which is not an assumption but the definition of
a row that was inserted one statement ago.

``bytes_used`` is measured against ``Limits.max_space_bytes`` (§3.3, 1 GiB) and
counts ACTIVE files only — a soft-deleted file has already given its bytes
back to the quota, so a listing that still counted them would contradict the
limit it exists to describe.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SpaceCreateIn(BaseModel):
    """``POST /spaces`` — a space has exactly one thing to say about itself.

    No ``id`` and no quota field: the identifier is the server's (UUIDv7) and
    the 1 GiB limit is the operator's setting, not a per-space value a client
    may propose. The name's rules (1..120 characters, no control characters)
    are the domain's ``SpaceName``, so an invalid one is a 422 with the
    domain's own reason rather than a Pydantic constraint duplicated here —
    the ``FileRenameIn`` precedent.
    """

    name: str


class SpaceRenameIn(BaseModel):
    """``PATCH /spaces/{id}`` — the one mutable field a space has.

    Required, not optional, for the reason ``FileRenameIn`` states: a PATCH
    body that may legally be empty asks the server to guess what "no change"
    means. Renaming to the name it already has is still accepted and is a
    no-op all the way down (``Space.rename``).
    """

    name: str


class SpaceOut(BaseModel):
    """One space as the API exposes it.

    ``version`` and ``deleted_at`` are deliberately absent — the ``SpaceView``
    argument, at the outer boundary this time: optimistic-lock state is the
    repository's business, and a deleted space is not returned by any route
    here at all, so a field for it could only ever say ``null``.
    """

    id: str
    name: str
    bytes_used: int
    file_count: int
    conversation_count: int
    created_at: datetime
