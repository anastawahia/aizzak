"""Unit tests for the Firebase ``AuthProvider`` adapter
(``infrastructure/auth/firebase_auth.py``, Phase 2.7): the full T1-T41
hermetic proof -- in-test 2048-bit RSA keys + ``httpx.MockTransport`` wired
into a REAL ``httpx.AsyncClient`` built through the adapter's own factory
(``create_firebase_http_client``). No Docker, no live Firebase, no marker.

Deviation A (from ``docs/design/09-testing-strategy.md`` §3's Auth/Firebase
row, which places this suite under ``tests/integration/``): landed here
instead, for four reasons. (1) §3's own header is "عبر testcontainers
بحاويات فعلية" -- there is no Firebase container and no live Firebase to run
one against; its own qualifier for this exact row, "بمفاتيح اختبار" (test
keys), concedes precisely what this suite does. (2)
``tests/integration/conftest.py``'s ``truncate_tables(live_db)`` fixture is
``autouse=True``, so EVERY test under that package silently skips when
Postgres is unreachable -- landing a zero-dependency auth suite there would
make an authentication boundary's tests silently skip on any Postgres-less
box, the worst possible outcome and exactly the "silence is worse than a
500" finding this very step makes about alpha's own bug. (3) The 2.5/2.6
precedent already splits adapter coverage into a hermetic
``tests/unit/test_*_mapping.py`` half and a live ``tests/integration/
test_*.py`` half; 2.7 simply has no live half to write, because JWKS
verification needs no real service, only real keys. (4) §8's "Adapters >=
75% via integration" is met in substance: this suite drives the adapter's
REAL code path end to end -- a real ``httpx.AsyncClient``, a real
``jwt.PyJWKSet``, a real ``jwt.decode`` -- only the TCP socket is faked.

Deviation B (from the ``live_*`` pytest-marker precedent used by 2.3-2.6):
this suite carries NO marker and always runs. Every ``live_*`` marker exists
to auto-skip a test when a real, unreachable SERVICE is involved; there is
no such service here to be unreachable, so a marker would only manufacture a
way to skip an auth suite. This also means zero ``pyproject.toml`` churn
(``--strict-markers`` would reject an undeclared marker anyway).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import InvalidKeyError, PyJWKSetError

from app.framework.errors import AppError, UnauthorizedError, ValidationError
from app.framework.types import Json
from app.infrastructure.auth import firebase_auth
from app.infrastructure.auth.firebase_auth import (
    _MIN_REFRESH_INTERVAL_S,
    FirebaseAuth,
    _guard_project_id,
    _guard_ttl,
    _invalid_token,
    _to_identity,
    _translate_fetch,
    create_firebase_http_client,
)

PROJECT_ID = "aizzak-test"
ISSUER = "https://securetoken.google.com/aizzak-test"
DEFAULT_TTL = 3600


# --------------------------------------------------------------------------- #
# Time control -- the module-level ``_now`` monkeypatch idiom               #
# (``test_vault_mapping.py``'s private-symbol style, applied to time).       #
# --------------------------------------------------------------------------- #
class _Clock:
    """A mutable stand-in for ``time.monotonic()`` -- the JWKS cache's clock
    seam. Deliberately UNRELATED to the wall-clock ``exp``/``iat`` claims
    PyJWT itself validates (see ``_claims`` below and the adapter module's
    "two clocks" note): advancing this clock affects ONLY TTL/refresh-budget
    arithmetic, never claim expiry."""

    def __init__(self, start: float) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Freezes ``firebase_auth._now`` for every test (deterministic, no
    ``asyncio.sleep``, no flake). Tests that exercise TTL expiry or the
    kid-miss refresh budget request this fixture explicitly and call
    ``frozen_clock.advance(seconds)``; everyone else just gets a stable
    clock for free."""
    clock = _Clock(1_700_000_000.0)  # an arbitrary, nonzero epoch
    monkeypatch.setattr(firebase_auth, "_now", clock)
    return clock


