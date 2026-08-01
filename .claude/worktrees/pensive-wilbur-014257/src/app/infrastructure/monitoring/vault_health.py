"""``VaultHealth`` over the ``SecretsProvider`` the process already holds —
the detection half of ن-10 (``docs/p1-hardening-plan.md`` §5-ب; the outage
that motivated it is ``docs/log/3.94.md``).

**The probe, and why it is a KV read of a path nobody ever writes.** The
obvious candidate, ``hvac``'s own ``client.is_authenticated()``, is WRONG here
and provably so: it issues ``auth/token/lookup-self``, and the AppRole is
created ``token_no_default_policy=true`` (``deploy/vault/bootstrap.sh``), so
Vault's built-in ``default`` policy — the thing that normally grants
``lookup-self`` — is never attached. It therefore returns ``False`` for a
PERFECTLY WORKING token. That is measured, not reasoned about
(``create_vault_client``'s own docstring), and ``deploy/smoke/
approle_smoke.py`` asserts it so the "obvious sanity check" fails loudly in a
smoke run instead of silently at boot. A health gauge built on it would read
``0`` forever.

So the probe is ``get_secret`` against ``_PROBE_PATH``, and the reasoning runs
through the adapter's own error policy (``infrastructure/secrets/
vault_secrets.py``):

* ``deploy/vault/app-policy.hcl`` already grants ``read`` on
  ``secret/data/*``. A path with nothing ever written answers with Vault's own
  404, which ``_translate_kv`` turns into ``NotFoundError``. Reaching a 404 at
  all means the request was authenticated AND authorized — **so
  ``NotFoundError`` is the HEALTHY outcome**, and the expected one.
* A successful read (if somebody ever writes to that path) is healthy too —
  the probe deliberately does not care what comes back, only that the call
  completed.
* Everything else folds into ``_translate``'s ``AppError("secrets operation
  failed", code="common.internal")``: an expired/revoked ``secret_id`` whose
  relogin fails, a sealed Vault, connection refused, a 5s timeout. ⇒ unhealthy.

**No Vault-side change is needed at all — this is load-bearing, not a
footnote.** No new policy grant, no role change, no restart, nothing minted.
The probe rides entirely on the ``secret/data/*`` read the application has
held since 7.3, so this module can ship to a running deployment without a
Vault-operator window.

**Why the ``secret_id``'s remaining TTL was rejected as the signal.** It is
the more informative number — it would warn BEFORE the credential dies rather
than after — and it was still refused, twice over. Reading it needs
``auth/approle/role/app/secret-id/lookup``, i.e. an ``auth/*`` grant, and
``app-policy.hcl``'s own "Deliberately NOT granted" section forbids exactly
that ("`sys/*`, `auth/*` — the application never administers Vault"); widening
that boundary to improve a gauge would trade a real security property for an
observability convenience. And the lookup takes the ``secret_id`` ITSELF as
its request body, so every scrape would put the one genuinely secret env value
(05 §3.3 keeps it out of every object that might be logged, which is why
``VaultSecrets`` holds a relogin CLOSURE and not the value) back on the wire.
Advance warning is worth having; it belongs to a Vault-operator-side check, or
to alternative (أ) — a real renewal sidecar — not to the application's own
metrics endpoint.

**Not folded into ``SqlRedisMetricsSource``/``MetricsSource``, deliberately.**
(a) That port's docstring binds it to the two 07-nfr-slo §7 signals computed
from Postgres/Redis — platform state, cheap and local; this is a liveness
probe against a third-party service with a different latency and trust
profile. (b) Adding a Vault call to ``SqlRedisMetricsSource`` would make the
class's NAME false and its whole module docstring ("where each number actually
lives") wrong, and would stale the historical logs that describe it by that
name (``docs/log/3.89.md``, ``docs/log/3.90.md``). Two ports, one endpoint.

**⚠️ Accepted, not engineered around: this probe can keep a Vault user lockout
armed.** ``VaultSecrets._call`` retries once through ``create_approle_relogin``
on ``Forbidden``/``Unauthorized``. In steady state that is a FEATURE here —
``token_ttl=1h`` and nothing renews, so the probe makes the process re-login
promptly instead of the next real tenant request paying for it. But when the
``secret_id`` itself is dead, EVERY scrape becomes a failed AppRole login, and
``docs/log/3.94.md`` §2 records what that costs: Vault's user-lockout feature
trips (``core: login attempts exceeded, user is locked out``) and from then on
a freshly minted, perfectly valid ``secret_id`` is refused with a bare
``403 permission denied`` — so the correct repair looks like it does not work.
A scraper polling every 15s would keep the lockout permanently re-armed and
make recovery look impossible.

Nothing is cached to soften it, because caching is exactly what ``api/
metrics.py``'s module docstring forbids: ``WEB_CONCURRENCY=2`` means sibling
processes with independent heaps, so anything remembered between requests
answers with whichever sibling caught the scrape (the P0-2 defect,
``docs/log/3.81.md``). It is accepted instead, on a measured fact: **nothing
scrapes this endpoint today** — there is no Prometheus service in
``docker-compose.yml`` or the RunPod image, deliberately. Whoever adds the
scraper inherits the ordering rule, which is written in the alert rule's own
``annotations.response`` and in ``08-local-runbook.md`` §3.1: **stop the
scraper as well as ``app`` before unlocking and minting.**
"""

