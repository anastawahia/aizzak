"""Workspace value objects (pure — 06-domain-models §1).

Frozen, self-validating primitives: ``WorkspaceName``, ``Email`` and the two
status enums. Construction is the only validation gate, so an instance is always
valid. Identifiers are plain UUIDv7 text (``str``); the domain never imports the
framework ``Uuid`` alias (import-linter contract 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.workspace.domain.errors import InvalidWorkspaceInput

_MAX_NAME_LEN = 80
# RFC 5322 "lite": exactly one @, non-empty local part, dotted domain. Pragmatic,
# not exhaustive — deep validation belongs to the identity provider, not us.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WorkspaceStatus(StrEnum):
    """Lifecycle of a tenant workspace."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class UserStatus(StrEnum):
    """Lifecycle of a user within a workspace."""

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class WorkspaceName:
    """A trimmed, non-empty display name of 1..80 characters."""

    value: str

    def __post_init__(self) -> None:
        trimmed = self.value.strip()
        if not trimmed:
            raise InvalidWorkspaceInput("workspace name must not be empty")
        if len(trimmed) > _MAX_NAME_LEN:
            raise InvalidWorkspaceInput(
                f"workspace name must be at most {_MAX_NAME_LEN} characters"
            )
        object.__setattr__(self, "value", trimmed)


@dataclass(frozen=True, slots=True)
class Email:
    """A lower-cased, syntactically valid email address (RFC 5322 lite)."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise InvalidWorkspaceInput("invalid email address")
        object.__setattr__(self, "value", normalized)
