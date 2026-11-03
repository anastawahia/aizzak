"""The WireTwin project aggregate (LLD 3.3.6, SRS D-29: one project per space). Stdlib only."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.modules.electrical.drawings.domain.errors import InvalidDrawingInput

MAX_NAME_CHARS: Final = 200
MAX_DESCRIPTION_CHARS: Final = 2_000


def normalize_project_name(raw: str) -> str:
    """NFC, trimmed, 1..200 characters, no control character (sanitised like ``FileName``)."""
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        raise InvalidDrawingInput("project name cannot be empty")
    if len(name) > MAX_NAME_CHARS:
        raise InvalidDrawingInput(f"project name is at most {MAX_NAME_CHARS} characters")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") for ch in name):
        raise InvalidDrawingInput("project name cannot contain control characters")
    return name


def normalize_description(raw: str | None) -> str | None:
    """NFC and trimmed, at most 2,000 characters; blank is no description."""
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw).strip()
    if not text:
        return None
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise InvalidDrawingInput(f"description is at most {MAX_DESCRIPTION_CHARS} characters")
    return text


@dataclass(slots=True)
class Project:
    id: str
    workspace_id: str
    space_id: str  # one project per space (SRS D-29); never moves
    name: str  # 1..200, sanitised like FileName
    description: str | None  # <= 2,000
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def rename(self, name: str, now: datetime) -> None:
        """A no-op (no ``updated_at`` bump) when the name does not change."""
        normalized = normalize_project_name(name)
        if normalized == self.name:
            return
        self.name = normalized
        self.updated_at = now

    def describe(self, description: str | None, now: datetime) -> None:
        normalized = normalize_description(description)
        if normalized == self.description:
            return
        self.description = normalized
        self.updated_at = now
