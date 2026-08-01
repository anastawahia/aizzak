"""Vault's OWN authentication material -- read directly from the process
environment, deliberately OUTSIDE ``Settings`` (05-rbac-config-secrets §3.3,
DD-11).

The bootstrap paradox: every other secret in this platform lives in Vault
and is resolved through ``SecretsProvider`` at runtime -- but the credential
that AUTHENTICATES to Vault in the first place cannot itself live in Vault
(nothing can read it there before a Vault client even exists). It has to
come from somewhere Vault does not gate: the process environment, exactly
like every other piece of bootstrap configuration.

DD-11 is still honoured: this is the SAME package (``infrastructure/config``)
that is the sole reader of ``os.environ``/``.env`` -- no other module reads
the environment directly. This file just adds a second, narrowly-scoped
reader function alongside ``env_settings.load_settings``, not a bypass of it.

``VaultAuth`` is kept OUT of the immutable ``Settings`` contract on purpose:
``Settings`` is designed to be freely logged/passed around/dumped (05 §2 --
non-secret by construction), and folding a live token or AppRole
``secret_id`` into it would turn one careless ``model_dump()``/log line into
a credential leak. Its ``__repr__`` is overridden to redact both fields
(``***`` when set, ``None`` when absent), so that even an accidental
``repr()``/``print()`` -- in a traceback, a debugger, a log line -- never
surfaces the actual value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _redact(value: str | None) -> str:
    return "None" if value is None else "***"


@dataclass(frozen=True, slots=True)
class VaultAuth:
    """Vault's own bootstrap credentials (05 §3.3): a root/dev ``token``, or
    an AppRole ``secret_id`` paired with ``Settings.vault.role_id`` (D-22).
    Both are optional -- which one (if either) is set depends on the
    deployment's authentication mode; ``create_vault_client`` decides."""

    token: str | None
    secret_id: str | None

    def __repr__(self) -> str:
        return f"VaultAuth(token={_redact(self.token)}, secret_id={_redact(self.secret_id)})"


def load_vault_auth() -> VaultAuth:
    """Read Vault's bootstrap credentials straight from the environment.

    ``VAULT_TOKEN`` / ``VAULT_SECRET_ID`` are the only two env keys this
    function touches -- the sensitive values in 05 §2/§3.3 (``VAULT_ROLE_ID``
    is non-secret and belongs to ``Settings`` proper, read by
    ``env_settings.load_settings`` instead).
    """
    return VaultAuth(
        token=os.environ.get("VAULT_TOKEN"),
        secret_id=os.environ.get("VAULT_SECRET_ID"),
    )
