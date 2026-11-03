"""``StreamingSourceStager``: the object-store bytes onto the stage volume (DEC-04, PLT-03).

``stage`` streams ``StreamingStorage.read_chunks`` into ``<root>/<workspace>/<document>.pdf.part``
(mode ``0640``), hashing with SHA-256 WHILE writing, so memory is one chunk however large the
file. It verifies the size against the file row, then ``os.replace``s to the final name: ele-parse
(a read-only mount of the same volume, DEC-04) never sees a half-written file. A size mismatch is
``ValidationError(code="electrical.source_unreadable")``; an object that is gone is the
storage's ``NotFoundError``; both leave nothing behind. ``discard`` removes both names and is
idempotent. ``remove_older_than`` is the start-up sweep for what a crashed worker left
(INC1-D-20).

Disk writes run in a thread (``asyncio.to_thread``): the loop is never blocked on a slow volume.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.observability import get_logger
from app.framework.ports.storage_provider import StreamingStorage
from app.framework.types import Uuid
from app.modules.electrical.drawings.ports.source import StagedSource

_logger = get_logger(__name__)
_FILE_MODE: Final = 0o640
_DIR_MODE: Final = 0o750


class StreamingSourceStager:
    def __init__(self, storage: StreamingStorage, root: Path, *, chunk_bytes: int) -> None:
        self._storage = storage
        self._root = root
        self._chunk_bytes = chunk_bytes

    async def stage(
        self, ctx: ExecutionContext, *, document_id: Uuid, storage_key: str, expected_size: int
    ) -> StagedSource:
        name = f"{ctx.workspace_id}/{document_id}.pdf"
        final = self._root / name
        part = final.with_name(final.name + ".part")
        await asyncio.to_thread(self._prepare, final.parent)
        digest = hashlib.sha256()
        size = 0
        chunks: AsyncIterator[bytes] = self._storage.read_chunks(
            storage_key, chunk_bytes=self._chunk_bytes
        )
        try:
            fd = await asyncio.to_thread(self._open, part)
            try:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > expected_size:
                        # stop at the first byte too many: the stage volume is shared and a
                        # swapped, far larger object must not be copied to it before it is refused
                        raise ValidationError(
                            f"the stored object is larger than the {expected_size} bytes the "
                            "file row says",
                            code="electrical.source_unreadable",
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(os.write, fd, chunk)
                await asyncio.to_thread(os.fsync, fd)
            finally:
                await asyncio.to_thread(os.close, fd)
            if size != expected_size:
                raise ValidationError(
                    f"the stored object has {size} bytes; the file row says {expected_size}",
                    code="electrical.source_unreadable",
                )
            await asyncio.to_thread(os.replace, part, final)
        except BaseException:
            await asyncio.to_thread(_unlink, part)
            await asyncio.to_thread(_unlink, final)
            raise
        finally:
            aclose = getattr(chunks, "aclose", None)
            if aclose is not None:
                await aclose()
        return StagedSource(name=name, sha256=digest.hexdigest(), size_bytes=size)

    async def discard(self, source: StagedSource) -> None:
        final = self._root / source.name
        await asyncio.to_thread(_unlink, final.with_name(final.name + ".part"))
        await asyncio.to_thread(_unlink, final)

    async def remove_older_than(self, max_age_s: float) -> int:
        """Delete staged files (and partials) not modified for ``max_age_s``; the count."""
        return await asyncio.to_thread(self._sweep, max_age_s)

    # -- blocking helpers (run in a thread) ----------------------------------------
    def _prepare(self, directory: Path) -> None:
        directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)

    @staticmethod
    def _open(path: Path) -> int:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)

    def _sweep(self, max_age_s: float) -> int:
        if not self._root.is_dir():
            return 0
        cutoff = time.time() - max_age_s
        removed = 0
        for path in self._root.glob("*/*.pdf*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # a file another run just removed, or one we cannot touch
                _logger.warning("electrical.stage.sweep_skipped", extra={"name": path.name})
        return removed


def _unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
