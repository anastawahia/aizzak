"""The principal cache — ``framework/auth/principal_cache.py`` (capacity-plan 1.1).

Hermetic: one dict behind the ``CacheProvider`` protocol, so nothing here needs
Redis. What it tests is what a cache of AUTHORIZATION facts has to get right and
an ordinary cache does not:

* an entry that cannot be read fully is a MISS, never a half-read principal and
  never a raise — the two ways a shared store turns one bad key into either an
  escalation or an outage;
* the entry holds ids and role names and nothing else, so a reader of the Redis
  keyspace learns nothing the token holder did not already know;
* one subject has exactly ONE key, which is the whole of "a principal cannot
  cross workspaces";
* the TTL is bounded at construction, because a TTL is the window a FAILED
  invalidation survives for.

The behaviour on the authentication path itself — when it is consulted, what it
does not skip, and which routes invalidate it — is in ``test_api_auth.py``,
where the real authenticator and the real routes are.
"""

from __future__ import annotations

import json

import pytest

from app.framework.auth.principal_cache import (
    DEFAULT_PRINCIPAL_CACHE_TTL_S,
    MAX_PRINCIPAL_CACHE_TTL_S,
    CachedPrincipal,
    PrincipalCache,
)
from app.framework.errors import ValidationError
from tests.unit.support_integrations import DictCache

_UID = "firebase-uid-1"
_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_KEY = f"auth:principal:{_UID}"


def _principal(
    *,
    workspace_id: str = _W1,
    user_id: str = _U1,
    roles: frozenset[str] = frozenset({"owner", "member"}),
    active: bool = True,
) -> CachedPrincipal:
    return CachedPrincipal(workspace_id=workspace_id, user_id=user_id, roles=roles, active=active)


# --------------------------------------------------------------------------- #
# The round trip                                                              #
# --------------------------------------------------------------------------- #
async def test_a_stored_principal_reads_back_identical() -> None:
    cache = DictCache()
    principals = PrincipalCache(cache)

    await principals.put(_UID, _principal())

    assert await principals.get(_UID) == _principal()


async def test_an_unknown_subject_is_a_miss_not_an_error() -> None:
    """A miss is the normal first request of every user, so it must be cheap
    and quiet — the caller then resolves from the database exactly as it did
    before this step existed."""
    assert await PrincipalCache(DictCache()).get("nobody") is None


async def test_invalidation_makes_the_next_read_a_miss() -> None:
    """The mechanism the four platform-admin routes rely on: a demotion is
    real on the next request because the entry is GONE, not because a clock
    ran out."""
    cache = DictCache()
    principals = PrincipalCache(cache)
    await principals.put(_UID, _principal())

    await principals.invalidate(_UID)

    assert await principals.get(_UID) is None


async def test_invalidating_a_subject_with_no_entry_is_a_no_op() -> None:
    """A write path must not have to ask whether a cache it does not own
    happens to be populated — and a deployment running with the cache OFF
    still calls this through the route."""
    await PrincipalCache(DictCache()).invalidate(_UID)


# --------------------------------------------------------------------------- #
# What an entry may hold                                                      #
# --------------------------------------------------------------------------- #
async def test_the_entry_holds_ids_and_role_names_and_nothing_else() -> None:
    """`10 §10`'s rule — the identity fields never reach a store — applied to
    the store it would be most convenient to put them in. An operator reading
    this key learns a tenant and a role, both of which the token holder
    already knows; they do not learn an email address."""
    cache = DictCache()
    await PrincipalCache(cache).put(_UID, _principal())

    payload = json.loads(cache.values[_KEY])

    assert set(payload) == {"v", "workspace_id", "user_id", "roles", "active"}


async def test_the_roles_are_stored_sorted_so_one_principal_is_one_encoding() -> None:
    """Two replicas resolving the same principal write the same bytes, which
    is what lets an operator diff them and a test compare values instead of
    parsing them."""
    cache = DictCache()
    await PrincipalCache(cache).put(_UID, _principal(roles=frozenset({"member", "owner"})))

    assert json.loads(cache.values[_KEY])["roles"] == ["member", "owner"]


async def test_one_subject_occupies_exactly_one_key() -> None:
    """The plan's second security criterion, made structural: there is no
    second entry for this uid under another workspace, so no principal can
    cross between workspaces — a re-resolution REPLACES, it does not add."""
    cache = DictCache()
    principals = PrincipalCache(cache)

    await principals.put(_UID, _principal())
    await principals.put(_UID, _principal(workspace_id="018f0000-0000-7000-8000-0000000000w2"))

    assert list(cache.values) == [_KEY]
    stored = await principals.get(_UID)
    assert stored is not None
    assert stored.workspace_id == "018f0000-0000-7000-8000-0000000000w2"


