"""The SQL the conversations adapter runs for citations (gap th-1).

No database: a recording session stands in for ``tenant_session`` and the
statements it was handed are compiled with the asyncpg dialect (the app driver). Live
round trips and tenant isolation are in
``tests/integration/test_conversations_repository_rls.py`` (CI only).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import asyncpg

from app.framework.context.execution_context import ExecutionContext
from app.modules.conversations.adapters import sql_repository
from app.modules.conversations.adapters.sql_repository import (
    SqlConversationRepository,
    _citations_from_json,
    _citations_to_json,
    _fallback_from_json,
    _fallback_to_json,
    _hydrate_message,
    _status_from_row,
    messages,
)
from app.modules.conversations.domain.entities import Message
from app.modules.conversations.domain.value_objects import (
    Citation,
    MessageContent,
    MessageFallback,
    MessageRole,
    MessageStatus,
)

_WS = "0190cccc-0000-7000-8000-000000000001"
_CONVERSATION = "0190dddd-0000-7000-8000-000000000001"
_DOC = "0190aaaa-0000-7000-8000-000000000001"
_CHUNK_1 = "0190bbbb-0000-7000-8000-000000000001"
_CHUNK_2 = "0190bbbb-0000-7000-8000-000000000002"
_NAME = "دليل الصيانة ٢٠٢٦.pdf"
_NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

_PG = asyncpg.dialect()


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WS, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _citations() -> tuple[Citation, ...]:
    return (
        Citation(document_id=_DOC, file_name=_NAME, page=12, chunk_id=_CHUNK_1, rank=1),
        Citation(document_id=_DOC, file_name=None, page=None, chunk_id=_CHUNK_2, rank=2),
    )


def _fb() -> MessageFallback:
    return MessageFallback(
        from_provider="openai",
        from_model="gpt-4.1-mini",
        to_provider="ollama",
        to_model="gemma3:1b",
    )


def _message(
    citations: tuple[Citation, ...] | None,
    status: MessageStatus | None = None,
    fallback: MessageFallback | None = None,
) -> Message:
    return Message(
        id="0190eeee-0000-7000-8000-000000000001",
        conversation_id=_CONVERSATION,
        workspace_id=_WS,
        role=MessageRole.ASSISTANT,
        content=MessageContent(text="answer"),
        token_count=4,
        seq=2,
        created_at=_NOW,
        deleted_at=None,
        citations=citations,
        status=status,
        fallback=fallback,
    )


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _RecordingSession:
    def __init__(self, sink: list[Any], rows: list[dict[str, Any]]) -> None:
        self._sink = sink
        self._rows = rows

    async def execute(self, statement: Any) -> _Result:
        self._sink.append(statement)
        return _Result(self._rows)


def _repository(
    sink: list[Any], rows: list[dict[str, Any]] | None = None
) -> SqlConversationRepository:
    @asynccontextmanager
    async def tenant_session(_ctx: ExecutionContext) -> AsyncIterator[_RecordingSession]:
        yield _RecordingSession(sink, [{"version": 2}] if rows is None else rows)

    return SqlConversationRepository(tenant_session)  # type: ignore[arg-type]


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=_PG))


def _bound_json(value: object) -> object:
    """What the driver would receive for ``value`` bound to the citations column."""
    processor = messages.c.citations.type.dialect_impl(_PG).bind_processor(_PG)
    assert processor is not None
    return processor(value)


# --------------------------------------------------------------------------- #
# the JSON mapping                                                             #
# --------------------------------------------------------------------------- #
def test_none_maps_to_none_and_an_empty_tuple_to_an_empty_list() -> None:
    assert _citations_to_json(None) is None
    assert _citations_to_json(()) == []


def test_each_item_is_exactly_the_five_keys_in_order_keeping_null_values() -> None:
    [first, second] = _citations_to_json(_citations()) or []

    assert list(first) == ["document_id", "file_name", "page", "chunk_id", "rank"]
    assert first == {
        "document_id": _DOC,
        "file_name": _NAME,
        "page": 12,
        "chunk_id": _CHUNK_1,
        "rank": 1,
    }
    assert second["file_name"] is None
    assert second["page"] is None
    assert "file_name" in second
    assert "page" in second


def test_python_none_is_bound_as_sql_null_and_an_empty_list_as_json() -> None:
    # none_as_null=True: None must reach the driver as None (SQL NULL), never as
    # the JSON text 'null'; [] must stay a distinct, serialised value.
    assert _bound_json(None) is None
    assert _bound_json([]) == "[]"


def test_the_arabic_name_is_serialised_without_escaping_loss() -> None:
    bound = _bound_json(_citations_to_json(_citations()))

    assert isinstance(bound, str)
    # the driver-side JSON round-trips to the same text (JSONB stores characters)
    assert json.loads(bound)[0]["file_name"] == _NAME


def test_from_json_round_trips_in_order() -> None:
    assert _citations_from_json(_citations_to_json(_citations())) == _citations()


def test_from_json_distinguishes_null_from_an_empty_list() -> None:
    assert _citations_from_json(None) is None
    assert _citations_from_json([]) == ()


def test_from_json_revalidates_rather_than_trusting_the_row() -> None:
    with pytest.raises(Exception, match="UUID"):
        _citations_from_json(
            [{"document_id": "x", "file_name": None, "page": None, "chunk_id": _CHUNK_1, "rank": 1}]
        )


# --------------------------------------------------------------------------- #
# the statements                                                               #
# --------------------------------------------------------------------------- #
async def test_append_writes_citations_in_the_same_insert_and_never_updates_messages() -> None:
    statements: list[Any] = []

    await _repository(statements).append_message(_ctx(), _message(_citations()))

    assert len(statements) == 2
    insert_sql, update_sql = (_sql(s) for s in statements)
    assert insert_sql.startswith("INSERT INTO conversations.messages")
    assert "citations" in insert_sql
    assert update_sql.startswith("UPDATE conversations.conversations")
    assert not any(_sql(s).startswith("UPDATE conversations.messages") for s in statements)
    assert statements[0].compile(dialect=_PG).params["citations"] == _citations_to_json(
        _citations()
    )


async def test_append_without_citations_binds_none() -> None:
    statements: list[Any] = []

    await _repository(statements).append_message(_ctx(), _message(None))

    assert statements[0].compile(dialect=_PG).params["citations"] is None


async def test_list_messages_is_one_select_on_messages_alone_that_includes_citations() -> None:
    statements: list[Any] = []

    await _repository(statements, rows=[]).list_messages(
        _ctx(), _CONVERSATION, limit=10, cursor=None
    )

    assert len(statements) == 1
    sql = _sql(statements[0])
    assert "messages.citations" in sql
    from_clause = sql.split("FROM", 1)[1].split("WHERE", 1)[0]
    assert from_clause.strip() == "conversations.messages"
    assert "files." not in sql
    assert "knowledge." not in sql
    assert "JOIN" not in sql


# --------------------------------------------------------------------------- #
# hydration                                                                    #
# --------------------------------------------------------------------------- #
def _row(citations: object, status: object = None, fallback: object = None) -> Any:
    return {
        "id": "0190eeee-0000-7000-8000-000000000001",
        "conversation_id": _CONVERSATION,
        "workspace_id": _WS,
        "role": "assistant",
        "content": {"text": "old", "attachments": []},
        "token_count": None,
        "seq": 1,
        "created_at": _NOW,
        "deleted_at": None,
        "citations": citations,
        "status": status,
        "fallback": fallback,
    }


def test_a_row_with_null_citations_hydrates_to_none() -> None:
    assert _hydrate_message(_row(None)).citations is None


def test_a_row_with_an_empty_array_hydrates_to_an_empty_tuple() -> None:
    assert _hydrate_message(_row([])).citations == ()


def test_a_row_with_citations_hydrates_in_order() -> None:
    raw = _citations_to_json(_citations())

    assert _hydrate_message(_row(raw)).citations == _citations()


def test_the_adapter_module_exposes_the_citations_column_as_nullable() -> None:
    assert sql_repository.messages.c.citations.nullable is True


# --------------------------------------------------------------------------- #
# status / fallback (th-2)                                                     #
# --------------------------------------------------------------------------- #
def test_the_fallback_json_is_final_fallbacks_shape() -> None:
    assert _fallback_to_json(None) is None
    stored = _fallback_to_json(_fb())
    assert stored == {
        "from": {"provider": "openai", "model": "gpt-4.1-mini"},
        "to": {"provider": "ollama", "model": "gemma3:1b"},
    }
    assert set(stored or {}) == {"from", "to"}


def test_the_fallback_json_round_trips() -> None:
    assert _fallback_from_json(_fallback_to_json(_fb())) == _fb()
    assert _fallback_from_json(None) is None


def test_a_non_object_fallback_row_is_an_explicit_read_error() -> None:
    with pytest.raises(TypeError):
        _fallback_from_json([])


def test_no_credential_can_reach_the_stored_fallback() -> None:
    assert "sk-secret" not in json.dumps(_fallback_to_json(_fb()))


def test_status_from_row() -> None:
    assert _status_from_row(None) is None
    assert _status_from_row("partial") is MessageStatus.PARTIAL
    with pytest.raises(ValueError):
        _status_from_row("error")


def test_status_and_fallback_are_nullable_columns_with_no_default() -> None:
    for name in ("status", "fallback"):
        column = messages.c[name]
        assert column.nullable is True
        assert column.server_default is None


def test_a_python_none_fallback_is_bound_as_sql_null() -> None:
    processor = messages.c.fallback.type.dialect_impl(_PG).bind_processor(_PG)
    assert processor is not None
    assert processor(None) is None


@pytest.mark.parametrize(
    ("status", "fallback"),
    [(MessageStatus.COMPLETE, None), (MessageStatus.PARTIAL, _fb())],
)
async def test_append_writes_status_and_fallback_in_the_same_insert(
    status: MessageStatus, fallback: MessageFallback | None
) -> None:
    statements: list[Any] = []

    await _repository(statements).append_message(_ctx(), _message(None, status, fallback))

    assert len(statements) == 2
    insert_sql, update_sql = (_sql(s) for s in statements)
    assert insert_sql.startswith("INSERT INTO conversations.messages")
    assert "status" in insert_sql
    assert "fallback" in insert_sql
    assert update_sql.startswith("UPDATE conversations.conversations")
    assert not any(_sql(s).startswith("UPDATE conversations.messages") for s in statements)
    params = statements[0].compile(dialect=_PG).params
    assert params["status"] == status.value
    assert params["fallback"] == _fallback_to_json(fallback)


async def test_append_without_status_binds_none_for_both() -> None:
    statements: list[Any] = []

    await _repository(statements).append_message(_ctx(), _message(None))

    params = statements[0].compile(dialect=_PG).params
    assert params["status"] is None
    assert params["fallback"] is None


async def test_list_messages_selects_status_and_fallback_from_messages_alone() -> None:
    statements: list[Any] = []

    await _repository(statements, rows=[]).list_messages(
        _ctx(), _CONVERSATION, limit=10, cursor=None
    )

    assert len(statements) == 1
    sql = _sql(statements[0])
    assert "messages.status" in sql
    assert "messages.fallback" in sql
    from_clause = sql.split("FROM", 1)[1].split("WHERE", 1)[0]
    assert from_clause.strip() == "conversations.messages"


def test_a_row_without_status_hydrates_to_none_none() -> None:
    message = _hydrate_message(_row(None))

    assert message.status is None
    assert message.fallback is None


def test_a_row_with_status_and_fallback_hydrates() -> None:
    message = _hydrate_message(_row(None, "partial", _fallback_to_json(_fb())))

    assert message.status is MessageStatus.PARTIAL
    assert message.fallback == _fb()


def test_a_row_with_an_unknown_status_is_a_read_error_not_an_invented_value() -> None:
    with pytest.raises(ValueError):
        _hydrate_message(_row(None, "error"))
