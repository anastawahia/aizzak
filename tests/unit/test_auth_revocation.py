"""Unit tests for the ``auth:revoked:<sub>`` denylist
(``framework/auth/revocation.py``, 3.79).

The guard's USE of the list (a revoked subject meets a 401, and meets it before
provisioning spends anything) is pinned in ``test_api_auth.py``, next to the
rest of the authentication path. What is pinned HERE is the list's own
contract, and every assertion below corresponds to a sentence of the
specification the 2.7 adapter wrote for it: the key SHAPE (the ``sub``, never
the token), the TTL CEILING (past which no token it could deny still exists),
and the miss/outage split (a miss is not revoked; an outage is not a miss).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.framework.auth.revocation import MAX_REVOCATION_TTL_S, SessionRevocationList
from app.framework.errors import AppError, ValidationError


@dataclass
class _RecordingCache:
    """A structural ``CacheProvider`` that REMEMBERS the ttl (the
    ``support_integrations.DictCache`` fake plus the one field this suite has
    to assert on)."""

    values: dict[str, bytes] = field(default_factory=dict)
    ttls: dict[str, int | None] = field(default_factory=dict)

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_s

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def incr(self, key: str, amount: int = 1) -> int:
        return amount

    async def expire(self, key: str, ttl_s: int) -> None:
        return None


class _BrokenCache(_RecordingCache):
    """Redis is down: the real adapter translates the driver failure into an
    ``AppError``/``common.internal`` rather than returning ``None``."""

    async def get(self, key: str) -> bytes | None:
        raise AppError("redis is unreachable", code="common.internal")


# --------------------------------------------------------------------------- #
# The key                                                                     #
# --------------------------------------------------------------------------- #
async def test_the_key_is_the_specified_namespace_over_the_subject() -> None:
    """``auth:revoked:<sub>``, verbatim. The prefix is what lets an operator
    (and a Redis eviction policy) see this family for what it is."""
    cache = _RecordingCache()

    await SessionRevocationList(cache).revoke("firebase-uid-1")

    assert list(cache.values) == ["auth:revoked:firebase-uid-1"]


async def test_the_stored_value_carries_nothing_about_the_session() -> None:
    """A membership set, not a record. A reason, a timestamp or a user id here
    would be identity data sitting in a store whose whole justification is that
    it holds nothing worth stealing — and a token here would be the control
    creating the asset it exists to contain."""
    cache = _RecordingCache()

    await SessionRevocationList(cache).revoke("firebase-uid-1")

    assert set(cache.values.values()) == {b"1"}


@pytest.mark.parametrize("subject", ["", "   "])
async def test_a_blank_subject_is_refused_rather_than_shared(subject: str) -> None:
    """One key that every blank subject shares would deny an identity nobody
    has — or, if an identity ever failed to parse, deny everyone."""
    cache = _RecordingCache()

    with pytest.raises(ValidationError):
        await SessionRevocationList(cache).revoke(subject)
    with pytest.raises(ValidationError):
        await SessionRevocationList(cache).is_revoked(subject)
    assert cache.values == {}


# --------------------------------------------------------------------------- #
# The TTL                                                                     #
# --------------------------------------------------------------------------- #
async def test_the_default_ttl_is_the_residual_token_lifetime() -> None:
    """3600s of Firebase token life + the 2.7 verifier's 60s leeway. Longer
    denies nothing extra (the token is expired and refused on ``exp`` anyway)
    and only keeps a legitimate re-login locked out."""
    cache = _RecordingCache()

    await SessionRevocationList(cache).revoke("uid")

    assert MAX_REVOCATION_TTL_S == 3660
    assert cache.ttls["auth:revoked:uid"] == 3660


async def test_a_shorter_ttl_is_allowed_and_used() -> None:
    cache = _RecordingCache()

    await SessionRevocationList(cache, ttl_s=300).revoke("uid")

    assert cache.ttls["auth:revoked:uid"] == 300


@pytest.mark.parametrize("ttl_s", [0, -1, MAX_REVOCATION_TTL_S + 1, 86_400])
def test_an_out_of_range_ttl_fails_at_construction(ttl_s: int) -> None:
    """The ``FirebaseAuth._guard_ttl`` precedent: fail fast at construction,
    which in production is boot — never at the first revocation, when an
    operator is already dealing with an incident."""
    with pytest.raises(ValidationError):
        SessionRevocationList(_RecordingCache(), ttl_s=ttl_s)


# --------------------------------------------------------------------------- #
# Miss vs outage                                                              #
# --------------------------------------------------------------------------- #
async def test_an_unknown_subject_is_not_revoked() -> None:
    assert await SessionRevocationList(_RecordingCache()).is_revoked("uid") is False


async def test_a_revoked_subject_reads_back_as_revoked() -> None:
    cache = _RecordingCache()
    revocations = SessionRevocationList(cache)

    await revocations.revoke("uid")

    assert await revocations.is_revoked("uid") is True
    assert await revocations.is_revoked("another-uid") is False


async def test_revoking_twice_is_idempotent() -> None:
    cache = _RecordingCache()
    revocations = SessionRevocationList(cache, ttl_s=300)

    await revocations.revoke("uid")
    await revocations.revoke("uid")

    assert cache.values == {"auth:revoked:uid": b"1"}
    assert cache.ttls == {"auth:revoked:uid": 300}


async def test_a_cache_outage_propagates_rather_than_reading_as_not_revoked() -> None:
    """The one behaviour a denylist must never have: quietly stopping denying
    while its store is down. The error is the adapter's own
    ``common.internal``, and nothing here catches it."""
    with pytest.raises(AppError):
        await SessionRevocationList(_BrokenCache()).is_revoked("uid")
