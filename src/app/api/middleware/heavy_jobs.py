"""The heavy-job route guard — ``Depends(heavy_job)`` (capacity-plan step 1.3 ·
07 §4, "حدّ المعدّل — مهام ثقيلة/مستخدم = 30 job/min").

One dependency, declared beside the route it bounds, exactly as ``require`` is:

    @router.post("/jobs", status_code=202, dependencies=[
        Depends(require(Permission.MEDIA_CREATE)), Depends(heavy_job)
    ])

**Which routes, and the rule that decides it.** The four operations that answer
**202**, and nothing else. 202 is not incidental in this API — it is the status
every route uses to say "a worker will do this later, here is the job to await"
(``routers/media.py``, ``routers/knowledge.py``), so the set of 202s IS the set
of queue entrances, and ``test_heavy_job_limit.py`` pins the two to each other
in BOTH directions rather than leaving four decorators to be kept in step by
hand. A fifth queueing route added without this guard fails that test on the
day it is written, which is the only moment the omission is cheap.

**What is deliberately NOT charged, and why each one would be a defect:**

* **``POST /files`` and ``POST /files/{id}/complete``.** An upload registers a
  slot and completes bytes; neither puts anything on a stream
  (``files.file.uploaded.v1`` is published and has no consumer —
  ``workers/bootstrap.py`` says so in as many words, and indexing has been
  explicit since it stopped happening automatically). Charging them would make
  ONE document cost THREE units of a thirty-unit budget, so "30 job/min" would
  be enforced as ten — the shape 1.2 caught inside FastAPI's dependency cache,
  where a ceiling announced as 120 came within a hair of being enforced as 60.
  Both stay bounded by 1.2's 120/min request ceiling and by the space quota.
* **``POST /workflows/{key}/run`` and the agent invocations.** Expensive, and
  not jobs: they run on the request's own connection and answer with the work
  itself, not with a receipt for it. What bounds them is concurrency (wave 4)
  and the in-flight guard, not a per-minute job budget.
* **The two ``.../cancel`` routes.** Cancelling REMOVES work from the queue. A
  ceiling that refused it would trap a user who has just filled their budget in
  exactly the state the budget exists to end, and would leave the jobs running
  while it did.

**Order on the route: after ``require``, never before.** A caller who lacks
``knowledge:manage`` must be told which permission it lacks, because that is
the answer it can act on; a 429 in its place would leave a client retrying a
request that can never succeed. It is also the honest accounting — a request
about to be refused 403 was never going to reach the queue, so charging it
would spend a budget on work the platform never accepted. 1.2 drew the
identical line between 401 and 429; this is that reasoning a second time.

**And it runs BEFORE the handler, which is the whole of the acceptance
criterion's second half** ("ولا يُقبَل عملٌ في المجرى بعد الرفض"). A route
dependency is solved before the handler body executes, so a refusal lands
before the idempotency ledger is claimed, before the aggregate is written, and
before its outbox row exists. A guard inside the handler — or inside the
``idempotent`` closure — would have had to undo work instead of declining it.

**The price of that position, stated rather than hidden: an idempotent REPLAY
is charged.** A retried ``POST /media/jobs`` carrying the same
``Idempotency-Key`` queues nothing, and this guard cannot know that without
performing the ledger read it is standing in front of. Charging it is the right
side to err on for a capacity control: what the ceiling bounds is the arrival
rate at the queue's door, and a client retrying thirty times a minute produces
that load whether or not its thirtieth attempt creates a row.

**Unwired is unlimited, not refused.** ``services.heavy_job_limiter`` is
``None`` for a hermetic test application and for a deployment running
``API_RATE_LIMIT_ENABLED=false`` (`م-8`), and in both the route behaves exactly
as it did before 1.3. That is the ``rate_limiter`` precedent and NOT the
``file_replacement`` one: a missing capacity control costs throughput
protection, while a missing cascade would have silently corrupted data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.v1.dependencies import Principal, Services, current_principal


async def heavy_job(
    principal: Annotated[Principal, Depends(current_principal)], services: Services
) -> None:
    """Charge this submission to its user's job ceiling, or raise the 429.

    A plain function rather than ``rbac``'s callable-object-from-a-factory:
    that one carries a permission worth reading back off the finished route,
    while this takes no parameter at all, so ``dependency.call is heavy_job``
    is already the sharpest question a test can ask of a route.

    **``Principal`` and not ``Context``, which is a type distinction and not a
    stylistic one.** The guard beside it reads ``ctx.roles`` and so takes the
    context; this needs the authenticated user, and ``ExecutionContext.user_id``
    is ``Uuid | None`` — legitimately, since a worker's context has no user at
    all. Keying a per-user ceiling off an optional would leave exactly two
    ways out, and both are worse than asking for the right type: refuse a
    request with no user (a branch no HTTP request can reach, so a guard
    nothing tests), or skip the ceiling for it (a hole that opens the moment
    somebody constructs a context differently). The principal is where the id
    is not optional, because a request that has one is the only kind that gets
    this far.

    Depending on ``current_principal`` also costs nothing: it is the same
    dependency the router already hangs, FastAPI solves it once per request,
    and it is where 1.2's request ceiling is charged — so both of a caller's
    ceilings meet the request at the same seam.
    """
    limiter = services.heavy_job_limiter
    if limiter is None:
        return
    await limiter.check(user_id=principal.user_id)
