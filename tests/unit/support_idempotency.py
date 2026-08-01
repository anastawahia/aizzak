"""An in-memory ``IdempotencyStore`` for the API router suites (3.79).

Structural ``Protocol`` match, like every fake here. It implements the REAL
claim semantics rather than a stub that always says "first attempt": the whole
point of the port is the four-way outcome, and a fake that never returned
``REPLAY``/``MISMATCH``/``IN_PROGRESS`` would let a router test pass while the
router ignored three of them.

The one thing it cannot prove is the thing only Postgres can: that two
concurrent claims are arbitrated by a primary key rather than by a
read-then-write both callers win. That proof lives in
``tests/integration/test_idempotency_live.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.idempotency_store import ClaimOutcome, IdempotencyClaim
from app.framework.types import Json


@dataclass
class _Row:
    request_hash: str
    response_body: Json | None = None
    completed: bool = False


class InMemoryIdempotencyStore:
    """``IdempotencyStore`` over a dict keyed exactly as the table is."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], _Row] = {}
        # Every claim, in order — so a test can assert the store was not
        # touched at all when no header was sent.
        self.claims: list[tuple[str, str, str]] = []

    async def claim(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, request_hash: str
    ) -> IdempotencyClaim:
        pk = (ctx.workspace_id, endpoint, key)
        self.claims.append(pk)
        existing = self.rows.get(pk)
        if existing is None:
            self.rows[pk] = _Row(request_hash=request_hash)
            return IdempotencyClaim(ClaimOutcome.CLAIMED)
        if existing.request_hash != request_hash:
            return IdempotencyClaim(ClaimOutcome.MISMATCH)
        if not existing.completed:
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)
        return IdempotencyClaim(ClaimOutcome.REPLAY, response_body=existing.response_body)

    async def complete(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, response_body: Json
    ) -> None:
        row = self.rows[(ctx.workspace_id, endpoint, key)]
        row.response_body = response_body
        row.completed = True

    async def release(self, ctx: ExecutionContext, *, endpoint: str, key: str) -> None:
        pk = (ctx.workspace_id, endpoint, key)
        row = self.rows.get(pk)
        # `completed_at IS NULL` in the SQL predicate, mirrored: a release must
        # never remove a row that already recorded a real response.
        if row is not None and not row.completed:
            del self.rows[pk]
