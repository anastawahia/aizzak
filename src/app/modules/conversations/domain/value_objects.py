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

# th-1 -- at most this many citations on one message; more is malformed (stored NULL).
MAX_CITATIONS = 50
_MAX_CITED_FILE_NAME_LEN = 255  # the files module's own bound (files/domain/value_objects.py)
# th-2 -- bound on each provider/model name a fallback stores. Route-level
# names from the model catalogue are far shorter; the bound keeps the jsonb
# small and mirrors _MAX_CITED_FILE_NAME_LEN.
_MAX_FALLBACK_NAME_LEN = 255
# Canonical 8-4-4-4-12 text. Matched, never normalised: the stored string must
# equal the one on the `final` frame.
_UUID_TEXT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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


def _require_uuid_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not _UUID_TEXT_RE.match(value):
        raise InvalidConversationInput(f"citation {name} must be a UUID")


def _require_positive_int(name: str, value: object) -> None:
    # `type(...) is int` rejects bool (True is an int in Python).
    if type(value) is not int or value < 1:
        raise InvalidConversationInput(f"citation {name} must be an integer >= 1")


@dataclass(frozen=True, slots=True)
class Citation:
    """One source an assistant reply was answered from (th-1, 06 section 4).

    A SNAPSHOT taken at answer time: ``file_name`` is the name the chunk
    carried then, and is never refreshed from ``files``. Values are stored
    verbatim -- nothing is stripped, lower-cased or re-ranked.
    """

    document_id: str
    file_name: str | None
    page: int | None
    chunk_id: str
    rank: int

    def __post_init__(self) -> None:
        _require_uuid_text("document_id", self.document_id)
        _require_uuid_text("chunk_id", self.chunk_id)
        if self.file_name is not None and (
            not isinstance(self.file_name, str)
            or not self.file_name
            or len(self.file_name) > _MAX_CITED_FILE_NAME_LEN
        ):
            raise InvalidConversationInput(
                f"citation file_name must be 1..{_MAX_CITED_FILE_NAME_LEN} characters"
            )
        if self.page is not None:
            _require_positive_int("page", self.page)
        _require_positive_int("rank", self.rank)


class MessageStatus(StrEnum):
    """How the assistant turn that wrote a message ended (th-2, 06 section 4).

    Closed on purpose: there is no `error` -- no code path stores a message
    for a turn that produced nothing (owner decision q-3). A failure with no
    message is th-3's attempt record, not a value here.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class MessageFallback:
    """The 6.4 switch an assistant message was answered under (th-2).

    Four route-level names -- what `final.fallback` carries -- and nothing
    else: no credential can be represented. A snapshot at answer time, like
    `Citation`: a later change of LLM_FALLBACK_ROUTE does not rewrite it.
    Stored verbatim (not stripped, not lower-cased) so it equals the frame.
    """

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str

    def __post_init__(self) -> None:
        for name in ("from_provider", "from_model", "to_provider", "to_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > _MAX_FALLBACK_NAME_LEN:
                raise InvalidConversationInput(
                    f"fallback {name} must be 1..{_MAX_FALLBACK_NAME_LEN} characters"
                )
