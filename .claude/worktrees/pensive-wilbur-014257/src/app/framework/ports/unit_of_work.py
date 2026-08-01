"""UnitOfWork driven port — the request-scoped transaction boundary that
closes 04-event-catalog §3.1's first delivery guarantee (D-18): "الأثر
النطاقي + صف Outbox في نفس المعاملة ⇒ لا فقد" ("the domain effect and the
Outbox row land in the same transaction ⇒ nothing is lost"). Landed in
5.1-أ; every producing use-case since Phase 3 already RETURNED its domain
events, but until this port existed nothing bound the aggregate write and
the Outbox append into one atomic transaction (4.7-d-1's and 4.7-d-2's own
docstrings recorded that gap honestly rather than pretending it closed).

A producer service (the ``MediaRequestService`` / ``CompleteUploadService``
/ ``RememberInteractionService`` precedent, application layer) depends on
THIS port, never on ``app.infrastructure`` directly — application code may
use domain + framework(ports) only (import-linter contract 3,
``application-boundaries``). The concrete transaction — one PostgreSQL
session, one ``SET LOCAL app.workspace_id``, one COMMIT/ROLLBACK — is built
by ``infrastructure/persistence/rls.py``'s ``TenantSessionFactory`` and
wired at the Composition Root; this module knows nothing about SQLAlchemy,
Postgres, or even that a relational database is involved.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


class UnitOfWork(Protocol):
    """Open ONE request-scoped transaction spanning every write a producer
    service issues inside it: the aggregate write (through a repository)
    and the ``EventOutbox.append`` call.

    ``begin`` is an async context manager yielding nothing — a caller writes
    ``async with uow.begin(ctx): ...`` purely for the transactional SIDE
    EFFECT. Nothing issued inside the block receives a session object from
    ``begin`` itself: an aggregate write through its repository, or an
    ``EventOutbox.append`` call, each resolves its OWN session through
    whatever (unit-of-work-aware) session provider it was already injected
    with, exactly as before — no existing adapter's signature has to change
    to gain atomicity (``infrastructure/persistence/outbox.py``'s own
    docstring: "closing that gap changes what ``tenant_session`` resolves
    to, not this class").
    """

    def begin(self, ctx: ExecutionContext) -> AbstractAsyncContextManager[None]: ...
