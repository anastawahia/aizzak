"""Conversations value objects (pure — 06-domain-models §4).

Frozen, self-validating primitives: ``AgentKey`` and ``MessageContent``, plus
the two closed vocabularies ``ConversationKind``/``MessageRole``. Construction
is the only validation gate, so an instance is always valid. Identifiers are
plain UUIDv7 text (``str``); the domain never imports the framework ``Uuid``
alias (import-linter contract 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.conversations.domain.errors import InvalidConversationInput

_MAX_AGENT_KEY_LEN = 64
# Slug: starts with a lowercase letter/digit, then any run of lowercase
# letters/digits/underscore/hyphen (06-domain-models §4).
_AGENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConversationKind(StrEnum):
    """Whether a conversation is threaded under an agent or a workflow run."""

    AGENT = "agent"
    WORKFLOW = "workflow"


class MessageRole(StrEnum):
    """The speaker of one message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class AgentKey:
    """The agent (or workflow) slug a conversation is threaded under.

    Deliberately self-contained to this module (06-domain-models §4 decision
    for the ``conversations`` slice): each module that needs an agent/workflow
    slug validates its own copy rather than depending on a shared kernel type.
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not normalized:
            raise InvalidConversationInput("agent key must not be empty")
        if len(normalized) > _MAX_AGENT_KEY_LEN:
            raise InvalidConversationInput(
                f"agent key must be at most {_MAX_AGENT_KEY_LEN} characters"
            )
        if not _AGENT_KEY_RE.match(normalized):
            raise InvalidConversationInput(f"invalid agent key: {self.value!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class MessageContent:
    """A message's text plus optional attachments.

    Attachments are ``file_id`` references only (INV-CV2) — the ``files``
    module owns the actual bytes; this value object never carries content.
    """

    text: str
    attachments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        trimmed_text = self.text.strip()
        attachments = tuple(self.attachments)
        if any(not attachment.strip() for attachment in attachments):
            raise InvalidConversationInput("attachment references must not be empty")
        if not trimmed_text and not attachments:
            raise InvalidConversationInput("message content requires text or an attachment")
        object.__setattr__(self, "text", trimmed_text)
        object.__setattr__(self, "attachments", attachments)
