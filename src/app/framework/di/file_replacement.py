"""The cascade that makes a duplicate NAME a replacement — س-29 rule 1, owner
decision 2026-08-25 (``docs/rag-fidelity-audit.md`` §4-هـ-2).

One rule: **a file arriving under a name that already exists in its space
replaces the file that had it, index and all** — and the new one is proven
before the old one is touched.

**The decision, and why each half of it is where it is.** The owner's stated
reason is that "الملف الجديد بنفس الاسم قد يحتوي على بيانات محدثه من الملف
القديم" — a file uploaded under an existing name is an UPDATED version of it.
Two things follow, and they pull in opposite directions:

* Nothing existing may be destroyed for a promise. Registering an upload mints
  a row and presigns a PUT; the bytes may never arrive. So the sweep cannot
  run in ``RegisterUpload``, and does not: it runs here, after
  ``CompleteUpload`` has recorded that they landed. That is the decision's own
  order — "رفعٌ أوّلًا ثمّ حذف" — read strictly.
* The NEWER file wins, never the older. ``FileRepository.live_namesakes``
  returns only rows strictly older than the one that just arrived, so a
  replacement always runs in the direction the decision states. A rename that
  moves an OLDER file onto a NEWER file's name therefore destroys nothing and
  leaves both — deleting the newer one would be this rule running backwards,
  asserting that stale content supersedes fresh.

**That same "older only" rule is the concurrency argument.** The completion
path holds no lock — ``framework/di/space_quota.py``'s space row lock is taken
on REGISTER, and this cascade runs long after it was released. It needs none.
"Older than" is a strict order, so of two uploads of one name completing at
once neither can be older than the other: exactly one deletes, and mutual
destruction is not a state the predicate can produce. The loser's own
completion then fails honestly against a deleted row (``File.complete``'s
``_guard_not_deleted`` → 409) rather than resurrecting itself.

**Why there is no unique index behind any of this.** ``spaces`` defends its
own names with one — ``ux_spaces_ws_name``, partial, on ``lower(name)`` — and
turns ``23505`` into ``spaces.duplicate_name``. It cannot be copied here
because the DECISION differs, not the schema: for a space a duplicate name is
an error, for a file it is a replacement, and between "the new row is
inserted" and "the old row is marked deleted" both are live. A unique index
would reject the very insert this feature is built on. Uniqueness is therefore
a state the application CONVERGES to, not an invariant the database asserts,
and ``files/0003_file_name_lookup.py`` is a lookup index that says so.

**Why this file is here and not in a module.** The replacement crosses
``files`` and ``knowledge`` — the old file's row goes AND the corpus built
from its bytes goes — and no module may import another (import-linter
contract 4). ``file_deletion.py`` states the rule and this file keeps it the
same way: it imports NO module, its collaborators are structural
``Protocol``\\ s declared here, and the Composition Root binds them where mypy
checks the fit. It does not even re-implement the delete: the second half is
``DeleteFileService``, which already destroys a file's index and is already
idempotent.

**A failed sweep does not fail the upload, and that is a decision.** The
completion has already succeeded by the time the sweep starts — the row is
``ready`` and ``FileUploaded`` is in the outbox — so propagating a purge
failure would hand the client a 5xx for an upload that worked, and their
retry of ``/complete`` would then be refused with 409 (``ready`` is not a
completable status). The user would be told twice that a successful upload
failed. So each namesake is swept independently, a failure is logged at ERROR
with both ids and does not stop the rest, and the completion's own result is
returned. The visible consequence of a failure is the mildest one available:
the older file is still listed, still downloadable and still deletable — the
state that existed before this feature, with a log line naming it. The
alternative (destroy first, then complete) trades that for the one failure
that cannot be undone.

**The sweep restores the rule; it does not react to a change.** A rename to
the name a file already has is a no-op in ``RenameFile`` and still sweeps
here, so a space that already holds two files of one name — uploaded before
this feature shipped, or left behind by a sweep that failed — is repaired by
the next write on the newer of them. That a request which changed nothing can
delete a file is surprising, and it is the deliberate side of the trade: the
alternative is a rule the product applies only to files that happened to
arrive after it.

**Retrieval is not left holding a deleted file.** ``DeleteFileService`` marks
the row before it purges, so from the first step the old file is invisible to
every listing and every download; the window in which its corpus outlives it
is the same one that cascade already documents, and re-running ``DELETE
/files/{id}`` closes it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.types import Uuid

_logger = get_logger(__name__)


class FileCompleter[R](Protocol):
    """``CompleteUploadService.complete`` — mark the uploaded bytes ready.

    Generic in its RESULT for ``UploadRegistrar``'s reason: the Composition
    Root binds the concrete service and mypy infers ``R = File`` at that call,
    so the router keeps the typed aggregate without this file naming an
    ``app.modules`` type.

    ``complete`` and not ``execute``: this binds to the atomic SERVICE — the
    one that pairs the use-case with its outbox append inside one unit of work
    — never to the bare use-case underneath it, whose events would be dropped
    on the floor. The name difference is what keeps that binding honest at the
    wiring site (``FileMarker``'s rule, applied to the other end).
    """

    async def complete(
        self, ctx: ExecutionContext, *, file_id: Uuid, checksum: str | None
    ) -> R: ...


class FileRenamer[R](Protocol):
    """``RenameFile.execute`` — give a file a different name.

    The bare use-case is the right binding here, unlike ``FileCompleter``
    above, and for the reason ``FileUseCases`` records: a rename produces no
    events, so there is no ``…Service`` wrapper to bind to and none is
    missing.
    """

    async def execute(self, ctx: ExecutionContext, file_id: Uuid, *, name: str) -> R: ...


class NamesakeFinder(Protocol):
    """``FindNamesakes.execute`` — the ids of the live files this one replaces.

    Every part of the rule ("same name" up to case and Unicode normalisation,
    same space, strictly older, not deleted) lives behind this call, in
    ``FileRepository.live_namesakes`` and the index it is written against.
    This service never re-states any of it, so the two cannot drift.
    """

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> Sequence[Uuid]: ...


class FileEraser(Protocol):
    """``DeleteFileService.delete`` — soft-delete a file AND empty its index.

    The CASCADE, not ``SoftDeleteFileService``. Binding the bare mark here
    would replace a file's row while leaving the corpus built from its bytes
    searchable — which is not a smaller version of this feature but the exact
    defect it exists to prevent, and the one the audit records as how "the
    file is indexed twice" was born.
    """

    async def delete(self, ctx: ExecutionContext, file_id: Uuid) -> object: ...


class ReplaceNamesakesService[R]:
    """Complete or rename a file, then destroy the files it replaced.

    Two entry points because a collision has two sources — an upload arriving
    under an existing name, and a rename creating the clash after the fact.
    The audit names both (``RenameFile`` "can create the same collision after
    the fact, so an implementation owes BOTH paths"), and they share one sweep
    so neither can implement a different rule.
    """

    def __init__(
        self,
        complete: FileCompleter[R],
        rename: FileRenamer[R],
        *,
        namesakes: NamesakeFinder,
        erase: FileEraser,
    ) -> None:
        self._complete = complete
        self._rename = rename
        self._namesakes = namesakes
        self._erase = erase

    async def complete(self, ctx: ExecutionContext, *, file_id: Uuid, checksum: str | None) -> R:
        completed = await self._complete.complete(ctx, file_id=file_id, checksum=checksum)
        await self._sweep(ctx, file_id)
        return completed

    async def rename(self, ctx: ExecutionContext, file_id: Uuid, *, name: str) -> R:
        renamed = await self._rename.execute(ctx, file_id, name=name)
        await self._sweep(ctx, file_id)
        return renamed

    async def _sweep(self, ctx: ExecutionContext, file_id: Uuid) -> None:
        """Delete every live file ``file_id`` replaced, one independent step
        each.

        Nothing raised here reaches the caller: the operation that produced
        ``file_id``'s new state has already succeeded and may not be reported
        as a failure (module docstring). Finding the namesakes is inside the
        guard for the same reason as deleting them — a read that fails is
        still a sweep that did not happen, and the completion is no less
        successful for it.
        """
        try:
            replaced = await self._namesakes.execute(ctx, file_id)
        except Exception:
            _logger.exception(
                "file.replacement.lookup_failed",
                extra={"file_id": file_id, "workspace_id": ctx.workspace_id},
            )
            return
        for older in replaced:
            try:
                await self._erase.delete(ctx, older)
            except Exception:
                # Per file, so one unpurgeable corpus does not spare the
                # others. The failure leaves the older file exactly as it was
                # before this feature existed -- listed, downloadable and
                # deletable by hand -- which is why it is survivable.
                _logger.exception(
                    "file.replacement.delete_failed",
                    extra={
                        "file_id": file_id,
                        "replaced_id": older,
                        "workspace_id": ctx.workspace_id,
                    },
                )
            else:
                _logger.info(
                    "file.replacement.replaced",
                    extra={
                        "file_id": file_id,
                        "replaced_id": older,
                        "workspace_id": ctx.workspace_id,
                    },
                )
