"""The four files Wave 0 step 0.3 added must agree with each other and with
``docker-compose.yml`` — ``deploy/prometheus/prometheus.yml``, the Grafana
datasource and dashboard providers, and the dashboard JSON itself.

**Why a test and not just careful reading.** Every failure this module
guards against is silent by construction: a scrape target naming a service
that does not exist produces an empty graph, a dashboard whose panels
reference a datasource uid Grafana never minted renders "Datasource not
found" on every panel, a provider whose ``options.path`` misses the bind
mount finds no dashboards at all, and a PromQL expression with a typo in a
metric name returns "no data" — which looks exactly like a healthy platform
with nothing happening. None of them raise, none of them log, and all of
them are found the same way: by someone opening the dashboard during an
incident and discovering it was never wired.

Three of the four were real mistakes made while writing these files, not
hypotheticals: the provider path pointed into the Grafana data volume rather
than the read-only bind, and the pgbouncer exporter's connection string was
passed under ``DATA_SOURCE_NAME`` — which v0.12.1 ignores in favour of its
own ``localhost:6543`` default while reporting ``pgbouncer_up 0``, i.e.
indistinguishable from a dead pooler.

This is the ``test_prometheus_alert_rules.py`` precedent (import the SAME
constant the code renders under, never a repeated literal) extended from one
file to the set of files that have to be consistent for any of them to mean
anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from app.api.metrics import (
    DLQ_DEPTH_METRIC,
    OUTBOX_AGE_METRIC,
    STREAM_LAG_METRIC,
    VAULT_AUTH_METRIC,
)
from app.framework.observability.metrics import (
    DB_POOL_AVAILABLE_METRIC,
    DB_POOL_IN_USE_METRIC,
    DB_POOL_OVERFLOW_METRIC,
    EVENT_LOOP_LAG_METRIC,
    HTTP_DURATION_METRIC,
    HTTP_REQUESTS_METRIC,
    RATE_LIMIT_REJECTIONS_METRIC,
    WS_CONNECTIONS_METRIC,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_PROM_YML = _REPO_ROOT / "deploy" / "prometheus" / "prometheus.yml"
_DATASOURCES = _REPO_ROOT / "deploy" / "grafana" / "provisioning" / "datasources"
_PROVIDERS = _REPO_ROOT / "deploy" / "grafana" / "provisioning" / "dashboards"
_DASHBOARDS = _REPO_ROOT / "deploy" / "grafana" / "dashboards"

# Every metric name this platform actually emits, taken from the constants the
# code renders under rather than retyped. A dashboard expression naming
# anything else is a typo that would render an empty panel forever.
KNOWN_METRICS = frozenset(
    {
        OUTBOX_AGE_METRIC,
        DLQ_DEPTH_METRIC,
        VAULT_AUTH_METRIC,
        STREAM_LAG_METRIC,
        HTTP_REQUESTS_METRIC,
        HTTP_DURATION_METRIC,
        DB_POOL_IN_USE_METRIC,
        DB_POOL_AVAILABLE_METRIC,
        DB_POOL_OVERFLOW_METRIC,
        EVENT_LOOP_LAG_METRIC,
        RATE_LIMIT_REJECTIONS_METRIC,
        WS_CONNECTIONS_METRIC,
    }
)

# prometheus_client renders a Histogram as three families; the dashboard has
# to name `..._bucket` to compute a quantile at all, so the suffix is stripped
# before the name is checked against the constants above.
_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")

_AIZZAK_METRIC = re.compile(r"\baizzak_[a-z0-9_]+")


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _compose() -> dict[str, Any]:
    return _load_yaml(_COMPOSE)


def _prom() -> dict[str, Any]:
    return _load_yaml(_PROM_YML)


def _dashboards() -> list[tuple[Path, dict[str, Any]]]:
    files = sorted(_DASHBOARDS.glob("*.json"))
    assert files, f"{_DASHBOARDS}: no dashboard JSON -- the provider would find nothing"
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in files]


def _panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Flattened panels, including any nested inside a collapsed row."""
    out: list[dict[str, Any]] = []
    for panel in dashboard.get("panels", []):
        out.append(panel)
        out.extend(panel.get("panels", []))
    return out


def _bind_target(service: dict[str, Any], host_path: str) -> str | None:
    """The container path a `./host:/container[:mode]` short-form mount lands on."""
    for mount in service.get("volumes", []):
        if isinstance(mount, str) and mount.startswith(f"{host_path}:"):
            return mount.split(":")[1]
    return None


# ── prometheus.yml against the Compose topology ────────────────────────────


def test_every_scrape_target_names_a_service_that_exists() -> None:
    """A target pointing at a hostname Compose never creates yields a
    permanently-down target, which the ``AizzakScrapeTargetDown`` rule then
    reports forever -- and an always-firing alert disarms the whole file."""
    services = set(_compose()["services"])
    for job in _prom()["scrape_configs"]:
        for static in job["static_configs"]:
            for target in static["targets"]:
                host = target.rsplit(":", 1)[0]
                if host == "127.0.0.1":
                    # Prometheus scraping itself; there is no service name here.
                    continue
                assert host in services, (
                    f"{_PROM_YML}: job {job['job_name']!r} scrapes {target!r}, but "
                    f"{host!r} is not a service in {_COMPOSE.name}"
                )


