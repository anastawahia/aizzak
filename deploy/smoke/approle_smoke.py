"""AppRole smoke test (7.3 · D-22 · 05-rbac-config-secrets §3.3).

Proves the AppRole path by USING it, not by reading Vault's configuration
back: logs in with role_id + secret_id through the application's own
``create_vault_client``, then drives the real ``VaultSecrets`` adapter over
every operation ``src/`` actually performs -- and confirms that the paths
``app-policy.hcl`` deliberately withholds are in fact refused.

A policy is only least-privilege if the denials are real. Reading
``vault policy read app`` back proves the text was stored; it proves nothing
about what a token carrying it can do. So this script asserts BOTH
directions, the §3.68/§3.75 bidirectional-comparison habit applied to an ACL.

Run inside the app container (it imports ``app.*``), with the secret_id
passed in the environment:

    docker compose exec -e VAULT_SECRET_ID=<id> app python /app/deploy/smoke/approle_smoke.py

⚠️ Prints no credential. role_id, secret_id and the resulting token are
never echoed (10 §10).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from collections.abc import Callable

import hvac

from app.framework.errors import AppError
from app.framework.settings.settings import VaultSettings
from app.infrastructure.config.vault_auth import load_vault_auth
from app.infrastructure.secrets.vault_secrets import VaultSecrets, create_vault_client

_TENANT_KEY = "tenant-secrets"
_MINIO_PATH = "secret/data/minio"


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


async def _assert_denied(label: str, call: Callable[[], object], consequence: str) -> None:
    """Assert that the policy REFUSES an operation.

    ``AppError`` as well as hvac's ``Forbidden``: the same 403 arrives raw
    from a direct client call and translated from anything routed through
    ``VaultSecrets``, and this asserts the denial, not which layer reported
    it. Awaits a coroutine result so async and sync calls share one helper.
    """
    try:
        result = call()
        if inspect.isawaitable(result):
            await result
    except (hvac.exceptions.Forbidden, AppError):
        print(f"{label}  DENIED  OK")
    else:
        _fail(f"{label} was PERMITTED -- {consequence}")


async def main() -> None:
    addr = os.environ["VAULT_ADDR"]
    role_id = os.environ.get("VAULT_ROLE_ID") or ""
    auth = load_vault_auth()

    if auth.token:
        _fail("VAULT_TOKEN is set -- this proves token auth, not AppRole. Blank it.")
    if not role_id or not auth.secret_id:
        _fail("VAULT_ROLE_ID and VAULT_SECRET_ID must both be set for AppRole login.")

    # [1] The login itself. `create_vault_client` is the SAME factory the
    # Composition Root calls -- not a reimplementation of it.
    settings = VaultSettings(addr=addr, role_id=role_id)
    client = create_vault_client(settings, token=None, secret_id=auth.secret_id)
    if not client.token:
        _fail("[1] AppRole login returned no token")
    print("[1] AppRole login (role_id + secret_id -> token)                 OK")

    # [1b] ⚠️ `is_authenticated()` is NOT the liveness check it looks like.
    # It issues `auth/token/lookup-self`, which `token_no_default_policy=true`
    # denies -- so it reports False for a token that works perfectly, as
    # every check below then demonstrates. Asserted rather than merely
    # commented, because the failure it would cause is a total boot failure
    # and the obvious "sanity check" to add to `create_vault_client` is
    # exactly this call. Its docstring already refuses it on latency grounds;
    # under this policy that refusal is load-bearing for correctness too.
    if client.is_authenticated():
        _fail("[1b] is_authenticated() succeeded -- default policy leaked back in")
    print("[1b] is_authenticated() is False (lookup-self denied) -- expected OK")

    secrets = VaultSecrets(client)

    # [2] KV read -- the operation that boots the app (`connect_storage`).
    payload = await secrets.get_secret(_MINIO_PATH)
    if not isinstance(payload, dict) or "access_key" not in payload:
        _fail(f"[2] {_MINIO_PATH} did not return the expected shape")
    print("[2] KV read  secret/data/minio                                   OK")

    # [3] Transit round trip on the ONE unified key (SEC-07). Encrypt and
    # decrypt are separate policy paths, so a round trip exercises both.
    plaintext = b"aizzak-approle-smoke"
    ciphertext = await secrets.encrypt(_TENANT_KEY, plaintext)
    if not ciphertext.startswith("vault:v"):
        _fail("[3] ciphertext is not a Vault Transit blob")
    if await secrets.decrypt(_TENANT_KEY, ciphertext) != plaintext:
        _fail("[3] decrypt did not round-trip the plaintext")
    print("[3] Transit encrypt + decrypt  tenant-secrets                    OK")

    # [3b] P1-9 (docs/p1-hardening-plan.md §3 step 12): `transit/rewrap` is a
    # THIRD Transit policy path, granted alongside its first (and only)
    # caller, `app.ops.rotate_transit`. Vault mints a fresh nonce on every
    # rewrap regardless of whether the key version changed (measured live --
    # `vault_secrets.py`'s own `rewrap` docstring), so the BYTES differ even
    # here where no rotation has happened in this smoke run; what must stay
    # constant is the key VERSION embedded in the `vault:v<n>:...` prefix,
    # which is exactly what `app.ops.rotate_transit` compares to tell
    # "already current" apart from "needs rewrapping".
    rewrapped = await secrets.rewrap(_TENANT_KEY, ciphertext)
    if not rewrapped.startswith("vault:v"):
        _fail("[3b] rewrap did not return a Vault Transit blob")
    if rewrapped.split(":")[1] != ciphertext.split(":")[1]:
        _fail("[3b] rewrap of an already-current ciphertext changed the key version")
    if await secrets.decrypt(_TENANT_KEY, rewrapped) != plaintext:
        _fail("[3b] the rewrapped ciphertext did not decrypt to the original plaintext")
    print("[3b] Transit rewrap  tenant-secrets  (policy grant P1-9)         OK")

    # [4..7] The denials. Each of these SUCCEEDS under the dev root token this
    # replaces, so a pass here is the whole difference AppRole buys.
    await _assert_denied(
        "[4] sys/mounts (root could list every engine)",
        client.sys.list_mounted_secrets_engines,
        "the policy is not confining this token",
    )
    await _assert_denied(
        "[5] transit/encrypt/some-other-key (not SEC-07's key)",
        lambda: secrets.encrypt("some-other-key", b"x"),
        "an unnamed Transit key was usable -- the grant is wildcarded",
    )
    await _assert_denied(
        "[6] KV write (read-only: cannot lock itself out)",
        lambda: client.secrets.kv.v2.create_or_update_secret(
            mount_point="secret", path="minio", secret={"access_key": "hijacked"}
        ),
        "the app could OVERWRITE its own MinIO credentials",
    )
    await _assert_denied(
        "[7] transit/keys/tenant-secrets/rotate (key ADMINISTRATION, not use)",
        lambda: client.secrets.transit.rotate_key(name=_TENANT_KEY, mount_point="transit"),
        "the app could mint a new key version on its own initiative -- "
        "rewrap [3b] must only ever re-encrypt under a version an operator "
        "already minted, never mint one itself",
    )

    print("\nAppRole smoke: all checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