# --------------------------------------------------------------------------- #
# RSA keys, JWKS bodies, tokens                                              #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def rsa_keys() -> dict[str, rsa.RSAPrivateKey]:
    """Three session-scoped 2048-bit RSA keypairs (``RSAAlgorithm.
    _MIN_KEY_SIZE == 2048``; smaller keys emit a fatal-under-strict-warnings
    ``InsecureKeyLengthWarning``). Generation is slow (tens of ms each), so
    this MUST be session-scoped or the suite crawls."""
    return {
        kid: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for kid in ("key-a", "key-b", "key-c")
    }


def _jwk(kid: str, private_key: rsa.RSAPrivateKey, *, use: str | None = "sig") -> Json:
    """One Google-shaped JWKS entry (D2's endpoint format) for
    ``private_key``'s public half. ``alg`` is always stamped (matching one
    of Google's two possible response shapes; the other -- omitted ``alg``
    -- is covered by ``PyJWK``'s own kty-based fallback, exercised nowhere
    here since it is pure library behaviour, not this adapter's code)."""
    entry = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    entry["kid"] = kid
    entry["alg"] = "RS256"
    if use is not None:
        entry["use"] = use
    return entry


def _jwks(*entries: Json) -> Json:
    return {"keys": list(entries)}


def _claims(
    *,
    sub: Any = "user-123",
    aud: Any = PROJECT_ID,
    iss: Any = ISSUER,
    exp_offset: float = 3600.0,
    iat_offset: float = 0.0,
    email: Any = "user@example.com",
    email_verified: Any = True,
    omit: tuple[str, ...] = (),
    extra: Json | None = None,
) -> Json:
    """Build a claim set with WALL-CLOCK ``exp``/``iat`` -- PyJWT validates
    these against ``datetime.now(UTC)`` (``api_jwt.py:399``), NOT the
    ``frozen_clock`` fixture, which only controls the JWKS cache's
    ``time.monotonic()`` seam. Mixing the two up is the one trap this helper
    exists to avoid."""
    wall_now = time.time()
    claims: Json = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "exp": wall_now + exp_offset,
        "iat": wall_now + iat_offset,
        "email": email,
        "email_verified": email_verified,
    }
    for key in omit:
        claims.pop(key, None)
    if extra:
        claims.update(extra)
    return claims


def _token(private_key: rsa.RSAPrivateKey, kid: str | None, **claim_overrides: Any) -> str:
    """Mint a validly-signed RS256 token via the real ``jwt.encode`` --
    every case that does NOT need to bypass PyJWT's own encode-time
    validation (contrast ``_hand_rolled``, needed for T11/T33)."""
    claims = _claims(**claim_overrides)
    headers: Json = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _signing_input(header: Json, claims: Json) -> bytes:
    header_seg = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_seg = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return header_seg + b"." + payload_seg


def _hand_rolled(header: Json, claims: Json, *, signature: bytes) -> str:
    """Build a compact JWS bypassing PyJWT's OWN encode-time header
    validation. TRAP (not called out in the design brief, verified against
    ``jwt/api_jws.py``'s ``PyJWS.encode``): ``_validate_headers(headers,
    encoding=True)`` calls ``_validate_kid`` UNCONDITIONALLY whenever
    ``"kid"`` is a header key, even while ENCODING -- so
    ``jwt.encode(..., headers={"kid": 123})`` itself raises
    ``InvalidTokenError`` and can never produce T33's token. This is the
    exact same family of trap as T11's HS256-confusion mint (PyJWT guards
    its OWN signing path, not what an attacker can hand-craft elsewhere) --
    hand-rolling is required, not optional, for both.
    """
    signing_input = _signing_input(header, claims)
    return (signing_input + b"." + _b64url(signature)).decode("ascii")


