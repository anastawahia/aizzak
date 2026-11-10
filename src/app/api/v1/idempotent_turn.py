"""The ``Idempotency-Key`` flow of ``POST /conversations/{id}/messages`` (gap th-3).

Not ``idempotent()``: a turn has an effect (a stored question, a billed run)
BEFORE it has an answer, the streaming branch has no body to store, and the
replay must be rebuilt from the rows as they are now.

**The row's life** (key: workspace, ``endpoint = "POST /conversations/{id}/messages"``,
key)::

    absent --claim--> S1 claimed --checkpoint--> S2 accepted --complete--> S3 replied | failed
                       |  ^ release (failure BEFORE the question was stored)
                       v
                     absent

* **S1** the key is owned, nothing is stored. A repeat is 409 while the owner may
  still be running, and a 502 ``agent.failed`` without ids once the claim is older
  than ``stream_max_duration_s`` + :data:`TURN_STALE_MARGIN_S` (the owner died
  between the claim and the checkpoint; we cannot tell whether the question was
  written, so nothing is invented).
* **S2** the user message is stored (``TurnLedger.accepted``). A repeat is 409
  until the same age threshold, then the turn is reported lost: ``agent.failed``
  with the question's id.
* **S3** the turn ended (``TurnLedger.settled``): ``replied`` (a stored answer) or
  ``failed`` (the code and status the first caller was given). A repeat gets the
  SAME outcome without running anything and without billing.

**What is stored is a handle** (:class:`TurnHandle`): ids, ``seq``, the outcome,
the code and status, the two usage counters. Never ``text``, ``content``,
``citations`` or ``detail`` (FR-17, NFR-9): a replay of a ``replied`` turn reads
the answer from the message row as it is now, so a message the user deleted is
not handed back (404).

**The ledger writes survive a client that leaves.** Starlette cancels an SSE body
when the client disconnects, and anyio re-cancels every ``await`` of that unwind;
a plain ``await store.complete(...)`` there never finishes, which would leave the
most common Android scenario (the app is closed mid-stream) at a 409 for twelve
minutes. :func:`_shielded` runs the write as its own task and keeps a strong
reference to it until it is done.

The key value is never put in a log line nor in an error detail (d-t13).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, replace
from typing import Final, Literal

from app.agents.orchestrator import AcceptedTurn, TurnOutcome
from app.api.v1.dto.conversations import MessageOut
from app.framework.agent_runtime.base_agent import AgentEvent
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.ports.idempotency_store import ClaimOutcome, IdempotencyStore
from app.framework.settings import Settings
from app.framework.types import Json, Uuid

# How long after `stream_max_duration_s` a claim is still given the benefit of the
# doubt: the time a live turn may spend before the stream and while saving. Fixed
# in code, not a setting (d-t3).
TURN_STALE_MARGIN_S: Final[float] = 120.0

_HANDLE_KIND = "agent_turn"
_HANDLE_VERSION = 1
_AGENT_FAILED = "agent.failed"
_AGENT_FAILED_STATUS = 502

# What a replayed failure says in `detail`: the original detail may carry text
# from a provider and is never stored (d-t9). The code, the status and the ids
# are the contract.
_REPLAYED_DETAIL = "the earlier attempt with this Idempotency-Key ended with this error"

# Strong references to the shielded ledger writes. The event loop only keeps weak
# references to tasks, and the whole point of the shield is that nobody is
# awaiting the write any more.
_DETACHED: set[asyncio.Task[None]] = set()


def turn_endpoint(conversation_id: Uuid) -> str:
    """``POST /conversations/{conversation_id}/messages`` -- the id is the
    conversation's CANONICAL id (``conversation.id``), never the path text, so two
    spellings of one id fall on the same row (d-t10)."""
    return f"POST /conversations/{conversation_id}/messages"


def content_hash(content: Json) -> str:
    """sha256 over the canonical JSON of ``content`` ONLY. ``stream`` and
    ``Accept`` choose how the answer is delivered, not which question it is."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stale_after_s(settings: Settings) -> float:
    """The claim age from which a still-unfinished turn is reported lost."""
    return float(settings.limits.stream_max_duration_s) + TURN_STALE_MARGIN_S


