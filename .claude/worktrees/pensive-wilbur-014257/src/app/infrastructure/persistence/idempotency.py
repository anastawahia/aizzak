"""SQL adapter for ``IdempotencyStore`` — writes/reads
``platform.idempotency_keys`` (03-api-spec §0, landed in 3.79).

Modelled on ``processed_events.py``, the DD-09 consumer ledger, because the
problem is the same one at a different boundary: an operation that may be
delivered twice must run once. What is shared is the mechanism — a plain
``INSERT`` whose ``23505`` on the primary key IS the "someone else already
has this" answer, contained by a SAVEPOINT so the expected duplicate does not
poison the caller's transaction (Postgres aborts the whole transaction on any
statement error, 25P02).

**Three deliberate differences from that ledger, each forced:**

1. **It reads.** ``processed_events`` is INSERT-only precisely so a compromised
   worker role cannot enumerate what the platform processed. This store's whole
   purpose is to hand back the FIRST response, so SELECT is unavoidable. The
   compensating control is RLS, which ``processed_events`` does not have and
   this table does (migration ``0002_idempotency_keys``): a role can read only
   its own workspace's rows, and only through a session that set
   ``app.workspace_id``.
2. **It updates and deletes.** ``complete`` fills the stored response;
   ``release`` removes a claim whose operation RAISED, so one 500 does not make
   a key permanently unusable.
3. **It commits the claim on its own.** The ledger's claim joins the handler's
   unit of work by design (DD-09: «داخل معاملة الأثر»). This one must NOT — an
   uncommitted claim is invisible to the concurrent duplicate it exists to
   block. That is why ``claim`` deliberately does not run inside the operation's
   transaction, and why ``release`` exists at all: the two are the manual
   compensation for giving up that atomicity, and giving it up is what buys
   mutual exclusion between two live requests.

**Defence in depth (the repo-wide rule).** Every statement carries an explicit
``workspace_id`` predicate in addition to the RLS policy. RLS is the guarantee;
the predicate is what keeps a query honest if a policy is ever dropped, and
what makes the intent readable at the call site.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    Uuid,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.ports.idempotency_store import ClaimOutcome, IdempotencyClaim
from app.framework.types import Json
from app.infrastructure.persistence.outbox import TenantSessionProvider

_metadata = MetaData()

idempotency_keys = Table(
    "idempotency_keys",
    _metadata,
    Column("workspace_id", Uuid(as_uuid=False), primary_key=True),
    Column("endpoint", Text, primary_key=True),
    Column("idempotency_key", Text, primary_key=True),
    Column("request_hash", Text, nullable=False),
    # NULL until the operation finishes — see the migration's own comment.
    Column("response_body", JSONB, nullable=True),
    # DB-defaulted (the `outbox.created_at` precedent): server time, never an
    # app instance's clock, decides when a key was claimed.
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    schema="platform",
)


class SqlIdempotencyStore:
    """``IdempotencyStore`` over ``platform.idempotency_keys``."""

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def claim(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, request_hash: str
    ) -> IdempotencyClaim:
        """Try to own ``(workspace, endpoint, key)``; on a conflict, report
        what the existing row means for this request.

        INSERT-then-inspect, never SELECT-then-INSERT: two concurrent requests
        both find nothing on a read, and both would proceed. The primary key is
        the only arbiter that cannot be raced, which is exactly the DD-09
        claim's reasoning applied to two live requests instead of two
        deliveries.

        The follow-up SELECT runs only on the conflict path, so the common case
        (a first attempt) costs one statement.
        """
        stmt = insert(idempotency_keys).values(
            workspace_id=ctx.workspace_id,
            endpoint=endpoint,
            idempotency_key=key,
            request_hash=request_hash,
        )
        try:
            async with self._tenant_session(ctx) as session:
                try:
                    async with session.begin_nested():
                        await session.execute(stmt)
                except IntegrityError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "23505":
                        raise
                    return await self._inspect(session, ctx, endpoint, key, request_hash)
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return IdempotencyClaim(ClaimOutcome.CLAIMED)

    async def complete(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, response_body: Json
    ) -> None:
        """Record the response the FIRST caller produced.

        Scoped by ``request_hash``? No — deliberately. The row was inserted by
        this same caller moments ago with its own hash, so re-checking it would
        only guard against a bug this code cannot have; scoping by the primary
        key alone keeps the statement (and the failure mode) obvious.
        """
        stmt = (
            update(idempotency_keys)
            .where(
                idempotency_keys.c.workspace_id == ctx.workspace_id,
                idempotency_keys.c.endpoint == endpoint,
                idempotency_keys.c.idempotency_key == key,
            )
            .values(response_body=response_body, completed_at=func.now())
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def release(self, ctx: ExecutionContext, *, endpoint: str, key: str) -> None:
        """Drop an UNFINISHED claim (the operation raised).

        ``completed_at IS NULL`` in the predicate is not belt and braces: it is
        what stops a late/duplicated release from deleting a row that has
        already recorded a real response, which would silently un-protect a key
        whose operation actually succeeded.
        """
        stmt = delete(idempotency_keys).where(
            idempotency_keys.c.workspace_id == ctx.workspace_id,
            idempotency_keys.c.endpoint == endpoint,
            idempotency_keys.c.idempotency_key == key,
            idempotency_keys.c.completed_at.is_(None),
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def _inspect(
        self,
        session: AsyncSession,
        ctx: ExecutionContext,
        endpoint: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyClaim:
        """Classify the row that won the insert race."""
        stmt = select(
            idempotency_keys.c.request_hash,
            idempotency_keys.c.response_body,
            idempotency_keys.c.completed_at,
        ).where(
            idempotency_keys.c.workspace_id == ctx.workspace_id,
            idempotency_keys.c.endpoint == endpoint,
            idempotency_keys.c.idempotency_key == key,
        )
        row = (await session.execute(stmt)).mappings().one_or_none()
        if row is None:
            # The winner released its claim between our INSERT failing and this
            # SELECT — a lost race with a FAILED first attempt. Treating it as
            # in-progress (a 409 the client can simply retry) is the honest
            # answer: we hold no claim, so we must not run the operation.
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)
        if row["request_hash"] != request_hash:
            return IdempotencyClaim(ClaimOutcome.MISMATCH)
        if row["completed_at"] is None:
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)
        return IdempotencyClaim(ClaimOutcome.REPLAY, response_body=row["response_body"])


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver failure onto the shared hierarchy (R6) — the
    ``SqlProcessedEventLedger._translate`` precedent. No ``23505`` branch: the
    duplicate key is ``claim``'s own normal outcome, consumed at the savepoint
    before this translator can see it."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return AppError(
            "idempotency store rejected: app role lacks privileges on platform.idempotency_keys",
            code="common.internal",
        )
    return AppError(
        "unexpected database error while using the idempotency store", code="common.internal"
    )