def test_the_app_is_scraped_directly_and_never_through_the_edge() -> None:
    """``/metrics`` is 404'd at the nginx edge on purpose (P1-3's mzalaq #2,
    ``src/app/api/metrics.py``'s docstring): Prometheus text about the Outbox,
    the DLQ and every request served is operator information. A scrape routed
    through ``nginx`` would either break -- or, far worse, be made to work by
    someone relaxing that 404."""
    jobs = {job["job_name"]: job for job in _prom()["scrape_configs"]}
    targets = [t for sc in jobs["aizzak-app"]["static_configs"] for t in sc["targets"]]
    assert targets == ["app:8000"], (
        f"{_PROM_YML}: the app must be scraped at its Compose-internal address, not "
        f"through the edge -- found {targets}"
    )


def test_the_rule_file_path_matches_the_mount_that_provides_it() -> None:
    """``rule_files:`` is a path INSIDE the container; the bind mount is what
    puts ``alerts.yml`` there. Drift between them loads zero rules, and
    Prometheus starts perfectly happily with no rules at all."""
    mounted = _bind_target(_compose()["services"]["prometheus"], "./deploy/prometheus/alerts.yml")
    assert mounted is not None, (
        f"{_COMPOSE.name}: the prometheus service does not mount deploy/prometheus/"
        "alerts.yml -- the rules would be absent, and Prometheus would not complain"
    )
    assert _prom()["rule_files"] == [mounted], (
        f"{_PROM_YML}: rule_files is {_prom()['rule_files']}, but the compose mount puts "
        f"alerts.yml at {mounted!r}"
    )


def test_the_scrape_and_evaluation_intervals_fit_the_acceptance_budget() -> None:
    """Step 0.3's acceptance criterion is a CLOCK -- "إسقاط `pgbouncer` يُشعل
    تنبيهاً خلال دقيقة" -- and it is spent as one scrape interval (to see the
    failure) + the rule's ``for:`` + one evaluation interval (to confirm it).
    Prometheus's own 1m defaults blow the budget on the first term alone."""
    glob = _prom()["global"]
    assert glob["scrape_interval"] == "15s", (
        f"{_PROM_YML}: scrape_interval drifted from 15s -- step 0.3's one-minute "
        "acceptance budget cannot absorb the 1m default"
    )
    assert glob["evaluation_interval"] == "15s", (
        f"{_PROM_YML}: evaluation_interval drifted from 15s -- see scrape_interval"
    )


def test_the_optional_target_is_the_one_behind_a_compose_profile() -> None:
    """``tier: optional`` and ``profiles:`` are two halves of one decision:
    cAdvisor needs the Docker socket, so it is opted into for a measurement
    rather than granted to every ``up``, and the label is what stops its
    ordinary absence from holding a target-down alert firing forever. Either
    half without the other is a bug."""
    compose = _compose()
    optional_hosts: set[str] = set()
    for job in _prom()["scrape_configs"]:
        for static in job["static_configs"]:
            if static.get("labels", {}).get("tier") == "optional":
                optional_hosts.update(t.rsplit(":", 1)[0] for t in static["targets"])

    profiled = {name for name, service in compose["services"].items() if service.get("profiles")}
    scraped = {
        t.rsplit(":", 1)[0]
        for job in _prom()["scrape_configs"]
        for static in job["static_configs"]
        for t in static["targets"]
    }
    assert optional_hosts == profiled & scraped, (
        f"{_PROM_YML}: the targets labelled `tier: optional` ({sorted(optional_hosts)}) must "
        f"be exactly the scraped services behind a Compose profile "
        f"({sorted(profiled & scraped)}). A profiled service without the label holds "
        "AizzakScrapeTargetDown firing forever; a labelled service that always runs is "
        "silently exempt from the only rule that would notice it dying."
    )


# ── The trust boundary ─────────────────────────────────────────────────────


def test_the_scraper_and_exporters_publish_no_host_port() -> None:
    """The same boundary ``embedding`` draws and ``api/metrics.py`` argues
    for. Prometheus holds operator information about the Outbox, the DLQ and
    every request this platform serves; the exporters hold a Postgres
    superuser session. None of it belongs on a host interface."""
    services = _compose()["services"]
    for name in ("prometheus", "pgbouncer-exporter", "redis-exporter", "cadvisor"):
        assert not services[name].get("ports"), (
            f"{_COMPOSE.name}: `{name}` must be `expose`-only -- publishing it puts "
            "operator information (or, for the exporters, a superuser session) on a host "
            "interface"
        )


