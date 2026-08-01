"""Unit tests for the files domain — value objects, aggregate, invariants.
Pure: no infrastructure, no ports."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.modules.files.domain.entities import File
from app.modules.files.domain.errors import FileStateError, InvalidFileInput
from app.modules.files.domain.value_objects import (
    ContentType,
    FileName,
    FileStatus,
    Sha256,
    StorageKey,
)

_SHA = "a" * 64


# --------------------------------------------------------------------------- #
# FileName                                                                     #
# --------------------------------------------------------------------------- #
def test_file_name_strips_directory_components() -> None:
    assert FileName("../a/b/x.pdf").value == "x.pdf"


def test_file_name_strips_windows_style_path() -> None:
    assert FileName("C:\\Users\\a\\report.docx").value == "report.docx"


def test_file_name_trims_whitespace() -> None:
    assert FileName("  report.pdf  ").value == "report.pdf"


def test_file_name_accepts_boundary_length() -> None:
    assert len(FileName("x" * 255).value) == 255


def test_file_name_rejects_too_long() -> None:
    with pytest.raises(InvalidFileInput):
        FileName("x" * 256)


def test_file_name_rejects_blank() -> None:
    with pytest.raises(InvalidFileInput):
        FileName("   ")


def test_file_name_rejects_path_that_reduces_to_empty() -> None:
    with pytest.raises(InvalidFileInput):
        FileName("a/b/")


def test_file_name_rejects_control_characters() -> None:
    with pytest.raises(InvalidFileInput):
        FileName("bad\x00name.txt")


# --------------------------------------------------------------------------- #
# ContentType                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    ["application/pdf", "text/plain", "IMAGE/PNG", "image/vnd.foo+xml", "a/b.c-d"],
)
def test_content_type_accepts_valid_syntax(value: str) -> None:
    assert ContentType(value).value == value.strip().lower()


@pytest.mark.parametrize("value", ["", "text", "text/", "/plain", "text plain/x", "a//b"])
def test_content_type_rejects_invalid_syntax(value: str) -> None:
    with pytest.raises(InvalidFileInput):
        ContentType(value)


# --------------------------------------------------------------------------- #
# StorageKey                                                                   #
# --------------------------------------------------------------------------- #
def test_storage_key_for_file_builds_workspace_prefixed_key() -> None:
    key = StorageKey.for_file("ws1", "file1")
    assert key.value == "ws1/file1"


@pytest.mark.parametrize("value", ["", "no-slash", "/suffixonly", "prefixonly/"])
def test_storage_key_rejects_keyless_strings(value: str) -> None:
    with pytest.raises(InvalidFileInput):
        StorageKey(value)


# --------------------------------------------------------------------------- #
# Sha256                                                                       #
# --------------------------------------------------------------------------- #
def test_sha256_normalizes_case() -> None:
    assert Sha256("A" * 64).value == "a" * 64


@pytest.mark.parametrize("value", ["", "abc", "g" * 64, "a" * 63, "a" * 65])
def test_sha256_rejects_invalid(value: str) -> None:
    with pytest.raises(InvalidFileInput):
        Sha256(value)


# --------------------------------------------------------------------------- #
# File aggregate                                                               #
# --------------------------------------------------------------------------- #
def _file(status: FileStatus = FileStatus.UPLOADED, deleted_at: datetime | None = None) -> File:
    now = utc_now()
    return File(
        id="f1",
        workspace_id="w1",
        name=FileName("report.pdf"),
        content_type=ContentType("application/pdf"),
        size_bytes=1024,
        storage_key=StorageKey.for_file("w1", "f1"),
        checksum=None,
        status=status,
        uploaded_by="u1",
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        version=1,
    )


def test_complete_from_uploaded_sets_ready_and_checksum() -> None:
    file = _file(FileStatus.UPLOADED)
    file.complete(Sha256(_SHA), utc_now())
    assert file.status is FileStatus.READY
    assert file.checksum is not None
    assert file.checksum.value == _SHA


def test_complete_from_scanning_sets_ready() -> None:
    file = _file(FileStatus.SCANNING)
    file.complete(Sha256(_SHA), utc_now())
    assert file.status is FileStatus.READY


def test_complete_on_quarantined_raises() -> None:
    file = _file(FileStatus.QUARANTINED)
    with pytest.raises(FileStateError):
        file.complete(Sha256(_SHA), utc_now())


def test_complete_on_soft_deleted_raises() -> None:
    file = _file(FileStatus.UPLOADED, deleted_at=utc_now())
    with pytest.raises(FileStateError):
        file.complete(Sha256(_SHA), utc_now())


def test_mark_scanning_transitions_from_uploaded() -> None:
    file = _file(FileStatus.UPLOADED)
    file.mark_scanning(utc_now())
    assert file.status is FileStatus.SCANNING


def test_quarantine_transitions_from_scanning() -> None:
    file = _file(FileStatus.SCANNING)
    file.quarantine(utc_now())
    assert file.status is FileStatus.QUARANTINED


def test_soft_delete_is_idempotent() -> None:
    file = _file(FileStatus.READY)
    first = utc_now()
    file.soft_delete(first)
    assert file.deleted_at == first
    file.soft_delete(utc_now())
    assert file.deleted_at == first  # no-op: timestamp unchanged


def test_is_ready_true_only_when_ready_and_not_deleted() -> None:
    assert _file(FileStatus.READY).is_ready is True
    assert _file(FileStatus.UPLOADED).is_ready is False
    assert _file(FileStatus.READY, deleted_at=utc_now()).is_ready is False
