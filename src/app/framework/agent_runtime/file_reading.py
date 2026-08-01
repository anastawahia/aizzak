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
* no ``ready`` file for the id → 404;
* non-textual content type → 415 (this agent reads text, not binaries);
* larger than ``max_bytes`` → 413 (bound the prompt; checked on metadata BEFORE
  fetching, so a huge object is never pulled into memory).

Decoding is lenient (``errors="replace"``) — a stray byte degrades one glyph
rather than crashing an otherwise-valid analysis.
"""

from __future__ import annotations

from app.framework.agent_runtime.deps_ports import FilesAccess
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


async def read_text_file(
    files: FilesAccess | None,
    storage: StorageProvider | None,
    ctx: ExecutionContext,
    file_id: Uuid,
    *,
    max_bytes: int,
) -> str:
    """Resolve ``file_id`` to decoded text, or raise the mapped ``AppError``.

    ``files``/``storage`` are accepted as optional so an agent can pass
    ``self.deps.files``/``self.deps.storage`` straight through; both unset is a
    wiring bug (500).
    """
    if files is None or storage is None:
        raise AppError(detail="file access is not wired", code="common.internal", status=500)
    view = await files.get_readable(ctx, file_id)
    if view is None:
        raise NotFoundError(f"file {file_id!r} was not found or is not ready")
    if not _is_textual(view.content_type):
        raise UnsupportedTypeError(f"file content type {view.content_type!r} is not textual")
    if view.size_bytes > max_bytes:
        raise TooLargeError(
            f"file is {view.size_bytes} bytes, exceeding the {max_bytes}-byte limit"
        )
    raw = await storage.get(view.storage_key)
    return raw.decode("utf-8", errors="replace")
