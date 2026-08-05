"""Live-Vault tests for ``VaultSecrets`` (02-port-contracts §1.9, Phase 2.6).

Runs against the real persistent local Compose Vault (see
``tests/integration/conftest.py``); auto-skips via ``live_vault`` when
unreachable. ``transit_key``/``kv_path`` hand out fresh, uniquely-named
per-test artifacts (created/removed through the raw root client, since key
and path *lifecycle* are Vault-admin operations the ``SecretsProvider`` port
itself does not expose); this file only ever exercises the port's three
methods (``get_secret``/``encrypt``/``decrypt``) through ``vault_secrets``,
built via the adapter's own factory.

Behaviours pinned beyond the plain round-trips: nested JSON shapes and
unicode survive ``get_secret`` verbatim with no KV-envelope leakage; a path
with nothing ever written raises ``NotFoundError`` (``common.not_found``/404
-- the port's ``get_secret`` returns ``Json``, never ``None``); Transit
ciphertext carries Vault's own ``vault:v<n>:...`` wire shape; encrypting with
one key and decrypting with another surfaces as a framework error, never a
raw hvac exception; an invalid token and a dead Vault endpoint both fold into
``common.internal``; and ``ValidationError`` from this adapter's own guards
(a path-traversal ``key_name``, a non-catalog KV path) fires before any
network call, never wrapped into a 500. ``rewrap`` (P1-9,
docs/p1-hardening-plan.md §3 step 12) is pinned against a REAL key rotation on a throwaway,
uniquely-named key -- never the shared ``tenant-secrets`` key, since
advancing ``min_decryption_version`` is irreversible.
"""

from __future__ import annotations

import contextlib

import hvac
import pytest

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import VaultSettings
from app.infrastructure.secrets.vault_secrets import VaultSecrets, create_vault_client
from tests.integration.conftest import LiveVault

pytestmark = [pytest.mark.live_vault]


# --------------------------------------------------------------------------- #
# (1)-(3) KV reads through the port                                           #
# --------------------------------------------------------------------------- #
async def test_get_secret_round_trips_a_value_written_by_the_raw_client(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, kv_path: str
) -> None:
    rel = kv_path.removeprefix("secret/data/")
    vault_client_raw.secrets.kv.v2.create_or_update_secret(
        path=rel,
        secret={"access_key": "AKIAEXAMPLE123", "secret_key": "s3cr3t-value"},
        mount_point="secret",
    )

    result = await vault_secrets.get_secret(kv_path)

    assert result == {"access_key": "AKIAEXAMPLE123", "secret_key": "s3cr3t-value"}


async def test_get_secret_preserves_nested_json_and_unicode_with_no_envelope_leak(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, kv_path: str
) -> None:
    rel = kv_path.removeprefix("secret/data/")
    payload = {
        "provider": "openai",
        "label": "مفتاح الإنتاج",
        "scopes": ["read", "write", "admin"],
        "meta": {"nested": {"deeper": True, "count": 3}},
    }
    vault_client_raw.secrets.kv.v2.create_or_update_secret(
        path=rel, secret=payload, mount_point="secret"
    )

    result = await vault_secrets.get_secret(kv_path)

    assert result == payload
    assert "metadata" not in result  # the KV v2 envelope must never leak through


async def test_get_secret_for_a_never_written_path_raises_not_found(
    vault_secrets: VaultSecrets, kv_path: str
) -> None:
    # ``kv_path`` only reserves a unique name -- nothing is written here.
    with pytest.raises(NotFoundError) as excinfo:
        await vault_secrets.get_secret(kv_path)

    assert excinfo.value.code == "common.not_found"
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# (4)-(6) Transit encrypt/decrypt through the port                           #
# --------------------------------------------------------------------------- #
async def test_encrypt_decrypt_round_trips_non_utf8_bytes(
    vault_secrets: VaultSecrets, transit_key: str
) -> None:
    payload = b"\x00\xff\xfe\xd8\x00 binary payload \x01\x02\x03"

    ciphertext = await vault_secrets.encrypt(transit_key, payload)
    recovered = await vault_secrets.decrypt(transit_key, ciphertext)

    assert recovered == payload


async def test_ciphertext_has_the_vault_transit_wire_shape(
    vault_secrets: VaultSecrets, transit_key: str
) -> None:
    ciphertext = await vault_secrets.encrypt(transit_key, b"hello")

    assert ciphertext.startswith("vault:v")


async def test_encrypt_with_one_key_cannot_decrypt_with_another(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, transit_key: str
) -> None:
    other_key = f"aizzak-test-{new_uuid7()}"
    vault_client_raw.secrets.transit.create_key(name=other_key, mount_point="transit")
    try:
        ciphertext = await vault_secrets.encrypt(transit_key, b"top secret")

        with pytest.raises(AppError) as excinfo:
            await vault_secrets.decrypt(other_key, ciphertext)

        assert excinfo.value.code == "common.internal"
        assert excinfo.value.status == 500
    finally:
        with contextlib.suppress(Exception):
            vault_client_raw.secrets.transit.update_key_configuration(
                name=other_key, deletion_allowed=True, mount_point="transit"
            )
            vault_client_raw.secrets.transit.delete_key(name=other_key, mount_point="transit")


