"""The request ceilings this API enforces — capacity-plan steps 1.2 and 1.3.

``Limits.api_rate_per_min = 120`` was declared in 07 §4 and read by nothing;
this is where it starts being enforced. It is enforced AFTER authentication
and against the USER, not the IP, because the two answer different questions:
nginx's ``limit_req`` per IP is a flood shield in front of a machine that
cannot yet know who is calling, while this is fairness between the people the
platform has identified. An office behind one NAT is a single IP and dozens of
legitimate users; a stolen token is one user across a botnet's worth of them.

**Two buckets, and the second is the whole point.** A per-user ceiling isolates
nobody on a multi-tenant platform: a workspace with fifty users reaches 6,000
requests a minute with every single one of them comfortably inside their own
limit, and the neighbour whose p95 that ruins never sees a 429 either. The
tenant ceiling is what turns "fairness between tenants" from an intention into
something a test can fail. Both are consumed in ONE atomic call, all or
nothing, so a request the tenant ceiling refuses has not spent the user's
allowance (``ports/rate_limiter.py`` requirement 2).

**Order: the user bucket first.** When a single client is hammering the API,
it should be told IT is over its limit; reporting the tenant ceiling instead
would point an operator at the workspace and at the fifty innocent colleagues
inside it.

**⚠️ The failure policy here is the opposite of ``ConnectionHub``'s: it fails
OPEN**, and 1.2 says so in as many words. A rate limiter is not a security
control — refusing a request is never *required* for correctness, it only
protects capacity — so a Redis outage that made this fail closed would convert
a degraded dependency into a total outage of a platform that was otherwise
perfectly able to serve. nginx's per-IP ceiling stays underneath as the shield
that does not depend on Redis at all. The denylist next door reasons the other
way round for the same reason: THAT one is a security control, and an
un-checkable revocation must refuse.

Every fail-open is counted (``unavailable``) rather than merely logged,
because the dangerous property of failing open is that it is INVISIBLE — the
platform serves normally, with no ceiling on it, and nothing in the request
stream looks different.

**Where it is called from, and why not from a middleware.** ``current_principal``
in ``api/v1/dependencies.py``: the limit is per user, so it cannot be applied
before the user is known, and every protected router hangs a router-level
``Depends(current_principal)``, which makes that function the one gate every
authenticated request already passes. An ASGI middleware would have to
authenticate a second time to learn the same fact. The burst guard that DOES
belong at the transport layer is a separate mechanism and lives in
``inflight.py``.

**Step 1.3 adds a THIRD ceiling here: heavy jobs, per user.** 07 §4 has
declared "30 job/min" for as long as it has declared the 120 req/min above,
and until now it was read by exactly as much -- nothing at all. It is
a separate class rather than a third bucket on ``ApiRateLimiter`` because the
two are charged at different moments to different populations: EVERY
authenticated request pays the first, and only a submission on one of the four
operations that answer **202** pays this one. Folding them into one call would
charge every ``GET`` against a job ceiling it can never reach, and would leave
the metric unable to say which of the two a refusal came from.

Everything that is POLICY is shared, and deliberately: the same port, the same
sliding log, the same computed ``Retry-After``, and the same fail-open answer
to a Redis that does not reply -- a job ceiling protects capacity too, and an
outage that refused every upload would BE the outage it exists to prevent.
Where the ceiling is hung, and on what, is ``api/middleware/heavy_jobs.py``.
"""

from __future__ import annotations

from app.framework.errors import AppError, RateLimitedError, ValidationError
from app.framework.observability import get_logger
from app.framework.observability.metrics import api_rate_limit_total, heavy_job_limit_total
from app.framework.ports.rate_limiter import RateBucket, RateLimiter
from app.framework.types import Uuid

_logger = get_logger(__name__)

# Both ceilings are per MINUTE — 07 §4 declares them that way and the
# acceptance criterion ("the 121st request in a minute") is written in it.
_WINDOW_S = 60

# The bucket scopes. Constants rather than literals because they are metric
# label values and the refusal messages are keyed by them.
USER_SCOPE = "user"
WORKSPACE_SCOPE = "workspace"
# 1.3's scope. A third value in the same namespace, so its bucket key cannot
# collide with the per-user REQUEST bucket above: `heavy:<user>` and
# `user:<user>` are different sorted sets for the same person, which is what
# lets one of that person's ceilings be reached without moving the other.
HEAVY_SCOPE = "heavy"

# What the client is told. The scope is named, the ceiling and the identifiers
# are not: a refused caller learns which of its own limits it hit, and nothing
# about the tenant it shares or how much room the platform has left.
_DETAILS = {
    USER_SCOPE: "request rate limit exceeded for this user",
    WORKSPACE_SCOPE: "request rate limit exceeded for this workspace",
    HEAVY_SCOPE: "heavy job rate limit exceeded for this user",
}


