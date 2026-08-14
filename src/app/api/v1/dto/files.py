"""Files DTOs — the wire shapes of ``/api/v1/files`` (03-api-spec §2, Phase
6.1-هـ-3).

Verbatim from the spec's Pydantic sketch. ``FileRegisterOut.expires_in`` is
the presigned PUT's lifetime in seconds (the application layer's constant,
§3.60); ``FileOut.download_url`` is a presigned GET when the file is ``ready``
and ``null`` otherwise — a present-but-not-ready file is a BODY with its
``status``, never a problem response (the §3.58 status-is-a-field logic).
``FileCompleteIn.checksum`` is optional end to end (03 §2 → the use-case → the
domain, §3.60): its whole BODY is optional on the route, so the DTO's default
must produce the same completion an absent body does.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FileRegisterIn(BaseModel):
    name: str
    content_type: str
    size_bytes: int


class FileRegisterOut(BaseModel):
    file_id: str
    upload_url: str
    expires_in: int


class FileCompleteIn(BaseModel):
    checksum: str | None = None


class FileRenameIn(BaseModel):
    """The one mutable field a file has. Required, not optional: a PATCH body
    that may legally be empty asks the server to guess what "no change" means,
    and the sanitation/extension policy that decides what this string becomes
    lives in the domain (``File.rename``), not in a Pydantic constraint."""

    name: str


class FileOut(BaseModel):
    id: str
    name: str
    content_type: str
    size_bytes: int
    status: str
    download_url: str | None
    created_at: datetime