# --------------------------------------------------------------------------- #
# The mock JWKS endpoint                                                     #
# --------------------------------------------------------------------------- #
class _JwksHandler:
    """A call-counting stand-in for Google's JWKS endpoint, wired in through
    ``httpx.MockTransport`` via the adapter's OWN factory
    (``create_firebase_http_client``) so the factory's real ``timeout``/
    ``trust_env`` choices are what actually get exercised (D9, the
    ``create_minio_client``/``tests/integration/conftest.py:487-493``
    precedent).

    Serves whatever ``self.body``/``self.status`` currently hold, or raises
    ``self.raises`` instead -- both mutable mid-test, so one test can warm
    the cache with one outcome then switch behaviour (TTL expiry, rotation,
    an outage, ...) before its next call. ``__call__`` is ``async`` and
    yields once (``asyncio.sleep(0)``, a deterministic scheduler tick, NOT a
    time-based wait) so concurrent callers (T31) genuinely interleave at the
    ``asyncio.Lock`` instead of happening to run to completion sequentially
    with no proof the lock did anything.
    """

    def __init__(self, body: Any) -> None:
        self.body = body
        self.status = 200
        self.raises: Exception | None = None
        self.calls: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        await asyncio.sleep(0)
        if self.raises is not None:
            raise self.raises
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body)
        return httpx.Response(self.status, json=self.body)

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _build(
    body: Any, *, project_id: str = PROJECT_ID, ttl: int = DEFAULT_TTL
) -> tuple[FirebaseAuth, _JwksHandler]:
    """A fresh, function-local ``FirebaseAuth`` wired to its own
    ``_JwksHandler`` -- for tests needing control the shared ``adapter``/
    ``handler`` fixtures below don't give them (a non-default TTL, a
    cold/partial key set, a failing transport, ...). Every adapter
    instance is its OWN construction (never shared) because its
    ``asyncio.Lock`` binds to the first loop that awaits it (D3)."""
    handler = _JwksHandler(body)
    client = create_firebase_http_client(transport=httpx.MockTransport(handler))
    return FirebaseAuth(client, project_id, ttl), handler


@pytest.fixture
def handler(rsa_keys: dict[str, rsa.RSAPrivateKey]) -> _JwksHandler:
    """The default published set: key-a AND key-b (Google's real JWKS
    always carries 2-4 keys mid-rotation), function-scoped -- fresh per
    test."""
    return _JwksHandler(_jwks(_jwk("key-a", rsa_keys["key-a"]), _jwk("key-b", rsa_keys["key-b"])))


@pytest.fixture
def adapter(handler: _JwksHandler) -> FirebaseAuth:
    """The adapter under test, function-scoped (its ``asyncio.Lock`` binds
    to the first loop that awaits it -- D3 -- so it must never be shared
    across tests/event loops)."""
    client = create_firebase_http_client(transport=httpx.MockTransport(handler))
    return FirebaseAuth(client, PROJECT_ID, DEFAULT_TTL)


