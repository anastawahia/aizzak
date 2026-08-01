"""Files value objects (pure — 06-domain-models §6).

Frozen, self-validating primitives. ``FileName``/``ContentType``/``StorageKey``/
``Sha256`` only check *structural* invariants at construction — the
*configurable* business limits (whitelist, size, per-workspace cap; 07-nfr-slo
§4) are deliberately left to the application layer (``RegisterUpload``), which
enforces them against an injected ``Limits`` instead of hard-coding them here.
Identifiers stay plain UUIDv7 text; the domain imports no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.files.domain.errors import InvalidFileInput

_MAX_NAME_LEN = 255
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
# Basic ``type/subtype`` MIME syntax (RFC 2045 token subset). The whitelist of
# *allowed* types is a configurable business limit, checked by the application,
# not here.
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FileStatus(StrEnum):
    """Lifecycle of an uploaded file (06-domain-models §6)."""

    UPLOADED = "uploaded"
    SCANNING = "scanning"
    READY = "ready"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class FileName:
    """A sanitized basename: any directory component is stripped (INV: cleaned
    of paths), trimmed, 1..255 characters, no control characters."""

    value: str

    def __post_init__(self) -> None:
        # Strip any directory component: cut after the last '/' or '\', whichever
        # comes later — a plain str/rfind approach keeps the type unambiguously
        # ``str`` (unlike ``re.Pattern.split``, whose stub widens to ``Any``).
        cut = max(self.value.rfind("/"), self.value.rfind("\\"))
        basename = self.value[cut + 1 :].strip()
        if not basename:
            raise InvalidFileInput("file name must not be empty")
        if len(basename) > _MAX_NAME_LEN:
            raise InvalidFileInput(f"file name must be at most {_MAX_NAME_LEN} characters")
        if _CONTROL_CHAR_RE.search(basename):
            raise InvalidFileInput("file name must not contain control characters")
        object.__setattr__(self, "value", basename)


@dataclass(frozen=True, slots=True)
class ContentType:
    """A lowercased, syntactically valid ``type/subtype`` MIME string.

    Only structural MIME syntax is validated here; whether the type is on the
    configured whitelist is a business limit enforced by the application
    (``RegisterUpload`` against ``Limits.allowed_mime``).
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _MIME_RE.match(normalized):
            raise InvalidFileInput(f"invalid content type: {self.value!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class StorageKey:
    """A MinIO object key, prefixed by a non-empty ``workspace_id/`` (INV-F1)."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvalidFileInput("storage key must not be empty")
        prefix, sep, suffix = self.value.partition("/")
        if not sep or not prefix or not suffix:
            raise InvalidFileInput("storage key must be prefixed by 'workspace_id/'")

    @classmethod
    def for_file(cls, workspace_id: str, file_id: str) -> StorageKey:
        """Build the canonical key for a newly registered file."""
        return cls(f"{workspace_id}/{file_id}")


@dataclass(frozen=True, slots=True)
class Sha256:
    """A lowercased, exactly-64-hex-character SHA-256 digest."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _SHA256_RE.match(normalized):
            raise InvalidFileInput("checksum must be a 64-character hex sha256 digest")
        object.__setattr__(self, "value", normalized)
