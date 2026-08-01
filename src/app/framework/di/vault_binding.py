"""Vault client/adapter/health construction, shared by TWO composition roots
(the API's ``CompositionRoot.from_env`` and the knowledge worker's
``build_knowledge_worker_from_env``, deferred-adapters-plan.md step 15).

This used to be a private helper (``_build_vault``) inside
``composition_root.py`` — moved out into its own module for exactly one
reason: the knowledge worker needs the SAME three objects (a raw
``hvac.Client`` for shutdown teardown, the ``SecretsProvider`` MinIO's keys
are read through, and the ``VaultHealth`` probe wrapping it), and importing
``composition_root`` from a worker process would drag the ENTIRE
module/agent graph into a process whose own docstring
(``workers/bootstrap.py``) says it must not pay that cost — that module
exists precisely because ``CompositionRoot.from_env()`` "boots the ENTIRE
agent runtime ... none of which the relay [or a Streams worker] needs or
should pay the cost of booting." A shared free function both roots call is
the smallest fix: no new class, no inheritance, one Vault-construction
recipe instead of two that could drift.
"""

from __future__ import annotations

import hvac

from app.framework.ports import SecretsProvider
from app.framework.ports.vault_health import VaultHealth
from app.framework.settings import Settings
from app.infrastructure.config import load_vault_auth
from app.infrastructure.monitoring.vault_health import VaultProbe
from app.infrastructure.secrets.vault_secrets import (
    VaultSecrets,
    create_approle_relogin,
    create_vault_client,
)


def build_vault(settings: Settings) -> tuple[hvac.Client, SecretsProvider, VaultHealth]:
    """The three Vault objects, built as one unit -- a helper so callers
    stay under their statement ceiling, the ``_build_embedding``/
    ``_build_metrics_source`` precedent (``composition_root.py``).

    ONE client, shared three ways on purpose:

    * ``vault_client`` is exposed raw for the shutdown-``close()`` precedent
      every other driver on the calling root follows (``disposables()``).
    * ``VaultSecrets`` gets a ``relogin`` closure (7.3) because AppRole tokens
      expire (08 §3.1: ``token_ttl=1h``) and hvac never renews -- without it
      the process loses Vault an hour after boot while still answering
      ``/health/ready``, which deliberately probes nothing (§3.75). ``None``
      under token auth, which keeps the local-dev path byte-for-byte as it was.
    * ``VaultProbe`` (ن-10, ``docs/log/3.94.md``) wraps that SAME
      ``SecretsProvider`` rather than building a second client, so the
      ``/metrics`` gauge answers for the exact credential -- client, token and
      relogin closure -- the request path uses. A probe with its own client
      would report on a session nobody else holds, reintroducing the silent
      divergence ن-10 is about one layer up. It costs nothing at boot:
      ``VaultProbe.__init__`` makes no network call.

    ``load_vault_auth()`` is called HERE rather than passed in, so the one
    sensitive value (``secret_id``, 05 §3.3) stays inside the smallest scope
    that needs it and never becomes a parameter something else could hold.
    """
    vault_auth = load_vault_auth()
    vault_client = create_vault_client(
        settings.vault, token=vault_auth.token, secret_id=vault_auth.secret_id
    )
    secrets = VaultSecrets(
        vault_client,
        relogin=create_approle_relogin(settings.vault, vault_client, secret_id=vault_auth.secret_id)
        if not vault_auth.token
        else None,
    )
    return vault_client, secrets, VaultProbe(secrets)
