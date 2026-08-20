"""Files inbound port (02-port-contracts §2).

``FilesQuery`` is injected into ``knowledge`` and the agent layer so they can
read a file's metadata without importing the files module directly (ARC-07/08).
``get_readable`` returns a view only for a fully-uploaded, ``ready`` file
(INV-F2) — never a half-uploaded, quarantined, or deleted one.

``names_for_files`` is the same authority answering for MANY files at once
(branch review §2): a consumer that walks a corpus holds a page of file ids
and wants the one field it can display, and one read is what keeps that walk
from being a round trip per document. Same readability rule, same tenant
scope — only the shape of the question differs.

The view carries the file's ``space_id`` since the spaces plan's step 7: a
consumer that must keep its own rows inside one space (``conversations``'
§3.5 pin rule) can only do that if what it reads back says which space this
file is in. Readability and ownership are different questions, and this port
answers both because both are the files module's own facts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class FileView:
    """A read-only projection of a ``ready`` file, safe to hand to other modules."""

    file_id: str
    # The owning space (spaces plan §3.5, step 7). Projected because a
    # consumer has to be able to refuse a file from ANOTHER space --
    # ``PinConversationFile`` is the one that must, and it cannot ask
    # ``spaces`` on this module's behalf. `| None` mirrors the column until
    # plan row 8-b.
    space_id: str | None
    name: str
    content_type: str
    size_bytes: int
    storage_key: str
    status: str


class FilesQuery(Protocol):
    """Injected into ``knowledge``/agents so they never import the files module;
    returns a view only when the file is ready.

    Two methods, one question asked at two SIZES. ``get_readable`` answers
    about one file and answers FULLY, because its callers are deciding
    something about that file (may this conversation pin it? are there bytes
    to index?). ``names_for_files`` answers about many and answers with the
    one field a LISTING needs — see its docstring for why the singular could
    not simply be called in a loop.
    """

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FileView | None: ...

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        """The display name of every READY file among ``file_ids``, in ONE
        read (branch review §2, remedy 1).

        **Why this exists at all.** ``knowledge`` walks its whole corpus twice
        on an answering turn — once to build the corpus-awareness header, once
        to offer ``resolve_file`` the candidate names — and both walks hold
        file ids and want names. Calling ``get_readable`` per document made
        that ``D + 50`` SEQUENTIAL round trips for a ``D``-document repository,
        paid before retrieval had begun. The names are this module's own fact,
        so the plural read belongs on this seam rather than on a denormalised
        copy of the name in the consumer's rows.

        **Presence means exactly what a non-``None`` ``FileView`` means**:
        ready, not deleted, not quarantined, not still uploading (INV-F2/F3).
        A file that fails any of those is ABSENT from the mapping — never
        present with an empty string, so a caller can still tell "no readable
        file" from "a readable file whose name is empty" and keep the two
        different behaviours it already had for them.

        **Only the name.** A caller that needs a file's space or size is
        deciding about ONE file and has ``get_readable`` for it; projecting
        the whole ``FileView`` per id would hand a listing four fields it must
        not act on, and make this the cheaper way to bulk-read the module's
        rows. ``Mapping``, not ``Sequence``, because every caller looks a name
        up by the id it already holds.

        ``ctx``-scoped and workspace-filtered like every other read here: ids
        from another tenant are simply absent, which is the same answer
        ``get_readable`` gives them.
        """
        ...
