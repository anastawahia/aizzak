"""The ingestion pipeline's identity (LLD 3.3.6). Stdlib only."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

PIPELINE_VERSION: Final = "1.0.0"  # bumps whenever a stage's output can change


class Stage(StrEnum):
    """Per-page stages. INC-1 has one; each later increment APPENDS its own
    (and widens ck_drawings_page_stages_stage)."""

    CLASSIFY = "classify"  # STG-01, FR-ING-08
