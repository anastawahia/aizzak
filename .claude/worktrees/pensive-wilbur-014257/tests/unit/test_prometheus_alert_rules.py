"""``deploy/prometheus/alerts.yml`` must actually reference the metric names
``GET /metrics`` really emits, and must carry a real, non-empty threshold +
justification for each of the two P1-3 signals (``docs/p1-hardening-plan.md``
§3 step 10) and for the Vault-authentication gauge ن-10 added — not merely
exist as a file nobody checks against the endpoint it is meant to alert on.

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


def test_the_file_declares_exactly_three_rules() -> None:
    """The scope guard -- the 3.69 "an empty/oversized set passes forever"
    lesson applied to a rules file instead of a role tuple.

    **The number moved from 2 to 3 on purpose, once, and this docstring is the
    record of why.** The original limit came from step 10's own brief ("مقياسان
    فقط ... مقياسٌ لكلّ شيء هو ما أجّل هذا البند أصلاً") and held exactly as
    intended for the two 07-nfr-slo §7 platform-state signals. ن-10 added a
    third of a DIFFERENT kind: ``aizzak_vault_authenticated``, a
    dependency-liveness probe, added after the failure it detects actually
    happened in production (``docs/log/3.94.md``) rather than because a metric
    seemed nice to have. That is the bar this guard is really enforcing —
    growth by a justified, logged decision, never by drift — so bumping the
    number without a matching entry in ``docs/p1-hardening-plan.md`` and a
    ``docs/log/`` write-up is the thing to refuse, not growth as such.
    """
    rules = _load_rules()
    assert len(rules) == 3, (
        f"{_ALERTS_YML}: expected exactly 3 alert rules (one per metric GET /metrics "
        f"emits), found {len(rules)} -- this file is scoped to the Outbox age + DLQ "
        "depth signals (P1-3, step 10) plus the Vault-authentication gauge (ن-10). "
        "A fourth rule needs its own logged justification first, not just a bumped "
        "number here."
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
