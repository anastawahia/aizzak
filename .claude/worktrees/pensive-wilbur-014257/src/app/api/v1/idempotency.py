"""The ``Idempotency-Key`` seam at the API boundary (03-api-spec §0 · 3.79).

``openapi.yaml`` declares the header on exactly three operations —
``registerFile``, ``createMediaJob``, ``runWorkflow`` — and 03 §0 promises
"إعادة المحاولة آمنة". From 6.1-هـ-3 until 3.79 the header was declared in the
contract, absent from every handler signature (so absent from the GENERATED
OpenAPI too) and silently discarded: a client that sent one on a billable
create got no protection at all and no way to find that out. This module is
that promise, made real.

**The shape: one function the handlers call, not a middleware.** A middleware
would have to re-derive which operations opt in, buffer every response body to
find out whether it should have stored one, and reason about streaming
responses it cannot store — all to avoid three call sites. The explicit call
keeps `10 §3`'s "thin router" honest (the handler still just validates a DTO
and calls a use-case; this wraps that call) and makes the opt-in visible in the
one place a reader looks: the route.

**Semantics, in the order they are decided:**

* **No header ⇒ nothing happens.** Byte-for-byte the pre-3.79 behaviour,
  including the absence of any database round trip. The header is OPTIONAL in
  ``openapi.yaml`` and making it load-bearing would break every existing
  client.
* **Same key, same request ⇒ the FIRST response**, replayed from the store.
  No second resource is created, nothing is billed twice.
* **Same key, DIFFERENT request ⇒ ``common.conflict``/409.** The code comes
  from the catalog (``framework/errors.py``: "Optimistic-lock or uniqueness
  conflict"), not from a new one invented for this feature — a key is a
  uniqueness claim, and reusing it for different content is precisely a
  uniqueness conflict. RFC 9457's requirement is a stable, documented ``type``,
  which the existing entry already gives.
* **Same key, same request, still in flight ⇒ ``common.conflict``/409 too.**
  A concurrent duplicate has no first response to replay yet, and the only
  alternatives are worse: running it anyway defeats the entire purpose, and
  blocking would hold a connection on a request whose sibling may take minutes.
  409 tells the client "your other attempt is live", and it is retryable.

**What is NOT covered, stated rather than hidden:** the streaming answer of
``runWorkflow``. An SSE stream is consumed, not stored, so there is no response
body to replay and no honest way to fake one — see ``routers/workflows.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header
from pydantic import BaseModel

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from app.framework.ports.idempotency_store import ClaimOutcome, IdempotencyStore

# The header exactly as `openapi.yaml`'s `components.parameters.IdempotencyKey`
# declares it: a header, optional, plain string. Annotated so FastAPI puts it in
# the GENERATED document too — the divergence 3.79 closed was that the hand-
# written contract declared it and `/openapi.json` did not.
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "Client-supplied key making a retry of this create safe. A repeat "
            "with the same key and the same body returns the first response; a "
            "repeat with a different body is a conflict."
        ),
    ),
]

# A bound, so the ledger's primary key cannot be used as a storage channel. 255
# is the ordinary header-value budget a client library assumes; anything longer
# is a bug or an abuse, and either way is better refused at the edge than stored.
_MAX_KEY_LENGTH = 255


def request_hash(body: BaseModel) -> str:
    """A stable fingerprint of the request DTO.

    Hashed rather than stored: the body may carry a prompt, a filename, or any
    other tenant content, and the ledger has no business keeping a second copy
    of it just to answer "is this the same request?". ``sort_keys`` makes the
    fingerprint independent of field ordering, and ``model_dump(mode="json")``
    normalises through the same JSON coercion the wire already applied, so a
    client that resends a byte-identical body always hashes identically.
    """
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def idempotent[T: BaseModel](
    store: IdempotencyStore,
    ctx: ExecutionContext,
    *,
    endpoint: str,
    key: str | None,
    body: BaseModel,
    model: type[T],
    run: Callable[[], Awaitable[T]],
) -> T:
    """Run ``run`` at most once per ``(workspace, endpoint, key)``.

    ``model`` is required rather than inferred from ``run``'s annotation: the
    replay path rebuilds the stored JSON into the SAME response DTO the first
    attempt returned, and validating it back through the model is what stops a
    schema change from silently replaying a body the current contract no longer
    describes.

    The failure path releases the claim. That is the difference between a
    transient 500 and a key that is bricked forever — and it is why ``release``
    exists on the port at all.
    """
    if key is None:
        return await run()
    _guard_key(key)

    claim = await store.claim(ctx, endpoint=endpoint, key=key, request_hash=request_hash(body))
    if claim.outcome is ClaimOutcome.REPLAY:
        return model.model_validate(claim.response_body)
    if claim.outcome is ClaimOutcome.MISMATCH:
        raise ConflictError(
            f"Idempotency-Key {key!r} was already used on {endpoint} with a different request body"
        )
    if claim.outcome is ClaimOutcome.IN_PROGRESS:
        raise ConflictError(
            f"Idempotency-Key {key!r} is already in flight on {endpoint}; retry shortly"
        )

    try:
        result = await run()
    except BaseException:
        # BaseException, not Exception: a cancelled request (the client hung
        # up) left the claim just as unusable as a raised one, and leaking it
        # would mean the client's own retry meets its own abandoned attempt.
        await store.release(ctx, endpoint=endpoint, key=key)
        raise
    await store.complete(
        ctx, endpoint=endpoint, key=key, response_body=result.model_dump(mode="json")
    )
    return result


def _guard_key(key: str) -> None:
    """Reject a key that cannot be a key. ``422`` (``common.validation_error``)
    rather than 409: nothing conflicted — the request itself is malformed."""
    if not key.strip():
        raise ValidationError("Idempotency-Key must not be blank")
    if len(key) > _MAX_KEY_LENGTH:
        raise ValidationError(f"Idempotency-Key must be at most {_MAX_KEY_LENGTH} characters")
