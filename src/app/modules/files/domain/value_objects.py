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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.modules.files.domain.errors import InvalidFileInput

_MAX_NAME_LEN = 255
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
# Basic ``type/subtype`` MIME syntax (RFC 2045 token subset). The whitelist of
# *allowed* types is a configurable business limit, checked by the application,
# not here.
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_UPLOAD_ID_LEN = 1024

# S3 protocol limits (PLT-01): every part but the last is at least 5 MiB, and an
# upload has at most 10,000 parts. `framework.settings.settings` restates both
# (the framework may not import a module); a test pins them equal.
MIN_PART_BYTES: Final = 5_242_880
MAX_PARTS: Final = 10_000


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

    @property
    def extension(self) -> str:
        """The extension INCLUDING its dot (``".pdf"``), or ``""`` for a name
        that has none.

        Structural, like everything else here: it reports what the name *says*
        and judges nothing. A LEADING dot is not an extension — ``".env"`` is a
        name whose whole value is its stem, the convention every filesystem
        uses — and neither is a trailing one, since ``"report."`` claims no
        type. The rule that this string may not CHANGE across a rename
        (INV-F4) is the aggregate's, not the value object's: it needs two
        names to compare, and a value object only ever sees one.
        """
        dot = self.value.rfind(".")
        if dot <= 0 or dot == len(self.value) - 1:
            return ""
        return self.value[dot:]


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


class UploadMode(StrEnum):
    """How the bytes of a registered file arrive (FR-ING-02)."""

    SINGLE = "single"  # one presigned PUT, <= Limits.max_single_put_bytes
    MULTIPART = "multipart"  # presigned UploadPart URLs, assembled at completion


@dataclass(frozen=True, slots=True)
class MultipartUpload:
    """The storage-side multipart session of one file. Fixed at registration
    and never re-pointed (INV-F5), like every field that describes the bytes."""

    upload_id: str
    part_bytes: int

    def __post_init__(self) -> None:
        if not self.upload_id or len(self.upload_id) > _MAX_UPLOAD_ID_LEN:
            raise InvalidFileInput(f"upload id must be 1..{_MAX_UPLOAD_ID_LEN} characters")
        if self.part_bytes < MIN_PART_BYTES:
            raise InvalidFileInput(f"part size must be at least {MIN_PART_BYTES} bytes")

    def part_count(self, size_bytes: int) -> int:
        """``max(1, ceil(size_bytes / part_bytes))``."""
        return max(1, -(-size_bytes // self.part_bytes))

    def expected_size(self, part_number: int, size_bytes: int) -> int:
        """``part_bytes`` for every part but the last; the remainder for the last."""
        count = self.part_count(size_bytes)
        if not 1 <= part_number <= count:
            raise InvalidFileInput(f"part number must be 1..{count}")
        if part_number < count:
            return self.part_bytes
        return size_bytes - (count - 1) * self.part_bytes


@dataclass(frozen=True, slots=True)
class PartsCheck:
    """What a comparison of stored parts against the declared size found."""

    missing: tuple[int, ...]  # sorted part numbers storage does not hold
    wrong_size: tuple[int, ...]  # sorted part numbers of an unexpected size

    @property
    def complete(self) -> bool:
        return not self.missing and not self.wrong_size


def check_parts(
    upload: MultipartUpload, size_bytes: int, uploaded: Mapping[int, int]
) -> PartsCheck:
    """Compare what storage holds (``{part_number: size}``) with what the declared
    size requires. Parts numbered above ``part_count`` are ignored: no URL is ever
    signed for one, and S3 drops unlisted parts at completion."""
    missing: list[int] = []
    wrong_size: list[int] = []
    for number in range(1, upload.part_count(size_bytes) + 1):
        held = uploaded.get(number)
        if held is None:
            missing.append(number)
        elif held != upload.expected_size(number, size_bytes):
            wrong_size.append(number)
    return PartsCheck(missing=tuple(missing), wrong_size=tuple(wrong_size))
