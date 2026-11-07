"""WireTwin DTOs -- the wire shapes of ``/api/v1/electrical`` (LLD 3.3.14, 03-api-spec section 2).

snake_case, no bare arrays, every enum an output ``str`` (an output ``Literal`` would promise a
closed vocabulary the domain owns). Three input rules worth stating:

* a profile ``body`` is a plain object here and is validated by the domain
  (``DrawingProfile.from_body``): a violation is ``422 electrical.invalid_profile`` naming the
  field and NEVER echoing what the client sent (PR-B security review S-8);
* ``ReprocessIn.pages`` is 1..1000 positive integers without repeats;
* ``DocumentRegisterIn.checksum`` is a SHA-256 as 64 hexadecimal characters of either case, else
  422 (the service normalises it); it is an early warning, not a guarantee -- STG-01 recomputes
  the hash from the bytes and is the judge (INC1-AC-7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from app.api.v1.dto.files import FileTagsOut


class ElectricalProjectIn(BaseModel):
    space_id: str
    name: str
    description: str | None = None
    profile: dict[str, Any] | None = None  # a DrawingProfile body; the default profile if absent


class ElectricalProjectPatchIn(BaseModel):
    name: str | None = None
    description: str | None = None


class ElectricalProjectOut(BaseModel):
    id: str
    space_id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class DrawingProfileIn(BaseModel):
    body: dict[str, Any]


class DrawingProfileOut(BaseModel):
    version: int
    source: str
    fingerprint: str
    body: dict[str, Any]
    created_at: datetime


class ProfileProposalOut(BaseModel):
    """The latest ``detected`` version while it matters. ``field_confidence`` and ``basis`` are
    what STG-01 stored with it; both are empty maps for a version that stored none."""

    confidence: float
    field_confidence: dict[str, float]
    mixed_standards: bool
    basis: dict[str, list[str]]
    body: dict[str, Any]


class EffectiveProfileOut(BaseModel):
    state: str  # none | proposed | accepted | confirmed
    effective: DrawingProfileOut
    project_version: int
    proposal: ProfileProposalOut | None


class DocumentRegisterIn(BaseModel):
    file_id: str
    checksum: str | None = Field(default=None, max_length=128)


class ProfileConfirmIn(BaseModel):
    body: dict[str, Any] | None = None


class ReprocessIn(BaseModel):
    pages: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=1, max_length=1_000
    )

    @field_validator("pages")
    @classmethod
    def _no_repeats(cls, pages: list[int] | None) -> list[int] | None:
        if pages is not None and len(set(pages)) != len(pages):
            raise ValueError("pages must not repeat")
        return pages


class IngestionJobOut(BaseModel):
    id: str
    document_id: str
    kind: str
    status: str
    stage: str | None
    pages: list[int] | None
    counts: dict[str, int]  # pages per StageStatus of the classify stage; {} inside a list
    seq: int
    attempt: int
    failure_code: str | None
    failure_detail: str | None
    queue_position: int | None
    estimated_wait_s: float | None
    stale: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ElectricalDocumentOut(BaseModel):
    id: str
    project_id: str
    file_id: str
    file_name: str | None  # from files; null once the file is unreadable
    tags: FileTagsOut | None  # from files on the single read only (INC1-D-26)
    status: str
    exclusion_reason: str | None
    page_count: int | None
    content_sha256: str | None
    profile_state: str
    latest_job: IngestionJobOut | None
    created_at: datetime


class DocumentRegisteredOut(BaseModel):
    document: ElectricalDocumentOut
    job: IngestionJobOut


class PageStageOut(BaseModel):
    stage: str
    status: str
    reason: str | None
    attempts: int


class DocumentPageOut(BaseModel):
    page_no: int
    kind: str | None
    language: str | None
    needs_ocr: bool | None
    stages: list[PageStageOut]


class CharacterizationOut(BaseModel):
    """One document's characterization record (FR-EVAL-14). ``creator`` and ``producer`` are the
    PDF's own metadata, untrusted text: control, format and hidden characters arrive escaped
    (R1-4)."""

    document_id: str
    content_sha256: str
    producer_family: str | None
    creator: str | None
    producer: str | None
    page_count: int
    text_kinds: dict[str, int]
    page_languages: dict[str, int]
    document_language: str
    conventions: dict[str, int]
    symbol_standard_guess: str | None
    symbol_standard_basis: list[str]
    plc_families: dict[str, int]
    cross_reference_formats: dict[str, int]
    ocr_needed_pages: int
    cid_fonts_without_tounicode: int
    pipeline_version: str
    shared_version: str
    rules_version: str
    mixed_standards: bool
    failed_pages: int


class CharacterizationReportOut(BaseModel):
    """A project's characterization, derived on every read from its ACTIVE documents' records."""

    documents: int
    pages: int
    failed_pages: int
    producers: dict[str, int]
    text_kinds: dict[str, int]
    document_languages: dict[str, int]
    conventions: dict[str, int]
    symbol_standards: dict[str, int]
    plc_families: dict[str, int]
    ocr_needed_ratio: float
    mixed_standard_documents: int
    versions: dict[str, list[str]]


class AffectedDocumentOut(BaseModel):
    document_id: str
    project_id: str
    status: str
    content_identical: bool | None


class ReplacedFileOut(BaseModel):
    file_id: str
    name: str | None
    documents: list[AffectedDocumentOut]


class KeptNamesakeOut(BaseModel):
    file_id: str
    name: str | None
    tags: FileTagsOut


class ReplacementPreviewOut(BaseModel):
    file_id: str
    replaced: list[ReplacedFileOut]
    kept: list[KeptNamesakeOut]


__all__ = [
    "AffectedDocumentOut",
    "CharacterizationOut",
    "CharacterizationReportOut",
    "DocumentPageOut",
    "DocumentRegisterIn",
    "DocumentRegisteredOut",
    "DrawingProfileIn",
    "DrawingProfileOut",
    "EffectiveProfileOut",
    "ElectricalDocumentOut",
    "ElectricalProjectIn",
    "ElectricalProjectOut",
    "ElectricalProjectPatchIn",
    "IngestionJobOut",
    "KeptNamesakeOut",
    "PageStageOut",
    "ProfileConfirmIn",
    "ProfileProposalOut",
    "ReplacedFileOut",
    "ReplacementPreviewOut",
    "ReprocessIn",
]
