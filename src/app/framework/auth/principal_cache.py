"""The ``auth:principal:<sub>`` cache — capacity plan wave 1, step 1.1.

`ح-2` in ``docs/capacity-plan.md §2``: the authentication path runs on EVERY
request and, before a single line of the work the caller asked for, it spends
**two separate database round trips** — ``provision_on_login`` (a
``get_by_firebase_uid`` on ``workspace.users``) and ``roles_of`` (a
``list_for_user`` on ``access.role_assignments``). At the plan's `§0` target of
300 rps that is 600 queries per second the platform performs in order to
discover facts that change perhaps once a month, and it is the single largest
avoidable load on `ح-3`, the connection pool.

This holds the resolved answer instead.

**Keyed by the Firebase subject, never by the token.** The denylist's rule
(``revocation.py``), for the same reason: a cache keyed by the credential would
mean storing bearer tokens in Redis, and it would also miss the moment a user
refreshes their token — the very thing that makes a token-keyed cache both
dangerous and useless.

The plan's own text says ``(firebase_uid, workspace_id)``. It is the uid
ALONE here, because the workspace is not an input to this lookup — it is its
OUTPUT. `INV-W1` gives a user exactly one owner workspace and
``provision_on_login`` derives it from the uid; a composite key would require
the caller to already know the answer it is asking for. And keying by the uid
alone is what makes the plan's second security criterion true by construction
rather than by test: there is exactly ONE entry per subject, carrying the
workspace it was resolved with, so no principal can cross between workspaces.

**What the entry may hold, and what it may not.** Two ids, a role-name set and
one boolean. No email, no display name, no claim — `10 §10`'s rule that the
identity fields never reach a store or a log, applied to the store that would
be most convenient to put them in. That is also why an entry is worth so little
to an attacker who reads it: it names a tenant and a role, both of which the
holder of the token already knows.

**Why ``active`` is in here, when ``api/middleware/auth.py`` says it is read
fresh on every request with no cache whatsoever.** That sentence was written
before 3.79 built the denylist, and the control it describes has since moved:
the platform-admin routes that disable (``routers/admin.py``, ``PATCH
/users/{id}/status``) and delete (``DELETE /users/{id}``) an account already
write ``auth:revoked:<sub>`` in the same handler, and THAT check stays
uncached on every request. So an account disabled through the platform's own
surface is refused on the very next request by the denylist, not by the
freshness of this flag — and caching the flag costs the guarantee nothing.
What it does cost is stated rather than hidden: a status written directly in
SQL, around the API, is not seen until this entry expires. `§4`'s decision
took that trade knowingly; the ceiling below is what bounds it.

**Every write that changes an answer here invalidates it explicitly** — the
plan's "كتابةٌ تُبطِل، لا انتظارُ انتهاء TTL". The four platform-admin routes
that can change a role or an account's state call ``invalidate`` after the
database has committed, in that order, for the ordering reason the status
route's existing comment already gives: PostgreSQL is the authority, so a cache
failure can never announce a change that did not commit.

The TTL is therefore NOT the mechanism — it is the backstop for the case where
an invalidation did not happen at all: a Redis write that was lost, a replica
that was partitioned when the change went through. That is what bounds it to
five minutes rather than to a token's hour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.framework.errors import ValidationError
from app.framework.observability import get_logger
from app.framework.ports.cache_provider import CacheProvider
from app.framework.types import Uuid

_logger = get_logger(__name__)

# The key namespace, beside the denylist's `auth:revoked:` and shaped like it.
_KEY_PREFIX = "auth:principal:"

# The plan's default (`1.1`), and the value `AUTH_PRINCIPAL_CACHE_TTL_S`
# carries when nothing sets it.
DEFAULT_PRINCIPAL_CACHE_TTL_S = 60

# The backstop's ceiling — see the module docstring. Not the token's 3660s:
# this entry does not stand in for a token's validity, it stands in for a role
# lookup, and the longest a wrongly-surviving role may last after a FAILED
# invalidation is a different (and much smaller) number than the longest a
# token may live.
MAX_PRINCIPAL_CACHE_TTL_S = 300

# Stamped into every entry. A shape change gets a new number and every entry
# written by the old code reads as a MISS rather than as a mis-parsed
# principal — the one failure mode a cache of authorization facts must not
# have.
_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CachedPrincipal:
    """The four facts a request needs that cost two queries to discover.

    ``created`` is deliberately absent. It is the "this login minted the
    workspace" flag the owner grant hangs on (05 §1.5), and a cache HIT is by
    definition not that login — an entry only exists because a previous
    request already resolved this subject. Storing it would invite a reader to
    branch on a value that is always ``False``.
    """

    workspace_id: Uuid
    user_id: Uuid
    roles: frozenset[str]
    active: bool


class PrincipalCache:
    """The resolved authentication answer, over ``CacheProvider``.

    Stateless apart from its injected cache and TTL — the ``SessionRevocationList``
    shape exactly, so every replica builds its own against the same Redis and an
    invalidation written by one is seen by all of them.
    """

    def __init__(self, cache: CacheProvider, *, ttl_s: int = DEFAULT_PRINCIPAL_CACHE_TTL_S) -> None:
        self._cache = cache
        self._ttl_s = _guard_ttl(ttl_s)

    @property
    def ttl_s(self) -> int:
        """The window an un-invalidated change may survive for."""
        return self._ttl_s

    async def get(self, subject: str) -> CachedPrincipal | None:
        """The stored principal for ``subject``, or ``None`` for a miss.

        A malformed or older-schema value is a MISS, not an error: the caller
        then resolves from the database exactly as it would have anyway. The
        alternative — raising — would turn one bad key into a 500 for that
        user and, if the shape ever changed under a rolling deploy, into a 500
        for every user at once.
        """
        raw = await self._cache.get(_key_for(subject))
        if raw is None:
            return None
        return _decode(raw)

    async def put(self, subject: str, principal: CachedPrincipal) -> None:
        """Store the resolved principal for this list's TTL."""
        await self._cache.set(_key_for(subject), _encode(principal), self._ttl_s)

    async def invalidate(self, subject: str) -> None:
        """Drop ``subject``'s entry so the next request resolves it fresh.

        Idempotent, and deliberately not conditional on there BEING an entry:
        a write path must not have to ask whether a cache it does not own
        happens to be populated.
        """
        await self._cache.delete(_key_for(subject))


