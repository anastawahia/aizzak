"""The drawings use-case bundle the API consumes (LLD 3.3.8).

Five services, one bundle (``FileUseCases``' precedent). Deliberately NOT in it:

* ``InspectDocument`` (STG-01) and ``SweepPurgedDocument``: the worker's, built by
  ``workers/electrical_bootstrap.py``;
* ``PurgeFileDocuments`` and ``PurgeSpaceDrawings``: the delete cascades' (they bind to
  ``framework/di/electrical_purge.py``). No route may purge, ``PurgeSpaceFiles``' rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.electrical.drawings.application.characterization import CharacterizationService
from app.modules.electrical.drawings.application.documents import DocumentService
from app.modules.electrical.drawings.application.inspection import InspectDocument
from app.modules.electrical.drawings.application.jobs import JobService
from app.modules.electrical.drawings.application.projects import ProjectService
from app.modules.electrical.drawings.application.purge import (
    PurgeFileDocuments,
    PurgeSpaceDrawings,
    SweepPurgedDocument,
)
from app.modules.electrical.drawings.application.replacement import ReplacementPreviewService


@dataclass(frozen=True, slots=True)
class DrawingsUseCases:
    projects: ProjectService  # create, get, list, update; profile get / replace
    documents: DocumentService  # register (STG-00), get, list, profile, confirm, reprocess, pages
    jobs: JobService  # get (counts, position, estimate, stale), cancel, resume
    replacement: ReplacementPreviewService
    characterization: CharacterizationService


__all__ = [
    "DrawingsUseCases",
    "InspectDocument",
    "PurgeFileDocuments",
    "PurgeSpaceDrawings",
    "SweepPurgedDocument",
]