# --------------------------------------------------------------------------- #
# (7)-(8) failure translation -- connection/auth faults                      #
# --------------------------------------------------------------------------- #
async def test_adapter_with_an_invalid_token_surfaces_as_common_internal(
    live_vault: LiveVault, kv_path: str
) -> None:
    client = create_vault_client(VaultSettings(addr=live_vault.addr), token="totally-bogus-token")
    secrets = VaultSecrets(client)

    with pytest.raises(AppError) as excinfo:
        await secrets.get_secret(kv_path)

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_adapter_against_a_dead_port_surfaces_as_common_internal() -> None:
    # A dead local port: no Vault ever listens here (07-nfr fail-fast timeout
    # applies -- this may take up to the adapter's own connect timeout).
    client = create_vault_client(VaultSettings(addr="http://127.0.0.1:8201"), token="irrelevant")
    secrets = VaultSecrets(client)

    with pytest.raises(AppError) as excinfo:
        await secrets.get_secret("secret/data/anything")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# (11) rewrap -- the honest key-rotation proof (P1-9, step 12)               #
# --------------------------------------------------------------------------- #
async def test_rewrap_is_load_bearing_across_a_real_key_rotation(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """The exit criterion the plan's own wording (docs/p1-hardening-plan.md
    §3 step 12) understates: "ciphertext encrypted with version 1 is readable
    after rotating to version 2" is true of Vault Transit with NO rewrap at
    all (old key versions are retained by design), so it proves nothing about
    this adapter's ``rewrap`` method. The only sequence in which ``rewrap``
    is load-bearing is the one below -- encrypt under v1, rotate to v2,
    rewrap ONE of two v1 ciphertexts, advance ``min_decryption_version`` to
    2, then assert BOTH directions in the same test: the un-rewrapped
    ciphertext now fails to decrypt (its version is below the new floor), and
    the rewrapped one still recovers the original plaintext exactly.

    Runs entirely on ``transit_key`` -- a fresh, uniquely-named, per-test key
    the ``transit_key`` fixture creates and deletes -- never the shared
    ``tenant-secrets`` key: advancing ``min_decryption_version`` is
    irreversible, which is exactly why it may only ever happen here.
    """
    plaintext = b"aizzak-rewrap-proof"
    rewrapped_source = await vault_secrets.encrypt(transit_key, plaintext)
    left_behind = await vault_secrets.encrypt(transit_key, plaintext)

    # Operator action (08-local-runbook.md §4.5): mint a new key version.
    vault_client_raw.secrets.transit.rotate_key(name=transit_key, mount_point="transit")

    # The adapter's own rewrap -- re-encrypts under the version just minted,
    # without ever exposing the plaintext.
    rewrapped = await vault_secrets.rewrap(transit_key, rewrapped_source)
    assert rewrapped != rewrapped_source
    assert rewrapped.startswith("vault:v2:")
    assert rewrapped_source.startswith("vault:v1:")

    # Operator action, IRREVERSIBLE (08-local-runbook.md §4.5's warning):
    # only the rewrapped ciphertext can survive this floor moving up.
    vault_client_raw.secrets.transit.update_key_configuration(
        name=transit_key, min_decryption_version=2, mount_point="transit"
    )

    # Direction 1: the ciphertext nobody rewrapped now fails outright.
    with pytest.raises(AppError) as excinfo:
        await vault_secrets.decrypt(transit_key, left_behind)
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500

    # Direction 2: the rewrapped ciphertext still recovers the SAME bytes.
    recovered = await vault_secrets.decrypt(transit_key, rewrapped)
    assert recovered == plaintext


async def test_rewrapping_a_ciphertext_already_at_the_current_version_keeps_the_key_version(
    vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """The obvious assumption is WRONG, measured live: Vault's AES-GCM mode
    mints a fresh nonce on every ``rewrap`` call regardless of whether the
    key version changed, so the raw BYTES differ even with no rotation in
    between. What ``app.ops.rotate_transit`` actually relies on to tell
    "already rotated" apart from "needs rewrapping" (its own module
    docstring) is the key VERSION embedded in Vault's ``vault:v<n>:...``
    prefix, which this pins as staying constant -- and both ciphertexts must
    still decrypt to the identical plaintext regardless."""
    plaintext = b"unchanged"
    ciphertext = await vault_secrets.encrypt(transit_key, plaintext)

    rewrapped = await vault_secrets.rewrap(transit_key, ciphertext)

    assert rewrapped != ciphertext, "Vault mints a fresh nonce on every rewrap -- bytes DO differ"
    assert rewrapped.split(":")[1] == ciphertext.split(":")[1], "the key version must not change"
    assert await vault_secrets.decrypt(transit_key, rewrapped) == plaintext


# --------------------------------------------------------------------------- #
# (9)-(10) fail-loud guards -- before any network call                       #
# --------------------------------------------------------------------------- #
async def test_encrypt_with_a_path_traversal_key_name_is_rejected_before_any_network_call(
    vault_secrets: VaultSecrets,
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        await vault_secrets.encrypt("../evil", b"x")

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


async def test_rewrap_with_a_path_traversal_key_name_is_rejected_before_any_network_call(
    vault_secrets: VaultSecrets,
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        await vault_secrets.rewrap("../evil", "vault:v1:irrelevant")

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


async def test_get_secret_rejects_the_non_catalog_metadata_path_shape(
    vault_secrets: VaultSecrets,
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        await vault_secrets.get_secret("secret/metadata/x")

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422
