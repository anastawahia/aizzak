"""What replicating the API server does to every number this platform reports
-- capacity step 3.4 (``docs/capacity-plan.md`` §5 wave 3).

Step 3.4 reads as an infrastructure change: give ``app`` three replicas and
give every service a ceiling. It is also, silently, a change to the
MEASUREMENT APPARATUS that Wave 0 built and that every later step's acceptance
criterion is written against -- and it breaks it in both directions at once.

⚠️ ONE SCRAPE TARGET, THREE CONTAINERS. `deploy/prometheus/prometheus.yml`
addressed the API as `static_configs: ["app:8000"]`, with a comment stating --
correctly, for one container -- that a single target is right because siblings
aggregate through ``PROMETHEUS_MULTIPROC_DIR``. With three containers, Docker's
DNS returns three addresses, Prometheus connects to one per scrape, and ONE
time series is fed by three independent counters in rotation. MEASURED live on
the `/metrics` route's own counter, one sample per 15s scrape:

    335 336   1   2   3   1   4  337 337

Every drop is read as a counter reset, so each hop back to the busy container
manufactures a spike. Against a truth of one request per 15s (0.067/s):

    rate(...[1m])   0.061 ... 0.044 0.031 0.020 0.027 0.104 7.025 6.660

**105x the real rate**, with no gap, no ``up == 0`` and no error anywhere -- a
failure that reads as load.

⚠️ AND THE REPAIR HAS ITS OWN, OPPOSITE COST. One target per container splits
both metric families, and they need opposite aggregations. The split is exactly
"is this fact this process's own history, or is it external state everyone
re-reads", which is the distinction
``framework/observability/metrics.py``'s docstring already draws for the
multiprocess problem -- one container's siblings then, three containers now:

* PER-CONTAINER SHARE -- each container holds its own part of one fleet total,
  so the fleet number is ``sum()``. Every dashboard panel already summed (for
  the multiprocess reason), so these kept reading correctly across the change.
* EXTERNAL STATE -- every container recomputes the SAME fact from
  Postgres/Redis on every scrape, so ``sum()`` multiplies it by the replica
  count. MEASURED: ``sum(aizzak_dlq_depth)`` read **90** for a real backlog of
  **30**; ``sum(max by (stream) (aizzak_dlq_depth))`` read **30**.

⭐ AND WRITING THIS GUARD FOUND A **THIRD** FAMILY, which the first cut of it
put in the wrong one. ``aizzak_vault_authenticated`` looks like external state
-- there is one Vault, and every container asks it the same question -- but the
gauge's own help string says what it actually reports: *"1 if **this process**
can currently make an authorized Vault call"*. An AppRole ``secret_id`` expires
per container (``infrastructure/monitoring/vault_health.py``), so a replica can
read 0 while its siblings read 1, and that is a true fact about one container
rather than a triplicated reading of one fact. The same is true of
``aizzak_event_loop_lag_seconds``.

So this family is neither summed (adding 1/0 flags answers no question) nor
forced to collapse: ``AizzakVaultAuthFailing``'s bare
``aizzak_vault_authenticated < 1`` is not a defect the replicas introduced --
it is the one rule that gets **more** precise under them, firing with
``$labels.instance`` naming the container that lost its token. The three
families are indistinguishable by metric name, nothing separated them, and a
wrong aggregation is silent in every direction. This module is what separates
them: the names come from the modules that define them, never retyped, and
every query in the alert rules and the dashboard is checked against the family
it belongs to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from app.api.metrics import (
    DLQ_DEPTH_METRIC,
    OUTBOX_AGE_METRIC,
    STREAM_LAG_METRIC,
    STREAM_LENGTH_METRIC,
    STREAM_MAXLEN_METRIC,
    STREAM_UNCONSUMED_AGE_METRIC,
    STREAM_UNREAD_TRIMMED_METRIC,
    VAULT_AUTH_METRIC,
)
from app.framework.observability.metrics import (
    API_RATE_LIMIT_METRIC,
    AUTH_PRINCIPAL_CACHE_METRIC,
    DB_POOL_AVAILABLE_METRIC,
    DB_POOL_IN_USE_METRIC,
    DB_POOL_OVERFLOW_METRIC,
    EMBEDDING_CACHE_METRIC,
    EVENT_LOOP_LAG_METRIC,
    HEAVY_JOB_LIMIT_METRIC,
    HTTP_DURATION_METRIC,
    HTTP_REQUESTS_METRIC,
    RATE_LIMIT_REJECTIONS_METRIC,
    WS_CONNECTIONS_METRIC,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_PROMETHEUS = _REPO_ROOT / "deploy" / "prometheus" / "prometheus.yml"
_ALERTS = _REPO_ROOT / "deploy" / "prometheus" / "alerts.yml"
_DASHBOARD = _REPO_ROOT / "deploy" / "grafana" / "dashboards" / "aizzak-capacity.json"

_APP_JOB = "aizzak-app"

# ── The two families ────────────────────────────────────────────────────────
# Imported from the modules that DEFINE them rather than retyped, so a metric
# renamed in one place cannot leave a stale literal passing here. Which family
# a metric belongs to is decided by which module defines it, and that is not a
# coincidence: `api/metrics.py` holds the gauges recomputed from external state
# on every scrape (its `SqlRedisMetricsSource` collector), and
# `framework/observability/metrics.py` holds the ones a process accumulates in
# its own heap.
#
# ⚠️ The membership test is NOT "which module defines it" alone. Three of these
# four `api/metrics.py` gauges are one fact read N times; the fourth reports on
# the reading PROCESS, and its own help string is what says so. See the
# docstring's third family.
#
# Capacity 5.5 adds four more, all external state: `aizzak_stream_maxlen` is
# configuration rather than Redis state, but every replica reports the same
# number for the same reason, and summing it would triple the backstop.
_EXTERNAL_STATE = frozenset(
    {
        OUTBOX_AGE_METRIC,
        DLQ_DEPTH_METRIC,
        STREAM_LAG_METRIC,
        STREAM_LENGTH_METRIC,
        STREAM_MAXLEN_METRIC,
        STREAM_UNCONSUMED_AGE_METRIC,
        STREAM_UNREAD_TRIMMED_METRIC,
    }
)

# One fleet total, split across containers -- these must be summed.
_PER_CONTAINER_SHARE = frozenset(
    {
        HTTP_REQUESTS_METRIC,
        HTTP_DURATION_METRIC,
        DB_POOL_IN_USE_METRIC,
        DB_POOL_AVAILABLE_METRIC,
        DB_POOL_OVERFLOW_METRIC,
        RATE_LIMIT_REJECTIONS_METRIC,
        WS_CONNECTIONS_METRIC,
        AUTH_PRINCIPAL_CACHE_METRIC,
        API_RATE_LIMIT_METRIC,
        HEAVY_JOB_LIMIT_METRIC,
        EMBEDDING_CACHE_METRIC,
    }
)

# A true statement about ONE container. Never summed -- adding 1/0 flags, or
# adding one loop's lag to another's, answers no question anyone asks -- and
# never required to collapse either: "which replica" is the useful half.
_PER_CONTAINER_HEALTH = frozenset({VAULT_AUTH_METRIC, EVENT_LOOP_LAG_METRIC})

# The PromQL aggregators that collapse a metric across scrape targets. `sum` is
# deliberately absent: summing external state is the defect this module exists
# to catch, and it is the one that reads as plausible.
_COLLAPSING = ("max", "min", "avg")


def _app_replicas() -> int:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return int((compose["services"]["app"].get("deploy") or {}).get("replicas", 1))


def _alert_expressions() -> dict[str, str]:
    rules = yaml.safe_load(_ALERTS.read_text(encoding="utf-8"))
    return {
        rule["alert"]: rule["expr"]
        for group in rules["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }


def _dashboard_expressions() -> list[str]:
    def walk(node: object) -> list[str]:
        if isinstance(node, dict):
            found = [str(node["expr"])] if "expr" in node else []
            return found + [e for value in node.values() for e in walk(value)]
        if isinstance(node, list):
            return [e for item in node for e in walk(item)]
        return []

    return walk(json.loads(_DASHBOARD.read_text(encoding="utf-8")))


def _uses(expression: str, metric: str) -> bool:
    return re.search(rf"\b{re.escape(metric)}\b", expression) is not None


def _collapses_replicas(expression: str, metric: str) -> bool:
    """Is this metric wrapped in something that folds identical series from
    different scrape targets into one? A `by (...)` grouping is allowed and is
    usually required -- `max by (stream)` keeps the streams apart while
    collapsing the replicas -- but it must not group by `instance`, which is
    the label that distinguishes the containers and therefore the one that
    would defeat the whole collapse."""
    for aggregator in _COLLAPSING:
        pattern = rf"\b{aggregator}\s*(?:by\s*\(([^)]*)\)\s*)?\([^()]*\b{re.escape(metric)}\b"
        match = re.search(pattern, expression)
        if match and "instance" not in (match.group(1) or ""):
            return True
    return False


# ────────────────────────────────────────────────── the scrape topology ──


def test_the_api_is_scraped_once_per_container() -> None:
    """With `deploy.replicas` on `app`, a single static target is a rotating
    read of three independent counters -- the 105x measurement in this module's
    docstring. Service discovery is what makes each container its own series
    with its own `instance` label, so a reset is a real restart rather than a
    hop."""
    scrape = yaml.safe_load(_PROMETHEUS.read_text(encoding="utf-8"))
    job = next(j for j in scrape["scrape_configs"] if j["job_name"] == _APP_JOB)

    if _app_replicas() <= 1:
        return  # one container: a static target is honest again

    assert "static_configs" not in job, (
        f"`app` runs {_app_replicas()} replicas and the `{_APP_JOB}` job still names one "
        "static target. Prometheus resolves the name and connects to ONE container per "
        "scrape, so one time series is fed by three counters in rotation and every drop "
        "reads as a counter reset (measured: rate() 105x the truth)."
    )
    discovery = job["dns_sd_configs"]
    assert [d["names"] for d in discovery] == [["app"]], discovery
    assert all(d["type"] == "A" for d in discovery), (
        "A records, not SRV: Compose publishes no SRV records for its services"
    )


def test_discovery_refreshes_at_least_as_often_as_it_scrapes() -> None:
    """A replica recreated between refreshes is scraped at its old address
    until discovery catches up, which is `up == 0` for a container that is
    fine. `7.2`'s rolling deploy recreates them one at a time, so this interval
    is what decides whether that deploy looks like an outage."""
    scrape = yaml.safe_load(_PROMETHEUS.read_text(encoding="utf-8"))
    job = next(j for j in scrape["scrape_configs"] if j["job_name"] == _APP_JOB)
    if "dns_sd_configs" not in job:
        return

    def seconds(value: str) -> int:
        return int(str(value).rstrip("s"))

    interval = seconds(scrape["global"]["scrape_interval"])
    for discovery in job["dns_sd_configs"]:
        assert seconds(discovery["refresh_interval"]) <= interval, (
            "DNS discovery refreshes more slowly than Prometheus scrapes, so a recreated "
            "replica spends whole scrapes being read at an address it no longer has"
        )


# ──────────────────────────────────────────── external state vs sum() ──


def test_no_rule_or_panel_sums_a_gauge_every_replica_recomputes() -> None:
    """⭐ The defect the repair introduces, guarded on both files at once.

    These four gauges are the same fact read three times, not three shares of
    one fact. `sum()` multiplies them by the replica count -- measured, a DLQ
    backlog of 30 reading as 90 -- and a bare expression draws one line per
    container for a quantity that has one value."""
    if _app_replicas() <= 1:
        return

    offenders: list[str] = []
    sources = [(f"alert {name}", expr) for name, expr in _alert_expressions().items()]
    sources += [(f"panel {expr[:60]}", expr) for expr in _dashboard_expressions()]

    for where, expression in sources:
        for metric in _EXTERNAL_STATE:
            if not _uses(expression, metric):
                continue
            if not _collapses_replicas(expression, metric):
                offenders.append(f"{where}: {metric} in `{expression}`")

    assert not offenders, (
        "these read an external-state gauge without collapsing the replicas that all "
        "report it, so the number is multiplied by the replica count (or drawn once per "
        "container):\n  " + "\n  ".join(offenders) + "\n"
        "Wrap it in max()/min(), keeping any `by (...)` that separates real dimensions -- "
        "`sum(max by (stream) (aizzak_dlq_depth))`, not `sum(aizzak_dlq_depth)`."
    )


def test_a_per_container_share_is_never_collapsed_to_one_containers_worth() -> None:
    """The opposite error, and the reason this module holds three lists rather
    than one rule. These metrics ARE shares of one fleet total, so collapsing
    them with max() reports a single replica's traffic as the whole platform's
    -- the same size of mistake as the other family's, in the other direction,
    and exactly as quiet."""
    if _app_replicas() <= 1:
        return

    offenders: list[str] = []
    for expression in _dashboard_expressions():
        for metric in _PER_CONTAINER_SHARE:
            if _uses(expression, metric) and _collapses_replicas(expression, metric):
                offenders.append(f"{metric} in `{expression}`")

    assert not offenders, (
        "these collapse a PER-CONTAINER SHARE across replicas, which reports one "
        "container's share as the whole fleet's:\n  " + "\n  ".join(offenders)
    )


def test_a_per_container_health_flag_is_never_added_up() -> None:
    """The third family. Summing "can this process reach Vault" over three
    containers yields 3, 2, 1 or 0 -- a number whose only useful threshold is
    the one `min()` already expresses, and whose middle values quietly hide a
    broken replica behind two healthy ones."""
    if _app_replicas() <= 1:
        return

    offenders: list[str] = []
    sources = [(f"alert {name}", expr) for name, expr in _alert_expressions().items()]
    sources += [(f"panel {expr[:60]}", expr) for expr in _dashboard_expressions()]

    for where, expression in sources:
        for metric in _PER_CONTAINER_HEALTH:
            if not _uses(expression, metric):
                continue
            if re.search(
                rf"\bsum\s*(?:by\s*\([^)]*\)\s*)?\([^()]*\b{re.escape(metric)}\b", expression
            ):
                offenders.append(f"{where}: {metric} in `{expression}`")

    assert not offenders, (
        "these ADD UP a per-container health signal, which turns "
        "'one replica is broken' into a number between 0 and N:\n  " + "\n  ".join(offenders)
    )


# ──────────────────────────────────────────────────────── the sentinels ──


def test_every_metric_this_module_judges_is_actually_used_somewhere() -> None:
    """Both lists can go stale in the same silent way: a metric renamed, or a
    panel deleted, leaves this module asserting over an empty set and reading
    green. So the families are checked for being non-empty AND for actually
    appearing in the files they are supposed to police."""
    families = (_EXTERNAL_STATE, _PER_CONTAINER_SHARE, _PER_CONTAINER_HEALTH)
    assert all(families)
    for one, other in ((0, 1), (0, 2), (1, 2)):
        assert not (families[one] & families[other]), "a metric cannot be in two families"

    text = _ALERTS.read_text(encoding="utf-8") + _DASHBOARD.read_text(encoding="utf-8")
    unused = sorted(m for m in _EXTERNAL_STATE if not _uses(text, m))
    assert not unused, (
        f"{unused} are declared external state here but appear in no rule and no panel; "
        "either the family list is stale or the measurement was dropped"
    )

    assert len(_alert_expressions()) >= 5, _alert_expressions()
    assert len(_dashboard_expressions()) >= 20, len(_dashboard_expressions())


def test_the_collapse_detector_can_tell_the_two_apart() -> None:
    """The check itself, checked. A predicate that answered True for everything
    would make the whole module vacuous, and `by (instance)` is the specific
    shape that LOOKS like a collapse and is not one -- `instance` is the label
    that separates the containers."""
    metric = DLQ_DEPTH_METRIC
    assert not _collapses_replicas(f"{metric}", metric)
    assert not _collapses_replicas(f"sum({metric})", metric)
    assert not _collapses_replicas(f"max by (instance) ({metric})", metric)
    assert not _collapses_replicas(f"sum(max by (stream, instance) ({metric}))", metric)
    assert _collapses_replicas(f"max({metric})", metric)
    assert _collapses_replicas(f"sum(max by (stream) ({metric}))", metric)
    assert _collapses_replicas(f"min ({metric})", metric)
