"""Observability adapters — everything ``GET /metrics`` renders from, wired
exclusively by the Composition Root (import-linter contract 6).

Two ports live here, not one, and deliberately so:

* ``MetricsSource`` (P1-3, ``docs/p1-hardening-plan.md`` §3 step 10) —
  ``metrics_source.SqlRedisMetricsSource``, the two 07-nfr-slo §7 health
  signals computed from Postgres/Redis.
* ``VaultHealth`` (ن-10, §5-ب; the outage in ``docs/log/3.94.md``) —
  ``vault_health.VaultProbe``, a dependency-liveness probe over the
  ``SecretsProvider`` the process already holds. See that module's docstring
  for why it is a second port rather than a third method on the first one.
"""

from __future__ import annotations
