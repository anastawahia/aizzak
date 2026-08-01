"""Firebase adapter for the ``AuthProvider`` port (02-port-contracts §1.10,
D-25). Phase 2.7.

Same split as ``cache/redis_cache.py`` (2.3), ``vector/qdrant_store.py``
(2.5) and ``secrets/vault_secrets.py`` (2.6): a factory builds the
technology client (``create_firebase_http_client``, Composition Root / test
harness only), and a thin adapter class (``FirebaseAuth``) implements the
port over it (structural Protocol match -- no inheritance). This module
never imports ``AuthProvider`` itself, only ``Identity``, mirroring
``qdrant_store.py`` importing ``VectorPoint``/``VectorHit`` but never
``VectorStore``.

Key source (D1): Google's public JWK endpoint over HTTPS, cached
in-process -- NOT Vault. D-25's own wording ("تحقّق JWT محلي بمفاتيح
مُخزّنة") buys "no round-trip per request"; reading Vault on every
verification would BE a round-trip per request, and a Vault snapshot goes
stale as Google rotates its signing keys roughly daily with nothing in v1
keeping it in sync (``scheduling`` is a RESERVED, unbuilt module). These are
public keys -- Vault's confidentiality/ACL/audit machinery buys nothing for
them and adds a dependency, a failure mode and a rotation obligation for no
benefit. ``secret/data/firebase`` stays reserved, unused in v1, for a
genuinely privileged Admin SDK call (``delete_user``) that this port does
not expose; v1 provisions no Firebase service account at all.

Endpoint (D2): the JWK endpoint
(``.../service_accounts/v1/jwk/securetoken@system.gserviceaccount.com``),
not X.509. A ``PyJWK`` binds its algorithm at construction
(``jwt.api_jwk.PyJWK.__init__``), so passing one as ``jwt.decode``'s
``key=`` makes PyJWT itself reject a header ``alg`` that does not match the
key's own algorithm (``api_jws.py`` verify path) -- a structural
algorithm-confusion defense the X.509/bare-``RSAPublicKey`` path does not
get for free. ``PyJWKSet`` also skips unusable individual keys rather than
exploding on one bad entry.

Cache (D3): an instance-level ``dict[str, PyJWK]`` + TTL + ``asyncio.Lock``
-- NOT the Redis ``CacheProvider``. Coupling auth to Redis would make
"Redis down" mean "all authentication down" (``RedisCache`` never fails
open, every failure becomes ``common.internal``); these keys are public,
tiny (2-4 entries) and identical across replicas, so per-process
duplication costs nothing and removes a network round trip from the
hottest path in the system. **We cache the public keys only -- signature
and ``exp`` are re-verified on every single call, with no exception.** This
is the deliberate opposite of alpha's Redis-cached *verification outcome*
(``sha256(token)`` -> skip ``verify_id_token`` entirely on a hit, refs
``docs/migration/refs/auth-firebase.md`` §1) -- that cache shape reintroduces
a revocation-window bug under a design that explicitly does not want one. A
refresh blocks concurrent callers on ``asyncio.Lock`` (double-checked
locking: a fast path outside the lock, a re-check inside) rather than
serve-stale-and-refresh-in-background -- simpler, and bounded to one fetch
per TTL window per process.

Unknown-``kid`` handling (D4): bounded refresh, ``_MIN_REFRESH_INTERVAL_S =
60.0``. ``PyJWKClient.get_signing_key`` refetches unboundedly on any kid
miss (``jwks_client.py:201-204``) -- an attacker minting random ``kid``s
would turn our auth path into a Google-hammering amplifier; a per-process
one-fetch-per-minute budget closes it (``_last_attempt_at`` is stamped
BEFORE the fetch, so a *failing* fetch also consumes the budget). The
401/500 predicate is one rule, no sticky flags: return 401 only when the
key set we hold is CURRENT (``now - fetched_at < ttl``); otherwise a ``kid``
we cannot resolve is a 500 -- we could not *establish* the key set, so we
are not entitled to call the token invalid. A failed refresh NEVER wipes
``_keys``/``_fetched_at`` (both are assigned only on success, in
``_fetch_keys``) -- PyJWT's own documented lesson
(``jwks_client.py:129-134``: writing ``jwk_set=None`` on error in a
``finally`` block turns a transient outage into a cache wipe) -- so a
transient Google outage against a warm cache still verifies successfully
against the old, merely-retired-not-compromised keys: **stale-if-error,
a Google outage is not an auth outage.**

Validation set (D5, exhaustive): algorithm pinned to ``("RS256",)`` (belt)
*and* the ``PyJWK``'s own bound algorithm (braces) -- the explicit allow-list
is NOT redundant with the ``PyJWK`` binding: without it, ``algorithms``
defaults to ``[key.algorithm_name]`` (``api_jws.py:379-380``), so a poisoned
JWKS advertising ``kty:"oct"``/``alg:"HS256"`` would self-authorize a
downgrade. ``aud == project_id`` under ``strict_aud=True`` (default
``False`` -- without it a list ``aud`` like ``[project_id, "evil"]`` silently
passes, ``api_jwt.py:550-561``). ``iss ==
https://securetoken.google.com/<project_id>``. ``exp``/``iat``/``aud``/
``iss``/``sub`` are all listed in ``options["require"]``: PyJWT's own claim
checks (``verify_exp`` etc.) only fire ``if <claim> in payload``
(``api_jwt.py:401-408``) -- without ``require`` a token missing ``exp``
would never expire. ``require`` itself uses ``payload.get(claim) is None``
(``api_jwt.py:430``), so ``sub: ""`` PASSES it -- ``_to_identity``'s own
non-empty check is load-bearing, not redundant. ``leeway=60.0`` (NOT
alpha's 0, refs ``auth-firebase.md`` §1) bounds real clock drift (container
suspend/resume; WSL2 sleep, this very repo's dev environment) at the Admin
SDK's own documented maximum ``clock_skew_seconds``. ``auth_time`` is
deliberately OUT of the validation set (zero v1 value, an extra failure
mode) but preserved verbatim in ``Identity.claims`` for any future step-up
policy. ``project_id`` emptiness is guarded ONCE, at construction
(``_guard_project_id``), never per call -- see ``FirebaseAuth.__init__``.

Error translation (D6, R6): every PyJWT/httpx exception is mapped onto the
shared framework hierarchy before it can escape this adapter.
``jwt.InvalidTokenError`` and every subclass (``ExpiredSignatureError``,
``InvalidAudienceError``, ``InvalidAlgorithmError``, ``DecodeError``, ...)
means the TOKEN is bad -> ``UnauthorizedError`` / ``auth.invalid_token`` /
401. Every other ``jwt.PyJWTError`` (``PyJWKError``, ``PyJWKSetError``,
``InvalidKeyError``, ``MissingCryptographyError``, ...) and every
``httpx.HTTPError`` means OUR key-fetch/deployment is broken ->
``AppError`` / ``common.internal`` / 500. This is the exact fix for alpha's
§7 bug: a bare ``except Exception:`` folded a transient Google outage into
the same 401 as "your token is garbage" (refs ``auth-firebase.md`` §7/§9)
-- PyJWT's own exception tree already draws this line (``InvalidTokenError``
is the base of the token family; ``PyJWKError``/``PyJWKSetError``
deliberately descend from ``PyJWTError`` directly, not from
``InvalidTokenError``); alpha's blanket catch erased a distinction the
library was handing it for free. Our OWN ``ValidationError`` guards
(``_guard_project_id``, ``_guard_ttl``) fire only at construction, never
inside a decode/fetch ``try``, mirroring ``vault_secrets.py:189``'s "parsed
before to_thread: our own ValidationError must skip the catch below" --
they must never be re-translated. **Nothing here imports a logger**
(matching ``cache``/``storage``/``vector``/``secrets``) and every 401
carries the fixed literal detail ``"invalid token"`` -- never the reason,
never a claim, never the raw token (``10 §10``: "لا يُسجَّل: توكنات، أسرار،
مفاتيح API"). ``raise ... from exc`` still preserves PyJWT's own message on
``__cause__`` for server-side diagnosis only -- verified safe: PyJWT's
messages name claims, never values.

Revocation (D7): ``check_revoked`` is NOT implemented in v1 -- a
deliberate, documented decision, not a silent inheritance of alpha's open
M3 bug. Checking revocation costs a network round trip per verification,
which directly contradicts D-25. The compensating control (inherited on
purpose, refs ``auth-firebase.md`` §9): a disabled/deleted account is read
FRESH from the DB on every request by the 6.4 guard, with no cache, so a
disabled account is locked out immediately regardless of the token's
remaining life.

The residual risk that control does NOT cover -- a token stolen before
revocation staying usable until ``exp`` (<= 3600s + 60s leeway) -- was
carried as a designed-but-unbuilt extension point until **3.79 built it**:
``framework/auth/revocation.py``, a short-TTL denylist over
``CacheProvider`` keyed ``auth:revoked:<sub>`` (the ``sub``, never the
token), capped at exactly that residual lifetime. It is checked by the 6.4
guard (``api/middleware/auth.py``) and **not here** -- which is the whole
point of naming that seam: ``verify_token`` stays a pure, zero-extra-I/O
function, so D-25's "no round trip per verification" survives the addition
untouched, and this adapter gained not one line. The writer is
``python -m app.ops.revoke`` rather than a use-case, because ``03 §1``
declares no logout/disable/delete operation in v1 (that module's docstring
explains the choice); a route that wants it later calls the same
``SessionRevocationList.revoke``.

Two clocks, deliberately: the JWKS cache uses ``time.monotonic()`` via the
module-level ``_now`` (immune to NTP steps/wall-clock jumps -- and the
monkeypatch seam this suite's time control uses); ``exp``/``iat`` are
checked by PyJWT itself against the wall clock
(``datetime.now(tz=timezone.utc)``, ``api_jwt.py:399``). These are two
different jobs and are not meant to agree.

No ``ExecutionContext``: ``verify_token(id_token) -> Identity`` takes none,
by the port's own contract -- authentication precedes context, there is no
``workspace_id`` before the caller's identity is known.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import jwt
from jwt import PyJWK, PyJWKSet
from jwt.exceptions import InvalidTokenError, PyJWKSetError, PyJWTError

from app.framework.errors import AppError, UnauthorizedError, ValidationError
from app.framework.ports.auth_provider import Identity
from app.framework.types import Json

# Google's JWK (not X.509) endpoint for the securetoken signer (D2) -- a
# module constant, never derived from a token header (`jku`/`x5u` are
# ignored entirely: nothing from the header is trusted beyond selecting a
# key from OUR OWN authoritative set).
_JWKS_URL: str = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)

# Firebase's issuer claim shape (05 §1.4): "<prefix><project_id>".
_ISSUER_PREFIX: str = "https://securetoken.google.com/"

# The ONLY algorithm this adapter ever verifies with. Passed explicitly
# alongside a PyJWK (whose own bound algorithm is a second, independent
# defense) -- D5: without this list, `algorithms` would default to
# `[key.algorithm_name]`, so a poisoned JWKS could self-authorize a
# downgrade.
_ALGORITHMS: tuple[str, ...] = ("RS256",)

# PyJWT's own claim checks (`verify_exp`, `verify_sub`, ...) only fire when
# the claim is PRESENT (`api_jwt.py:401-408`); `require` is what makes a
# claim's ABSENCE itself fail closed (D5).
_REQUIRED_CLAIMS: tuple[str, ...] = ("exp", "iat", "aud", "iss", "sub")

# Deliberate departure from alpha's 0 (D5, D7) -- bounds real clock drift
# (container suspend/resume, WSL2 sleep) at the Admin SDK's own documented
# maximum `clock_skew_seconds`.
_LEEWAY_S: float = 60.0

# Fail fast instead of hanging a request-handling coroutine on a dead/slow
# Google endpoint (the 2.3-2.6 precedent; 07-nfr latency budgets are
# sub-second).
_HTTP_TIMEOUT_S: float = 5.0

# The kid-miss refetch budget (D4): bounds the DoS amplifier a naive
# refetch-on-any-miss (`PyJWKClient.get_signing_key`, `jwks_client.py:
# 201-204`) would hand an attacker minting random `kid`s.
_MIN_REFRESH_INTERVAL_S: float = 60.0


def _now() -> float:
    """The JWKS cache's clock seam -- ``time.monotonic()`` (immune to NTP
    steps/wall-clock jumps), monkeypatched by the test suite for
    deterministic TTL/refresh-budget control. Distinct from the wall clock
    PyJWT itself uses for ``exp``/``iat`` (see the module docstring)."""
    return time.monotonic()


def _guard_project_id(project_id: str) -> str:
    """Strip and reject an empty ``project_id`` -- fail fast at
    construction, zero network (D5). ``FirebaseSettings.project_id``
    defaults to ``""`` and ``.env.example`` ships it empty: an adapter that
    boots in that state is misconfigured, full stop. Deliberately no shape
    regex (contrast ``_guard_key_name`` in ``vault_secrets.py``, which needs
    one because hvac interpolates it into a request URL): ``project_id`` is
    only ever a comparison operand, never a URL this adapter fetches."""
    stripped = project_id.strip()
    if not stripped:
        raise ValidationError("FIREBASE_PROJECT_ID must not be empty")
    return stripped


def _guard_ttl(jwks_cache_ttl: int) -> int:
    """Reject a non-positive cache TTL at construction (the
    ``PyJWKClient(lifespan<=0)`` precedent, ``jwks_client.py:92-95``):
    ``ttl<=0`` would silently mean "cache never fresh", turning every
    unknown ``kid`` into a 500 with the fetch budget permanently spent."""
    if jwks_cache_ttl <= 0:
        raise ValidationError(f"jwks_cache_ttl must be positive: {jwks_cache_ttl!r}")
    return jwks_cache_ttl


def _to_identity(claims: Json) -> Identity:
    """Map verified JWT claims onto the port's ``Identity`` (D5, the
    mapper's exact rules).

    ``sub`` -- never ``uid``/``user_id`` (alpha's Admin SDK normalization
    chain, refs ``auth-firebase.md`` §3, does not apply to a manual decode)
    -- is checked again here even though ``require`` already demands its
    presence: ``require`` only checks ``payload.get(claim) is None``
    (``api_jwt.py:430``), so ``sub: ""`` passes PyJWT entirely. An empty
    ``firebase_uid`` reaching JIT seeding must fail closed.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise _invalid_token()
    email = claims.get("email")
    return Identity(
        firebase_uid=sub,
        # A malformed `email` degrades to None rather than 401: `email` is
        # never an identity key (`firebase_uid` is), so this is not a
        # security failure.
        email=email if isinstance(email, str) else None,
        # `is True`, NOT `bool(...)`: `bool("false")` is `True`.
        email_verified=claims.get("email_verified") is True,
        claims=dict(claims),  # a copy -- never aliases PyJWT's own dict
    )