# --------------------------------------------------------------------------- #
# T1-T16 -- happy path + the exhaustive validation set (D5)                  #
# --------------------------------------------------------------------------- #
async def test_t1_happy_path_maps_claims_to_identity(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(
        rsa_keys["key-a"], "key-a", sub="user-42", email="u@example.com", email_verified=True
    )

    identity = await adapter.verify_token(token)

    assert identity.firebase_uid == "user-42"
    assert identity.email == "u@example.com"
    assert identity.email_verified is True
    assert identity.claims["sub"] == "user-42"


async def test_t2_expired_token_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", exp_offset=-3600.0)

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t3_expired_within_leeway_is_accepted(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", exp_offset=-30.0)

    identity = await adapter.verify_token(token)
    assert identity.firebase_uid == "user-123"


async def test_t4_iat_in_the_future_beyond_leeway_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", iat_offset=300.0)

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t5_iat_within_leeway_is_accepted(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", iat_offset=30.0)

    identity = await adapter.verify_token(token)
    assert identity.firebase_uid == "user-123"


async def test_t6_wrong_audience_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", aud="some-other-project")

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t7_list_audience_is_rejected_by_strict_aud(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """Would PASS without ``strict_aud: True`` (``api_jwt.py:550-561``)."""
    token = _token(rsa_keys["key-a"], "key-a", aud=[PROJECT_ID, "evil"])

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t8_wrong_issuer_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", iss="https://evil.example.com/" + PROJECT_ID)

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t9_signature_is_verified_against_the_cached_key_not_the_claimed_kid(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """Signed by key-b's private key, but the header claims ``kid=key-a``:
    verification MUST use the cached key-a public key, not whatever the
    token claims -- so this fails signature verification, not audience/
    issuer/anything else."""
    token = _token(rsa_keys["key-b"], "key-a")

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t10_alg_none_is_rejected_by_the_algorithm_allowlist(
    adapter: FirebaseAuth,
) -> None:
    """``jwt.encode(payload, "", algorithm="none")`` mints cleanly (verified:
    ``NoneAlgorithm.prepare_key("")`` -> ``None``); the token is rejected
    before any key work.

    Scope of what this proves (measured by mutation, not assumed): with a
    legitimately-RS256 key set this test passes with OR without the explicit
    ``algorithms=_ALGORITHMS``, because ``jwt.decode``'s fallback for a
    ``PyJWK`` key is ``[key.algorithm_name]`` (``api_jws.py:379-380``) --
    already ``["RS256"]`` here. So this is a guard on ``alg:none`` being
    rejected *at all*; the pin's own necessity is proved by T11b.
    """
    token = jwt.encode(_claims(), "", algorithm="none", headers={"kid": "key-a"})

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t11_hs256_confusion_is_rejected_by_the_algorithm_allowlist(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """TRAP: ``jwt.encode(..., algorithm="HS256")`` with a ``cryptography``
    RSA key raises ``InvalidKeyError`` -- ``HMACAlgorithm.prepare_key``
    rejects a PEM/asymmetric-shaped key (``algorithms.py:331-334``). That
    guard is on OUR signing side, in OUR process; a real attacker signs
    elsewhere, so the token is hand-rolled here: ``hmac.new(pub_pem,
    signing_input, sha256)`` keyed by key-a's own PUBLIC key bytes (the
    textbook algorithm-confusion attack -- treating an RSA public key as an
    HMAC secret). Rejected before any signature math.

    Scope of what this proves (measured by mutation, not assumed): like T10,
    this passes with OR without the explicit ``algorithms=_ALGORITHMS``,
    because the cached key-a is a genuine RS256 ``PyJWK`` whose
    ``algorithm_name`` the fallback already pins to ``RS256``, and the
    ``PyJWK`` alg-binding backstop (``api_jws.py:389-399``) rejects the
    ``HS256`` header on its own. That backstop only holds while the KEY SET
    is honest -- so the pin's necessity is proved by T11b, not here.
    """
    pub_pem = (
        rsa_keys["key-a"]
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    header = {"typ": "JWT", "alg": "HS256", "kid": "key-a"}
    claims = _claims()
    signature = hmac.new(pub_pem, _signing_input(header, claims), hashlib.sha256).digest()
    token = _hand_rolled(header, claims, signature=signature)

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t11b_poisoned_jwks_advertising_hs256_cannot_downgrade(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    """The scenario the explicit ``algorithms=_ALGORITHMS`` pin actually
    exists for -- and the ONLY test here that fails if the pin is removed.

    T10/T11 both pass with the pin deleted (proved by mutation): against a
    legitimately-RS256 key set, ``jwt.decode``'s fallback
    (``algorithms = [key.algorithm_name]``, ``api_jws.py:379-380``) already
    equals ``["RS256"]``, so the allow-list looks redundant. It is not --
    it is only redundant while the KEY SET ITSELF is trustworthy.

    Here the key set is hostile: a ``kty:"oct"`` (symmetric) entry whose
    HMAC secret ``k`` is key-a's own PUBLIC key PEM -- public knowledge by
    definition, so any attacker who can substitute the key set (a MITM on
    the JWKS fetch; cf. the ``trust_env=False`` closure) can mint this.
    ``PyJWK`` binds ``algorithm_name = "HS256"`` for such an entry
    (verified: ``api_jwk.py:33-55``, ``kty == "oct"`` -> ``HS256``), so the
    key *self-authorizes* the downgrade and the ``PyJWK`` alg-binding
    backstop cannot fire -- ``header.alg == key.algorithm_name == "HS256"``
    agree. Verified experimentally: WITHOUT the pin this exact token is
    ACCEPTED as ``sub="ATTACKER"``; the literal ``("RS256",)`` is the only
    thing that rejects it.
    """
    pub_pem = (
        rsa_keys["key-a"]
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    poisoned = {
        "kty": "oct",
        "use": "sig",
        "kid": "key-a",
        "alg": "HS256",
        "k": _b64url(pub_pem).decode("ascii"),
    }
    adapter, _handler = _build(_jwks(poisoned))

    header = {"typ": "JWT", "alg": "HS256", "kid": "key-a"}
    claims = _claims(sub="ATTACKER")
    signature = hmac.new(pub_pem, _signing_input(header, claims), hashlib.sha256).digest()
    token = _hand_rolled(header, claims, signature=signature)

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


@pytest.mark.parametrize("token", ["not.a.jwt", "", "a.b"])
async def test_t12_malformed_tokens_are_401_never_a_crash(
    adapter: FirebaseAuth, token: str
) -> None:
    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t13_missing_sub_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", omit=("sub",))

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t14_empty_sub_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """PyJWT's own ``require`` uses ``payload.get(claim) is None``
    (``api_jwt.py:430``), so ``sub: ""`` PASSES it -- this adapter's own
    emptiness check in ``_to_identity`` is load-bearing, not redundant."""
    token = _token(rsa_keys["key-a"], "key-a", sub="")

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t15_missing_exp_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """The highest-value ``require`` test: without it, ``verify_exp`` only
    fires ``if "exp" in payload`` (``api_jwt.py:407``) -- a token missing
    ``exp`` would otherwise never expire."""
    token = _token(rsa_keys["key-a"], "key-a", omit=("exp",))

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


async def test_t16_missing_iat_is_rejected(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", omit=("iat",))

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


# --------------------------------------------------------------------------- #
# T17-T26 -- caching, bounded refresh, stale-if-error (D3, D4)               #
# --------------------------------------------------------------------------- #
async def test_t17_cache_hit_avoids_a_second_fetch(
    adapter: FirebaseAuth, handler: _JwksHandler, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a")

    first = await adapter.verify_token(token)
    second = await adapter.verify_token(token)

    assert first.firebase_uid == second.firebase_uid == "user-123"
    assert handler.call_count == 1  # D-25: no round-trip per request


async def test_t18_ttl_expiry_triggers_a_refetch(
    rsa_keys: dict[str, rsa.RSAPrivateKey], frozen_clock: _Clock
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])), ttl=100)

    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))
    frozen_clock.advance(101.0)
    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))

    assert handler.call_count == 2


async def test_t19_unknown_kid_triggers_bounded_refetch_and_rotation_works(
    rsa_keys: dict[str, rsa.RSAPrivateKey], frozen_clock: _Clock
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])))

    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))  # warms on key-a
    assert handler.call_count == 1

    # The warm-up fetch itself stamped `_last_attempt_at`, so the refresh
    # budget must replenish before an unknown kid is allowed to trigger a
    # second attempt (D4) -- advanced past `_MIN_REFRESH_INTERVAL_S` but well
    # under the (default 3600s) TTL, so the cache is simultaneously
    # "budget available" and still "fresh" per `_is_fresh()`.
    frozen_clock.advance(_MIN_REFRESH_INTERVAL_S)

    # Google "rotates": key-b now published too.
    handler.body = _jwks(_jwk("key-a", rsa_keys["key-a"]), _jwk("key-b", rsa_keys["key-b"]))
    identity = await adapter.verify_token(_token(rsa_keys["key-b"], "key-b"))

    assert identity.firebase_uid == "user-123"
    assert handler.call_count == 2