@dataclass(frozen=True, slots=True)
class TurnHandle:
    """What ``response_body`` holds for this endpoint. No text, ever."""

    conversation_id: Uuid
    user_message_id: Uuid
    seq: int
    outcome: Literal["accepted", "replied", "failed"]
    message_id: Uuid | None = None
    code: str | None = None
    status: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @classmethod
    def of_accepted(cls, turn: AcceptedTurn) -> TurnHandle:
        return cls(
            conversation_id=turn.conversation_id,
            user_message_id=turn.user_message_id,
            seq=turn.seq,
            outcome="accepted",
        )

    @classmethod
    def of_outcome(cls, outcome: TurnOutcome) -> TurnHandle:
        return cls(
            conversation_id=outcome.accepted.conversation_id,
            user_message_id=outcome.accepted.user_message_id,
            seq=outcome.accepted.seq,
            outcome="replied" if outcome.replied else "failed",
            message_id=outcome.message_id,
            code=outcome.code,
            status=outcome.status,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
        )

    def lost(self) -> TurnHandle:
        """An accepted turn whose server died: the question is stored, no answer
        ever will be (FR-14)."""
        return replace(
            self,
            outcome="failed",
            message_id=None,
            code=_AGENT_FAILED,
            status=_AGENT_FAILED_STATUS,
            prompt_tokens=None,
            completion_tokens=None,
        )

    def to_json(self) -> Json:
        usage: Json | None = None
        if self.prompt_tokens is not None and self.completion_tokens is not None:
            usage = {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            }
        return {
            "kind": _HANDLE_KIND,
            "v": _HANDLE_VERSION,
            "conversation_id": self.conversation_id,
            "user_message_id": self.user_message_id,
            "seq": self.seq,
            "outcome": self.outcome,
            "message_id": self.message_id,
            "code": self.code,
            "status": self.status,
            "usage": usage,
        }

    @classmethod
    def from_json(cls, raw: Json | None) -> TurnHandle | None:
        """``None`` for a missing body or one that is not a v1 turn handle."""
        if not isinstance(raw, dict):
            return None
        if raw.get("kind") != _HANDLE_KIND or raw.get("v") != _HANDLE_VERSION:
            return None
        conversation_id = raw.get("conversation_id")
        user_message_id = raw.get("user_message_id")
        seq = raw.get("seq")
        outcome = raw.get("outcome")
        if not (isinstance(conversation_id, str) and isinstance(user_message_id, str)):
            return None
        if not isinstance(seq, int) or isinstance(seq, bool):
            return None
        if outcome not in ("accepted", "replied", "failed"):
            return None
        message_id = raw.get("message_id")
        code = raw.get("code")
        status = raw.get("status")
        usage = raw.get("usage")
        prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
        return cls(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            seq=seq,
            outcome=outcome,
            message_id=message_id if isinstance(message_id, str) else None,
            code=code if isinstance(code, str) else None,
            status=status if isinstance(status, int) and not isinstance(status, bool) else None,
            prompt_tokens=prompt if isinstance(prompt, int) else None,
            completion_tokens=completion if isinstance(completion, int) else None,
        )


def _reap(task: asyncio.Task[None]) -> None:
    """Forget a finished detached write, and read its exception so a failure
    after the awaiter left is not reported as "never retrieved"."""
    _DETACHED.discard(task)
    if not task.cancelled():
        task.exception()


async def _shielded(write: Awaitable[None]) -> None:
    """Run the ledger write as its own task and await it through ``asyncio.shield``.

    The normal path awaits it to the end (and sees its failure). When the AWAITER
    is cancelled -- Starlette cancels an SSE body on disconnect and anyio
    re-cancels every await of that unwind -- the write keeps running to
    completion in the background, held in ``_DETACHED`` until done.
    """
    task = asyncio.ensure_future(write)
    _DETACHED.add(task)
    task.add_done_callback(_reap)
    await asyncio.shield(task)


class IdempotentTurnLedger:
    """``TurnLedger`` over ``IdempotencyStore`` for ONE claimed key."""

    def __init__(
        self, store: IdempotencyStore, ctx: ExecutionContext, *, endpoint: str, key: str
    ) -> None:
        self._store = store
        self._ctx = ctx
        self._endpoint = endpoint
        self._key = key
        # Set BEFORE the checkpoint write: even when that write fails, the turn
        # was accepted (the question is stored) and the claim must not be released.
        self.accepted_turn: AcceptedTurn | None = None

    async def accepted(self, turn: AcceptedTurn) -> None:
        self.accepted_turn = turn
        await _shielded(
            self._store.checkpoint(
                self._ctx,
                endpoint=self._endpoint,
                key=self._key,
                response_body=TurnHandle.of_accepted(turn).to_json(),
            )
        )

    async def settled(self, outcome: TurnOutcome) -> None:
        await _shielded(
            self._store.complete(
                self._ctx,
                endpoint=self._endpoint,
                key=self._key,
                response_body=TurnHandle.of_outcome(outcome).to_json(),
            )
        )

    async def release_unless_accepted(self) -> None:
        """Free the key, but only while no question was stored (FR-11): a failure
        before the message exists leaves nothing to protect, a failure after it
        does not free the key."""
        if self.accepted_turn is not None:
            return
        await _shielded(self._store.release(self._ctx, endpoint=self._endpoint, key=self._key))