def _invalid_token(detail: str = "invalid token") -> AppError:
    """Every 401 this adapter raises carries this ONE fixed literal detail
    -- never the reason, never a claim, never the raw token (``10 §10``,
    D6)."""
    return UnauthorizedError(detail, code="auth.invalid_token")


def _translate_fetch(exc: Exception) -> AppError:
    """Map a JWKS-fetch-path failure (``httpx.HTTPError``, any
    ``jwt.PyJWTError`` that is not an ``InvalidTokenError``, a malformed
    JSON body, ...) onto the shared framework hierarchy (R6, D6) -- this is
    OUR key material/deployment being broken, not the caller's token, so it
    is never confused with a 401 (the exact fix for alpha's §7 bug, refs
    ``auth-firebase.md`` §7)."""
    return AppError("firebase key set unavailable", code="common.internal")


def create_firebase_http_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """Build the shared Firebase JWKS HTTP client (Composition Root / test
    harness only).

    ``trust_env=False`` (D8, a DD-11 trap of the same family as 2.6's hvac
    finding): httpx's default (``trust_env=True``) reads ``$HTTP_PROXY``/
    ``$HTTPS_PROXY``/``$ALL_PROXY``/``$NO_PROXY`` (and env-driven SSL
    config such as ``$SSL_CERT_FILE``) via ``urllib.request.getproxies()``
    whenever no explicit ``transport`` is given (``_client.py:685``) --
    letting whoever can set an env var on this process redirect the JWKS
    fetch and substitute Google's signing keys, i.e. forge tokens. Closing
    it is *stronger* than what the Firebase Admin SDK itself does. If an
    egress proxy is ever genuinely required it must arrive through
    ``Settings`` (DD-11), never ambient env.

    ``transport`` defaults to ``None`` (httpx's normal HTTP transport); the
    test suite passes an ``httpx.MockTransport`` through this SAME factory
    parameter, so the factory's real ``timeout``/``trust_env`` choices are
    what actually get exercised (the ``create_minio_client``
    ``access_key``/``secret_key`` precedent: a client-shape parameter the
    factory's own contract already flexes for).

    No socket is opened here: ``httpx.AsyncClient.__init__`` only sets up
    client state (``_state = ClientState.UNOPENED``) -- the first network
    call happens lazily, inside ``verify_token``, on the first
    unresolvable ``kid``/expired cache. This keeps ``from_env`` synchronous
    and connection-free (D8), the same ground already proven by
    ``create_redis_client``/``create_qdrant_client``.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, trust_env=False, transport=transport)


class FirebaseAuth:
    """Firebase-backed ``AuthProvider`` (02 §1.10, structural Protocol match).

    Takes plain values, not a ``Settings`` object (the
    ``MinioStorage(client, bucket: str)`` precedent) -- this adapter
    imports no config model at all. ``project_id``/``jwks_cache_ttl`` are
    guarded ONCE here, in ``__init__`` (not only in a factory), so every
    construction path -- Composition Root, tests, a future worker -- is
    protected; a factory-only check would be bypassable by constructing the
    class directly (D5, the repair of alpha's lazy-init anti-pattern).
    """

    def __init__(self, client: httpx.AsyncClient, project_id: str, jwks_cache_ttl: int) -> None:
        self._client = client
        self._project_id = _guard_project_id(project_id)
        self._issuer = _ISSUER_PREFIX + self._project_id
        self._ttl_s = _guard_ttl(jwks_cache_ttl)
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_attempt_at: float | None = None
        # Constructed OUTSIDE a running loop (`from_env` is sync); safe on
        # Python 3.12, where `asyncio.Lock` binds to its loop lazily on the
        # first `await` (D3). Consequence: every test needs its OWN
        # adapter instance -- a lock bound to a finished loop raises.
        self._lock = asyncio.Lock()

    async def verify_token(self, id_token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(id_token)
        except InvalidTokenError as exc:  # DecodeError, non-str kid, bad crit
            raise _invalid_token() from exc
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _invalid_token()

        # Resolved OUTSIDE the decode try: this may raise our own AppError
        # (common.internal for an unresolvable key set) which must not be
        # re-translated -- the vault_secrets.py:189 idiom.
        signing_key = await self._resolve_key(kid)

        try:
            claims = jwt.decode(
                id_token,
                key=signing_key,  # PyJWK -> alg bound at construction
                algorithms=_ALGORITHMS,  # Sequence[str]: the tuple is fine
                audience=self._project_id,
                issuer=self._issuer,
                leeway=_LEEWAY_S,
                options={
                    "verify_signature": True,
                    "require": list(_REQUIRED_CLAIMS),  # TypedDict demands list[str]
                    "strict_aud": True,
                },
            )
        except InvalidTokenError as exc:  # base of EVERY token failure
            raise _invalid_token() from exc
        except PyJWTError as exc:  # PyJWKError/InvalidKeyError/... = infra
            raise _translate_fetch(exc) from exc

        return _to_identity(claims)

    async def _resolve_key(self, kid: str) -> PyJWK:
        key = self._lookup(kid)
        if key is not None and self._is_fresh():
            return key  # fast path -- zero HTTP, no lock

        async with self._lock:
            key = self._lookup(kid)  # double-check: a peer may have refreshed
            if key is not None and self._is_fresh():
                return key

            fetch_error: Exception | None = None
            if self._may_attempt_fetch():
                self._last_attempt_at = _now()  # stamped BEFORE: a FAILING fetch
                try:  # must also consume the budget
                    await self._fetch_keys()
                except (httpx.HTTPError, PyJWTError, ValueError, TypeError) as exc:
                    fetch_error = exc  # tolerated iff the cache can still answer

            key = self._lookup(kid)
            if key is not None:
                return key  # incl. the stale-if-error fallback

            if self._is_fresh():
                raise _invalid_token()  # authoritative miss -> 401
            raise _translate_fetch(fetch_error or RuntimeError()) from fetch_error

    async def _fetch_keys(self) -> None:
        response = await self._client.get(_JWKS_URL)
        response.raise_for_status()
        body = response.json()

        # TRAP 1 (R6): PyJWKSet.from_dict does `obj.get("keys", [])` -- a
        # JSON *array* body would escape as a raw AttributeError, a foreign
        # exception leaking out of the adapter. PyJWKClient guards this
        # itself (jwks_client.py:155-156); so must we.
        if not isinstance(body, dict):
            raise PyJWKSetError("the JWKS endpoint did not return a JSON object")

        # TRAP 1b (R6): the same hole one level deeper, and NOT guarded by the
        # library. PyJWKSet.__init__ wraps each entry in `except PyJWTError`
        # (api_jwk.py:145-152), but PyJWK.__init__'s first act is
        # `self._jwk_data.get("kty")` (api_jwk.py:33) -- on a non-dict entry
        # that is a raw AttributeError, which is not a PyJWTError, so the
        # library's own skip-unusable-keys loop does not catch it, and it
        # escapes this adapter untranslated (R6 violation on an auth path).
        # A 200 whose body is `{"keys": [123]}` is the exact shape; probed:
        # every non-dict entry type leaks, while every *dict* entry with
        # garbage values is safely caught (TypeError/ValueError/PyJWKSetError,
        # all in _resolve_key's tuple), and a non-list/missing/empty `keys` is
        # PyJWKSetError'd by the library. So this guard is complete, and is
        # deliberately narrow: catching AttributeError in _resolve_key instead
        # would mask genuine bugs in our own code as "key set unavailable".
        raw_keys = body.get("keys")
        if not isinstance(raw_keys, list) or not all(isinstance(k, dict) for k in raw_keys):
            raise PyJWKSetError("the JWKS endpoint returned a malformed key list")

        jwk_set = PyJWKSet.from_dict(body)
        # TRAP 2 (mypy --strict): `key.key_id` is `str | None`; a plain
        # `if key.key_id` does NOT narrow it in the key position ->
        # dict[str | None, PyJWK]. The walrus narrows it to `str` with no
        # `type: ignore`. The `public_key_use` filter mirrors
        # PyJWKClient.get_signing_keys (jwks_client.py:174-178).
        keys = {
            kid: key
            for key in jwk_set.keys
            if key.public_key_use in ("sig", None) and (kid := key.key_id) is not None
        }
        # TRAP 3 (security): an empty post-filter set installed as "fresh"
        # would turn every kid miss into a 401 -- an infra fault dressed as
        # a bad token.
        if not keys:
            raise PyJWKSetError("the JWKS endpoint returned no usable signing keys")

        # Assigned ONLY on success -- a failed refresh must never wipe a
        # usable key set (PyJWT's own documented lesson,
        # jwks_client.py:129-134). This IS the stale-if-error fallback.
        self._keys, self._fetched_at = keys, _now()

    def _lookup(self, kid: str) -> PyJWK | None:
        return self._keys.get(kid)

    def _is_fresh(self) -> bool:
        return self._fetched_at is not None and (_now() - self._fetched_at) < self._ttl_s

    def _may_attempt_fetch(self) -> bool:
        return (
            self._last_attempt_at is None
            or (_now() - self._last_attempt_at) >= _MIN_REFRESH_INTERVAL_S
        )
