"""Files aggregate (pure — 06-domain-models §6).

``File`` is a workspace-scoped object descriptor shared among every agent in
the workspace (INV-F3); its bytes live in MinIO, addressed by ``storage_key``
(INV-F1). Since the spaces plan's step 6 it also carries the ``space_id`` it is
owned by — an opaque ownership axis INSIDE the tenant, never a tenant of its
own. Behaviour lives on the aggregate; mutations touch only ``status``,
``checksum``, ``deleted_at``, ``name`` (INV-F4 — the one descriptive field a
rename may change) and ``updated_at``. Everything that describes the BYTES —
``content_type``, ``size_bytes``, ``storage_key`` — has no mutator at all,
which is what keeps the descriptor honest about the object it points at.
The optimistic ``version`` is advanced by the repository on
``save`` (02-port-contracts §2). Identifiers are UUIDv7 text (``str``);
timestamps are timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.files.domain.errors import FileStateError, InvalidFileInput
from app.modules.files.domain.value_objects import (
    ContentType,
    FileName,
    FileStatus,
    Sha256,
    StorageKey,
)

_SCANNABLE = (FileStatus.UPLOADED, FileStatus.SCANNING)


@dataclass(slots=True)
class File:
    """A shared, workspace-scoped file descriptor."""

    id: str
    workspace_id: str
    # The owning space (`docs/spaces-backend-plan.md` step 6). NOT a second
    # security boundary -- the workspace stays the only one, and RLS stays on
    # `workspace_id` alone (§3.2); this is an ownership axis, filtered in the
    # query. Opaque here: the aggregate stores the id and never asks what it
    # means (`ports/spaces.py` proves it names something real).
    #
    # `| None` mirrors the column, which is NULLable until plan row 8-b, and
    # it has no default ON PURPOSE: every construction site must SAY which
    # space it files under, so a writer that has none is visible in the source
    # rather than inheriting one silently. The one such writer today is the
    # media worker, whose generated file has no space to belong to until
    # `conversations` carries one (step 7).
    #
    # There is no mutator, and that is decision 3: a file does not move
    # between spaces. `save` leaves the column out of its UPDATE, which is
    # what enforces this in the database rather than only in the type.
    space_id: str | None
    name: FileName
    content_type: ContentType
    size_bytes: int
    storage_key: StorageKey
    checksum: Sha256 | None
    status: FileStatus
    uploaded_by: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int

    def mark_scanning(self, now: datetime) -> None:
        """``uploaded -> scanning`` once the antivirus scan begins."""
        self._guard_not_deleted()
        if self.status is not FileStatus.UPLOADED:
            raise FileStateError(f"cannot start scanning from status {self.status.value!r}")
        self.status = FileStatus.SCANNING
        self.updated_at = now

    def complete(self, checksum: Sha256 | None, now: datetime) -> None:
        """``uploaded|scanning -> ready`` (INV-F2): the file becomes usable and
        its checksum is recorded when the caller supplied one.

        ``checksum`` is optional because the wire contract makes it so
        (03-api-spec §2: ``FileCompleteIn.checksum: str | None = None`` — and
        the published event schema likewise lists ``checksum`` as optional): a
        client that cannot hash its upload may still complete it, and a
        ``ready`` file with ``checksum=None`` is an honest record of that."""
        self._guard_not_deleted()
        if self.status not in _SCANNABLE:
            raise FileStateError(f"cannot complete from status {self.status.value!r}")
        self.status = FileStatus.READY
        self.checksum = checksum
        self.updated_at = now

    def quarantine(self, now: datetime) -> None:
        """``uploaded|scanning -> quarantined``: the antivirus scan flagged the file."""
        self._guard_not_deleted()
        if self.status not in _SCANNABLE:
            raise FileStateError(f"cannot quarantine from status {self.status.value!r}")
        self.status = FileStatus.QUARANTINED
        self.updated_at = now

    def rename(self, new_name: FileName, now: datetime) -> None:
        """Rename the file — the only field of it that may change after
        registration (INV-F4).

        **The extension is immutable.** The bytes, ``content_type``,
        ``storage_key`` and ``size_bytes`` are all fixed at registration, and
        the extension is the user-visible claim ABOUT those bytes: letting a
        rename turn ``report.pdf`` into ``report.exe`` would make the displayed
        name lie about what the file is, and hand a download to the wrong
        handler on the way out. So a new name whose extension matches the
        current one (case-insensitively, since ``.PDF`` and ``.pdf`` are the
        same claim) is taken as sent; a new name with NO extension INHERITS the
        current one, which is what a person renaming ``report.pdf`` to
        ``Q1 summary`` means and what every file manager does; and a new name
        with a DIFFERENT extension is refused as invalid input.

        Renaming to the name it already has is a no-op — no ``updated_at``
        bump, no version churn (``soft_delete``'s idempotency precedent): a
        "modified at" that moves when nothing was modified is a false record.

        Any status may be renamed. A half-uploaded or quarantined file is
        exactly the one whose name a person wants to fix, and the name has no
        bearing on the status machine. A soft-deleted file is refused like
        every other write.
        """
        self._guard_not_deleted()
        current = self.name.extension
        if new_name.extension.lower() != current.lower():
            if new_name.extension:
                raise InvalidFileInput(
                    f"the file extension cannot change: expected {current!r}"
                    if current
                    else "the file has no extension, so the new name may not add one"
                )
            # Re-built through the value object on purpose: appending can push
            # the name past 255 characters, and that limit is its business.
            new_name = FileName(new_name.value + current)
        if new_name.value == self.name.value:
            return
        self.name = new_name
        self.updated_at = now

    def soft_delete(self, now: datetime) -> None:
        """Soft-delete. Idempotent: deleting an already-deleted file is a no-op."""
        if self.deleted_at is not None:
            return
        self.deleted_at = now
        self.updated_at = now

    @property
    def is_ready(self) -> bool:
        """INV-F2/F3: readable by any agent in the workspace only when ``ready``
        and not (soft-)deleted — ownership is workspace-level, not agent-level."""
        return self.status is FileStatus.READY and self.deleted_at is None

    def _guard_not_deleted(self) -> None:
        if self.deleted_at is not None:
            raise FileStateError("file is deleted")
