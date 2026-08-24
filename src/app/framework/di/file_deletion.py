"""The cascade that empties a deleted file — the ``space_deletion.py`` shape at
the scale of one file.

One rule: **deleting a file destroys its index**, and it survives being
interrupted.

**The gap this closes.** ``files`` raises ``FileDeleted`` (``files/domain/
events.py``) and 04-event-catalog §5 gives it no promotion asterisk, so
``to_outbox_record`` maps it to ``None`` and it never crosses the wire. Nothing
in ``knowledge`` was listening on any other channel either. The result was a
file that vanished from every listing while its ``Document`` stayed ``indexed``,
its ``chunks`` rows stayed joinable and its Qdrant points stayed searchable —
retrieval answering out of a file the user removed a month ago, and the agent
citing it by name. It is also how "the file is indexed twice" was born: delete,
re-upload, and the first corpus outlives the file it was built from, so one
document's worth of content answers every search from two places at once.

**Why a synchronous cascade and not an event.** The alternative was to promote
``FileDeleted`` to ``stream.files`` and give the knowledge worker a consumer,
and it was refused for three reasons. (1) That subscription was deliberately
removed — ``workers/bootstrap.py`` says the knowledge worker no longer listens
on ``stream.files`` at all, and 04 §4 records ``files.file.uploaded.v1`` as
having no consumer since indexing became manual; re-opening the stream to carry
a delete would reinstate the coupling that decision spent a phase removing.
(2) An asynchronous purge leaves a window — however short, and unbounded when
the worker is down or the event lands in the DLQ — in which a deleted file is
still citable, which is the exact defect being repaired. (3) ``spaces`` already
answers this question for the same three stores, in this directory, with no
event at all: a space deletion runs ``knowledge`` then ``files`` then
``conversations`` inline. Deleting one file is that cascade with one step.

**Why this file is here and not in a module.** The deletion crosses ``files``
and ``knowledge``, and no module may import another (import-linter contract 4).
``space_deletion.py`` states the rule and this file keeps it the same way: it
imports NO module at all — the collaborators are structural ``Protocol``\\ s
declared right here and bound by the Composition Root, where mypy checks the
fit at the call. The framework-kernel contract (7) needs no new exception.

**There is no unit of work around the cascade, deliberately.** Qdrant sits
inside it, and network I/O may not run under an open database transaction (R2).
Each step is its own transaction, which is why both are idempotent: re-deleting
a file emits no second event, a point already deleted is a no-op, and a
``DELETE`` matching no rows succeeds. An interrupted cascade leaves a deleted
file with part of its corpus still standing, and the fix is to run it again —
``DELETE /files/{id}`` is that re-run.

**The marking comes first, and it is the only step that can refuse.** Same
order as the space cascade, for the same two reasons. It is the step that reads
the file, so an unknown id is a ``NotFoundError`` here rather than a silent
"purged nothing" reported as a successful deletion — ``PurgeFileKnowledge``
cannot tell a file with no documents from a file that never existed. And it is
what makes the purge's window the harmless one: between the two steps the file
is already invisible to every listing and every download, so the corpus that
briefly outlives it is unreachable through anything but retrieval, and the
retry closes that. Ordering the purge first would invert the risk into the one
that cannot be undone — a live, still-listed file whose index was destroyed
under it.

Re-deleting an already-deleted file is NOT refused: that is the resume path,
and ``SoftDeleteFile`` is idempotent by design.

**Nothing is published.** ``FileDeleted`` keeps its 04 §5 place as an
internal-only event: this service is its consumer, reached by a call rather
than by a subscription. ``SoftDeleteFileService`` still owns the outbox append
that would carry it the day 04 §5 promotes it, so nothing here has to move for
that to happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class FileMarker(Protocol):
    """``SoftDeleteFileService.delete`` — soft-delete the file row (step 1).

    The result is deliberately ``object``: it is the ``File`` aggregate, an
    ``app.modules`` type this file may not name and does not need — what
    matters here is the RAISE, so that an id naming no file never reaches
    step 2, and the WRITE, which is what makes the file invisible before its
    corpus goes.

    ``delete`` and not ``execute``: this binds to the atomic SERVICE (the one
    that pairs the use-case with its outbox append inside one unit of work),
    never to the bare use-case underneath it. The name difference is what
    keeps that binding honest at the wiring site.
    """

    async def delete(self, ctx: ExecutionContext, file_id: Uuid) -> object: ...


class FileContentPurge(Protocol):
    """One module's "destroy everything you own that was built from this
    file", returning how many of its own top-level rows went.

    ``SpaceContentPurge`` at the file scope, and declared here rather than
    reused from ``space_deletion`` for the reason that one is declared at all:
    the shape is the contract, and the two scopes take different ids. Today
    ``knowledge`` is the only implementation — it is the only module that
    derives content from a file's BYTES. ``conversations`` is not one and must
    not become one: a pin is a reference to a file, and a deleted file already
    resolves to nothing through ``FilesQuery``.
    """

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int: ...


@dataclass(frozen=True, slots=True)
class FileDeletion:
    """What one cascade destroyed — the ``SpaceDeletion`` shape, at the scale
    of one file.

    A count and not a boolean, for that record's reason: a cascade whose whole
    job is to leave nothing behind looks identical from the outside whether it
    purged a file's four documents or none of them. ``documents`` is
    ``knowledge``'s own top-level row count — normally ``0`` (a file that was
    never indexed) or ``1``, and more than one when a re-index left a
    replacement beside its source.
    """

    file_id: Uuid
    documents: int


class DeleteFileService:
    """Soft-delete a file, then empty its corpus — in that order.

    Two steps rather than the space cascade's seven, and the asymmetry is
    correct rather than an omission:

    * ``files`` itself keeps the bytes. This is a SOFT delete — the row gets a
      ``deleted_at`` and the MinIO object stays, which is what makes the
      operation recoverable at the storage layer. The corpus cannot follow that
      model (``DocumentRepository.purge_file`` says why: the vector store has
      no view of a ``deleted_at`` column), so the index goes hard while the
      bytes do not, and an undelete would re-index rather than un-hide.
    * ``conversations`` is untouched: a pin names a file, and a pin to a
      deleted file already resolves to nothing.
    """

    def __init__(self, mark: FileMarker, *, knowledge: FileContentPurge) -> None:
        self._mark = mark
        self._knowledge = knowledge

    async def delete(self, ctx: ExecutionContext, file_id: Uuid) -> FileDeletion:
        # Step 1. Raises `NotFoundError` for an id this workspace does not
        # own -- and that is the whole existence check for step 2, which
        # cannot tell an unindexed file from an absent one.
        await self._mark.delete(ctx, file_id)
        documents = await self._knowledge.execute(ctx, file_id)
        return FileDeletion(file_id=file_id, documents=documents)