async def test_two_subjects_never_share_a_key() -> None:
    cache = DictCache()
    principals = PrincipalCache(cache)

    await principals.put(_UID, _principal())
    await principals.put("firebase-uid-2", _principal(workspace_id="w-other", user_id="u-other"))

    first = await principals.get(_UID)
    assert first is not None
    assert first.workspace_id == _W1


# --------------------------------------------------------------------------- #
# Everything unreadable is a miss — never a raise, never a partial principal  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("not json at all", b"\xff\xfe not json"),
        ("json, but not an object", b'["owner"]'),
        (
            "an older schema version",
            b'{"v":0,"workspace_id":"w","user_id":"u","roles":[],"active":true}',
        ),
        ("no version stamp", b'{"workspace_id":"w","user_id":"u","roles":[],"active":true}'),
        ("a missing workspace", b'{"v":1,"user_id":"u","roles":[],"active":true}'),
        ("a blank workspace", b'{"v":1,"workspace_id":"","user_id":"u","roles":[],"active":true}'),
        ("a missing user", b'{"v":1,"workspace_id":"w","roles":[],"active":true}'),
        (
            "roles that are not a list",
            b'{"v":1,"workspace_id":"w","user_id":"u","roles":"owner","active":true}',
        ),
        (
            "a role that is not a string",
            b'{"v":1,"workspace_id":"w","user_id":"u","roles":[7],"active":true}',
        ),
        (
            "an active flag that is not a bool",
            b'{"v":1,"workspace_id":"w","user_id":"u","roles":[],"active":1}',
        ),
    ],
)
async def test_an_unreadable_entry_is_a_miss(label: str, value: bytes) -> None:
    """Redis is shared infrastructure: another process, or an older version of
    this one, can write to this key. Reading a role list out of it without
    checking that it IS a list of strings would be accepting an authorization
    decision from whatever happens to be there. Refusing loudly would be worse
    still — under a rolling deploy that changed the shape, EVERY user's request
    would 500 at once. So it degrades to the database read it would have done
    anyway."""
    cache = DictCache()
    cache.values[_KEY] = value

    assert await PrincipalCache(cache).get(_UID) is None, label


async def test_an_entry_written_by_this_version_survives_the_strictness() -> None:
    """The guard above is only correct if it does not also reject the real
    thing — including the two edge shapes: no roles at all, and inactive."""
    cache = DictCache()
    principals = PrincipalCache(cache)
    await principals.put(_UID, _principal(roles=frozenset(), active=False))

    stored = await principals.get(_UID)

    assert stored == _principal(roles=frozenset(), active=False)


# --------------------------------------------------------------------------- #
# The TTL and the key guard                                                   #
# --------------------------------------------------------------------------- #
async def test_the_configured_ttl_is_what_reaches_the_store() -> None:
    recorded: list[int | None] = []
    cache = DictCache()
    original = cache.set

    async def _record(key: str, value: bytes, ttl_s: int | None = None) -> None:
        recorded.append(ttl_s)
        await original(key, value, ttl_s)

    cache.set = _record  # type: ignore[method-assign]
    await PrincipalCache(cache, ttl_s=45).put(_UID, _principal())

    assert recorded == [45]


def test_the_default_ttl_is_the_plans_sixty_seconds() -> None:
    assert DEFAULT_PRINCIPAL_CACHE_TTL_S == 60
    assert PrincipalCache(DictCache()).ttl_s == 60


@pytest.mark.parametrize("ttl_s", [0, -1])
def test_a_non_positive_ttl_is_refused_at_construction(ttl_s: int) -> None:
    """Zero is refused rather than quietly meaning "off", because off is a
    WIRING decision: `AUTH_PRINCIPAL_CACHE_TTL_S=0` builds no cache at all, so
    a baseline run pays not even a Redis round trip."""
    with pytest.raises(ValidationError):
        PrincipalCache(DictCache(), ttl_s=ttl_s)


def test_a_ttl_above_the_ceiling_is_refused_at_construction() -> None:
    """The ceiling bounds one thing only: how long a role change whose
    invalidation FAILED may keep being wrong. An operator who wants an hour
    has misunderstood what this number is."""
    with pytest.raises(ValidationError):
        PrincipalCache(DictCache(), ttl_s=MAX_PRINCIPAL_CACHE_TTL_S + 1)


@pytest.mark.parametrize("subject", ["", "   "])
async def test_a_blank_subject_is_refused_rather_than_sharing_one_key(subject: str) -> None:
    """One shared key for every identity whose uid failed to arrive would hand
    one user's workspace and roles to another — the denylist's own guard, and
    here the stakes are higher, because that list only ever DENIES."""
    with pytest.raises(ValidationError):
        await PrincipalCache(DictCache()).get(subject)
    with pytest.raises(ValidationError):
        await PrincipalCache(DictCache()).put(subject, _principal())
