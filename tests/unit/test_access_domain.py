"""Unit tests for the access domain — the RBAC catalog and the pure decision.
Pure: no ports, no infrastructure. Guards the approved matrix (05 §1.3)."""

from __future__ import annotations

import pytest

from app.modules.access.domain.value_objects import (
    Permission,
    RoleCatalog,
    RoleName,
    permission_granted,
)


def test_owner_holds_all_tenant_permissions_but_not_platform_admin() -> None:
    owner = RoleCatalog[RoleName.OWNER]
    assert Permission.WORKSPACE_TRANSFER in owner
    assert Permission.WORKSPACE_ARCHIVE in owner
    assert Permission.PLATFORM_ADMIN not in owner
    assert len(owner) == len(Permission) - 1


def test_admin_lacks_transfer_and_archive() -> None:
    admin = RoleCatalog[RoleName.ADMIN]
    assert Permission.WORKSPACE_MANAGE in admin
    assert Permission.WORKSPACE_TRANSFER not in admin
    assert Permission.WORKSPACE_ARCHIVE not in admin


def test_viewer_is_read_only() -> None:
    viewer = RoleCatalog[RoleName.VIEWER]
    assert Permission.FILES_READ in viewer
    assert Permission.FILES_WRITE not in viewer
    assert Permission.AGENTS_INVOKE not in viewer


def test_platform_admin_sees_no_tenant_content() -> None:
    catalog = RoleCatalog[RoleName.PLATFORM_ADMIN]
    assert Permission.PLATFORM_ADMIN in catalog
    assert Permission.ACCESS_READ in catalog
    assert Permission.CONVERSATIONS_READ not in catalog
    assert Permission.FILES_READ not in catalog


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (RoleName.OWNER, Permission.WORKSPACE_TRANSFER, True),
        (RoleName.OWNER, Permission.PLATFORM_ADMIN, False),
        (RoleName.ADMIN, Permission.WORKSPACE_TRANSFER, False),
        (RoleName.ADMIN, Permission.ACCESS_MANAGE, True),
        (RoleName.MEMBER, Permission.AGENTS_INVOKE, True),
        (RoleName.MEMBER, Permission.FILES_DELETE, False),
        (RoleName.MEMBER, Permission.CREDENTIALS_READ, False),
        (RoleName.VIEWER, Permission.FILES_READ, True),
        (RoleName.VIEWER, Permission.AGENTS_INVOKE, False),
        (RoleName.PLATFORM_ADMIN, Permission.PLATFORM_ADMIN, True),
        (RoleName.PLATFORM_ADMIN, Permission.FILES_READ, False),
    ],
)
def test_permission_matrix(role: RoleName, permission: Permission, expected: bool) -> None:
    assert permission_granted(frozenset({role}), permission) is expected


def test_permission_granted_unions_roles() -> None:
    roles = frozenset({RoleName.VIEWER, RoleName.MEMBER})
    assert permission_granted(roles, Permission.FILES_WRITE) is True  # from member


def test_permission_granted_empty_roles_denies() -> None:
    assert permission_granted(frozenset(), Permission.WORKSPACE_READ) is False
