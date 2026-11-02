"""What registering (or re-tagging) a file would replace (PLT-24, HLD §8.11, INC1-AC-11).

A READ: it writes nothing. ``OlderNamesakes.preview`` is files' own answer (it 404s an unknown or
foreign file): the ids the operation would delete (``replaced``) and the same-name files it would
leave because their tags differ (``kept``). This service adds names (one bulk read), the kept
files' tags (one read each; there are few), and the drawing documents of the replaced files with
their project and whether the request's SHA-256 equals theirs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import normalize_checksum
from app.modules.electrical.drawings.domain.document import DocumentStatus
from app.modules.electrical.drawings.ports.files import DrawingFiles
from app.modules.electrical.drawings.ports.namesakes import OlderNamesakes
from app.modules.electrical.drawings.ports.repository import DocumentRepository


@dataclass(frozen=True, slots=True)
class AffectedDocument:
    document_id: str
    project_id: str
    status: DocumentStatus
    content_identical: bool | None  # None when the request or the document has no SHA-256


@dataclass(frozen=True, slots=True)
class ReplacedFile:
    file_id: str
    name: str | None
    documents: tuple[AffectedDocument, ...]


@dataclass(frozen=True, slots=True)
class KeptNamesake:
    file_id: str
    name: str | None
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReplacementPreview:
    file_id: str
    replaced: tuple[ReplacedFile, ...]
    kept: tuple[KeptNamesake, ...]


class ReplacementPreviewService:
    def __init__(
        self, namesakes: OlderNamesakes, files: DrawingFiles, documents: DocumentRepository
    ) -> None:
        self._namesakes = namesakes
        self._files = files
        self._documents = documents

    async def preview(
        self, ctx: ExecutionContext, file_id: Uuid, *, checksum: str | None = None
    ) -> ReplacementPreview:
        sha256 = normalize_checksum(checksum)
        view = await self._namesakes.preview(ctx, file_id)
        replaced_ids = tuple(view.replaced)
        kept_ids = tuple(view.kept)
        names = await self._files.names_for_files(ctx, [*replaced_ids, *kept_ids])
        replaced: list[ReplacedFile] = []
        for older in replaced_ids:
            documents = await self._documents.not_purging_for_file(ctx, older)
            replaced.append(
                ReplacedFile(
                    file_id=older,
                    name=names.get(older),
                    documents=tuple(
                        AffectedDocument(
                            document_id=d.id,
                            project_id=d.project_id,
                            status=d.status,
                            content_identical=(
                                None
                                if sha256 is None or d.content_sha256 is None
                                else d.content_sha256 == sha256
                            ),
                        )
                        for d in documents
                    ),
                )
            )
        kept: list[KeptNamesake] = []
        for other in kept_ids:
            readable = await self._files.get_readable(ctx, other)
            kept.append(
                KeptNamesake(
                    file_id=other,
                    name=names.get(other),
                    tags={} if readable is None else dict(readable.tags),
                )
            )
        return ReplacementPreview(file_id=file_id, replaced=tuple(replaced), kept=tuple(kept))