def _key_for(subject: str) -> str:
    """``auth:principal:<sub>``, with the denylist's own non-empty guard.

    A blank subject would make ONE key shared by every identity whose uid
    failed to arrive — which here means handing one user's workspace and roles
    to another. Refused loudly instead.
    """
    cleaned = subject.strip()
    if not cleaned:
        raise ValidationError("principal cache subject must not be empty")
    return f"{_KEY_PREFIX}{cleaned}"


def _encode(principal: CachedPrincipal) -> bytes:
    """JSON, with the roles sorted.

    Sorted so two encodings of the same principal are the same bytes — which
    is what lets an operator diff what a replica wrote against what another
    one did, and keeps a test comparing values rather than parsing them.
    """
    return json.dumps(
        {
            "v": _SCHEMA_VERSION,
            "workspace_id": principal.workspace_id,
            "user_id": principal.user_id,
            "roles": sorted(principal.roles),
            "active": principal.active,
        },
        separators=(",", ":"),
    ).encode()


def _decode(raw: bytes) -> CachedPrincipal | None:
    """The inverse, and total: anything it cannot read fully is ``None``.

    Every field is type-checked rather than trusted. The value comes from
    Redis, which is shared infrastructure — a store that another process, or
    an older version of this one, can write to. Reading a role list out of it
    without checking that it IS a list of strings would be accepting an
    authorization decision from whatever happens to be at that key.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        _logger.warning("auth.principal_cache_unreadable")
        return None
    if not isinstance(payload, dict) or payload.get("v") != _SCHEMA_VERSION:
        return None
    workspace_id = payload.get("workspace_id")
    user_id = payload.get("user_id")
    roles = payload.get("roles")
    active = payload.get("active")
    if not (
        isinstance(workspace_id, str)
        and workspace_id
        and isinstance(user_id, str)
        and user_id
        and isinstance(active, bool)
        and isinstance(roles, list)
        and all(isinstance(role, str) for role in roles)
    ):
        return None
    return CachedPrincipal(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=frozenset(roles),
        active=active,
    )


def _guard_ttl(ttl_s: int) -> int:
    """Fail fast at CONSTRUCTION — the ``SessionRevocationList`` precedent.

    Zero is refused here rather than silently meaning "off", because "off" is
    a wiring decision and not a TTL: the Composition Root builds NO cache at
    all for ``AUTH_PRINCIPAL_CACHE_TTL_S=0``, so a baseline run pays not even
    a Redis round trip (`م-8`: a measurement taken on a tuned server cannot
    later answer "did the tuning help?").
    """
    if ttl_s <= 0:
        raise ValidationError("principal cache ttl_s must be positive")
    if ttl_s > MAX_PRINCIPAL_CACHE_TTL_S:
        raise ValidationError(
            f"principal cache ttl_s must not exceed {MAX_PRINCIPAL_CACHE_TTL_S}s: "
            "every write path invalidates explicitly, so this bounds only how long a "
            "role change whose invalidation FAILED may survive"
        )
    return ttl_s