from __future__ import annotations

from app.framework.errors import AppError, NotFoundError
from app.framework.ports.secrets_provider import SecretsProvider

# A KV v2 path under the mount the app already reads, chosen so that NOTHING
# ever writes it: the healthy answer is Vault's own 404. It satisfies
# `_parse_kv_path`'s `<mount>/data/<rel>` shape (and carries no `..`
# segment), so it reaches the network rather than dying in validation -- a
# `ValidationError` here would be an `AppError` and would read as "Vault is
# down", which is why the shape is stated rather than assumed.
_PROBE_PATH = "secret/data/_probe/vault-health"


class VaultProbe:
    """Structural ``VaultHealth`` (Protocol match, no inheritance -- the
    ``SqlRedisMetricsSource``/``RedisCache`` precedent).

    Takes the ``SecretsProvider`` PORT, never an ``hvac.Client``: the whole
    point is to exercise the exact object the application's own request path
    uses, over the same client, the same token and the same relogin closure
    the Composition Root already built. A probe with its own client would
    answer for a credential nobody else holds -- which is the failure mode
    ن-10 is about, reintroduced one layer up.
    """

    def __init__(self, secrets: SecretsProvider) -> None:
        self._secrets = secrets

    async def authenticated(self) -> bool:
        """``True`` iff this process can currently make an authorized Vault
        call. Total -- see the port's docstring for why a scrape must not lose
        the Outbox/DLQ gauges to a Vault outage."""
        try:
            await self._secrets.get_secret(_PROBE_PATH)
        except NotFoundError:
            # ⚠️ MUST stay ABOVE the `except AppError` below. `NotFoundError`
            # is a SUBCLASS of `AppError` (`framework/errors.py`), so swapping
            # these two clauses would route the HEALTHY case into the
            # unhealthy one and invert the metric silently -- a gauge pinned
            # at 0 on a perfectly working Vault. `tests/unit/
            # test_vault_health_probe.py` asserts this ordering directly.
            #
            # Vault answered 404 for a path with nothing written: the request
            # was authenticated and the policy allowed the read. That IS the
            # expected healthy answer, and the reason this probe needs no new
            # grant (module docstring).
            return True
        except AppError:
            # The contracted failure: `_translate` folds an expired/revoked
            # credential, a sealed Vault, connection refused and a timeout
            # into one `common.internal`. None of them are distinguishable
            # from here, and none of them are healthy.
            return False
        except Exception:
            # A deliberately BLIND catch -- totality is the port's contract.
            # The backstop that makes "never raises" true rather than
            # aspirational: any adapter that ever leaks a driver exception, or
            # any future implementation of the port, still yields a number.
            # `BaseException` is deliberately NOT caught -- `CancelledError`
            # must keep propagating so a cancelled scrape stays cancelled.
            return False
        return True
