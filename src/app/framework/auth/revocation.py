"""The ``auth:revoked:<sub>`` session denylist (3.79).

``infrastructure/auth/firebase_auth.py``'s D7 paragraph specified this exactly
and then left it unbuilt, so v1 shipped with the residual risk it names: **a
token stolen before revocation stays usable until ``exp``** (<= 3600s + 60s
leeway). Every word of that specification is honoured here, because each one is
load-bearing:

* **Over ``CacheProvider``, keyed ``auth:revoked:<sub>``.** The Firebase
  ``sub``, never the token. A denylist keyed by the token would mean storing
  bearer credentials in Redis to decide whether to reject bearer credentials —
  a store worth stealing, created by the control meant to contain a theft
  (``10 §10``: tokens are never logged, and this is the same rule applied to
  persistence).
* **Checked by the 6.4 guard, NOT by the 2.7 adapter.** ``verify_token`` stays
  a pure, zero-extra-I/O function: that purity is what D-25 buys (no network
  round trip per verification), and putting a cache read inside it would make
  every future caller of the port pay for a policy that belongs to the request
  path. ``api/middleware/auth.py`` does the check.
* **Short TTL, capped at the residual token lifetime.** A revocation only has
  to outlive the longest-lived token that could still be presented; past that
  point ``verify_token`` refuses the token on ``exp`` anyway, so a longer entry
  denies nothing extra — it only keeps a legitimate re-login locked out and
  keeps a key alive in a store sized for short-lived things.

**The consequence of keying by ``sub``, stated rather than discovered.** The
entry blocks that subject entirely for its TTL, including a FRESH login with
new credentials. That is correct for what this control is for — an operator
killing a compromised session — and it is the price of not storing tokens.
A logout-shaped "revoke only the session I am holding" would need a per-token
identifier, which is exactly the thing the specification refuses to store.

**Fail-open on absence, never on failure.** A cache MISS means "not revoked"
and the request proceeds — that is the overwhelmingly common case and the only
sane default for a denylist. A cache OUTAGE is not a miss: the ``CacheProvider``
adapter raises, the guard does not catch it, and the request dies as
``common.internal``/500. That is the authentication path's existing posture
(``api/middleware/auth.py``: "Nothing here catches broadly — an infrastructure
failure surfaces as ``common.internal``/500"), and it is fail-closed for
access, which is what a security control must be. Swallowing the error would
turn "Redis is down" into "revocation is off" precisely when an attacker would
most like it to be.
"""

from __future__ import annotations

from app.framework.errors import ValidationError
from app.framework.ports.cache_provider import CacheProvider

# The key namespace, verbatim from the 2.7 adapter's specification.
_KEY_PREFIX = "auth:revoked:"

# The ceiling: a Firebase ID token lives 3600s and the 2.7 adapter verifies it
# with `leeway=60.0`, so 3660s after a revocation there is no token in existence
# that this entry could still deny. Named from those two numbers rather than
# written as a literal, so a change to either is a change to this bound.
_TOKEN_LIFETIME_S = 3600
_VERIFY_LEEWAY_S = 60
MAX_REVOCATION_TTL_S = _TOKEN_LIFETIME_S + _VERIFY_LEEWAY_S

# One byte, and it is a placeholder. The denylist answers a membership
# question; a value carrying a reason, a timestamp or a user id would be
# tenant/identity data sitting in a store whose whole justification is that it
# holds nothing worth stealing.
_PRESENT = b"1"


class SessionRevocationList:
    """The denylist itself — a membership set over ``CacheProvider``.

    Stateless apart from its injected cache and TTL, like every other seam
    here, so the API guard and the ops entrypoint can each build their own
    against the same Redis without coordinating.
    """

    def __init__(self, cache: CacheProvider, *, ttl_s: int = MAX_REVOCATION_TTL_S) -> None:
        self._cache = cache
        self._ttl_s = _guard_ttl(ttl_s)

    async def revoke(self, subject: str, *, ttl_s: int | None = None) -> None:
        """Deny ``subject`` for this list's TTL.

        Idempotent by construction: re-revoking simply rewrites the key and
        restarts its TTL, which is the behaviour an operator running the tool
        twice would expect anyway.
        """
        await self._cache.set(_key_for(subject), _PRESENT, _guard_ttl(ttl_s or self._ttl_s))

    async def clear(self, subject: str) -> None:
        """Remove a temporary denylist entry after an account is re-enabled.

        This is intentionally an application-only recovery path, not an ops
        command: it is called only after the durable account status has been
        changed back to ``active`` and that transition has been audited.
        """
        await self._cache.delete(_key_for(subject))

    async def is_revoked(self, subject: str) -> bool:
        """Whether ``subject``'s sessions are currently denied.

        A miss is a plain ``False`` (see the module docstring); an outage is
        the adapter's own error, deliberately not caught here.
        """
        return await self._cache.get(_key_for(subject)) is not None


def _key_for(subject: str) -> str:
    """``auth:revoked:<sub>``, with the same non-empty guard the 2.7 adapter
    applies to every identity field it trusts.

    A blank subject would make ONE key that every blank-subject lookup shares —
    a denylist entry that denies an identity nobody has, or worse, denies
    everyone whose identity failed to parse. Refused loudly instead.
    """
    cleaned = subject.strip()
    if not cleaned:
        raise ValidationError("revocation subject must not be empty")
    return f"{_KEY_PREFIX}{cleaned}"


def _guard_ttl(ttl_s: int) -> int:
    """Fail fast at CONSTRUCTION, the ``FirebaseAuth._guard_ttl`` precedent —
    a denylist whose entries outlive every token they could deny is a memory
    leak dressed as a security control, and one whose TTL is zero is not a
    denylist at all."""
    if ttl_s <= 0:
        raise ValidationError("revocation ttl_s must be positive")
    if ttl_s > MAX_REVOCATION_TTL_S:
        raise ValidationError(
            f"revocation ttl_s must not exceed {MAX_REVOCATION_TTL_S}s "
            "(a Firebase ID token's 3600s lifetime plus the verifier's 60s leeway): "
            "past that point no token this entry could deny still exists"
        )
    return ttl_s
