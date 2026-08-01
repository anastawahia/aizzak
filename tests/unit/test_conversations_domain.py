"""Unit tests for the conversations domain — value objects, aggregates,
invariants. Pure: no infrastructure, no ports."""

from __future__ import annotations

import pytest

from app.framework.clock import utc_now
from app.modules.conversations.domain.entities import Conversation, Message
from app.modules.conversations.domain.errors import (
    ConversationDeletedError,
    InvalidConversationInput,
)
from app.modules.conversations.domain.value_objects import (
    AgentKey,
    ConversationKind,
    MessageContent,
    MessageRole,
)


# --------------------------------------------------------------------------- #
# value objects                                                                #
# --------------------------------------------------------------------------- #
def test_agent_key_normalizes_case_and_whitespace() -> None:
    assert AgentKey("  RAG-Agent  ").value == "rag-agent"


def test_agent_key_accepts_boundary_length() -> None:
    assert len(AgentKey("a" * 64).value) == 64


def test_agent_key_accepts_single_char() -> None:
    assert AgentKey("a").value == "a"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "a" * 65, "-leading-hyphen", "_leading-underscore", "has space", "Bad!Char"],
)
def test_agent_key_rejects_invalid(bad: str) -> None:
    with pytest.raises(InvalidConversationInput):
        AgentKey(bad)


def test_message_content_accepts_text_only() -> None:
    content = MessageContent(text="  hello  ")
    assert content.text == "hello"
    assert content.attachments == ()


def test_message_content_accepts_attachments_only() -> None:
    content = MessageContent(text="", attachments=("file-1", "file-2"))
    assert content.text == ""
    assert content.attachments == ("file-1", "file-2")


def test_message_content_rejects_empty_text_and_no_attachments() -> None:
    with pytest.raises(InvalidConversationInput):
        MessageContent(text="   ")


def test_message_content_rejects_blank_attachment() -> None:
    with pytest.raises(InvalidConversationInput):
        MessageContent(text="hi", attachments=("  ",))


def test_conversation_kind_and_message_role_values() -> None:
    assert ConversationKind.WORKFLOW.value == "workflow"
    assert MessageRole.TOOL.value == "tool"


# --------------------------------------------------------------------------- #
# conversation aggregate                                                       #
# --------------------------------------------------------------------------- #
def _conversation(deleted: bool = False) -> Conversation:
    now = utc_now()
    return Conversation(
        id="c1",
        workspace_id="w1",
        agent_key=AgentKey("rag-agent"),
        kind=ConversationKind.AGENT,
        title=None,
        created_by="u1",
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
        version=1,
        message_count=0,
    )


def test_append_message_assigns_ascending_seq() -> None:
    conv = _conversation()
    now = utc_now()
    m1 = conv.append_message("m1", MessageRole.USER, MessageContent(text="hi"), None, now)
    m2 = conv.append_message("m2", MessageRole.ASSISTANT, MessageContent(text="yo"), None, now)
    m3 = conv.append_message("m3", MessageRole.USER, MessageContent(text="again"), 5, now)
    assert (m1.seq, m2.seq, m3.seq) == (1, 2, 3)
    assert conv.message_count == 3


def test_append_message_returns_linked_message() -> None:
    conv = _conversation()
    now = utc_now()
    message = conv.append_message("m1", MessageRole.USER, MessageContent(text="hi"), 3, now)
    assert isinstance(message, Message)
    assert message.conversation_id == conv.id
    assert message.workspace_id == conv.workspace_id
    assert message.token_count == 3
    assert message.deleted_at is None


def test_append_message_after_soft_delete_raises() -> None:
    conv = _conversation()
    conv.soft_delete(utc_now())
    with pytest.raises(ConversationDeletedError):
        conv.append_message("m1", MessageRole.USER, MessageContent(text="hi"), None, utc_now())


def test_soft_delete_sets_timestamps() -> None:
    conv = _conversation()
    now = utc_now()
    conv.soft_delete(now)
    assert conv.deleted_at == now
    assert conv.updated_at == now


def test_soft_delete_is_idempotent() -> None:
    conv = _conversation(deleted=True)
    marker = conv.deleted_at
    conv.soft_delete(utc_now())
    assert conv.deleted_at == marker  # a no-op delete leaves deleted_at untouched


def test_rename_updates_title() -> None:
    conv = _conversation()
    conv.rename("New title", utc_now())
    assert conv.title == "New title"


def test_rename_on_deleted_raises() -> None:
    conv = _conversation(deleted=True)
    with pytest.raises(ConversationDeletedError):
        conv.rename("New title", utc_now())


# --------------------------------------------------------------------------- #
# message entity                                                               #
# --------------------------------------------------------------------------- #
def test_message_soft_delete_is_idempotent() -> None:
    now = utc_now()
    message = Message(
        id="m1",
        conversation_id="c1",
        workspace_id="w1",
        role=MessageRole.USER,
        content=MessageContent(text="hi"),
        token_count=None,
        seq=1,
        created_at=now,
        deleted_at=None,
    )
    message.soft_delete(now)
    assert message.deleted_at == now
    marker = message.deleted_at
    message.soft_delete(utc_now())
    assert message.deleted_at == marker
