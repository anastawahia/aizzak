"""Access value objects + the static RBAC catalog (pure — 06-domain-models §2,
05-rbac-config-secrets §1).

``RoleName`` and ``Permission`` are closed vocabularies; ``RoleCatalog`` is the
authoritative, code-static role→permissions matrix (D-24); ``permission_granted``
is the pure authorization decision (no I/O). The catalog mirrors the approved
matrix in 05 §1.3 verbatim — owners hold every in-workspace permission but never
``platform:admin``; admins additionally lack ownership transfer and archival.
"""

from __future__ import annotations

from enum import StrEnum


class RoleName(StrEnum):
    """Assignable roles (05 §1.1)."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    PLATFORM_ADMIN = "platform_admin"


class Permission(StrEnum):
    """The closed ``<resource>:<action>`` permission catalog (05 §1.2)."""

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_MANAGE = "workspace:manage"
    WORKSPACE_TRANSFER = "workspace:transfer"
    WORKSPACE_ARCHIVE = "workspace:archive"
    AGENTS_READ = "agents:read"
    AGENTS_INVOKE = "agents:invoke"
    CONVERSATIONS_READ = "conversations:read"
    CONVERSATIONS_WRITE = "conversations:write"
    CONVERSATIONS_DELETE = "conversations:delete"
    FILES_READ = "files:read"
    FILES_WRITE = "files:write"
    FILES_DELETE = "files:delete"
    # `docs/spaces-backend-plan.md` §3.7 — TWO permissions for four routes, so
    # `spaces:write` covers create, rename AND delete. That is the plan's
    # decision and it is honoured here; what it costs is spelled out at
    # `RoleCatalog` below, where the members-of-a-workspace question is
    # actually answered.
    SPACES_READ = "spaces:read"
    SPACES_WRITE = "spaces:write"
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    MEDIA_READ = "media:read"
    MEDIA_CREATE = "media:create"
    WORKFLOWS_READ = "workflows:read"
    WORKFLOWS_RUN = "workflows:run"
    CREDENTIALS_READ = "credentials:read"
    CREDENTIALS_MANAGE = "credentials:manage"
    INTEGRATIONS_READ = "integrations:read"
    INTEGRATIONS_MANAGE = "integrations:manage"
    USAGE_READ = "usage:read"
    USAGE_MANAGE = "usage:manage"
    ACCESS_READ = "access:read"
    ACCESS_MANAGE = "access:manage"
    PLATFORM_ADMIN = "platform:admin"


# Owner: every in-workspace permission; never the cross-tenant platform:admin.
_ALL_TENANT_PERMISSIONS = frozenset(Permission) - {Permission.PLATFORM_ADMIN}

# `spaces:write` is deliberately NOT a member's, and the reason is the cascade,
# not the create. `DELETE /api/v1/spaces/{id}` erases every file, conversation,
# document and vector the space owns (`spaces-backend-plan.md` §3.6), and the
# route guard is one permission per route by construction (`middleware/rbac.py`
# — `test_api_rbac.py` pins exactly one guard per operation). So whichever
# permission opens that route is, in effect, `files:delete` and
# `conversations:delete` together — both of which this matrix withholds from a
# member on purpose. Granting `spaces:write` to `member` would hand back, in
# one call and irreversibly, precisely what two rows below refuse one file and
# one thread at a time.
#
# Members and viewers therefore get `spaces:read` — enough to LIST the spaces
# and to name one in `?space_id=`, which is what every other route now
# requires of them — and owners/admins own the structure itself. Widening this
# later is a matrix edit; narrowing it after the fact is a revocation, and the
# safe direction to be wrong in is this one.

RoleCatalog: dict[RoleName, frozenset[Permission]] = {
    RoleName.OWNER: _ALL_TENANT_PERMISSIONS,
    # Admin: full management minus ownership transfer and workspace archival.
    RoleName.ADMIN: _ALL_TENANT_PERMISSIONS
    - {Permission.WORKSPACE_TRANSFER, Permission.WORKSPACE_ARCHIVE},
    RoleName.MEMBER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.AGENTS_READ,
            Permission.AGENTS_INVOKE,
            Permission.CONVERSATIONS_READ,
            Permission.CONVERSATIONS_WRITE,
            Permission.FILES_READ,
            Permission.FILES_WRITE,
            Permission.SPACES_READ,
            Permission.KNOWLEDGE_READ,
            Permission.MEDIA_READ,
            Permission.MEDIA_CREATE,
            Permission.WORKFLOWS_READ,
            Permission.WORKFLOWS_RUN,
            Permission.INTEGRATIONS_READ,
            Permission.USAGE_READ,
        }
    ),
    RoleName.VIEWER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.AGENTS_READ,
            Permission.CONVERSATIONS_READ,
            Permission.FILES_READ,
            Permission.SPACES_READ,
            Permission.KNOWLEDGE_READ,
            Permission.MEDIA_READ,
            Permission.WORKFLOWS_READ,
            Permission.INTEGRATIONS_READ,
            Permission.USAGE_READ,
        }
    ),
    # Platform admin: cross-tenant support only — never tenant content (05 §1.3).
    RoleName.PLATFORM_ADMIN: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.WORKSPACE_MANAGE,
            Permission.AGENTS_READ,
            Permission.ACCESS_READ,
            Permission.PLATFORM_ADMIN,
        }
    ),
}


def permission_granted(roles: frozenset[RoleName], permission: Permission) -> bool:
    """Pure decision: ``True`` iff any of ``roles`` grants ``permission`` (INV: union)."""
    return any(permission in RoleCatalog[role] for role in roles)
