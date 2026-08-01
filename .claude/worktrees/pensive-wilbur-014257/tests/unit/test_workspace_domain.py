"""Unit tests for the workspace domain — value objects, aggregates, invariants.
Pure: no infrastructure, no ports."""

from __future__ import annotations

import pytest

from app.framework.clock import utc_now
from app.modules.workspace.domain.entities import Workspace
from app.modules.workspace.domain.errors import (
    InvalidWorkspaceInput,
    WorkspaceArchivedError,
)
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)


# --------------------------------------------------------------------------- #
# value objects                                                                #
# --------------------------------------------------------------------------- #
def test_workspace_name_trims_and_accepts_valid() -> None:
    assert WorkspaceName("  Acme  ").value == "Acme"


def test_workspace_name_accepts_boundary_length() -> None:
    assert len(WorkspaceName("x" * 80).value) == 80


def test_workspace_name_rejects_blank() -> None:
    with pytest.raises(InvalidWorkspaceInput):
        WorkspaceName("   ")


def test_workspace_name_rejects_too_long() -> None:
    with pytest.raises(InvalidWorkspaceInput):
        WorkspaceName("x" * 81)


def test_email_normalizes_case_and_whitespace() -> None:
    assert Email("  User@Example.COM ").value == "user@example.com"


@pytest.mark.parametrize("bad", ["no-at", "a@b", "a@@b.com", "a b@c.com", ""])
def test_email_rejects_invalid(bad: str) -> None:
    with pytest.raises(InvalidWorkspaceInput):
        Email(bad)


def test_status_enum_values() -> None:
    assert WorkspaceStatus.ACTIVE.value == "active"
    assert UserStatus.DISABLED.value == "disabled"


# --------------------------------------------------------------------------- #
# workspace aggregate                                                          #
# --------------------------------------------------------------------------- #
def _workspace(status: WorkspaceStatus = WorkspaceStatus.ACTIVE) -> Workspace:
    now = utc_now()
    return Workspace(
        id="w1",
        owner_user_id="u1",
        name=WorkspaceName("Acme"),
        status=status,
        created_at=now,
        updated_at=now,
        version=1,
    )


def test_rename_updates_name() -> None:
    ws = _workspace()
    ws.rename(WorkspaceName("Renamed"), utc_now())
    assert ws.name.value == "Renamed"


def test_rename_on_archived_raises() -> None:
    ws = _workspace(WorkspaceStatus.ARCHIVED)
    with pytest.raises(WorkspaceArchivedError):
        ws.rename(WorkspaceName("Renamed"), utc_now())


def test_archive_sets_status() -> None:
    ws = _workspace()
    ws.archive(utc_now())
    assert ws.status is WorkspaceStatus.ARCHIVED


def test_archive_is_idempotent_and_preserves_timestamp() -> None:
    ws = _workspace(WorkspaceStatus.ARCHIVED)
    marker = ws.updated_at
    ws.archive(utc_now())
    assert ws.status is WorkspaceStatus.ARCHIVED
    assert ws.updated_at == marker  # a no-op archive leaves updated_at untouched