async def test_t20_and_t20b_kid_miss_storm_is_bounded_then_budget_replenishes(
    rsa_keys: dict[str, rsa.RSAPrivateKey], frozen_clock: _Clock
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])))

    # T20: 5 tokens with random, totally unknown kids.
    for i in range(5):
        token = _token(rsa_keys["key-a"], f"unknown-{i}")
        with pytest.raises(UnauthorizedError) as excinfo:
            await adapter.verify_token(token)
        assert excinfo.value.code == "auth.invalid_token"

    storm_calls = handler.call_count
    assert storm_calls <= 2  # the DoS-amplifier guard (D4)

    # T20b: the budget replenishes exactly once 60s later.
    frozen_clock.advance(_MIN_REFRESH_INTERVAL_S)
    with pytest.raises(UnauthorizedError):
        await adapter.verify_token(_token(rsa_keys["key-a"], "unknown-last"))

    assert handler.call_count == storm_calls + 1


async def test_t21_jwks_500_with_a_cold_cache_is_500_not_401(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])))
    handler.status = 500

    with pytest.raises(AppError) as excinfo:
        await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))

    assert not isinstance(excinfo.value, UnauthorizedError)  # alpha's §7 bug, fixed
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_t22_jwks_timeout_is_500_not_401(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])))
    handler.raises = httpx.ConnectTimeout("connect timed out")

    with pytest.raises(AppError) as excinfo:
        await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))

    assert not isinstance(excinfo.value, UnauthorizedError)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