def test_grafana_is_published_on_loopback_only() -> None:
    """The one deviation from step 0.3's "both services internal", and it is
    deliberate: a dashboard nobody can open is not a dashboard. It follows
    the pattern this file already uses for every operator interface it has
    (postgres, pgbouncer, redis, minio, qdrant, vault) -- 127.0.0.1 with an
    offset port, never 0.0.0.0. The distinction the plan's sentence is
    protecting is the public edge, and loopback is not it."""
    ports = _compose()["services"]["grafana"]["ports"]
    assert len(ports) == 1, f"{_COMPOSE.name}: grafana should publish exactly one port"
    assert ports[0].startswith("127.0.0.1:"), (
        f"{_COMPOSE.name}: grafana's publication must be bound to 127.0.0.1, not "
        f"{ports[0]!r} -- an unbound publication reaches every interface on the host"
    )


# ── Grafana provisioning against the dashboards it must find ───────────────


def test_the_dashboard_provider_path_matches_its_bind_mount() -> None:
    """The mistake this test was written after: the provider pointed at
    ``/var/lib/grafana/dashboards`` -- inside the DATA volume -- while the
    bind mount put the JSON at ``/etc/grafana/dashboards``. Grafana starts
    fine, provisions zero dashboards, and says so only at debug level."""
    providers = _load_yaml(_PROVIDERS / "aizzak.yml")["providers"]
    mounted = _bind_target(_compose()["services"]["grafana"], "./deploy/grafana/dashboards")
    assert mounted is not None, f"{_COMPOSE.name}: grafana does not mount deploy/grafana/dashboards"
    for provider in providers:
        assert provider["options"]["path"] == mounted, (
            f"{_PROVIDERS / 'aizzak.yml'}: provider {provider['name']!r} reads "
            f"{provider['options']['path']!r}, but the compose mount puts the dashboards "
            f"at {mounted!r} -- Grafana would provision nothing and log it at debug level"
        )


def test_every_panel_points_at_the_provisioned_datasource_uid() -> None:
    """Left unset, Grafana mints a RANDOM datasource uid at first start, and
    the committed dashboards then reference a uid that exists on nobody
    else's machine -- every panel renders "Datasource not found". Pinning the
    uid is only half the fix; this is the half that keeps it pinned."""
    sources = _load_yaml(_DATASOURCES / "prometheus.yml")["datasources"]
    uids = {source["uid"] for source in sources}
    assert len(sources) == 1, (
        f"{_DATASOURCES}: expected exactly one datasource; the dashboards name a single uid"
    )
    for path, dashboard in _dashboards():
        for panel in _panels(dashboard):
            for holder in [panel, *panel.get("targets", [])]:
                datasource = holder.get("datasource")
                if not isinstance(datasource, dict):
                    continue
                assert datasource.get("uid") in uids, (
                    f"{path.name}: panel {panel.get('title')!r} references datasource uid "
                    f"{datasource.get('uid')!r}, which is not provisioned ({sorted(uids)})"
                )


def test_the_datasource_url_names_the_scraper_service() -> None:
    source = _load_yaml(_DATASOURCES / "prometheus.yml")["datasources"][0]
    services = _compose()["services"]
    assert source["url"] == "http://prometheus:9090", (
        f"{_DATASOURCES}: the datasource must reach Prometheus on the Compose network "
        f"({source['url']!r} found) -- it is `expose`-only, so no host address works"
    )
    assert "9090" in services["prometheus"]["expose"], (
        f"{_COMPOSE.name}: prometheus must expose 9090 for the Grafana datasource to reach it"
    )


def test_every_platform_metric_a_panel_queries_actually_exists() -> None:
    """A typo in a PromQL expression returns "no data", which on a capacity
    dashboard is indistinguishable from a healthy platform doing nothing.
    Checked against the constants the code renders under, so a rename on
    either side fails here rather than being discovered during an incident."""
    for path, dashboard in _dashboards():
        for panel in _panels(dashboard):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for raw in _AIZZAK_METRIC.findall(expr):
                    name = raw
                    for suffix in _HISTOGRAM_SUFFIXES:
                        if name.endswith(suffix):
                            name = name[: -len(suffix)]
                            break
                    assert name in KNOWN_METRICS, (
                        f"{path.name}: panel {panel.get('title')!r} queries {raw!r}, which "
                        f"this platform does not emit. Known: {sorted(KNOWN_METRICS)}"
                    )


def test_every_panel_carries_a_description() -> None:
    """A capacity dashboard is read during an incident by whoever is awake,
    and a line with no explanation of what "good" looks like is a line nobody
    can act on -- the same "إنذارٌ يُرى لا يُستجاب له" the alert rules refuse,
    moved from a rule to a graph. Rows are structure and are exempt."""
    for path, dashboard in _dashboards():
        for panel in _panels(dashboard):
            if panel.get("type") == "row":
                continue
            assert panel.get("description", "").strip(), (
                f"{path.name}: panel {panel.get('title')!r} has no description -- say what "
                "the line means and what number would be bad, or the panel is decoration"
            )
