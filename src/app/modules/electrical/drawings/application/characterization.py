"""Reading the characterization records (FR-EVAL-14, INC1-AC-13).

STG-01 writes one record per document (``InspectDocument``). A document's is read as it is; a
project's is DERIVED each time by ``aggregate`` over its ACTIVE documents' records (an excluded
or purging document is out of it), so the report always equals the sum of what it lists.
"""

from __future__ import annotations

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError
from app.framework.types import Uuid
from app.modules.electrical.drawings.domain.characterization import (
    Characterization,
    CharacterizationReport,
    aggregate,
)
from app.modules.electrical.drawings.domain.document import DocumentStatus
from app.modules.electrical.drawings.ports.repository import (
    CharacterizationRepository,
    DocumentRepository,
    ProjectRepository,
)


class CharacterizationService:
    def __init__(
        self,
        projects: ProjectRepository,
        documents: DocumentRepository,
        characterizations: CharacterizationRepository,
    ) -> None:
        self._projects = projects
        self._documents = documents
        self._characterizations = characterizations

    async def for_document(self, ctx: ExecutionContext, document_id: Uuid) -> Characterization:
        document = await self._documents.get(ctx, document_id)
        if document is None or document.status is not DocumentStatus.ACTIVE:
            raise NotFoundError("document not found")
        item = await self._characterizations.get(ctx, document_id)
        if item is None:
            raise NotFoundError("the document has not been characterized yet")
        return item

    async def for_project(self, ctx: ExecutionContext, project_id: Uuid) -> CharacterizationReport:
        if await self._projects.get(ctx, project_id) is None:
            raise NotFoundError("project not found")
        return aggregate(await self._characterizations.for_project(ctx, project_id))