class ApiRateLimiter:
    """The two ceilings of step 1.2, over a ``RateLimiter`` port.

    Stateless apart from its injected limiter and its two numbers — the
    ``PrincipalCache`` shape — so every replica builds its own against the
    same Redis and they enforce ONE ceiling between them rather than one each.
    """

    def __init__(self, limiter: RateLimiter, *, user_per_min: int, workspace_per_min: int) -> None:
        self._limiter = limiter
        self._user_per_min = _guard("user_per_min", user_per_min)
        self._workspace_per_min = _guard("workspace_per_min", workspace_per_min)

    @property
    def user_per_min(self) -> int:
        return self._user_per_min

    @property
    def workspace_per_min(self) -> int:
        return self._workspace_per_min

    async def check(self, *, user_id: Uuid, workspace_id: Uuid) -> None:
        """Admit this request, or raise the 429 the API contract renders.

        ``RateLimitedError`` carries the computed ``Retry-After``, which
        ``api/main.py``'s ``AppError`` handler turns into the RFC 9110 header
        on an RFC 9457 body. The number is the instant the binding window's
        oldest entry falls out of it — measured, not a fixed guess, which is
        exactly why 3.79 left the header unshipped until something could
        compute one.
        """
        buckets = (
            RateBucket(
                scope=USER_SCOPE,
                key=f"{USER_SCOPE}:{user_id}",
                limit=self._user_per_min,
                window_s=_WINDOW_S,
            ),
            RateBucket(
                scope=WORKSPACE_SCOPE,
                key=f"{WORKSPACE_SCOPE}:{workspace_id}",
                limit=self._workspace_per_min,
                window_s=_WINDOW_S,
            ),
        )
        try:
            verdict = await self._limiter.try_consume(buckets)
        except AppError as exc:
            # Fail OPEN -- see the module docstring. Counted, and logged at
            # WARNING with the code only: this line fires once per request for
            # as long as the outage lasts, so it carries nothing per-request
            # that would make it expensive or identifying.
            api_rate_limit_total.labels(outcome="unavailable").inc()
            _logger.warning("api.rate_limit_unavailable", extra={"error_code": exc.code})
            return
        if verdict.allowed:
            api_rate_limit_total.labels(outcome="allowed").inc()
            return
        api_rate_limit_total.labels(outcome=f"refused_{verdict.scope}").inc()
        raise RateLimitedError(
            _DETAILS.get(verdict.scope, "request rate limit exceeded"),
            retry_after_s=verdict.retry_after_s,
        )


class HeavyJobRateLimiter:
    """07 §4's "30 job/min" ceiling -- capacity-plan step 1.3.

    ONE bucket, and per USER, because that is the row 07 §4 writes ("حدّ
    المعدّل — مهام ثقيلة/مستخدم"). It is not the tenant ceiling in miniature:
    ``ApiRateLimiter``'s workspace bucket already bounds how many REQUESTS a
    tenant may make in a minute, and this bounds how many of one person's
    requests may be the expensive kind. **A tenant-wide job ceiling is a
    number 07 §4 does not declare, so it is not invented here** -- it is
    recorded as an observation in ``capacity-status.md`` instead, where a
    number nobody has signed belongs.

    Same shape as its neighbour and for the same reasons: stateless over an
    injected limiter, so every replica builds its own against one Redis and
    they hold ONE ceiling between them rather than one each.
    """

    def __init__(self, limiter: RateLimiter, *, jobs_per_min: int) -> None:
        self._limiter = limiter
        self._jobs_per_min = _guard("jobs_per_min", jobs_per_min)

    @property
    def jobs_per_min(self) -> int:
        return self._jobs_per_min

    async def check(self, *, user_id: Uuid) -> None:
        """Charge one job to this user, or raise the 429 the contract renders.

        A single bucket still goes through ``try_consume``'s sequence
        interface rather than a scalar shortcut, because the port's
        all-or-nothing guarantee is stated over a sequence and a caller that
        quietly took a different path would be the caller that stops getting
        it when a second bucket is added.
        """
        bucket = RateBucket(
            scope=HEAVY_SCOPE,
            key=f"{HEAVY_SCOPE}:{user_id}",
            limit=self._jobs_per_min,
            window_s=_WINDOW_S,
        )
        try:
            verdict = await self._limiter.try_consume((bucket,))
        except AppError as exc:
            # Fail OPEN, exactly as the request ceilings do. The reasoning
            # transfers WHOLE: this is a capacity control, not a security one,
            # and a Redis outage that refused every upload and every index
            # request would convert a degraded dependency into the exact
            # symptom the ceiling exists to prevent.
            heavy_job_limit_total.labels(outcome="unavailable").inc()
            _logger.warning("api.heavy_job_limit_unavailable", extra={"error_code": exc.code})
            return
        if verdict.allowed:
            heavy_job_limit_total.labels(outcome="allowed").inc()
            return
        heavy_job_limit_total.labels(outcome="refused").inc()
        raise RateLimitedError(_DETAILS[HEAVY_SCOPE], retry_after_s=verdict.retry_after_s)


def _guard(name: str, per_min: int) -> int:
    """Fail fast at CONSTRUCTION — the ``PrincipalCache`` precedent.

    Zero is refused rather than quietly meaning "off", because off is a WIRING
    decision and not a ceiling: ``API_RATE_LIMIT_ENABLED=false`` builds no
    limiter at all, so a baseline run makes not even a Redis round trip
    (`م-8` — a measurement taken with the guard half-installed answers
    nothing). A zero accepted here would instead refuse every request in the
    platform, and it would do it with a perfectly well-formed 429.
    """
    if per_min <= 0:
        raise ValidationError(f"rate limiter {name} must be positive")
    return per_min
