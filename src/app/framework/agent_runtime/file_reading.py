"""``read_text_file`` — load a workspace file's text for an agent (4.6-b).

The Data-Analysis and File-Editing agents both need the SAME thing: turn a
``file_id`` into decoded text, guarding the failure modes. Since agents are
independent plugins that cannot import each other, this correctness-sensitive
loading lives here once (a framework-pure agent-runtime utility, sibling to the
executor) rather than being duplicated — it touches only the ``FilesAccess``
DIP seam + the ``StorageProvider`` framework port, so the kernel stays 8/0.

Two-step by design (files metadata → storage bytes): ``FilesAccess`` yields a
``ready``-file view with a ``storage_key`` but no bytes; the bytes come from
``StorageProvider.get`` (files metadata and object storage are different ports,
per the module boundary). Guards map to the shared ``AppError`` codes so the
executor/transport surface them uniformly:

* ``files``/``storage`` unwired → 500 (a composition bug, not a user error);
* no ``ready`` file for the id, **or one outside ``space_id``** → 404;
* non-textual content type → 415 (this agent reads text, not binaries);
* larger than ``max_bytes`` → 413 (bound the prompt; checked on metadata BEFORE
  fetching, so a huge object is never pulled into memory).

Decoding is lenient (``errors="replace"``) — a stray byte degrades one glyph
rather than crashing an otherwise-valid analysis.

**The space check (spaces plan step 10) closes finding 2-ح**, the standing leak
that ``FilesAccess.get_readable`` answers for ANY file id in the workspace. It
lives here rather than in the seam because this is the only caller of that
seam, and because the files module's job is "is this file readable?" while
"may THIS caller read it?" is the consumer's — the same split ``conversations``
makes for pins (§3.5).

Its answer is **404, worded identically to a missing file**, and that is the
whole point rather than a shortcut. ``get_readable`` already collapses "absent"
and "not ready" into one ``None`` on purpose (03 §4: a read face must not tell
a caller that a file it cannot read nevertheless exists); a distinct "wrong
space" reply would undo that by turning the id into an existence oracle —
worse here than at the API, since the id can arrive from a model's output or a
prompt injection, not from a listing the user already saw. Hence 404 and not
the pin rule's ``409 spaces.cross_space_pin``: there a human picked a file out
of a listing of their own workspace and is owed an actionable reason.
"""

from __future__ import annotations

from app.framework.agent_runtime.deps_ports import FileReadView, FilesAccess
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    AppError,
    NotFoundError,
    TooLargeError,
    UnsupportedTypeError,
)
from app.framework.ports.storage_provider import StorageProvider
from app.framework.types import Uuid

_TEXTUAL_PREFIX = "text/"
_TEXTUAL_TYPES = frozenset({"application/json"})


def _is_textual(content_type: str) -> bool:
    return content_type.startswith(_TEXTUAL_PREFIX) or content_type in _TEXTUAL_TYPES


def _is_outside(view: FileReadView, space_id: Uuid | None) -> bool:
    """Is this file outside the caller's space?

    ``space_id is None`` is UNSCOPED and matches everything. That is why this
    is not the plain ``!=`` the pin rule uses: there both sides are "the space
    of a thing", here the left side is a scope, and a scope's absence must not
    be read as "only files that belong to no space".

    ⚠️ **Since س-32 (owner decision 2026-08-26) no AGENT reaches this with
    ``None`` on an ordinary turn.** The three bundled agents read
    ``AgentDependencies.space_id``, which the orchestrator fills from the
    thread the turn belongs to, so the unscoped branch survives for exactly one
    shape: an orchestrator with no conversations seam wired, where no space can
    be known at all. The RAG agent answers that case by not retrieving; these
    two read the file, because a caller who named a ``file_id`` in a deployment
    that tracks no threads has named the only scope that deployment has.

    A caller that DOES name a space gets the strict reading, ``None`` on the
    file included: a file no space owns is not a file that every space owns.
    """
    return space_id is not None and view.space_id != space_id


async def read_text_file(
    files: FilesAccess | None,
    storage: StorageProvider | None,
    ctx: ExecutionContext,
    file_id: Uuid,
    *,
    max_bytes: int,
    space_id: Uuid | None,
) -> str:
    """Resolve ``file_id`` to decoded text, or raise the mapped ``AppError``.

    ``files``/``storage`` are accepted as optional so an agent can pass
    ``self.deps.files``/``self.deps.storage`` straight through; both unset is a
    wiring bug (500).

    ``space_id`` is keyword-only and deliberately NOT defaulted, matching
    ``KnowledgeAccess.retrieve`` (step 8) for the same reason: a forgotten
    scope and a chosen one must not look alike at a call site, and here the
    forgotten one reads across every space in the workspace.
    """
    if files is None or storage is None:
        raise AppError(detail="file access is not wired", code="common.internal", status=500)
    view = await files.get_readable(ctx, file_id)
    if view is None or _is_outside(view, space_id):
        # ONE answer for three causes (absent · not ready · another space),
        # phrased as the first two — see the module docstring. Raised BEFORE
        # any storage fetch, so a foreign object is never pulled either.
        raise NotFoundError(f"file {file_id!r} was not found or is not ready")
    if not _is_textual(view.content_type):
        raise UnsupportedTypeError(f"file content type {view.content_type!r} is not textual")
    if view.size_bytes > max_bytes:
        raise TooLargeError(
            f"file is {view.size_bytes} bytes, exceeding the {max_bytes}-byte limit"
        )
    raw = await storage.get(view.storage_key)
    return raw.decode("utf-8", errors="replace")
