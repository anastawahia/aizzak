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
        space_id="s1",
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


def test_no_mutator_moves_a_thread_between_spaces() -> None:
    """Decision 3's shape on this aggregate (spaces plan step 7): every
    behaviour the thread has, run in sequence, and the space is still the one
    it was opened in. A `move_to_space` added later fails here first — which
    matters more than the `save` that would refuse to persist it, because a
    thread whose space changed in memory answers from the wrong scope for the
    rest of the request."""
    conv = _conversation()
    now = utc_now()
    conv.append_message("m1", MessageRole.USER, MessageContent(text="hi"), None, now)
    conv.rename("New title", now)
    conv.pin_model_route("rag_agent", now)
    conv.soft_delete(now)
    assert conv.space_id == "s1"


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


# --------------------------------------------------------------------------- #
# ب-9 (خطة السيناريوهات §7، ف-1أ) — the pending clarification                  #
# --------------------------------------------------------------------------- #
def test_a_thread_starts_waiting_for_nothing() -> None:
    """The default, and the semantics of every row that predates the column:
    no question outstanding. It is the SECOND defaulted field on this
    aggregate, beside `model_route`, and for the same reason — a construction
    site that says nothing means this, which is almost all of them."""
    assert _conversation().pending_clarification == ()


def test_the_names_a_thread_asked_about_round_trip_in_order() -> None:
    """⚠️ Order is part of the value, not an incidental property of a
    sequence: it is what «الثاني» indexes on the next turn, so nothing here
    sorts, trims or de-duplicates it."""
    conv = _conversation()

    conv.expect_clarification(["b.pdf", "a.pdf", "b.pdf"], utc_now())

    assert conv.pending_clarification == ("b.pdf", "a.pdf", "b.pdf")


def test_the_same_call_that_asks_is_the_call_that_forgets() -> None:
    """Decision 1 made structural. There is no `clear`, so the erasure cannot
    be a step somebody omits: the only thing anyone can write is what is
    outstanding NOW, and an intent that survived two turns would read a
    brand-new question as an answer to a forgotten one."""
    conv = _conversation()
    conv.expect_clarification(["a.pdf"], utc_now())

    conv.expect_clarification([], utc_now())

    assert conv.pending_clarification == ()


def test_the_stored_list_is_not_a_live_handle_on_the_callers_own() -> None:
    """Copied, not aliased: a caller that kept mutating its list would
    otherwise be editing this thread's memory of what it asked."""
    options = ["a.pdf"]
    conv = _conversation()
    conv.expect_clarification(options, utc_now())

    options.append("b.pdf")

    assert conv.pending_clarification == ("a.pdf",)


def test_asking_a_question_stamps_the_thread_as_touched() -> None:
    """`rename` and `pin_model_route`'s rule: a mutation moves `updated_at`."""
    conv = _conversation()
    later = utc_now()

    conv.expect_clarification(["a.pdf"], later)

    assert conv.updated_at == later


def test_a_deleted_thread_refuses_to_be_asked_anything() -> None:
    """A deleted thread refuses WRITES rather than denying its own existence —
    the guard `rename` and `pin_model_route` keep, and this is a write."""
    conv = _conversation(deleted=True)

    with pytest.raises(ConversationDeletedError):
        conv.expect_clarification(["a.pdf"], utc_now())
