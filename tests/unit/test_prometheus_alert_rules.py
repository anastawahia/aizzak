"""``deploy/prometheus/alerts.yml`` must actually reference the metric names
``GET /metrics`` really emits, and must carry a real, non-empty threshold +
justification for each of the two P1-3 signals (``docs/p1-hardening-plan.md``
§3 step 10) and for the Vault-authentication gauge ن-10 added — not merely
exist as a file nobody checks against the endpoint it is meant to alert on.

Since Wave 0 step 0.3 (``docs/capacity-plan.md``) the file also carries two
rules of a second kind — ``up`` and ``pgbouncer_up``, about whether the
measurement apparatus itself is intact rather than about a platform number —
and a Prometheus service that actually evaluates all five. The wiring between
this file, ``prometheus.yml``, the Grafana dashboards and
``docker-compose.yml`` is guarded next door, in
``test_observability_stack_wiring.py``.

**Why this guard, not just "the file parses as YAML".** A rules file that
parses cleanly but names a metric ``/metrics`` never emits (a typo, or a
rename on one side that forgot the other) is a silent alert that can never
fire — worse than no alert at all, since a dashboard built against it would
look monitored while actually watching nothing. This module imports
``OUTBOX_AGE_METRIC``/``DLQ_DEPTH_METRIC`` from ``api.metrics`` itself
(the SAME constants the endpoint renders under) rather than repeating the
literal strings, so a rename on either side fails this test immediately
instead of drifting unnoticed — the ``test_role_provisioning_wiring.py``/
``test_deploy_worker_default.py`` precedent applied to a Prometheus rule
file instead of a shell script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.api.metrics import DLQ_DEPTH_METRIC, OUTBOX_AGE_METRIC, VAULT_AUTH_METRIC

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_YML = _REPO_ROOT / "deploy" / "prometheus" / "alerts.yml"


def _load_rules() -> list[dict[str, Any]]:
    doc = yaml.safe_load(_ALERTS_YML.read_text(encoding="utf-8"))
    groups = doc["groups"]
    assert groups, f"{_ALERTS_YML}: `groups:` is empty -- no rule would ever be loaded"
    rules: list[dict[str, Any]] = []
    for group in groups:
        rules.extend(group["rules"])
    return rules


def _rule_for(rules: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    matches = [rule for rule in rules if metric in rule["expr"]]
    assert len(matches) == 1, (
        f"{_ALERTS_YML}: expected exactly one alert rule referencing {metric!r}, "
        f"found {len(matches)}"
    )
    return matches[0]


# Every alert the file is allowed to declare, by name. An exact SET rather
# than the bare count this guard used to carry: a count says "three" and lets
# a rule be quietly swapped for a different one, while this says which three
# -- and a rename now fails here instead of passing silently.
EXPECTED_ALERTS = frozenset(
    {
        # P1-3 step 10 -- the two 07-nfr-slo §7 platform-state signals.
        "AizzakOutboxCycleTimeHigh",
        "AizzakDlqNotEmpty",
        # ن-10, after the outage in docs/log/3.94.md.
        "AizzakVaultAuthFailing",
        # Wave 0 step 0.3 (docs/capacity-plan.md) -- the first two rules about
        # the measurement apparatus rather than about the platform.
        "AizzakScrapeTargetDown",
        "AizzakPgbouncerDown",
    }
)


def test_the_file_declares_exactly_the_expected_alerts() -> None:
    """The scope guard -- the 3.69 "an empty/oversized set passes forever"
    lesson applied to a rules file instead of a role tuple.

    **The set has grown twice, both times on purpose, and this docstring is
    the record.** The original limit was two, from step 10's own brief
    ("مقياسان فقط ... مقياسٌ لكلّ شيء هو ما أجّل هذا البند أصلاً"). ن-10 added
    a third of a different kind -- ``aizzak_vault_authenticated``, a
    dependency-liveness probe -- after the failure it detects actually
    happened (``docs/log/3.94.md``). Wave 0 step 0.3 adds the fourth and
    fifth, and they are different again: every rule before them thresholds a
    number ``GET /metrics`` emits, while these two threshold whether the
    measurement apparatus itself is intact. They could not have existed
    earlier, because until 0.3 there was no Prometheus and therefore no
    ``up`` series to alert on.

    That is the bar this guard enforces -- growth by a justified, logged
    decision, never by drift -- so a sixth entry needs its own written
    reason (a ``docs/log/`` write-up, or a named step in
    ``docs/capacity-plan.md``) first, not just a name added here.

    **And note what growth is still refused.** Step 0.2 added RED and
    saturation metrics and step 0.3 plots all of them, but no latency,
    error-rate or ``cl_waiting`` rule appears in this set. Every such rule
    needs a threshold, and the only honest source for one is step 0.5's
    measured baseline, which does not exist yet.
    """
    rules = _load_rules()
    names = {rule["alert"] for rule in rules}
    assert len(names) == len(rules), (
        f"{_ALERTS_YML}: two rules share an alert name -- Prometheus allows it, but the "
        "two are then indistinguishable in ALERTS and in any receiver downstream"
    )
    assert names == EXPECTED_ALERTS, (
        f"{_ALERTS_YML}: the declared alerts drifted from the expected set.\n"
        f"  unexpected: {sorted(names - EXPECTED_ALERTS)}\n"
        f"  missing:    {sorted(EXPECTED_ALERTS - names)}\n"
        "This file is scoped to the Outbox age + DLQ depth signals (P1-3, step 10), the "
        "Vault-authentication gauge (ن-10) and the two scrape-health rules (capacity-plan "
        "Wave 0 step 0.3). A new rule needs its own logged justification first, not just a "
        "name added to EXPECTED_ALERTS."
    )


def test_scrape_target_down_rule_excludes_the_optional_tier() -> None:
    """Without the exclusion this rule fires forever in the ordinary case.

    cAdvisor sits behind the ``container-metrics`` Compose profile because it
    needs the Docker socket, so a default ``docker compose up`` leaves that
    target down by design (``deploy/prometheus/prometheus.yml`` labels it
    ``tier: optional`` for this rule to read). A rule that is permanently
    firing is worse than a missing one: it trains its reader to skip the
    whole file, which silently disarms the four rules that DO mean something.
    """
    rule = _rule_for(_load_rules(), "up{")
    assert 'tier!="optional"' in rule["expr"], (
        f'{_ALERTS_YML}: the target-down rule must exclude `tier="optional"` -- the '
        "cAdvisor target is absent from a default `up` on purpose, and an always-firing "
        "alert disarms the rest of this file by habituation"
    )
    assert rule["for"] == "30s", (
        f"{_ALERTS_YML}: the target-down rule's `for:` drifted from the documented "
        "30-second window (two consecutive failed evaluations at a 15s "
        "evaluation_interval)"
    )


def test_pgbouncer_rule_thresholds_the_exporter_verdict_not_the_scrape() -> None:
    """``pgbouncer_up`` and ``up`` are different failures, and confusing them
    makes this alert unable to fire at all.

    The exporter is its own container. Stopping ``pgbouncer`` leaves it
    answering scrapes perfectly well -- measured: ``up{job="pgbouncer"}``
    stayed 1 throughout while ``pgbouncer_up`` went to 0 -- so a rule written
    against ``up`` would sleep through exactly the outage step 0.3's
    acceptance criterion names.

    The ``for:`` is also load-bearing rather than stylistic. That criterion
    is "إسقاط `pgbouncer` يُشعل تنبيهاً خلال دقيقة", and the budget is spent
    as ≤15s to the first failing scrape + 15s of hysteresis + ≤15s to the
    confirming evaluation. Measured end to end at 34s.
    """
    rule = _rule_for(_load_rules(), "pgbouncer_up")
    assert rule["expr"].strip() == "pgbouncer_up == 0", (
        f"{_ALERTS_YML}: the pooler rule must threshold the exporter's own 1/0 login "
        "verdict; `up` cannot see this failure, since the exporter container survives "
        "the pooler it reports on"
    )
    assert rule["for"] == "15s", (
        f"{_ALERTS_YML}: the pooler rule's `for:` drifted from 15s -- step 0.3's "
        "acceptance criterion budgets one minute end to end, and the other two terms "
        "(scrape + confirming evaluation) already cost up to 30s of it"
    )
    assert rule["labels"]["severity"] == "critical", (
        f"{_ALERTS_YML}: the pooler rule must be `critical` -- every DATABASE_URL in "
        "docker-compose.yml routes through pgbouncer:6432 and Postgres is reachable no "
        "other way, so this is total loss of the data path, not a backlog"
    )


def test_outbox_cycle_time_rule_references_the_real_metric_and_slo_threshold() -> None:
    rule = _rule_for(_load_rules(), OUTBOX_AGE_METRIC)
    # 07-nfr-slo.md §2's own p99 budget for "زمن دورة الـ Outbox (نشر بعد
    # الالتزام)" -- the exact number this rule's own `reason` cites.
    assert "> 3" in rule["expr"], (
        f"{_ALERTS_YML}: {OUTBOX_AGE_METRIC} rule should threshold at 3s "
        "(07-nfr-slo.md §2's p99 budget for this exact quantity)"
    )
    assert rule["for"] == "2m", (
        f"{_ALERTS_YML}: {OUTBOX_AGE_METRIC} rule's `for:` drifted from the documented "
        "2-minute hysteresis window"
    )
    annotations = rule["annotations"]
    assert annotations.get("reason"), f"{_ALERTS_YML}: {OUTBOX_AGE_METRIC} rule has no `reason`"
    assert "07-nfr-slo" in annotations["reason"], (
        f"{_ALERTS_YML}: {OUTBOX_AGE_METRIC} rule's `reason` should cite the SLO document "
        "the threshold is actually grounded in"
    )


def test_dlq_depth_rule_references_the_real_metric_and_the_step_7_tool() -> None:
    rule = _rule_for(_load_rules(), DLQ_DEPTH_METRIC)
    assert "> 0" in rule["expr"], (
        f"{_ALERTS_YML}: {DLQ_DEPTH_METRIC} rule should threshold at 0 -- there is no "
        "'normal' non-zero DLQ depth (module comment's own reasoning)"
    )
    assert rule["for"] == "5m", (
        f"{_ALERTS_YML}: {DLQ_DEPTH_METRIC} rule's `for:` drifted from the documented "
        "5-minute hysteresis window"
    )
    annotations = rule["annotations"]
    assert annotations.get("reason"), f"{_ALERTS_YML}: {DLQ_DEPTH_METRIC} rule has no `reason`"
    response = annotations.get("response", "")
    assert "python -m app.ops.dlq" in response, (
        f"{_ALERTS_YML}: {DLQ_DEPTH_METRIC} rule's `response` must name `python -m "
        "app.ops.dlq` (P1-4, step 7) -- this is the operator's actual response tool, "
        "the design brief's own 'الأداة قبل المقياس' link this test enforces"
    )


def test_vault_auth_rule_references_the_real_metric_and_the_lockout_ordering() -> None:
    rule = _rule_for(_load_rules(), VAULT_AUTH_METRIC)
    assert "< 1" in rule["expr"], (
        f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule should threshold at `< 1` -- the gauge "
        "is 1/0, so there is no partial state to calibrate a larger number against"
    )
    assert rule["for"] == "5m", (
        f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule's `for:` drifted from the documented "
        "5-minute noise-suppression window"
    )
    assert rule["labels"]["severity"] == "critical", (
        f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule must be `critical`, not `warning` like "
        "the two backlog rules -- a dead Vault credential means the credentials/"
        "integrations runtime path is ALREADY failing and the next restart will not "
        "boot at all (docs/log/3.94.md)"
    )
    annotations = rule["annotations"]
    assert annotations.get("reason"), f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule has no `reason`"
    response = annotations.get("response", "")
    # The ordering is the whole operational value of this rule: every failed
    # probe is a failed AppRole login, so a scraper left running keeps Vault's
    # user lockout armed and makes a freshly minted, VALID secret_id look
    # broken (docs/log/3.94.md §2). An operator reading only the alert must
    # still be told to stop the scraper, not merely the app.
    assert "scraper" in response.lower(), (
        f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule's `response` must tell the operator to "
        "stop the SCRAPER as well as the app -- a scraper still polling re-arms the "
        "Vault user lockout by itself and makes the correct repair look broken"
    )
    assert "lock" in response.lower(), (
        f"{_ALERTS_YML}: {VAULT_AUTH_METRIC} rule's `response` must name the Vault user "
        "lockout and its unlock step -- without it the operator mints a new secret_id, "
        "sees it refused with an empty 403, and starts suspecting policies instead"
    )


def test_every_rule_carries_the_minimum_operator_fields() -> None:
    """A rule missing `summary`/`reason`/`response` is exactly the "إنذارٌ
    يُرى لا يُستجاب له" (an alert seen, not acted on) the design brief warns
    against -- every rule must carry all three, not just the one under test
    above."""
    for rule in _load_rules():
        annotations = rule["annotations"]
        for field in ("summary", "description", "reason", "response"):
            assert annotations.get(field), (
                f"{_ALERTS_YML}: rule {rule['alert']!r} is missing a non-empty "
                f"`annotations.{field}`"
            )
