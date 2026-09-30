"""Capacity 5.7 against a real Vault: the key metadata ``rotate_transit``
reads to know whether the key is actually rotating, and the verdict it draws.

Every test runs on the ``transit_key`` fixture -- a fresh, uniquely-named key
created and deleted around the test -- never on ``tenant-secrets``: this file
rotates keys and changes their configuration.
"""

from __future__ import annotations

import time

import hvac
import pytest

from app.infrastructure.secrets.vault_secrets import VaultSecrets
from app.ops.rotate_transit import ROTATION_GRACE_S, rotation_cycle

pytestmark = [pytest.mark.live_vault]

_HOUR = 3_600.0


async def test_a_key_nobody_declared_a_cycle_for_is_read_as_no_cycle(
    vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """The live ``tenant-secrets`` on 2026-09-30, in miniature: a key Vault
    will never rotate. A nightly rewrap next to it would succeed every night."""
    info = await vault_secrets.transit_key_info(transit_key)

    assert info.latest_version == 1
    assert info.min_decryption_version == 1
    assert info.auto_rotate_period_s == 0
    assert set(info.version_created_at) == {1}
    assert abs(info.version_created_at[1] - time.time()) < 300
    assert rotation_cycle(info, now=time.time()).verdict == "no_cycle"


async def test_a_declared_cycle_is_read_back_and_a_fresh_key_is_on_it(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """What ``deploy/vault/bootstrap.sh`` now writes, read back through the
    adapter: Vault reports the period in seconds."""
    vault_client_raw.secrets.transit.update_key_configuration(
        name=transit_key, auto_rotate_period="720h", mount_point="transit"
    )

    info = await vault_secrets.transit_key_info(transit_key)

    assert info.auto_rotate_period_s == 720 * _HOUR
    assert rotation_cycle(info, now=time.time()).ok


async def test_a_rotation_shows_up_as_a_newer_version_and_old_ones_still_decrypt(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """What Vault's auto-rotation does on the declared cycle is exactly this
    call: a version is ADDED, nothing is retired -- ciphertext from version 1
    still decrypts, and the rewrap sweep moves it forward."""
    before = await vault_secrets.encrypt(transit_key, b"tenant secret")
    vault_client_raw.secrets.transit.rotate_key(name=transit_key, mount_point="transit")

    info = await vault_secrets.transit_key_info(transit_key)

    assert info.latest_version == 2
    assert set(info.version_created_at) == {1, 2}
    assert info.min_decryption_version == 1
    assert await vault_secrets.decrypt(transit_key, before) == b"tenant secret"
    assert (await vault_secrets.rewrap(transit_key, before)).startswith("vault:v2:")


async def test_a_key_older_than_its_cycle_plus_grace_is_overdue(
    vault_client_raw: hvac.Client, vault_secrets: VaultSecrets, transit_key: str
) -> None:
    """Judged against Vault's own creation stamp: an hour-long cycle, read
    two hours and a day later, is a cycle that stopped."""
    vault_client_raw.secrets.transit.update_key_configuration(
        name=transit_key, auto_rotate_period="1h", mount_point="transit"
    )
    info = await vault_secrets.transit_key_info(transit_key)
    created = info.version_created_at[info.latest_version]

    assert rotation_cycle(info, now=created + _HOUR + ROTATION_GRACE_S - 1).ok
    late = rotation_cycle(info, now=created + _HOUR + ROTATION_GRACE_S + 1)
    assert late.verdict == "overdue"
