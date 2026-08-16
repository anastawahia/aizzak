"""Spaces aggregate (pure — 06-domain-models §6).

``Space`` is the ownership axis INSIDE a workspace
(``docs/spaces-backend-plan.md`` §1.1): every file and every conversation
belongs to exactly one, and a space's contents are what its conversations can
see. It is **not** a tenant — the workspace stays the only security boundary,
RLS stays on ``app.workspace_id`` alone, and a space is filtered for in the
query, never in a policy (§3.2).

The aggregate is deliberately thin, and that is the design rather than an
omission: a space has no status machine, no counters and no configuration.
It has a name that may change and an existence that may end. Everything that
makes a space *interesting* — its bytes, its files, its threads — is owned by
other modules and reached by filtering on this row's id.

The optimistic ``version`` is advanced by the repository on ``save``
(02-port-contracts §2). Identifiers are UUIDv7 text (``str``); timestamps are
timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.spaces.domain.errors import SpaceStateError
from app.modules.spaces.domain.value_objects import SpaceName


@dataclass(slots=True)
class Space:
    """A named ownership axis inside one workspace."""

    id: str
    workspace_id: str
    name: SpaceName
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int

    def rename(self, new_name: SpaceName, now: datetime) -> None:
        """Rename the space — the only field of it that may change.

        Unlike ``File.rename`` there is no extension to preserve: a space name
        is a label, and the whole of it is the user's to change. What IS shared
        with the files precedent is the no-op rule — renaming to the name it
        already has changes nothing, so it bumps no ``updated_at`` and burns no
        ``version``. A "modified at" that moves when nothing was modified is a
        false record.

        Case-only edits (``Work`` → ``work``) are real changes and are applied:
        the stored name is what a person reads, and only the UNIQUENESS rule
        folds case (``ux_spaces_ws_name`` on ``lower(name)``, §3.2) — which is
        why such a rename cannot collide with anything but the row itself.
        """
        self._guard_not_deleted()
        if new_name.value == self.name.value:
            return
        self.name = new_name
        self.updated_at = now

    def soft_delete(self, now: datetime) -> None:
        """Soft-delete. Idempotent: deleting an already-deleted space is a no-op.

        This marks the ROW only. Erasing what the space owns — its files, its
        conversations, its index — is a seven-step cascade across five modules
        that no module can perform, and it lives in a composition-root service
        (§3.6). Marking first is that cascade's step 1 on purpose: the space
        disappears from the interface immediately, so a caller never sees a
        half-deleted one if a later step stumbles.
        """
        if self.deleted_at is not None:
            return
        self.deleted_at = now
        self.updated_at = now

    @property
    def is_active(self) -> bool:
        """Whether this space may still be named by a write — the one question
        ``files``/``conversations`` ask about a ``space_id`` before storing it."""
        return self.deleted_at is None

    def _guard_not_deleted(self) -> None:
        if self.deleted_at is not None:
            raise SpaceStateError("space is deleted")