@pytest.mark.parametrize(
    "body",
    [
        [],  # top-level JSON array          -> the `isinstance(body, dict)` guard
        "<html>",  # not JSON at all         -> JSONDecodeError (a ValueError)
        {"keys": [123]},  # ---------------- | the TRAP 1b family: a dict body whose
        {"keys": ["x"]},  #                  | "keys" list holds a NON-DICT entry
        {"keys": [None]},  # --------------- | (see the docstring)
    ],
)
async def test_t23_malformed_jwks_body_never_leaks_a_raw_exception(
    body: Any, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    """R6 on an auth boundary: a 200 with a hostile/garbage body must become
    a translated 500, never a raw foreign exception.

    The last three shapes are a real regression guard, not padding. PyJWT's
    ``PyJWKSet.__init__`` skips bad entries with ``except PyJWTError``
    (``api_jwk.py:145-152``), but ``PyJWK.__init__``'s first act is
    ``self._jwk_data.get("kty")`` (``api_jwk.py:33``) -- on a non-dict entry
    that raises a raw ``AttributeError``, which is NOT a ``PyJWTError``, so
    the library's own loop does not catch it and (before ``_fetch_keys``'
    TRAP 1b guard) it escaped the adapter entirely untranslated. Probed
    exhaustively: every non-dict entry type leaks this way, while every
    *dict* entry carrying garbage values (``n``/``k`` of the wrong type,
    unknown ``kty``, bad base64) is already caught safely -- which is what
    makes ``TypeError``/``ValueError`` in ``_resolve_key``'s catch tuple
    load-bearing rather than speculative.
    """
    adapter, _handler = _build(body)

    with pytest.raises(AppError) as excinfo:
        await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))

    assert not isinstance(excinfo.value, UnauthorizedError)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_t24_jwks_body_with_only_encryption_keys_is_500_not_a_false_401(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    """An empty post-filter key set installed as "fresh" would turn every
    kid miss into a 401 -- an infra fault dressed as a bad token. Must be
    500."""
    adapter, _handler = _build(_jwks(_jwk("key-c", rsa_keys["key-c"], use="enc")))

    with pytest.raises(AppError) as excinfo:
        await adapter.verify_token(_token(rsa_keys["key-c"], "key-c"))

    assert not isinstance(excinfo.value, UnauthorizedError)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_t25_stale_key_set_still_verifies_when_a_refresh_fails(
    rsa_keys: dict[str, rsa.RSAPrivateKey], frozen_clock: _Clock
) -> None:
    """Stale-if-error: a Google outage must not become an auth outage."""
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])), ttl=100)

    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))  # warms the cache
    frozen_clock.advance(101.0)  # cache goes stale
    handler.status = 500  # Google is down

    identity = await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))
    assert identity.firebase_uid == "user-123"


async def test_t26_stale_cache_plus_unknown_kid_plus_failed_fetch_is_500(
    rsa_keys: dict[str, rsa.RSAPrivateKey], frozen_clock: _Clock
) -> None:
    """The freshness predicate: we could not ESTABLISH the key set, so we
    are not entitled to call the token invalid."""
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])), ttl=100)

    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))
    frozen_clock.advance(101.0)
    handler.status = 500

    with pytest.raises(AppError) as excinfo:
        await adapter.verify_token(_token(rsa_keys["key-b"], "key-b"))  # unknown kid

    assert not isinstance(excinfo.value, UnauthorizedError)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# T27-T30 -- construction guards, zero network (D5)                          #
# --------------------------------------------------------------------------- #
def test_t27_empty_project_id_raises_validation_error_at_construction() -> None:
    client = create_firebase_http_client()

    with pytest.raises(ValidationError) as excinfo:
        FirebaseAuth(client, "", 3600)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


