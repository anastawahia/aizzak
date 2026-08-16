"""Spaces value objects (pure — 06-domain-models §6).

One frozen, self-validating primitive. ``SpaceName`` checks only what the
STORED value must satisfy — the same three things the column's
``CHECK (char_length(name) BETWEEN 1 AND 120)`` and the platform's text rules
say (``docs/spaces-backend-plan.md`` §3.2) — and nothing configurable: there
is no per-workspace naming policy to consult, so unlike ``files`` there is no
limit left for the application layer to enforce.

**120 characters, and the number lives here as well as in the DDL.** A value
object that accepts what the column rejects turns a user's 200-character name
into a 500 from a CHECK constraint instead of a 422 with a reason. The
duplicate is deliberate and one-directional: the DDL is the authority, this is
the mirror that fails first and fails politely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.spaces.domain.errors import InvalidSpaceInput

_MAX_NAME_LEN = 120
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class SpaceName:
    """A trimmed display name: 1..120 characters, no control characters.

    Unlike ``FileName`` nothing is stripped but surrounding whitespace: a
    space name is a label a person typed, not a path, so ``a/b`` is a
    perfectly good name and cutting it at the slash would silently rename it.
    """

    value: str

    def __post_init__(self) -> None:
        trimmed = self.value.strip()
        if not trimmed:
            raise InvalidSpaceInput("space name must not be empty")
        if len(trimmed) > _MAX_NAME_LEN:
            raise InvalidSpaceInput(f"space name must be at most {_MAX_NAME_LEN} characters")
        if _CONTROL_CHAR_RE.search(trimmed):
            raise InvalidSpaceInput("space name must not contain control characters")
        object.__setattr__(self, "value", trimmed)
