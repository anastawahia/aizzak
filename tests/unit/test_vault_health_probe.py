"""``VaultProbe`` — the ن-10 liveness probe's decision logic, over a fake
``SecretsProvider`` rather than a live Vault (``docs/p1-hardening-plan.md``
§5-ب; the outage that motivated it is ``docs/log/3.94.md``).

**Two properties are worth a test here, and they are not the obvious one.**

1. **``NotFoundError`` means HEALTHY, and it must be caught BEFORE
   ``AppError``.** ``NotFoundError`` is a *subclass* of ``AppError``
   (``framework/errors.py``), so writing the two ``except`` clauses in the
   wrong order silently routes the healthy case into the unhealthy one — the
   gauge would then read ``0`` against a perfectly working Vault, arming a
   ``critical`` alert forever, with no exception and no other failing test
   anywhere. Python resolves ``except`` clauses top-down, so this is a real,
   easily-made edit, which is why one test asserts the ordering directly
   rather than trusting the two outcome tests to imply it.
2. **``authenticated`` is TOTAL.** The port's contract is that it never
   raises, because the caller is a ``/metrics`` scrape that must still render
   the Outbox/DLQ gauges when Vault is down. An adapter that leaked an
   exception would turn "Vault is unreachable" into "the operator loses three
   signals at once". Proven with an arbitrary non-``AppError`` exception, the
   case a future refactor is most likely to let through.
"""

from __future__ import annotations

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.infrastructure.monitoring.vault_health import _PROBE_PATH, VaultProbe
from app.infrastructure.secrets.vault_secrets import _parse_kv_path


class _FakeSecrets:
    """A ``SecretsProvider`` whose ``get_secret`` outcome the test picks.

    Only ``get_secret`` is exercised: the probe deliberately never touches
    Transit (``encrypt``/``decrypt``), because a KV read of an unwritten path
    needs no grant beyond the ``secret/data/*`` read the app already holds.
    """

    def __init__(self, *, raises: BaseException | None = None, returns: object = None) -> None:
        self.raises = raises
        self.returns = returns
        self.paths: list[str] = []

    async def get_secret(self, path: str) -> object:
        self.paths.append(path)
        if self.raises is not None:
            raise self.raises
        return self.returns


async def test_a_not_found_answer_is_healthy() -> None:
    """Vault's own 404 for a path with nothing written proves the request was
    authenticated AND authorized — the probe's whole design."""
    secrets = _FakeSecrets(raises=NotFoundError("secret does not exist in Vault"))

    assert await VaultProbe(secrets).authenticated() is True
    assert secrets.paths == [_PROBE_PATH]


async def test_a_successful_read_is_healthy_too() -> None:
    """If somebody ever writes to the probe path, the probe must keep working:
    it cares that the call COMPLETED, never what came back."""
    secrets = _FakeSecrets(returns={"anything": "at all"})

    assert await VaultProbe(secrets).authenticated() is True


async def test_a_translated_vault_failure_is_unhealthy() -> None:
    """`_translate` folds an expired secret_id whose relogin failed, a sealed
    Vault, connection refused and a timeout into ONE `common.internal`
    `AppError` — none of them distinguishable here, none of them healthy."""
    secrets = _FakeSecrets(raises=AppError("secrets operation failed", code="common.internal"))

    assert await VaultProbe(secrets).authenticated() is False


async def test_an_arbitrary_exception_is_unhealthy_rather_than_raised() -> None:
    """Totality (the port's contract): a `/metrics` scrape must not lose its
    Outbox/DLQ numbers because something unforeseen escaped the adapter."""
    secrets = _FakeSecrets(raises=RuntimeError("hvac leaked something"))

    assert await VaultProbe(secrets).authenticated() is False


async def test_not_found_is_caught_before_the_app_error_clause() -> None:
    """The subclass trap, asserted directly.

    ``NotFoundError`` IS an ``AppError``, so `except AppError` placed first
    would swallow it and invert the metric. This test pins the property that
    makes the ordering observable: the SAME exception object is an instance of
    both classes, yet the probe answers `True` for it and `False` for a plain
    `AppError` — which can only hold if the narrow clause runs first.
    """
    not_found = NotFoundError("secret does not exist in Vault")
    assert isinstance(not_found, AppError), (
        "the whole point of this test evaporates if NotFoundError stops being an "
        "AppError subclass -- re-read VaultProbe.authenticated's except ordering"
    )

    assert await VaultProbe(_FakeSecrets(raises=not_found)).authenticated() is True
    assert await VaultProbe(_FakeSecrets(raises=AppError("boom"))).authenticated() is False


async def test_a_validation_error_is_unhealthy_not_a_crash() -> None:
    """`_parse_kv_path` raises `ValidationError` (an `AppError`) for a
    malformed path. `_PROBE_PATH` is well-formed today, so this is defence
    against a future edit to that constant: it must degrade to `0`, never
    take the scrape down."""
    secrets = _FakeSecrets(raises=ValidationError("secret path must contain '/data/'"))

    assert await VaultProbe(secrets).authenticated() is False


def test_the_probe_path_matches_the_kv_v2_shape_the_adapter_requires() -> None:
    """`_parse_kv_path` accepts only `<mount>/data/<rel>` and rejects any `..`
    segment. A constant that failed it would raise `ValidationError` before
    any network call, so the gauge would read `0` on a perfectly healthy
    Vault — the same silent inversion the except-ordering test guards.

    Calls the adapter's OWN parser rather than re-deriving its rules here: a
    copy of those rules would keep passing after `_parse_kv_path` tightened,
    which is precisely the drift this test exists to catch (the
    `test_prometheus_alert_rules.py` precedent of importing the real constant
    instead of repeating the literal).
    """
    mount, rel = _parse_kv_path(_PROBE_PATH)

    # And the mount is the one `app-policy.hcl` grants `read` on -- a probe
    # under any other mount would 403 rather than 404, inverting the gauge
    # (`secret/data/*` is the whole KV surface the AppRole holds).
    assert mount == "secret"
    assert rel
