"""Unit tests for the Idempotency-Key flow of ``POST /conversations/{id}/messages``
(``api/v1/idempotent_turn.py``, gap th-3).

The router suites pin the behaviour over the wire (``test_api_conversations_router``).
What is pinned HERE is the part no route reaches cleanly: the fingerprint and the
scope, the stored handle's exact shape, the state machine's thresholds, the
ledger's writes, and the one property that needs a controlled store -- a write
that survives the cancellation of whoever awaited it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from app.agents.orchestrator import AcceptedTurn, TurnOutcome
from app.api.v1.dto.conversations import Citation, MessageFallback, MessageOut
from app.api.v1.idempotent_turn import (
    _DETACHED,
    TURN_STALE_MARGIN_S,
    IdempotentTurnLedger,
    TurnHandle,
    claim_turn,
    content_hash,
    replay_error,
    replay_events,
    stale_after_s,
    turn_endpoint,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, ValidationError
from app.framework.ports.idempotency_store import ClaimOutcome, IdempotencyClaim
from app.framework.settings import Settings
from app.framework.types import Json
from tests.unit.support_idempotency import InMemoryIdempotencyStore

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_CONV = "018f0000-0000-7000-8000-00000000c0c0"
_USER = "018f0000-0000-7000-8000-0000000000u7"
_ANSWER = "018f0000-0000-7000-8000-0000000000a8"
_PARTIAL = "018f0000-0000-7000-8000-0000000000a9"
_KEY = "k-1"
_CONTENT: Json = {"text": "hello"}
_T = 600 + TURN_STALE_MARGIN_S


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_W1,
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000c1",
        roles=frozenset(),
    )


def _accepted() -> AcceptedTurn:
    return AcceptedTurn(conversation_id=_CONV, user_message_id=_USER, seq=7)


def _replied() -> TurnOutcome:
    return TurnOutcome(
        accepted=_accepted(),
        replied=True,
        message_id=_ANSWER,
        prompt_tokens=812,
        completion_tokens=140,
    )


def _failed() -> TurnOutcome:
    return TurnOutcome(
        accepted=_accepted(),
        replied=False,
        message_id=_PARTIAL,
        code="agent.failed",
        status=502,
    )


# --------------------------------------------------------------------------- #
# fingerprint, scope, threshold                                               #
# --------------------------------------------------------------------------- #
def test_the_scope_is_the_endpoint_of_the_conversation_id() -> None:
    assert turn_endpoint(_CONV) == f"POST /conversations/{_CONV}/messages"
    assert turn_endpoint(_CONV) != turn_endpoint(_CONV.replace("c0c0", "d0d0"))


async def test_the_row_is_scoped_by_exactly_the_id_the_caller_passes() -> None:
    """The caller (the router) passes the conversation's CANONICAL id; this
    function must not substitute anything else. The router-level proof that two
    spellings land on one row is in the router suite."""
    store = InMemoryIdempotencyStore()

    await claim_turn(
        store, _ctx(), conversation_id=_CONV, key=_KEY, content=_CONTENT, stale_after_s=_T
    )

    assert list(store.rows) == [(_W1, turn_endpoint(_CONV), _KEY)]


def test_a_lone_surrogate_is_a_validation_error_and_valid_text_hashes_as_before() -> None:
    with pytest.raises(ValidationError):
        content_hash({"text": "\ud800"})
    assert content_hash({"text": "hello"}) == hashlib.sha256(b'{"text":"hello"}').hexdigest()


def test_the_hash_is_over_the_content_only_and_ignores_key_order() -> None:
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"text": "a"}) != content_hash({"text": "b"})
    # the contract: one key `content`, nothing about `stream`/`Accept` is in it
    expected = hashlib.sha256(b'{"text":"hello"}').hexdigest()
    assert content_hash({"text": "hello"}) == expected


def test_the_hash_is_stable_for_arabic_and_never_escapes_it() -> None:
    content: Json = {"text": "ما هو دليل الصيانة؟ 🙂"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert content_hash(content) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert content_hash(content) == content_hash(dict(content))


def test_the_stale_threshold_is_the_stream_cap_plus_120_seconds() -> None:
    settings = Settings()

    assert TURN_STALE_MARGIN_S == 120.0
    assert stale_after_s(settings) == settings.limits.stream_max_duration_s + 120
    assert stale_after_s(settings) == 720.0


# --------------------------------------------------------------------------- #
# the handle                                                                  #
# --------------------------------------------------------------------------- #
def test_the_stored_keys_are_exactly_the_closed_list_with_no_text() -> None:
    body = TurnHandle.of_outcome(_replied()).to_json()

    assert set(body) == {
        "kind",
        "v",
        "conversation_id",
        "user_message_id",
        "seq",
        "outcome",
        "message_id",
        "code",
        "status",
        "usage",
    }
    assert body["kind"] == "agent_turn"
    assert body["v"] == 1
    assert body["usage"] == {"prompt_tokens": 812, "completion_tokens": 140}
    assert len(json.dumps(body)) < 512


def test_an_accepted_handle_has_no_answer_and_no_usage() -> None:
    body = TurnHandle.of_accepted(_accepted()).to_json()

    assert body["outcome"] == "accepted"
    assert (body["message_id"], body["code"], body["status"], body["usage"]) == (
        None,
        None,
        None,
        None,
    )


def test_a_handle_round_trips_through_json() -> None:
    for handle in (
        TurnHandle.of_accepted(_accepted()),
        TurnHandle.of_outcome(_replied()),
        TurnHandle.of_outcome(_failed()),
    ):
        assert TurnHandle.from_json(handle.to_json()) == handle


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"kind": "other", "v": 1},
        {"kind": "agent_turn", "v": 2},
        {"kind": "agent_turn", "v": 1, "conversation_id": 5, "user_message_id": "u", "seq": 1},
        {
            "kind": "agent_turn",
            "v": 1,
            "conversation_id": "c",
            "user_message_id": "u",
            "seq": True,
            "outcome": "accepted",
        },
        {
            "kind": "agent_turn",
            "v": 1,
            "conversation_id": "c",
            "user_message_id": "u",
            "seq": 1,
            "outcome": "exploded",
        },
    ],
)
def test_a_body_that_is_not_a_v1_handle_reads_as_none(raw: Json | None) -> None:
    assert TurnHandle.from_json(raw) is None


def test_lost_turns_an_accepted_handle_into_a_failure_with_the_question_id() -> None:
    lost = TurnHandle.of_accepted(_accepted()).lost()

    assert lost.outcome == "failed"
    assert (lost.code, lost.status) == ("agent.failed", 502)
    assert lost.message_id is None
    assert lost.user_message_id == _USER
    assert (lost.conversation_id, lost.seq) == (_CONV, 7)


# --------------------------------------------------------------------------- #
# claim_turn: the state machine                                               #
# --------------------------------------------------------------------------- #
async def _claim(store: InMemoryIdempotencyStore, content: Json = _CONTENT) -> object:
    return await claim_turn(
        store, _ctx(), conversation_id=_CONV, key=_KEY, content=content, stale_after_s=_T
    )


def _pk() -> tuple[str, str, str]:
    return (_W1, turn_endpoint(_CONV), _KEY)


async def test_a_first_attempt_is_claimed_and_hands_back_a_ledger() -> None:
    store = InMemoryIdempotencyStore()

    result = await _claim(store)

    assert isinstance(result, IdempotentTurnLedger)
    assert store.calls == ["claim"]
    assert _pk() in store.rows


async def test_a_completed_turn_replays_its_handle() -> None:
    store = InMemoryIdempotencyStore()
    ledger = await _claim(store)
    assert isinstance(ledger, IdempotentTurnLedger)
    await ledger.accepted(_accepted())
    await ledger.settled(_replied())

    result = await _claim(store)

    assert result == TurnHandle.of_outcome(_replied())


async def test_a_replayed_body_that_is_not_a_handle_is_a_500() -> None:
    store = InMemoryIdempotencyStore()
    await store.claim(
        _ctx(), endpoint=turn_endpoint(_CONV), key=_KEY, request_hash=content_hash(_CONTENT)
    )
    await store.complete(_ctx(), endpoint=turn_endpoint(_CONV), key=_KEY, response_body={"x": 1})

    with pytest.raises(AppError) as excinfo:
        await _claim(store)

    assert excinfo.value.code == "common.internal"


async def test_other_content_under_the_same_key_is_a_conflict_without_the_key_in_it() -> None:
    store = InMemoryIdempotencyStore()
    await _claim(store)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(store, {"text": "different"})

    assert _KEY not in excinfo.value.detail


async def test_a_young_unfinished_claim_is_a_conflict_in_both_states() -> None:
    store = InMemoryIdempotencyStore()
    ledger = await _claim(store)
    assert isinstance(ledger, IdempotentTurnLedger)
    with pytest.raises(ConflictError):  # S1: claimed, nothing recorded
        await _claim(store)
    await ledger.accepted(_accepted())
    store.set_age(_pk(), 10)
    with pytest.raises(ConflictError) as excinfo:  # S2: accepted
        await _claim(store)

    assert _KEY not in excinfo.value.detail


async def test_the_threshold_is_exclusive_below_and_inclusive_at() -> None:
    store = InMemoryIdempotencyStore()
    ledger = await _claim(store)
    assert isinstance(ledger, IdempotentTurnLedger)
    await ledger.accepted(_accepted())

    store.set_age(_pk(), _T - 1)
    with pytest.raises(ConflictError):
        await _claim(store)

    store.set_age(_pk(), _T)
    result = await _claim(store)
    assert result == TurnHandle.of_accepted(_accepted()).lost()


async def test_an_old_claim_without_a_handle_is_agent_failed_with_no_ids() -> None:
    store = InMemoryIdempotencyStore()
    await _claim(store)
    store.set_age(_pk(), _T)

    with pytest.raises(AppError) as excinfo:
        await _claim(store)

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502
    assert dict(excinfo.value.extensions) == {}


async def test_an_in_progress_with_no_age_is_treated_as_young() -> None:
    class _Vanishing:
        async def claim(self, *_a: object, **_k: object) -> IdempotencyClaim:
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)

    with pytest.raises(ConflictError):
        await claim_turn(
            _Vanishing(),  # type: ignore[arg-type]
            _ctx(),
            conversation_id=_CONV,
            key=_KEY,
            content=_CONTENT,
            stale_after_s=_T,
        )


async def test_the_scope_is_per_conversation_and_per_workspace() -> None:
    store = InMemoryIdempotencyStore()
    await _claim(store)

    other_conversation = await claim_turn(
        store,
        _ctx(),
        conversation_id=_CONV.replace("c0c0", "d0d0"),
        key=_KEY,
        content=_CONTENT,
        stale_after_s=_T,
    )
    other_workspace = await claim_turn(
        store,
        ExecutionContext(
            workspace_id=_W1.replace("w1", "w2"),
            user_id=None,
            correlation_id="c",
            roles=frozenset(),
        ),
        conversation_id=_CONV,
        key=_KEY,
        content=_CONTENT,
        stale_after_s=_T,
    )

    assert isinstance(other_conversation, IdempotentTurnLedger)
    assert isinstance(other_workspace, IdempotentTurnLedger)


# --------------------------------------------------------------------------- #
# the ledger                                                                  #
# --------------------------------------------------------------------------- #
async def _ledger(store: InMemoryIdempotencyStore) -> IdempotentTurnLedger:
    ledger = await _claim(store)
    assert isinstance(ledger, IdempotentTurnLedger)
    return ledger


async def test_accepted_checkpoints_the_handle_and_settled_completes_it() -> None:
    store = InMemoryIdempotencyStore()
    ledger = await _ledger(store)

    await ledger.accepted(_accepted())
    assert store.rows[_pk()].response_body == TurnHandle.of_accepted(_accepted()).to_json()
    assert store.rows[_pk()].completed is False

    await ledger.settled(_failed())
    assert store.rows[_pk()].response_body == TurnHandle.of_outcome(_failed()).to_json()
    assert store.rows[_pk()].completed is True
    assert store.calls == ["claim", "checkpoint", "complete"]


async def test_release_frees_the_key_only_while_nothing_was_accepted() -> None:
    store = InMemoryIdempotencyStore()
    ledger = await _ledger(store)
    await ledger.release_unless_accepted()
    assert store.rows == {}

    again = await _ledger(store)
    await again.accepted(_accepted())
    store.calls.clear()
    await again.release_unless_accepted()

    assert store.calls == []  # not even attempted
    assert _pk() in store.rows


async def test_the_accepted_turn_is_recorded_before_the_checkpoint_write() -> None:
    """A checkpoint that fails must still count as accepted: the question is
    stored, so the claim may not be released (FR-11)."""

    class _FailingCheckpoint(InMemoryIdempotencyStore):
        async def checkpoint(self, *_a: object, **_k: object) -> None:
            raise AppError("store down", code="common.internal")

    store = _FailingCheckpoint()
    ledger = await _ledger(store)

    with pytest.raises(AppError):
        await ledger.accepted(_accepted())

    assert ledger.accepted_turn == _accepted()
    await ledger.release_unless_accepted()
    assert _pk() in store.rows  # not released


class _GatedStore(InMemoryIdempotencyStore):
    """A store whose ``complete`` waits for the test to open a gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.finished = asyncio.Event()

    async def complete(self, *args: object, **kwargs: object) -> None:
        await self.gate.wait()
        await super().complete(*args, **kwargs)  # type: ignore[arg-type]
        self.finished.set()