async def claim_turn(
    store: IdempotencyStore,
    ctx: ExecutionContext,
    *,
    conversation_id: Uuid,
    key: str,
    content: Json,
    stale_after_s: float,
) -> IdempotentTurnLedger | TurnHandle:
    """Claim ``key`` for this turn, or say what the earlier attempt left.

    * CLAIMED -> an :class:`IdempotentTurnLedger` (the caller runs the turn);
    * REPLAY -> the stored :class:`TurnHandle` (a body that is not one is a 500);
    * MISMATCH -> 409, the key was used with different content;
    * IN_PROGRESS -> 409 while younger than ``stale_after_s``; after it, the
      handle's ``lost()`` outcome, or 502 ``agent.failed`` without ids when no
      question was ever recorded.
    """
    endpoint = turn_endpoint(conversation_id)
    claim = await store.claim(ctx, endpoint=endpoint, key=key, request_hash=content_hash(content))
    if claim.outcome is ClaimOutcome.CLAIMED:
        return IdempotentTurnLedger(store, ctx, endpoint=endpoint, key=key)
    if claim.outcome is ClaimOutcome.REPLAY:
        handle = TurnHandle.from_json(claim.response_body)
        if handle is None:
            raise AppError(
                "the stored outcome of this Idempotency-Key could not be read",
                code="common.internal",
            )
        return handle
    if claim.outcome is ClaimOutcome.MISMATCH:
        raise ConflictError(
            "Idempotency-Key was already used on this conversation with different content"
        )
    # IN_PROGRESS
    if claim.age_s is None or claim.age_s < stale_after_s:
        raise ConflictError(
            "an earlier attempt with this Idempotency-Key is still running; retry shortly"
        )
    handle = TurnHandle.from_json(claim.response_body)
    if handle is not None:
        return handle.lost()
    raise AppError(
        "the earlier attempt with this Idempotency-Key was lost before its message was stored",
        code=_AGENT_FAILED,
    )


def _error_ids(handle: TurnHandle) -> dict[str, str]:
    ids = {"user_message_id": handle.user_message_id}
    if handle.message_id is not None:
        ids["message_id"] = handle.message_id
    return ids


def replay_error(handle: TurnHandle) -> AppError:
    """The problem a failed turn is replayed as: the stored code and status, a
    fixed detail, and the ids."""
    return AppError(
        _REPLAYED_DETAIL,
        code=handle.code or _AGENT_FAILED,
        status=handle.status or _AGENT_FAILED_STATUS,
        extensions=_error_ids(handle),
    )


async def replay_events(handle: TurnHandle, reply: MessageOut | None) -> AsyncIterator[AgentEvent]:
    """Exactly two events: ``accepted`` (with ``replayed: True``), then ``final``
    (rebuilt from ``reply`` and the handle) for a replied turn, else ``error``.
    No ``token``, no ``notice``."""
    yield AgentEvent(
        type="accepted",
        data={
            "conversation_id": handle.conversation_id,
            "user_message_id": handle.user_message_id,
            "seq": handle.seq,
            "replayed": True,
        },
    )
    if handle.outcome == "replied":
        if reply is None:
            raise AppError("the replayed answer is missing", code="common.internal")
        final: Json = {
            "message_id": reply.id,
            "content": reply.content,
            "usage": {
                "prompt_tokens": handle.prompt_tokens or 0,
                "completion_tokens": handle.completion_tokens or 0,
            },
            "user_message_id": handle.user_message_id,
        }
        if reply.citations is not None:
            final["citations"] = [c.model_dump(mode="json") for c in reply.citations]
        if reply.fallback is not None:
            final["fallback"] = reply.fallback.model_dump(mode="json", by_alias=True)
        yield AgentEvent(type="final", data=final)
        return
    error: Json = {
        "code": handle.code or _AGENT_FAILED,
        "status": handle.status or _AGENT_FAILED_STATUS,
        "detail": _REPLAYED_DETAIL,
        **_error_ids(handle),
    }
    yield AgentEvent(type="error", data=error)
