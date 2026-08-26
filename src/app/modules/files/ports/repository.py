"""Files persistence port (02-port-contracts §2).

Outbound repository contract for the ``File`` aggregate. Every method takes
``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter (DD-04).
``save`` uses an optimistic lock on ``version`` — a stale write surfaces as a
conflict at the adapter. ``count`` backs the per-workspace file cap
(07-nfr-slo §4) and counts only active (non-deleted) files.

``add`` persists the aggregate's ``space_id`` and ``save`` deliberately does
not: a file does not move between spaces (spaces plan, decision 3), and
leaving the column out of the UPDATE is what makes that true of the database
and not only of the type — the ``content_type``/``storage_key`` argument.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.files.domain.entities import File


@dataclass(frozen=True, slots=True)
class SpaceFileTotals:
    """What one space's ACTIVE files add up to — the two numbers ``GET
    /api/v1/spaces`` publishes beside a space's name (§3.7, step 12).

    A read model, not an aggregate: it is computed by the database in a
    ``GROUP BY`` and never stored, so nothing can go stale and there is no
    counter for a concurrent write to corrupt. It lives on the PORT rather
    than in the domain for that reason — ``File`` has no idea how many
    siblings it has, and a space is not this module's concept at all.
    """

    bytes_used: int
    file_count: int


class FileRepository(Protocol):
    """Tenant-scoped persistence for the ``File`` aggregate."""

    async def get(self, ctx: ExecutionContext, file_id: Uuid) -> File | None: ...

    async def add(self, ctx: ExecutionContext, file: File) -> None: ...

    async def save(self, ctx: ExecutionContext, file: File) -> None: ...

    async def list(
        self, ctx: ExecutionContext, *, space_id: Uuid | None, limit: int, cursor: str | None
    ) -> Page[File]:
        """Active files, newest first, keyset-paginated on ``id``.

        ``space_id`` narrows the page to one space's files; ``None`` returns
        the workspace's. Since step 12 the router always passes one —
        ``?space_id=`` is mandatory on ``GET /files`` (§3.7) — so ``None`` is
        no longer any route's answer; it survives for callers with no space to
        name at all. It is a REQUIRED keyword with no default so that "all
        spaces" is a decision written at the call site, never one a caller
        falls into by omission.
        """
        ...

    async def count(self, ctx: ExecutionContext) -> int: ...

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """Total ``size_bytes`` of the ACTIVE files owned by one space — the
        left-hand side of the 1 GiB quota (``docs/spaces-backend-plan.md``
        §3.3, step 5). ``0`` for an unknown or empty space.

        ``space_id`` is an OPAQUE identifier here: this module does not know
        what a space is, only that its rows carry one and can be totalled by
        it. The decision to spend the room belongs to the coordination service
        that holds the space's row lock (``framework/di/space_quota.py``) —
        this port answers "how much is stored", never "may I store more".

        Soft-deleted files are excluded, so deleting a file frees its bytes
        immediately, before the purge sweep ever runs. That is the intended
        meaning of the quota (a user who deletes must get their space back)
        and it is why this cannot reuse ``count``'s shape and be an argument
        away from it.
        """
        ...

    async def totals_by_space(
        self, ctx: ExecutionContext, space_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, SpaceFileTotals]:
        """``bytes_used``/``file_count`` for MANY spaces in one read — what a
        page of ``GET /api/v1/spaces`` needs (§3.7, step 12).

        **Plural on purpose.** ``bytes_in_space`` answers for one space
        because its caller holds one space's row lock and is deciding about
        that space alone; this one serves a LISTING, and calling the singular
        once per row would turn a page of twenty spaces into forty round
        trips for two columns of decoration. One ``GROUP BY`` costs one.

        Same active-only rule as ``bytes_in_space``, and for the same reason
        stated once: a soft-deleted file has already given its bytes back, so
        a listing that still counted it would contradict the quota the same
        listing is describing.

        **A space with nothing in it is ABSENT from the mapping**, not present
        with zeros. ``GROUP BY`` returns groups that exist, and inventing rows
        for ids that matched nothing would be this adapter asserting that
        those ids name real spaces — which it cannot know, because it never
        sees ``spaces.spaces``. The caller defaults a miss to zero, which is
        the same answer with the authority in the right place.

        An empty ``space_ids`` returns an empty mapping without a query.
        """
        ...

    async def ready_names(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        """The display name of every READY file among ``file_ids``, in ONE
        read — what ``FilesQuery.names_for_files`` is implemented over.

        **Plural for ``totals_by_space``' reason, on a hotter path.** Its
        caller (``knowledge``' corpus walk) holds a page of file ids and needs
        a name for each; asking ``get`` once per id turned a 200-row page into
        200 sequential round trips before a question could even be routed. One
        ``WHERE id IN (…)`` costs one.

        **Same readability rule as ``get``+``is_ready``, pushed into SQL** —
        ``status = 'ready'`` and ``deleted_at IS NULL`` (INV-F2/F3). It has to
        be the same rule and not a wider one: the caller uses PRESENCE in the
        mapping exactly as it used a non-``None`` ``FileView``, so a name
        returned here for a quarantined or deleted file would name it to a
        user the singular read refuses to name.

        **A file that is unknown, deleted, quarantined or still uploading is
        ABSENT from the mapping**, never present with an empty string — the
        ``totals_by_space`` rule (a query returns the rows that exist), and
        the one that keeps "there is no readable file" distinguishable from
        "the file is readable and its name is empty".

        Duplicate ids collapse: a mapping is keyed by id, and two documents
        built from one file ask about it once.

        An empty ``file_ids`` returns an empty mapping without a query.
        """
        ...

    async def live_namesakes(self, ctx: ExecutionContext, file: File) -> Sequence[Uuid]:
        """The ids of the ACTIVE files ``file`` REPLACES — same space, same
        name, registered before it (س-29 rule 1, owner decision 2026-08-25;
        ``docs/rag-fidelity-audit.md`` §4-هـ-2).

        The whole aggregate is the argument rather than four loose values,
        for ``add``/``save``' reason: the question is about a file, and every
        part of the predicate — its space, its name, when it arrived and which
        row it is — is read off the same row so that no caller can pair one
        file's name with another's space.

        **"Same name" is ``lower(normalize(name, NFC))``**, and both halves
        are load-bearing. ``lower`` is ``ux_spaces_ws_name``'s rule ("Report"
        and "report" are one name to a human); ``normalize(.., NFC)`` is what
        makes it true of ARABIC, where the same filename typed on two
        keyboards differs by combining marks and by nothing else.

        It is STORED (``files.name_key``, ``GENERATED ALWAYS ... STORED``)
        rather than computed in an index expression, and that is a measured
        constraint rather than a preference: neither function is ``LEAKPROOF``,
        so under this table's ``FORCE ROW LEVEL SECURITY`` an expression index
        over them can never be a search key -- the planner is forbidden from
        evaluating them before the row-security qual and drops them into a
        filter (``migrations/versions/files/0003_file_name_lookup.py`` records
        both ``EXPLAIN`` plans). An adapter that spelled this predicate against
        ``name`` instead would answer the same question by scanning one
        space's ten thousand files, on every upload completion.

        **Same SPACE, and the scope is the model's rather than a preference**
        (س-32): spaces are isolated completely, so one space's ``report.pdf``
        does not replace another's and there is no cross-space branch of this
        rule to decide. ``space_id`` is compared NULL-safely — two spaceless
        files still replace each other, because "no space" is one bucket and
        not a wildcard.

        **OLDER only** — strictly before ``file`` in ``(created_at, id)``, and
        this asymmetry is the concurrency argument, not a detail. Two uploads
        of one name can complete at once; because each may only delete rows
        that arrived before it, the relation is a strict order and no pair can
        ever delete each other. It converges on the newest arrival without a
        lock, which matters because the completion path holds none (the space
        row lock is taken on REGISTER, ``framework/di/space_quota.py``). The
        loser's own completion then fails honestly on the deleted row rather
        than resurrecting itself.

        **Soft-deleted rows are excluded** (the ``bytes_in_space`` rule): a
        deleted file has already given up its name, and re-deleting it would
        make a second cascade's worth of work out of nothing.

        Status is NOT filtered. An older row still ``uploaded`` holds the name
        and the space's quota exactly as a ``ready`` one does, and abandoning
        an upload must not leave a name reserved forever.

        Returns an empty sequence when nothing matches — the ordinary case,
        and never an error.
        """
        ...

    async def storage_keys_in_space(self, ctx: ExecutionContext, space_id: Uuid) -> Sequence[str]:
        """Every stored object key this space's files name — step 5 of the
        cascade (``docs/spaces-backend-plan.md`` §3.6, step 11).

        **Soft-deleted files are INCLUDED**, unlike ``bytes_in_space`` right
        above, and the difference is the whole reason this is a second method
        rather than an argument on the first. A deleted file has already given
        its bytes back to the quota, but its OBJECT is still in MinIO — that
        is what "soft" means — so a purge that skipped it would leave the
        space's storage behind with no row left to name it, unreachable by
        anything but ``app.ops.purge`` and only when the whole workspace dies.

        Read BEFORE ``purge_space``, and it must be: afterwards there is no row
        left to say which objects were the space's.
        """
        ...

    async def purge_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """HARD-delete every file row of one space; returns how many went
        (step 6 of the cascade, §3.6).

        The module's only hard delete, and it is deliberate: a space that was
        deleted must stop counting against nothing, stop appearing in every
        listing, and above all stop holding rows that point at objects this
        cascade has already destroyed. A soft delete would leave exactly that —
        a ``ready`` file whose bytes are gone, which is worse than either
        state on its own.

        Soft-deleted rows go too, for the reason ``storage_keys_in_space``
        keeps them: their objects have just been deleted, so leaving the rows
        would be leaving a promise the storage can no longer keep.

        Deleting nothing is a no-op, not an error — a re-run of a cascade that
        died half-way is the case this exists to survive (§3.6).
        """
        ...
