"""Page-level values of the ingestion pipeline (LLD 3.3.6). Stdlib only, immutable."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.electrical.drawings.domain.pipeline import Stage


class PageKind(StrEnum):  # FR-ING-08, plus EMPTY (INC1-D-18)
    VECTOR_TEXT = "vector_text"
    VECTOR_OUTLINED = "vector_outlined"  # text drawn as curves / SHX strokes
    VECTOR_GARBLED = "vector_garbled"  # CID text without a sane Unicode mapping
    RASTER = "raster"
    MIXED = "mixed"
    EMPTY = "empty"


class PageLanguage(StrEnum):
    EN = "en"
    ZH = "zh"
    MIXED = "mixed"
    NONE = "none"


class StageStatus(StrEnum):  # FR-ING-10
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class StageReason(StrEnum):
    PARSE_TIMEOUT = "parse_timeout"
    MEMORY_LIMIT = "memory_limit"
    PARSE_ERROR = "parse_error"
    AMBIGUOUS_CLASSIFICATION = "ambiguous_classification"


@dataclass(frozen=True, slots=True)
class PdfSummary:
    page_count: int
    pdf_version: str
    encrypted: bool
    creator: str | None
    producer: str | None
    outline_count: int


@dataclass(frozen=True, slots=True)
class FontSignal:
    name: str
    font_type: str
    cid: bool
    embedded: bool
    has_tounicode: bool


@dataclass(frozen=True, slots=True)
class PageSignals:
    """One page as ele-parse saw it (contract schema 1, LLD 3.3.10)."""

    page_no: int
    width_pt: float
    height_pt: float
    rotation: int
    char_count: int
    unmapped_char_count: int  # no Unicode, or U+FFFD
    pua_char_count: int  # Private Use Area
    cjk_char_count: int
    latin_char_count: int
    word_count: int
    vector_path_count: int
    short_curve_path_count: int  # stroke-sized curves: the SHX / outlined-text signal
    image_count: int
    image_area_ratio: float
    link_count: int
    fonts: tuple[FontSignal, ...]  # <= 32
    tokens: tuple[str, ...]  # <= ELE_PARSE_TOKEN_SAMPLE, each <= 32 chars


@dataclass(frozen=True, slots=True)
class ClassificationThresholds:  # part of the profile (NFR-CFG-01); defaults below
    min_text_chars: int = 20
    garbled_ratio: float = 0.20
    raster_area_ratio: float = 0.60
    outlined_curves_min: int = 400
    ambiguity_margin: float = 0.10
    cjk_share: float = 0.30
    script_mix_share: float = 0.15


@dataclass(frozen=True, slots=True)
class PageClassification:
    page_no: int
    kind: PageKind
    language: PageLanguage
    needs_ocr: bool  # RASTER, VECTOR_OUTLINED, VECTOR_GARBLED, or MIXED with either
    status: StageStatus  # SUCCEEDED, or NEEDS_REVIEW when two kinds sit within the margin
    reason: StageReason | None


@dataclass(frozen=True, slots=True)
class PageResult:
    """What a stage run writes for one page: the classification, the page's signals (the
    repository stores an abridged form in jsonb) and how many attempts it took."""

    classification: PageClassification
    signals: PageSignals
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class PageRow:
    """A stored page with the state of each of its stages (read side)."""

    page_no: int
    classification: PageClassification | None  # None while the classify stage is pending
    stages: Mapping[Stage, StageStatus]
    attempts: int
