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
        # Wave 4 step 4.5 (docs/capacity-plan.md) -- the sixth, and the first
        # whose subject is a MIGRATION rather than a dependency: an embedding
        # model changed without its corpus, and every other signal in this
        # file stays green while retrieval quietly stops seeing new documents.
        "AizzakVectorShadowWrites",
        # Wave 5 step 5.2 (docs/capacity-plan.md) -- the seventh and eighth,
        # and the first two that could not have been WRITTEN before their own
        # step. Until 5.2 there was one Redis at `maxmemory 0`: with no
        # ceiling there is no "80% of" anything to threshold, and on a shared
        # instance an eviction is ambiguous between "the cache is doing its
        # job" and "an unconsumed stream entry was just deleted". Splitting
        # the server is what turned both into questions with one answer.
        "AizzakRedisStreamMemoryHigh",
        "AizzakRedisStreamEvicted",
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

    Wave 4 step 4.5 adds the sixth, and it earns its place the same way the
    third did: it thresholds a failure with NO other symptom. A deployment
    that changed the embedding model and skipped the corpus migration keeps
    every number in this file green -- normal latency, zero errors, a healthy
    scrape -- while retrieval answers from a corpus nothing writes to any
    more, and every document indexed since is invisible.

    Wave 5 step 5.2 adds the seventh and eighth, and they clear the bar in a
    way none of the six before them did: they are the first rules in this file
    that were IMPOSSIBLE to write earlier. Both threshold a property of a
    Redis instance that did not exist until that step split one server in two
    -- a `maxmemory` to be 80% of, and an eviction counter whose movement is
    unambiguous because the instance's policy pins it at zero.

    That is the bar this guard enforces -- growth by a justified, logged
    decision, never by drift -- so a NINTH entry needs its own written
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
        "Vault-authentication gauge (ن-10), the two scrape-health rules (capacity-plan "
        "Wave 0 step 0.3), the shadow-corpus write counter (step 4.5) and the two "
        "redis-stream rules (step 5.2). A new rule needs its own logged justification "
        "first, not just a name added to EXPECTED_ALERTS."
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


def _rule_named(rules: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [rule for rule in rules if rule.get("alert") == name]
    assert len(matches) == 1, f"{_ALERTS_YML}: expected exactly one {name!r} rule"
    return matches[0]


def test_both_redis_rules_are_scoped_to_the_noeviction_instance() -> None:
    """capacity 5.2 -- the ``job="redis-stream"`` selector is the rule, not a
    detail of it.

    Both expressions are true CONTINUOUSLY on ``redis-cache``: it runs
    ``allkeys-lru``, so it evicts by design and it is SUPPOSED to sit near its
    ceiling (an LRU cache that never reaches `maxmemory` is a cache nobody
    sized). Dropping the selector would not widen these rules, it would make
    them fire forever and be silenced -- which is how an alert that matters
    gets turned off for a reason that has nothing to do with it.
    """
    rules = _load_rules()
    for name in ("AizzakRedisStreamMemoryHigh", "AizzakRedisStreamEvicted"):
        expr = _rule_named(rules, name)["expr"]
        assert 'job="redis-stream"' in expr, (
            f'{_ALERTS_YML}: {name} must select `job="redis-stream"` -- unscoped, it also '
            "matches the allkeys-lru instance, where both conditions are the design working"
        )
        assert "redis-cache" not in expr, (
            f"{_ALERTS_YML}: {name} names the cache instance; these two rules are about "
            "the instance that must never evict and must never fill"
        )


def test_the_redis_memory_rule_thresholds_the_80_percent_the_step_asks_for() -> None:
    """The number 5.2 states in words ("تنبيهٌ عند 80%"), as a fraction of
    ``maxmemory`` rather than as an absolute byte count.

    An absolute threshold would have to be re-edited every time
    ``docker-compose.yml`` changes the ceiling, and would go silently wrong in
    the direction that matters: raise `maxmemory` to buy room and the alert
    starts firing at 40% instead of 80%, so the operator silences it.
    """
    rule = _rule_named(_load_rules(), "AizzakRedisStreamMemoryHigh")
    expr = " ".join(rule["expr"].split())
    assert "redis_memory_used_bytes" in expr and "redis_memory_max_bytes" in expr, (
        f"{_ALERTS_YML}: the memory rule must be a RATIO of used to maxmemory -- an "
        "absolute byte threshold silently changes meaning when the ceiling moves"
    )
    assert "> 0.8" in expr, f"{_ALERTS_YML}: 5.2's threshold is 80% of maxmemory"
    assert rule["for"] == "5m", (
        f"{_ALERTS_YML}: the memory rule's `for:` drifted -- 80% of a 2 GB ceiling is a "
        "trend with hours of runway, not a spike to debounce"
    )
    response = rule["annotations"]["response"]
    assert "app.ops.dlq" in response, (
        f"{_ALERTS_YML}: the memory rule's `response` must name `python -m app.ops.dlq` "
        "-- the DLQs are the only keys on that instance with no bound at all "
        "(`dead_letter` XADDs without `maxlen`, deliberately), so they are the first "
        "thing an operator looks at"
    )


def test_the_redis_eviction_rule_refuses_to_debounce() -> None:
    """The one rule in this file with no waiting window, and the reason is a
    property of the instance rather than an opinion about urgency.

    ``maxmemory-policy noeviction`` pins ``redis_evicted_keys_total`` at zero
    structurally: on a correctly configured server this expression cannot
    become true. So there is no flapping to suppress and no benign amount to
    wait out -- the first increment is the entire finding, and every second of
    `for:` is a second of silent loss on the instance holding the streams, the
    WS registry and the session denylist.
    """
    rule = _rule_named(_load_rules(), "AizzakRedisStreamEvicted")
    assert "redis_evicted_keys_total" in rule["expr"]
    assert str(rule.get("for", "0s")) in {"0s", "0"}, (
        f"{_ALERTS_YML}: AizzakRedisStreamEvicted must not debounce -- a `for:` here waits "
        "out data loss on an instance where the counter cannot legitimately move at all"
    )
    assert rule["labels"]["severity"] == "critical", (
        f"{_ALERTS_YML}: an eviction on the noeviction instance can drop an "
        "`auth:revoked:<sub>` entry, which re-validates a revoked token until it expires"
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