def test_t28_whitespace_only_project_id_raises_validation_error() -> None:
    client = create_firebase_http_client()

    with pytest.raises(ValidationError) as excinfo:
        FirebaseAuth(client, "   ", 3600)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


async def test_t29_project_id_is_stripped_and_the_happy_path_still_works(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    handler = _JwksHandler(_jwks(_jwk("key-a", rsa_keys["key-a"])))
    client = create_firebase_http_client(transport=httpx.MockTransport(handler))
    adapter = FirebaseAuth(client, " my-project ", 3600)
    token = _token(
        rsa_keys["key-a"],
        "key-a",
        aud="my-project",
        iss="https://securetoken.google.com/my-project",
    )

    identity = await adapter.verify_token(token)
    assert identity.firebase_uid == "user-123"


@pytest.mark.parametrize("ttl", [0, -1])
def test_t30_non_positive_ttl_raises_validation_error_at_construction(ttl: int) -> None:
    client = create_firebase_http_client()

    with pytest.raises(ValidationError) as excinfo:
        FirebaseAuth(client, PROJECT_ID, ttl)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# T31 -- concurrency: the asyncio.Lock single-flight (D3)                    #
# --------------------------------------------------------------------------- #
async def test_t31_concurrent_cold_cache_verification_single_flights_the_fetch(
    rsa_keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    adapter, handler = _build(_jwks(_jwk("key-a", rsa_keys["key-a"])))
    token = _token(rsa_keys["key-a"], "key-a")

    results = await asyncio.gather(*(adapter.verify_token(token) for _ in range(10)))

    assert len(results) == 10
    assert all(identity.firebase_uid == "user-123" for identity in results)
    assert handler.call_count == 1


# --------------------------------------------------------------------------- #
# T32-T36 -- header/claims edge cases + the JWK-not-X.509 endpoint proof     #
# --------------------------------------------------------------------------- #
async def test_t32_header_without_kid_is_401_not_500(
    adapter: FirebaseAuth, handler: _JwksHandler, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], None)  # no `kid` header at all

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)

    assert excinfo.value.code == "auth.invalid_token"
    assert handler.call_count == 0  # cannot select a key -> never even tries


async def test_t33_non_string_kid_header_is_401(adapter: FirebaseAuth) -> None:
    """TRAP (not called out in the design brief): ``jwt.encode`` itself
    rejects a non-string ``kid`` header at ENCODE time (see ``_hand_rolled``'s
    docstring) -- must be hand-rolled, exactly like T11."""
    header = {"typ": "JWT", "alg": "RS256", "kid": 123}
    claims = _claims()
    token = _hand_rolled(header, claims, signature=b"placeholder-signature")

    with pytest.raises(UnauthorizedError) as excinfo:
        await adapter.verify_token(token)
    assert excinfo.value.code == "auth.invalid_token"


