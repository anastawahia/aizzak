"""VaultHealth driven port — "can THIS process still authenticate to Vault?",
the detection half of ن-10 (``docs/p1-hardening-plan.md`` §5-ب; the live
outage is written up in ``docs/log/3.94.md``).

**The gap this closes.** The application's Vault credential dies silently.
``create_vault_client`` logs in once at composition and nothing renews the
token; ``create_approle_relogin`` re-authenticates with the SAME
``VAULT_SECRET_ID``, so once that ``secret_id`` itself expires there is no
recovery from inside the process at all. And nothing notices: ``/health/ready``
deliberately probes no dependency (§3.75 — "الجاهزيّة انتهى الإقلاع لا
التبعيّات حيّة"), so ``docker compose ps`` keeps saying ``healthy`` while every
``credentials``/``integrations`` operation 500s. On 2026-07-30 that stopped
being theoretical: a routine restart woke the app onto an expired
``secret_id`` and it crash-looped, and the crash loop then tripped Vault's own
user lockout so that a freshly minted, perfectly valid ``secret_id`` was ALSO
refused. Detection is what would have turned that outage into a page hours
before the restart.

**Why a port and not a method on ``MetricsSource``.** That port's contract is
bound to 07-nfr-slo §7's two signals, both computed from Postgres/Redis state
this platform owns; this is a dependency-LIVENESS probe against a third-party
service, with a different latency profile (one network round trip to Vault)
and a different trust profile (a failure here means "the process cannot do its
job", not "a queue is deep"). Two ports, one ``/metrics`` endpoint — see
``infrastructure/monitoring/vault_health.py`` for the rest of that reasoning.

**The contract, and it is the whole point of this file: ``authenticated`` is
TOTAL — it never raises.** A ``/metrics`` scrape renders the Outbox age and
the DLQ depths from the SAME request; an implementation that let a Vault
timeout or a connection error escape would take those two numbers down with
it, i.e. Vault being unreachable would blind the operator to two unrelated
signals at exactly the moment they matter most. A failed probe is therefore
the VALUE ``False`` (rendered ``0``), never an exception. An implementation
that cannot honour that is not an implementation of this port.

Implemented by ``infrastructure.monitoring.vault_health.VaultProbe`` (the
Composition Root's only caller); a fake substitutes it in
``tests/unit/test_api_metrics_router.py`` so the rendering logic can be
exercised without a live Vault.
"""

from __future__ import annotations

from typing import Protocol


class VaultHealth(Protocol):
    async def authenticated(self) -> bool:
        """``True`` when this process's Vault client can currently make an
        authorized call, ``False`` for every other outcome — an expired or
        revoked ``secret_id`` whose re-login fails, a sealed Vault, connection
        refused, a timeout, a policy that no longer grants what the probe
        reads.

        **Never raises.** See the module docstring: the caller is a metrics
        scrape that must still render its other gauges when Vault is down.
        """
        ...
