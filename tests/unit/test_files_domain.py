"""Unit tests for the files domain — value objects, aggregate, invariants.
Pure: no infrastructure, no ports."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.modules.files.domain.entities import File
from app.modules.files.domain.errors import FileStateError, InvalidFileInput
from app.modules.files.domain.value_objects import (
    MAX_PARTS,
    MIN_PART_BYTES,
    ContentType,
    FileName,
    FileStatus,
    MultipartUpload,
    PartsCheck,
    Sha256,
    StorageKey,
    UploadMode,
    check_parts,
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
        space_id="s1",
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


# --------------------------------------------------------------------------- #
# FileName.extension + File.rename — the BE-RAG-006 policy (INV-F4)            #
# --------------------------------------------------------------------------- #
def test_extension_includes_its_dot() -> None:
    assert FileName("report.pdf").extension == ".pdf"


def test_extension_reads_only_the_last_dot() -> None:
    assert FileName("archive.tar.gz").extension == ".gz"


def test_a_dotfile_has_no_extension() -> None:
    """A LEADING dot is not an extension — `.env` is all stem."""
    assert FileName(".env").extension == ""


def test_a_trailing_dot_is_no_extension() -> None:
    """`report.` claims no type, so there is nothing to preserve."""
    assert FileName("report.").extension == ""


def test_a_name_without_a_dot_has_no_extension() -> None:
    assert FileName("README").extension == ""


def test_rename_changes_the_stem_and_stamps_updated_at() -> None:
    file = _file(FileStatus.READY)
    later = utc_now()
    file.rename(FileName("Q1 summary.pdf"), later)
    assert file.name.value == "Q1 summary.pdf"
    assert file.updated_at == later


def test_rename_inherits_the_current_extension_when_the_new_name_has_none() -> None:
    """What a person renaming `report.pdf` to `Q1 summary` means — and what
    every file manager does."""
    file = _file(FileStatus.READY)
    file.rename(FileName("Q1 summary"), utc_now())
    assert file.name.value == "Q1 summary.pdf"


def test_rename_refuses_a_different_extension() -> None:
    file = _file(FileStatus.READY)
    with pytest.raises(InvalidFileInput):
        file.rename(FileName("report.exe"), utc_now())
    assert file.name.value == "report.pdf"


def test_rename_accepts_a_matching_extension_in_any_case() -> None:
    """`.PDF` and `.pdf` are the same claim about the same bytes."""
    file = _file(FileStatus.READY)
    file.rename(FileName("report.PDF"), utc_now())
    assert file.name.value == "report.PDF"


def test_rename_refuses_adding_an_extension_to_a_name_that_had_none() -> None:
    """The rule is symmetric: inventing a claim about the bytes is the same
    lie as changing one."""
    file = _file(FileStatus.READY)
    file.name = FileName("README")
    with pytest.raises(InvalidFileInput):
        file.rename(FileName("README.md"), utc_now())


def test_rename_to_the_same_name_leaves_updated_at_alone() -> None:
    """A "modified at" that moves when nothing was modified is a false record."""
    file = _file(FileStatus.READY)
    stamped = file.updated_at
    file.rename(FileName("report.pdf"), utc_now())
    assert file.updated_at == stamped


def test_rename_that_only_inherits_back_the_same_name_is_also_a_no_op() -> None:
    file = _file(FileStatus.READY)
    stamped = file.updated_at
    file.rename(FileName("report"), utc_now())  # inherits `.pdf` -> unchanged
    assert file.name.value == "report.pdf"
    assert file.updated_at == stamped


def test_rename_strips_directory_components_like_registration_does() -> None:
    file = _file(FileStatus.READY)
    file.rename(FileName("../../etc/passwd.pdf"), utc_now())
    assert file.name.value == "passwd.pdf"


def test_rename_refuses_a_name_the_inherited_extension_pushes_over_the_limit() -> None:
    file = _file(FileStatus.READY)
    with pytest.raises(InvalidFileInput):
        file.rename(FileName("x" * 255), utc_now())


def test_rename_refuses_a_deleted_file() -> None:
    """A write is a write: the guard every other mutator uses."""
    file = _file(FileStatus.READY, deleted_at=utc_now())
    with pytest.raises(FileStateError):
        file.rename(FileName("late.pdf"), utc_now())


def test_rename_works_on_a_quarantined_file() -> None:
    """The name has no bearing on the status machine, and a flagged file is
    exactly the one whose name someone may need to correct."""
    file = _file(FileStatus.QUARANTINED)
    file.rename(FileName("suspect.pdf"), utc_now())
    assert file.name.value == "suspect.pdf"
    assert file.status is FileStatus.QUARANTINED


def test_no_mutator_moves_a_file_between_spaces() -> None:
    """Decision 3: a file does not move between spaces. The aggregate has no
    ``move``/``reassign``, so this walks the ENTIRE mutation surface — every
    state transition plus the one descriptive change ``rename`` allows — and
    asserts the space is the same on the other side. A mutator added later
    that touches ``space_id`` fails here even if it never touches the adapter.
    """
    file = _file(FileStatus.UPLOADED)
    assert file.space_id == "s1"

    file.mark_scanning(utc_now())
    file.complete(None, utc_now())
    file.rename(FileName("renamed.pdf"), utc_now())
    file.soft_delete(utc_now())

    assert file.space_id == "s1"


def test_quarantine_leaves_the_space_alone_too() -> None:
    """The other terminal transition, which the sequence above cannot reach
    (a completed file can no longer be quarantined)."""
    file = _file(FileStatus.SCANNING)
    file.quarantine(utc_now())
    assert file.space_id == "s1"


# --------------------------------------------------------------------------- #
# Multipart upload (PLT-01, INV-F5)                                            #
# --------------------------------------------------------------------------- #
_PART = MIN_PART_BYTES * 2  # 10 MiB: small enough to keep the arithmetic readable


def test_upload_mode_values() -> None:
    assert {m.value for m in UploadMode} == {"single", "multipart"}


def test_s3_constants() -> None:
    assert MIN_PART_BYTES == 5 * 1024 * 1024
    assert MAX_PARTS == 10_000


@pytest.mark.parametrize("upload_id", ["", "x" * 1025])
def test_multipart_upload_rejects_a_bad_upload_id(upload_id: str) -> None:
    with pytest.raises(InvalidFileInput):
        MultipartUpload(upload_id, _PART)


def test_multipart_upload_rejects_a_part_below_the_s3_minimum() -> None:
    with pytest.raises(InvalidFileInput):
        MultipartUpload("u", MIN_PART_BYTES - 1)
    assert MultipartUpload("u" * 1024, MIN_PART_BYTES).part_bytes == MIN_PART_BYTES


@pytest.mark.parametrize(
    ("size", "count", "last"),
    [
        (1, 1, 1),  # a single tiny part
        (0, 1, 0),  # the floor is one part
        (_PART - 1, 1, _PART - 1),
        (_PART, 1, _PART),  # exact multiple: no empty trailing part
        (_PART + 1, 2, 1),  # one byte over: a one-byte last part
        (_PART * 3, 3, _PART),
        (_PART * 3 + 7, 4, 7),
    ],
)
def test_part_count_and_expected_size(size: int, count: int, last: int) -> None:
    upload = MultipartUpload("u", _PART)
    assert upload.part_count(size) == count
    for n in range(1, count):
        assert upload.expected_size(n, size) == _PART
    assert upload.expected_size(count, size) == last
    assert sum(upload.expected_size(n, size) for n in range(1, count + 1)) == size


@pytest.mark.parametrize("number", [0, 3, -1])
def test_expected_size_rejects_a_part_outside_the_range(number: int) -> None:
    with pytest.raises(InvalidFileInput):
        MultipartUpload("u", _PART).expected_size(number, _PART + 1)


def test_check_parts_complete() -> None:
    upload = MultipartUpload("u", _PART)
    check = check_parts(upload, _PART + 5, {1: _PART, 2: 5})
    assert check == PartsCheck(missing=(), wrong_size=())
    assert check.complete


def test_check_parts_reports_missing_and_wrong_size_sorted() -> None:
    upload = MultipartUpload("u", _PART)
    check = check_parts(upload, _PART * 4 + 5, {4: _PART + 1, 2: _PART - 1, 5: 5})
    assert check.missing == (1, 3)
    assert check.wrong_size == (2, 4)
    assert not check.complete


def test_check_parts_ignores_parts_beyond_the_count() -> None:
    upload = MultipartUpload("u", _PART)
    check = check_parts(upload, _PART, {1: _PART, 2: 123, 99: 1})
    assert check.complete


def test_check_parts_treats_a_zero_byte_part_as_held() -> None:
    upload = MultipartUpload("u", _PART)
    assert check_parts(upload, 0, {1: 0}).complete
    assert check_parts(upload, 0, {}).missing == (1,)


def test_file_is_single_put_by_default_and_multipart_when_given_a_session() -> None:
    single = _file()
    assert single.multipart is None
    assert single.is_multipart is False
    single.multipart = MultipartUpload("u", _PART)
    assert single.is_multipart is True