@pytest.mark.parametrize(
    ("email_verified_claim", "expected"),
    [
        ("false", False),  # `bool("false")` is True -- `is True` strictness catches this
        (0, False),
        (1, False),  # only the exact bool True counts
        (False, False),
        (True, True),
    ],
)
async def test_t34_email_verified_uses_is_true_strictness(
    adapter: FirebaseAuth,
    rsa_keys: dict[str, rsa.RSAPrivateKey],
    email_verified_claim: Any,
    expected: bool,
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", email_verified=email_verified_claim)

    identity = await adapter.verify_token(token)
    assert identity.email_verified is expected


async def test_t34_email_verified_absent_maps_to_false(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", omit=("email_verified",))

    identity = await adapter.verify_token(token)
    assert identity.email_verified is False


async def test_t34_email_absent_maps_to_none(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = _token(rsa_keys["key-a"], "key-a", omit=("email",))

    identity = await adapter.verify_token(token)
    assert identity.email is None


async def test_t35_claims_is_a_faithful_superset_including_auth_time_and_custom(
    adapter: FirebaseAuth, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    auth_time = int(time.time())
    token = _token(
        rsa_keys["key-a"], "key-a", extra={"auth_time": auth_time, "custom_claim": "value-123"}
    )

    identity = await adapter.verify_token(token)

    assert identity.claims["auth_time"] == auth_time
    assert identity.claims["custom_claim"] == "value-123"
    assert identity.claims["sub"] == "user-123"


async def test_t36_fetch_hits_the_jwk_endpoint_not_x509(
    adapter: FirebaseAuth, handler: _JwksHandler, rsa_keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    await adapter.verify_token(_token(rsa_keys["key-a"], "key-a"))

    assert len(handler.calls) == 1
    assert handler.calls[0].url == firebase_auth._JWKS_URL


# --------------------------------------------------------------------------- #
# T37-T40 -- the pure helpers, directly (the test_vault_mapping.py idiom)    #
# --------------------------------------------------------------------------- #
def test_t37_guard_project_id_accepts_and_strips() -> None:
    assert _guard_project_id("p") == "p"
    assert _guard_project_id(" p ") == "p"


@pytest.mark.parametrize("value", ["", "   "])
def test_t37_guard_project_id_rejects_empty_or_blank(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _guard_project_id(value)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


@pytest.mark.parametrize("value", [1, 3600])
def test_t38_guard_ttl_accepts_positive_values(value: int) -> None:
    assert _guard_ttl(value) == value


@pytest.mark.parametrize("value", [0, -1])
def test_t38_guard_ttl_rejects_non_positive_values(value: int) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _guard_ttl(value)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


def test_t39_invalid_token_maps_to_unauthorized_with_the_catalog_code() -> None:
    error = _invalid_token()

    assert isinstance(error, UnauthorizedError)
    assert error.code == "auth.invalid_token"
    assert error.status == 401
    assert error.detail == "invalid token"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        httpx.HTTPStatusError(
            "500", request=httpx.Request("GET", "http://x"), response=httpx.Response(500)
        ),
        PyJWKSetError("no usable keys"),
        InvalidKeyError("bad key"),
        ValueError("not json"),
    ],
)
def test_t39_translate_fetch_always_maps_to_common_internal_never_unauthorized(
    exc: Exception,
) -> None:
    result = _translate_fetch(exc)

    assert isinstance(result, AppError)
    assert not isinstance(result, UnauthorizedError)
    assert result.code == "common.internal"
    assert result.status == 500


def test_t40_to_identity_maps_claims_per_the_exact_rules() -> None:
    claims = {"sub": "user-1", "email": "a@b.com", "email_verified": True, "extra": "x"}

    identity = _to_identity(claims)

    assert identity.firebase_uid == "user-1"
    assert identity.email == "a@b.com"
    assert identity.email_verified is True
    assert identity.claims == claims
    assert identity.claims is not claims  # a copy, never an alias of PyJWT's dict


def test_t40_to_identity_rejects_empty_or_non_string_sub() -> None:
    with pytest.raises(UnauthorizedError) as excinfo:
        _to_identity({"sub": ""})
    assert excinfo.value.code == "auth.invalid_token"

    with pytest.raises(UnauthorizedError) as excinfo:
        _to_identity({"sub": 123})
    assert excinfo.value.code == "auth.invalid_token"


def test_t40_to_identity_email_verified_is_true_strictness() -> None:
    assert _to_identity({"sub": "u", "email_verified": "false"}).email_verified is False
    assert _to_identity({"sub": "u", "email_verified": True}).email_verified is True


def test_t40_to_identity_non_string_or_absent_email_degrades_to_none() -> None:
    assert _to_identity({"sub": "u", "email": 123}).email is None
    assert _to_identity({"sub": "u"}).email is None


# --------------------------------------------------------------------------- #
# T41 -- the factory: the DD-11 trust_env closure + the fail-fast timeout    #
# --------------------------------------------------------------------------- #
def test_t41_factory_closes_the_dd11_trust_env_trap_and_sets_a_fail_fast_timeout() -> None:
    """Both are verified public properties (``httpx/_client.py:231-232,
    253-255``). Honest limitation (per the design): ``trust_env``'s proxy
    EFFECT cannot be behaviourally proven through the ``MockTransport`` seam,
    because ``allow_env_proxies = trust_env and transport is None``
    (``_client.py:685``) already disables it once a transport is injected --
    hence this structural assertion instead."""
    client = create_firebase_http_client()

    assert client.trust_env is False
    assert client.timeout == httpx.Timeout(5.0)