async def test_a_settled_write_survives_the_cancellation_of_its_awaiter() -> None:
    """AC-4.3: Starlette cancels an SSE body on disconnect and anyio re-cancels
    every await of that unwind. The write is its own task, held strongly, and
    finishes after the awaiter is gone."""
    store = _GatedStore()
    ledger = await _ledger(store)
    await ledger.accepted(_accepted())

    awaiter = asyncio.ensure_future(ledger.settled(_failed()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    awaiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await awaiter
    assert store.rows[_pk()].completed is False  # still pending, nobody is waiting
    assert len(_DETACHED) == 1  # held by a strong reference

    store.gate.set()
    await asyncio.wait_for(store.finished.wait(), timeout=1)
    await asyncio.sleep(0)

    assert store.rows[_pk()].completed is True
    assert store.rows[_pk()].response_body == TurnHandle.of_outcome(_failed()).to_json()
    assert len(_DETACHED) == 0


async def test_the_normal_path_awaits_the_write_to_the_end_and_sees_its_failure() -> None:
    class _Failing(InMemoryIdempotencyStore):
        async def complete(self, *_a: object, **_k: object) -> None:
            raise AppError("store down", code="common.internal")

    ledger = await _ledger(_Failing())

    with pytest.raises(AppError):
        await ledger.settled(_replied())

    await asyncio.sleep(0)
    assert len(_DETACHED) == 0


async def test_a_failure_after_the_awaiter_left_is_reaped_not_left_unretrieved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")

    class _FailsLater(InMemoryIdempotencyStore):
        gate = asyncio.Event()

        async def complete(self, *_a: object, **_k: object) -> None:
            await self.gate.wait()
            raise AppError("store down", code="common.internal")

    store = _FailsLater()
    ledger = await _ledger(store)
    awaiter = asyncio.ensure_future(ledger.settled(_replied()))
    await asyncio.sleep(0)
    awaiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await awaiter

    store.gate.set()
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(_DETACHED) == 0
    (record,) = [r for r in caplog.records if r.message == "idempotent_turn.detached_write_failed"]
    assert record.conversation_id == _CONV  # type: ignore[attr-defined]
    assert record.phase == "settled"  # type: ignore[attr-defined]
    assert record.error_type == "AppError"  # type: ignore[attr-defined]
    assert record.exc_info is None  # a driver chain would carry bound parameters
    assert _KEY not in caplog.text
    assert _W1 not in caplog.text


async def test_a_failure_the_awaiter_saw_is_not_logged_a_second_time_by_the_reaper(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")

    class _Failing(InMemoryIdempotencyStore):
        async def complete(self, *_a: object, **_k: object) -> None:
            raise AppError("store down", code="common.internal")

    ledger = await _ledger(_Failing())
    with pytest.raises(AppError):
        await ledger.settled(_replied())
    await asyncio.sleep(0)

    assert [r for r in caplog.records if "detached" in r.message] == []


# --------------------------------------------------------------------------- #
# replay                                                                      #
# --------------------------------------------------------------------------- #
def _reply(*, with_extras: bool = True) -> MessageOut:
    return MessageOut.model_validate(
        {
            "id": _ANSWER,
            "role": "assistant",
            "content": {"text": "الجواب", "attachments": []},
            "citations": (
                [
                    Citation(
                        document_id="d", file_name=None, page=None, chunk_id="c", rank=1
                    ).model_dump()
                ]
                if with_extras
                else None
            ),
            "status": "complete",
            "fallback": (
                MessageFallback.model_validate(
                    {
                        "from": {"provider": "openai", "model": "m1"},
                        "to": {"provider": "ollama", "model": "m2"},
                    }
                ).model_dump(by_alias=True)
                if with_extras
                else None
            ),
            "reply_to": _USER,
            "token_count": 3,
            "seq": 8,
            "created_at": "2026-11-10T09:00:00Z",
        }
    )


async def _events(handle: TurnHandle, reply: MessageOut | None) -> list[tuple[str, Json]]:
    return [(e.type, e.data) async for e in replay_events(handle, reply)]


async def test_a_replayed_turn_is_exactly_accepted_then_final() -> None:
    events = await _events(TurnHandle.of_outcome(_replied()), _reply())

    assert [name for name, _ in events] == ["accepted", "final"]
    assert events[0][1] == {
        "conversation_id": _CONV,
        "user_message_id": _USER,
        "seq": 7,
        "replayed": True,
    }
    final = events[1][1]
    assert final["message_id"] == _ANSWER
    assert final["content"] == {"text": "الجواب", "attachments": []}
    assert final["usage"] == {"prompt_tokens": 812, "completion_tokens": 140}
    assert final["user_message_id"] == _USER
    assert final["citations"] == [
        {"document_id": "d", "file_name": None, "page": None, "chunk_id": "c", "rank": 1}
    ]
    assert final["fallback"] == {
        "from": {"provider": "openai", "model": "m1"},
        "to": {"provider": "ollama", "model": "m2"},
    }


async def test_a_replayed_final_omits_citations_and_fallback_that_are_null() -> None:
    events = await _events(TurnHandle.of_outcome(_replied()), _reply(with_extras=False))

    final = events[1][1]
    assert "citations" not in final
    assert "fallback" not in final
    assert set(final) == {"message_id", "content", "usage", "user_message_id"}


async def test_a_replayed_failure_is_accepted_then_error_with_a_fixed_detail() -> None:
    events = await _events(TurnHandle.of_outcome(_failed()), None)

    assert [name for name, _ in events] == ["accepted", "error"]
    error = events[1][1]
    assert error["code"] == "agent.failed"
    assert error["status"] == 502
    assert error["user_message_id"] == _USER
    assert error["message_id"] == _PARTIAL
    assert error["detail"] == (
        "the earlier attempt with this Idempotency-Key ended with this error"
    )


async def test_a_replayed_failure_without_a_partial_has_no_message_id() -> None:
    handle = TurnHandle.of_accepted(_accepted()).lost()

    events = await _events(handle, None)

    assert "message_id" not in events[1][1]
    assert events[1][1]["user_message_id"] == _USER


def test_the_replayed_problem_carries_the_stored_code_status_and_ids() -> None:
    error = replay_error(TurnHandle.of_outcome(_failed()))

    assert (error.code, error.status) == ("agent.failed", 502)
    assert dict(error.extensions) == {"user_message_id": _USER, "message_id": _PARTIAL}
    assert "Idempotency-Key" in error.detail
