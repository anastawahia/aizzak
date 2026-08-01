"""Session revocation — the WRITER of the ``auth:revoked:<sub>`` denylist
(3.79).

**Why an operational entrypoint and not a route.** The 2.7 adapter's D7
paragraph names "the logout/disable/delete use-case" as this list's writer, and
v1 has none: ``03 §1``'s resource map declares no logout, no user-disable and
no user-delete operation, and ``openapi.yaml`` declares no such path. Building
an application service for a caller that does not exist would ship exactly the
unreachable surface this repository refuses elsewhere. What DOES exist is the
operator, and the platform already has a home for the operator's artifacts:
``app.ops`` (``provision.py``, created in 7.1 for the same reason — a runbook
step that had no code). So the writer is a command, and the moment a logout
route is designed it calls ``SessionRevocationList.revoke`` directly; nothing
here has to move.

This is a composition root for its own one-shot process, exactly like
``workers/bootstrap.py``: it may import ``app.infrastructure``, it builds the
one adapter it needs, and it closes it before exiting.

**Usage** (the subject is the Firebase ``sub``/uid, which is what the API logs
as ``uid`` on a refused request — never a token, never an email)::

    python -m app.ops.revoke <firebase_uid> [<firebase_uid> ...]

Effect: within one cache round trip, every replica sharing that Redis refuses
that subject's requests with the same opaque ``auth.invalid_token``/401 any
other bad token gets. The entry expires on its own after at most
``MAX_REVOCATION_TTL_S`` — by which point no token it could deny still exists —
so there is no "un-revoke" command and deliberately no way for this tool to
lock an account out permanently. Permanent removal is a different decision with
a different mechanism (the account's own ``status``, read fresh by the guard on
every request).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.framework.auth.revocation import MAX_REVOCATION_TTL_S, SessionRevocationList
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import RedisCache, create_redis_client
from app.infrastructure.config import load_settings

_logger = logging.getLogger(__name__)


async def revoke_subjects(redis: RedisSettings, subjects: tuple[str, ...]) -> None:
    """Deny every named subject, then close the client.

    The client is closed in a ``finally`` for the ``provision.py`` reason: this
    is a short-lived process, and a connection left open is a connection the
    server side keeps until its own timeout — noise an operator running the
    tool in a loop would notice first.
    """
    client = create_redis_client(redis)
    try:
        revocations = SessionRevocationList(RedisCache(client))
        for subject in subjects:
            await revocations.revoke(subject)
            _logger.info("ops.revoked", extra={"subject": subject, "ttl_s": MAX_REVOCATION_TTL_S})
    finally:
        await client.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    subjects = tuple(argument for argument in sys.argv[1:] if argument.strip())
    if not subjects:
        raise SystemExit(
            "usage: python -m app.ops.revoke <firebase_uid> [<firebase_uid> ...]\n"
            "The subject is the Firebase uid (the token's `sub`), which is what the "
            "API logs as `uid` — never a token and never an email."
        )
    asyncio.run(revoke_subjects(load_settings().redis, subjects))


if __name__ == "__main__":
    main()
