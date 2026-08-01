"""Vault adapter for the ``SecretsProvider`` port (02-port-contracts §1.9,
D-03/D-22). Phase 2.6.

Same split as ``cache/redis_cache.py`` (2.3), ``storage/minio_storage.py``
(2.4) and ``vector/qdrant_store.py`` (2.5): a factory builds the technology
client (``create_vault_client``, Composition Root / test harness only), and a
thin adapter class (``VaultSecrets``) implements the port over it (structural
Protocol match -- no inheritance). hvac is a SYNCHRONOUS client (built on
``requests``), so -- exactly like minio-py's -- every port method offloads
its one blocking call through ``asyncio.to_thread`` so the event loop stays
free.

Path convention (05 §3.1, literal): a ``get_secret`` path is the KV v2
catalog string exactly AS WRITTEN in the secrets catalog -- ``secret/data/db``,
``secret/data/minio``, ``secret/data/providers/platform``, ... -- because
hvac's own ``read_secret_version`` already injects the ``/data/`` infix into
the wire URL itself (``/v1/{mount_point}/data/{path}``). Requiring callers to
carry that infix keeps every call site ``grep``-able against the catalog
verbatim, and turns a caller's mistake (a bare KV path, or the ``metadata/``
endpoint) into an immediate, loud ``ValidationError`` instead of a silent
misread of the wrong sub-path.

Error policy (R6, the 2.3/2.4/2.5 precedent): KV reads carve out ONE
caller-branchable case -- a path with nothing ever written raises hvac's
``InvalidPath``, kept clean and separate (the ``NoSuchKey`` precedent from
2.4) and translated to ``NotFoundError`` (``common.not_found``/404), because
``get_secret``'s contract returns ``Json``, never ``None``, so absence must
surface as an exception. Folding EVERY Vault failure into one 500 would make
"connection refused" and "secret genuinely absent" indistinguishable to a
caller that legitimately needs to branch on the latter. Transit has no such
caller-branchable case -- an unknown/mismatched key, a sealed Vault, a
permission failure, ... are all infrastructure/config faults -- so every
Transit failure folds straight into ``common.internal``. ``rewrap`` (P1-9,
docs/p1-hardening-plan.md §3 step 12) is a Transit operation and follows the
identical policy -- Vault answers a rewrap of already-current ciphertext
exactly like any other successful call (a fresh ciphertext string, never a
distinguishable error -- see ``rewrap``'s own docstring for what actually
does and does not stay constant), so there is no caller-branchable case to
carve out here either.

``encrypt``/``decrypt`` round-trip through base64 in both directions (Vault
Transit's own wire encoding for plaintext/ciphertext material): the plaintext
is base64-encoded before the call and the recovered plaintext is
base64-decoded before returning -- preserving arbitrary non-UTF-8 byte
strings exactly, matching the port's ``bytes`` (never ``str``) contract on
both sides.

``key_name`` is guarded (``_guard_key_name``) BEFORE any network call: hvac
interpolates it directly into the Transit request URL
(``/v1/transit/encrypt/{name}``), so an unguarded value could carry a ``/``
or ``..`` segment straight into the request path -- the same
path-traversal concern ``_parse_kv_path`` polices on the KV side, enforced
here before hvac/``requests`` ever sees the value.

DD-11 trap, closed once at construction (``create_vault_client``): hvac's own
``Client.__init__`` silently falls back to ``os.getenv("VAULT_ADDR", ...)``
and an env-derived token (``$VAULT_TOKEN``) whenever ``url``/``token`` are
left ``None`` -- which would let Vault reach into the process environment
behind ``infrastructure/config``'s back (DD-11: that package is the ONLY
env/``.env`` reader). Both are therefore always passed explicitly.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Callable
from functools import partial
from typing import TypeVar

import hvac
from hvac.exceptions import Forbidden, InvalidPath, Unauthorized, VaultError

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.framework.settings.settings import VaultSettings
from app.framework.types import Json

_T = TypeVar("_T")

# Fail fast instead of hanging a request-handling coroutine on a dead/slow
# Vault (the 2.3/2.4/2.5 precedent; 07-nfr latency budgets are sub-second).
_TIMEOUT_S = 5.0

# KV v2's catalog-literal path convention (05 §3.1) -- see the module
# docstring: hvac's own ``read_secret_version`` injects this infix into the
# wire URL itself, so every path this adapter accepts must already carry it.
_KV_DATA_INFIX = "/data/"

# The one Transit mount this platform provisions (05 §3.2). The unified
# ``tenant-secrets`` key (SEC-07) is a caller-supplied ``key_name`` -- not a
# concern of this constant.
_TRANSIT_MOUNT = "transit"

# A Transit key name is interpolated directly into hvac's request URL
# (``/v1/transit/encrypt/{name}``) -- restricted to a bare token before any
# network call ever sees it (``_guard_key_name``).
_KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def create_vault_client(
    settings: VaultSettings, *, token: str | None = None, secret_id: str | None = None
) -> hvac.Client:
    """Build the shared Vault client (Composition Root / test harness only).

    ``url``/``token`` are always passed explicitly -- never left for hvac to
    default -- closing the DD-11 trap described in the module docstring:
    ``hvac.Client.__init__`` reads ``$VAULT_ADDR``/``$VAULT_TOKEN`` itself
    whenever either argument is omitted, which would let Vault credentials
    leak in behind ``infrastructure/config``'s back. ``token or ""`` (never
    bare ``token``, which could be ``None``) is what actually suppresses
    hvac's fallback: it only consults the environment when the argument is
    ``None``.

    Two authentication paths, matching 05 §3.3 / D-22:
    * ``token`` given (a root/dev token, or a token obtained some other way)
      -- the client is immediately usable, with zero network calls here.
    * otherwise, when both ``settings.role_id`` (non-secret, 05 §2) and
      ``secret_id`` (the one sensitive env value, 05 §3.3) are present, this
      function makes the SOLE network call this factory ever performs:
      ``client.auth.approle.login(...)`` (AppRole, D-22) -- the client bakes
      in the resulting token. This is the non-local/production path.

    Deliberately no ``client.is_authenticated()`` call anywhere here. It was
    refused originally as a network round trip a routine construction has no
    reason to spend -- and 7.3 turned that into a correctness requirement:
    ``is_authenticated()`` issues ``auth/token/lookup-self``, which the
    AppRole policy denies (the role is created ``token_no_default_policy=
    true``, so Vault's built-in ``default`` policy -- the thing that normally
    grants ``lookup-self`` -- is not attached). It therefore returns **False
    for a perfectly working token**. Measured live, and asserted in
    ``deploy/smoke/approle_smoke.py`` so the obvious "sanity check" someone
    adds here later fails loudly in a smoke run rather than silently at boot.
    """
    client = hvac.Client(url=settings.addr, token=(token or ""), timeout=_TIMEOUT_S)
    relogin = create_approle_relogin(settings, client, secret_id=secret_id) if not token else None
    if relogin is not None:
        relogin()
    return client


def create_approle_relogin(
    settings: VaultSettings, client: hvac.Client, *, secret_id: str | None
) -> Callable[[], None] | None:
    """Build the callable that re-runs this client's AppRole login, or
    ``None`` when the deployment is not using AppRole (7.3).

    Exists because an AppRole token EXPIRES and this client never renews it.
    ``08-local-runbook §3.1`` fixes ``token_ttl=1h``; ``create_vault_client``
    logs in exactly once, at composition. Measured on a throwaway 8-second
    role rather than reasoned about: the moment the token lapses, BOTH
    ``get_secret`` and ``encrypt``/``decrypt`` start failing -- and since
    Vault is on the runtime path for every ``credentials``/``integrations``
    operation (not just startup), the symptom is an app that serves traffic
    perfectly for an hour and then cannot touch a single tenant secret,
    while ``/health/ready`` stays green throughout (it deliberately probes
    no dependency -- §3.75).

    Returned as an opaque callable rather than handing ``VaultSecrets`` the
    ``secret_id`` itself: 05 §3.3 keeps that value out of every object that
    might be logged or dumped, which is the same reason ``VaultAuth`` is kept
    out of ``Settings`` and redacts its own ``__repr__``. A closure carries
    the credential without giving the adapter a field to leak.

    ``None`` when either half of the AppRole pair is absent -- the token-auth
    path (local dev) then keeps its exact previous behaviour, including the
    absence of any retry.
    """
    role_id = settings.role_id
    if not role_id or not secret_id:
        return None

    def _relogin() -> None:
        client.auth.approle.login(role_id=role_id, secret_id=secret_id)

    return _relogin


def _parse_kv_path(path: str) -> tuple[str, str]:
    """Split a catalog-literal KV v2 path (05 §3.1) into hvac's
    ``(mount_point, path)`` pair.

    Only the exact ``<mount>/data/<rel>`` shape is accepted (see the module
    docstring for why): a bare KV path (``minio``), the ``metadata/``
    endpoint (``secret/metadata/x``), an empty mount or relative segment, and
    any ``..`` path segment (defence in depth against path traversal, the
    same concern ``_guard_key_name`` polices on the Transit side) all raise
    ``ValidationError`` rather than being guessed at.
    """
    if _KV_DATA_INFIX not in path:
        raise ValidationError(f"secret path must contain {_KV_DATA_INFIX!r}: {path!r}")
    mount, _, rel = path.partition(_KV_DATA_INFIX)
    if not mount or "/" in mount:
        raise ValidationError(f"secret path has an invalid mount segment: {path!r}")
    if not rel:
        raise ValidationError(f"secret path is missing a relative path: {path!r}")
    if ".." in rel.split("/"):
        raise ValidationError(f"secret path must not contain '..' segments: {path!r}")
    return mount, rel


def _guard_key_name(key_name: str) -> None:
    """Reject a Transit ``key_name`` that is not a bare token BEFORE any
    network call. hvac interpolates ``key_name`` directly into the request
    URL (``/v1/transit/encrypt/{name}``), so an unguarded value carrying a
    ``/`` or ``..`` segment could redirect the request to an unrelated path
    -- caught here, before hvac/``requests`` ever sees it."""
    if not _KEY_NAME_RE.fullmatch(key_name):
        raise ValidationError(f"invalid Transit key name: {key_name!r}")


def _translate(exc: Exception) -> AppError:
    """Map ANY Vault driver failure onto the shared framework hierarchy
    (03-api-spec §4, R6) -- hvac/``requests`` exception types never escape
    this adapter. Every Transit failure (an unknown or mismatched key,
    permission denied, a sealed/down Vault, connection refused, a timeout,
    ...) is an infrastructure/config fault a caller cannot meaningfully
    branch on, so it folds into the 500-class ``common.internal`` -- the same
    policy 2.3/2.4/2.5 apply to their own drivers."""
    return AppError("secrets operation failed", code="common.internal")


def _translate_kv(exc: Exception) -> AppError:
    """KV reads carve ONE caller-branchable case out of ``_translate``:
    ``InvalidPath`` (Vault's 404 for a path with no version ever written)
    becomes ``NotFoundError`` (``common.not_found``/404) -- the ``NoSuchKey``
    precedent from 2.4 -- because ``get_secret``'s contract returns ``Json``,
    never ``None``, so absence must surface as a distinguishable exception.
    Every other exception (``Forbidden``, ``Unauthorized``, a sealed/down
    Vault, connection refused, ...) still folds into ``_translate``'s
    ``common.internal``."""
    if isinstance(exc, InvalidPath):
        return NotFoundError("secret does not exist in Vault")
    return _translate(exc)


class VaultSecrets:
    """Vault-backed ``SecretsProvider`` (02 §1.9, structural Protocol match)."""

    def __init__(self, client: hvac.Client, *, relogin: Callable[[], None] | None = None) -> None:
        self._client = client
        self._relogin = relogin

    async def _call(self, fn: Callable[[], _T]) -> _T:
        """Run one hvac call off the event loop, re-authenticating ONCE if
        Vault rejects the token (7.3).

        Wraps only the network call, never the response unwrapping: each
        method's ``resp[...]`` stays inside its own ``try`` so an off-contract
        200 still translates rather than escaping (the 2.6 verifier
        follow-up). Exceptions propagate untouched to that same ``except``,
        so the error-translation contract is unchanged.

        ``Forbidden``/``Unauthorized`` is what an EXPIRED token looks like on
        the wire -- and it is also what a genuine policy denial looks like,
        which cannot be told apart from here. Retrying a real denial costs one
        wasted round trip and then fails identically, so the ambiguity is paid
        for in latency on an already-failing request, never in correctness.

        Exactly one retry, and no backoff: a second rejection means the
        credentials are wrong rather than stale, and a loop would turn a
        misconfigured ``secret_id`` into a login storm against Vault. The
        no-internal-retry discipline the Redis/Streams adapters follow is not
        violated -- this re-establishes an expired SESSION, it does not retry
        a failed operation.

        ``None`` relogin (token auth) re-raises immediately: byte-for-byte
        the previous behaviour.
        """
        try:
            return await asyncio.to_thread(fn)
        except (Forbidden, Unauthorized):
            if self._relogin is None:
                raise
            await asyncio.to_thread(self._relogin)
            return await asyncio.to_thread(fn)

    async def get_secret(self, path: str) -> Json:
        # Parsed before to_thread: our own ValidationError must skip the catch below.
        mount, rel = _parse_kv_path(path)

        try:
            resp = await self._call(
                partial(
                    self._client.secrets.kv.v2.read_secret_version,
                    path=rel,
                    mount_point=mount,
                    raise_on_deleted_version=True,
                )
            )
            # Unwrapped INSIDE the try (2.6 verifier follow-up): a 200 body
            # that is off the KV-v2 contract (missing/renamed keys, a
            # non-dict) is the same operational fault as a driver error and
            # must translate, never escape as a raw KeyError/TypeError (R6).
            return dict(resp["data"]["data"])  # explicit -- the metadata wrapper never leaks
        except (VaultError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _translate_kv(exc) from exc

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        _guard_key_name(key_name)  # before to_thread -- a bad name never reaches the network
        encoded = base64.b64encode(plaintext).decode("ascii")

        try:
            resp = await self._call(
                partial(
                    self._client.secrets.transit.encrypt_data,
                    name=key_name,
                    plaintext=encoded,
                    mount_point=_TRANSIT_MOUNT,
                )
            )
            # Unwrapped INSIDE the try -- same off-contract-200 rationale as
            # ``get_secret`` (R6, 2.6 verifier follow-up).
            return str(resp["data"]["ciphertext"])
        except (VaultError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _translate(exc) from exc

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        _guard_key_name(key_name)  # before to_thread -- a bad name never reaches the network

        try:
            resp = await self._call(
                partial(
                    self._client.secrets.transit.decrypt_data,
                    name=key_name,
                    ciphertext=ciphertext,
                    mount_point=_TRANSIT_MOUNT,
                )
            )
            # Unwrapped INSIDE the try -- same off-contract-200 rationale as
            # ``get_secret``. ``validate=True`` makes a corrupted (non-base64)
            # body raise ``binascii.Error`` (a ``ValueError``, translated
            # below) instead of silently decoding to WRONG secret bytes --
            # for a secrets adapter, silent corruption is worse than a 500.
            return base64.b64decode(str(resp["data"]["plaintext"]), validate=True)
        except (VaultError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _translate(exc) from exc

    async def rewrap(self, key_name: str, ciphertext: str) -> str:
        """Re-encrypt ``ciphertext`` under the key's CURRENT Transit version,
        without the plaintext ever leaving Vault (P1-9, docs/p1-hardening
        -plan.md §3 step 12 -- the key-rotation path 05 §3.3 names).

        Deliberately absent from the ``SecretsProvider`` port
        (``framework/ports/secrets_provider.py``): no application use case
        rotates keys, and putting rewrap on the port would hand every
        request-path consumer of ``SecretsProvider`` -- which is every
        module resolving a credential or a connector token -- a rotation
        capability none of them should ever be able to reach. The one
        caller is ``app.ops.rotate_transit``, which imports this ADAPTER
        directly (05 §3.3's port boundary governs application code; an ops
        tool is composition-root-adjacent the same way ``app.ops.provision``
        already imports ``app.infrastructure.persistence`` directly).

        ⚠️ Measured live, and worth stating precisely because the obvious
        assumption is wrong: rewrapping ciphertext already at the key's
        current version does NOT return the same bytes back. Vault's AES-GCM
        mode picks a fresh nonce on every ``rewrap`` call regardless of
        whether the key version actually changed, so two consecutive
        rewraps of the SAME input ciphertext -- with no rotation between
        them -- come back different from each other and from the input.
        What stays constant is the key VERSION NUMBER embedded in Vault's
        own ``vault:v<n>:...`` wire prefix. ``app.ops.rotate_transit``
        relies on comparing THAT (parsed, never decrypted) to tell "already
        at the current version" apart from "just rotated forward", not on
        byte-for-byte ciphertext equality.
        """
        _guard_key_name(key_name)  # before to_thread -- the encrypt/decrypt precedent

        try:
            resp = await self._call(
                partial(
                    self._client.secrets.transit.rewrap_data,
                    name=key_name,
                    ciphertext=ciphertext,
                    mount_point=_TRANSIT_MOUNT,
                )
            )
            # Unwrapped INSIDE the try -- same off-contract-200 rationale as
            # ``encrypt``/``decrypt`` (R6, 2.6 verifier follow-up).
            return str(resp["data"]["ciphertext"])
        except (VaultError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _translate(exc) from exc
