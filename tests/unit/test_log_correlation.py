"""The three ids capacity step 0.6 puts on every log line, and the pipeline
that carries them — ``docs/capacity-plan.md`` 0.6.

**What was actually broken, and why it was invisible.** ``JsonFormatter`` has
read ``correlation_id_var``/``workspace_id_var``/``request_id_var`` since it
was written, and *nothing in the codebase ever set them*. Every JSON line the
platform emitted carried a timestamp, a level, a logger and a message, and no
identifier tying it to anything. That state is worse than a missing feature:
``logging.py``'s own docstring promised the ids, ``07 §7`` promised the ids,
and a reader of either would have concluded they were shipping. The failure
mode is silence, which is precisely what a test is for.

Two halves, matching the step's two halves:

* **Emission** — the formatter's payload, the three binding sites, and the
  pseudonymisation the plan asks for (``workspace_id`` مموّهاً). Hermetic:
  a ``LogRecord`` and a context variable, no I/O.
* **The pipeline** — that ``docker-compose.yml``, ``deploy/alloy/config.alloy``,
  ``deploy/loki/loki.yml`` and the two nginx configs agree with each other and
  with the field names the formatter writes. Every disagreement here is silent
  in the same way 0.3's were: a field name that does not match produces an
  empty column, not an error, and is found by someone searching for a
  correlation id during an incident and getting nothing back.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.api.main import CORRELATION_HEADER, REQUEST_ID_HEADER
from app.framework.observability.context import (
    correlation_id_var,
    event_id_var,
    log_context,
)
from app.framework.observability.logging import WORKSPACE_FIELD, JsonFormatter
from app.framework.observability.pseudonymity import pseudonymous_id

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ALLOY = _REPO_ROOT / "deploy" / "alloy" / "config.alloy"
_LOKI = _REPO_ROOT / "deploy" / "loki" / "loki.yml"
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_NGINX_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"
_RUNPOD_NGINX = _REPO_ROOT / "deploy" / "runpod" / "nginx.conf"
_ENGINE = _REPO_ROOT / "src" / "app" / "infrastructure" / "messaging" / "consumers" / "engine.py"

# The identifiers the plan names for the aggregated store, plus the edge's
# per-hop id. Every one of them has to survive the whole path: formatter ->
# json-file driver -> Alloy's parse stages -> Loki structured metadata ->
# a dashboard panel. Named once here so a rename fails in one place.
LOG_ID_FIELDS = ("correlation_id", "request_id", "event_id", WORKSPACE_FIELD)


def _format(record_extra: dict[str, Any] | None = None, *, message: str = "hi") -> dict[str, Any]:
    """Run one record through the real formatter and return the parsed line."""
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in (record_extra or {}).items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_text(_COMPOSE))


# ── Emission: what a line carries ──────────────────────────────────────────


def test_a_line_carries_nothing_when_nothing_is_bound() -> None:
    """The floor, and the state the platform was ACTUALLY in before 0.6: no
    binding means no id fields at all -- not empty strings, which would look
    like ids that failed to resolve."""
    payload = _format()
    for field in LOG_ID_FIELDS:
        assert field not in payload, (
            f"an unbound {field!r} must be ABSENT from the line, not present-and-empty: "
            "an empty value in the store reads as 'this request had no id', which is a "
            f"different and much more alarming fact. Got {payload!r}"
        )


def test_every_bound_identifier_reaches_the_line() -> None:
    """The whole point of the step, in one assertion. ``log_context`` is the
    same helper the consumer engine uses, so this exercises the production
    binding path rather than a parallel one."""
    with log_context(
        correlation_id="cid-1",
        workspace_id="ws-uuid-1",
        event_id="evt-1",
        request_id="req-1",
    ):
        payload = _format()

    assert payload["correlation_id"] == "cid-1"
    assert payload["request_id"] == "req-1"
    assert payload["event_id"] == "evt-1"
    assert payload[WORKSPACE_FIELD] == pseudonymous_id("ws-uuid-1")


def test_log_context_restores_every_variable_on_the_way_out() -> None:
    """The reason the worker uses the context manager and the HTTP middleware
    does not: the consumer engine is ONE long-lived task handling message
    after message, so an unrestored binding would stamp the previous
    message's correlation id onto every line emitted between deliveries --
    including the poll loop's own."""
    with log_context(correlation_id="outer"):
        with log_context(correlation_id="inner", event_id="evt"):
            assert correlation_id_var.get() == "inner"
        assert correlation_id_var.get() == "outer"
        assert event_id_var.get() is None, "the inner block's event id outlived its block"
    assert correlation_id_var.get() is None


def test_log_context_restores_even_when_the_body_raises() -> None:
    """A handler that raises is the NORMAL path here (policy 4: no ack,
    redeliver), so an exception must not be the case that leaks a binding."""
    try:
        with log_context(correlation_id="doomed"):
            raise RuntimeError("handler_failed")
    except RuntimeError:
        pass
    assert correlation_id_var.get() is None


def test_a_none_argument_leaves_an_outer_binding_alone() -> None:
    """An envelope without a ``correlationid`` extension must not ERASE an id
    an outer scope established; ``None`` means "say nothing", not "clear"."""
    with log_context(correlation_id="outer"), log_context(workspace_id="ws-1"):
        assert correlation_id_var.get() == "outer"


# ── Emission: pseudonymity ─────────────────────────────────────────────────


def test_the_tenant_identifier_never_reaches_a_line_in_the_clear() -> None:
    """«``workspace_id`` مموّهاً» (0.6). Aggregation is what changes the
    stakes: before it, a tenant id in a log line was visible to whoever could
    already read the row out of Postgres; after it, the same id is durable,
    indexed and queryable by anyone with the Grafana URL."""
    raw = "0192f3c4-5d6e-7f80-9012-3456789abcde"
    with log_context(workspace_id=raw):
        payload = _format()
    assert raw not in json.dumps(payload, ensure_ascii=False)
    assert payload[WORKSPACE_FIELD].startswith("ws-")


def test_an_explicit_extra_field_is_pseudonymised_too() -> None:
    """The sweep runs AFTER the extras are merged, deliberately: five call
    sites in this codebase log ``extra={"workspace_id": …}`` today, and each
    of them overwrites the context value. Pseudonymising only the context
    variable would leave the raw id on exactly the lines that named a tenant
    on purpose."""
    raw = "0192f3c4-5d6e-7f80-9012-3456789abcde"
    payload = _format({WORKSPACE_FIELD: raw, "file_id": "f-1"})
    assert payload[WORKSPACE_FIELD] == pseudonymous_id(raw)
    assert payload["file_id"] == "f-1", "unrelated extras must pass through untouched"


def test_the_pseudonym_is_stable_across_processes() -> None:
    """The property 0.6's acceptance criterion rests on. A random per-process
    salt -- the reflex when hashing an identifier -- would give ``app`` and
    ``worker-media`` different pseudonyms for one tenant and silently destroy
    the cross-service join the whole step exists to deliver."""
    assert pseudonymous_id("ws-a") == pseudonymous_id("ws-a")
    assert pseudonymous_id("ws-a") != pseudonymous_id("ws-b")


def test_redaction_still_runs_on_the_extras() -> None:
    """0.6 added a sweep to this formatter; it must not have displaced the
    one that was already there (10 §10)."""
    payload = _format({"api_key": "sk-live-secret", "detail": "ok"})
    assert "sk-live-secret" not in json.dumps(payload)
    assert payload["detail"] == "ok"


# ── Emission: the three binding sites exist ────────────────────────────────


def test_the_http_middleware_binds_the_correlation_id() -> None:
    """Guarding against the exact regression 0.6 fixed: a middleware that
    stamps ``request.state`` and returns the header while binding nothing
    leaves every log line of that request unfindable."""
    source = _text(_REPO_ROOT / "src" / "app" / "api" / "main.py")
    assert "correlation_id_var.set(correlation_id)" in source, (
        "api/main.py's correlation middleware must BIND the id, not only stamp it on "
        "request.state -- the log formatter reads the context variable and nothing else"
    )
    assert "request_id_var.set(request_id)" in source


def test_the_context_dependency_binds_the_workspace() -> None:
    source = _text(_REPO_ROOT / "src" / "app" / "api" / "v1" / "dependencies.py")
    assert "workspace_id_var.set(principal.workspace_id)" in source, (
        "the tenant must be bound from the PRINCIPAL in current_context -- binding it "
        "from anything the client sends would be the tenant-isolation hole 03 §0 names"
    )


def test_the_consumer_engine_binds_all_three_around_dispatch() -> None:
    """``handler_failed`` and ``dead_lettered`` are the two lines an operator
    goes looking for when an event produced no effect. Without this binding
    they carry an entry id and a stream name and nothing that ties them to
    the request that caused the event."""
    source = _text(_ENGINE)
    assert "with log_context(" in source
    for argument in (
        "correlation_id=correlation_id",
        "workspace_id=workspace_id",
        "event_id=event_id",
    ):
        assert argument in source, f"{_ENGINE.name}: dispatch must bind {argument}"


# ── The pipeline: the edge ─────────────────────────────────────────────────


def test_the_edge_mints_a_correlation_id_when_the_client_sends_none() -> None:
    """The edge is the FIRST hop, so it is the only place that can guarantee
    an id exists at all. An id the app mints for itself is an id nginx never
    saw, and the acceptance criterion needs the edge line and the app line to
    say the same thing."""
    conf = _text(_NGINX)
    assert "map $http_x_correlation_id $aizzak_correlation_id" in conf
    assert "$request_id" in conf, "the fallback must be nginx's own per-request id"


def test_the_edge_access_log_is_json_and_escapes_it() -> None:
    """``escape=json`` is load-bearing rather than tidy: the user agent and
    the request line are attacker-controlled, and without it one quote
    produces a line the collector's parse stage drops -- so the single
    request an attacker cared about is the one missing from the store."""
    conf = _text(_NGINX)
    assert "log_format aizzak_json escape=json" in conf
    assert "access_log  /var/log/nginx/access.log  aizzak_json;" in conf
    assert "main;" not in conf, "the plain-text format must be gone, not merely unused"


def test_the_edge_log_names_the_same_fields_the_app_does() -> None:
    """One regex in the Grafana derived field, one ``stage.json`` in Alloy,
    two producers. The join only exists because the two formats agreed to
    call the id the same thing."""
    conf = _text(_NGINX)
    for field in ("correlation_id", "request_id"):
        assert f'"{field}":"' in conf, (
            f"{_NGINX.name}: the JSON access log must emit {field!r} under exactly the "
            "name the app's formatter uses, or the two cannot be joined"
        )


def test_the_edge_never_logs_a_query_string() -> None:
    """``$uri`` and not ``$request_uri``. Query strings on this API carry
    pagination cursors and OAuth ``code``/``state`` values, and a log store is
    the last place an authorisation code should be durably indexed -- the
    ``redaction.py`` rule applied to the one field the app's own formatter
    never sees."""
    conf = _text(_NGINX)
    assert '"path":"$uri"' in conf
    assert "$request_uri" not in conf.split("log_format aizzak_json")[1].split(";")[0]


def test_the_edge_passes_both_ids_upstream() -> None:
    conf = _text(_NGINX_LOCATIONS)
    assert f"proxy_set_header {CORRELATION_HEADER} $aizzak_correlation_id;" in conf
    assert f"proxy_set_header {REQUEST_ID_HEADER}     $request_id;" in conf


def test_the_runpod_edge_says_all_of_it_again() -> None:
    """RunPod is ONE container that reads neither ``docker-compose.yml`` nor
    ``deploy/nginx/`` -- the same drift 0.4 guarded for ``pg_stat_statements``.
    Without this, correlation ids work on Compose and are silently absent on
    the Pod, which is the deployment an operator is least able to debug."""
    conf = _text(_RUNPOD_NGINX)
    assert "log_format aizzak_json escape=json" in conf
    assert "map $http_x_correlation_id $aizzak_correlation_id" in conf
    assert f"proxy_set_header {CORRELATION_HEADER} $aizzak_correlation_id;" in conf
    assert f"proxy_set_header {REQUEST_ID_HEADER}     $request_id;" in conf
    assert "access_log  /dev/stdout  aizzak_json;" in conf


# ── The pipeline: collection ───────────────────────────────────────────────


def test_the_collector_holds_no_docker_socket() -> None:
    """THE design decision of this step. The published recipe mounts
    /var/run/docker.sock so the collector can ask the API for container
    names; that is root on the host -- the capability 0.3 put ``cadvisor``
    behind an opt-in profile rather than grant for free. 0.6 cannot be
    opt-in (a log store that is off by default is a switch someone forgets
    before the incident), so the service name rides inside the log file
    instead."""
    alloy = _compose()["services"]["alloy"]
    for mount in alloy["volumes"]:
        assert "docker.sock" not in mount, (
            f"{_COMPOSE.name}: alloy must not mount the Docker socket -- it is root on "
            "the host, and the whole point of the `labels` log-opt below is that it is "
            "not needed. See deploy/alloy/config.alloy's header."
        )
    assert "/var/lib/docker/containers:/var/lib/docker/containers:ro" in alloy["volumes"], (
        f"{_COMPOSE.name}: alloy reads the container log files directly, READ-ONLY"
    )


def test_the_log_driver_stamps_the_service_name_on_every_line() -> None:
    """The half of the no-socket design that lives in Compose. Without the
    ``labels`` log-opt the json-file driver writes no ``attrs`` object, every
    line arrives with an empty ``project``, and Alloy's own project filter
    drops the ENTIRE stack -- a store that is running, healthy and empty."""
    options = _compose()["x-app-logging"]["logging"]["options"]
    labels = options.get("labels", "")
    assert "com.docker.compose.service" in labels
    assert "com.docker.compose.project" in labels


def test_every_service_merges_the_logging_anchor() -> None:
    """One anchor, so one line labels the whole stack -- and a service that
    forgot to merge it is invisible in the store while looking perfectly
    healthy in ``docker compose ps``."""
    expected = _compose()["x-app-logging"]["logging"]
    for name, service in _compose()["services"].items():
        # PyYAML resolves the `<<:` merge, so this reads the EFFECTIVE logging
        # block each service ends up with -- not the text of the anchor
        # reference, which would pass for a service that merged a different one.
        assert service.get("logging") == expected, (
            f"{_COMPOSE.name}: service {name!r} does not end up with the *app-logging "
            f"block ({service.get('logging')!r}), so its lines carry no service label "
            "and Alloy drops them -- invisible in the store, healthy in `compose ps`"
        )


def test_the_collector_extracts_the_dotted_label_keys_correctly() -> None:
    """Compose's label keys contain dots, and JMESPath reads a dot as nesting:
    unquoted, ``attrs.com.docker.compose.service`` looks for five nested
    fields that do not exist and every line arrives unlabelled."""
    config = _text(_ALLOY)
    assert 'attrs.\\"com.docker.compose.service\\"' in config
    assert 'attrs.\\"com.docker.compose.project\\"' in config


def test_the_collector_uses_no_negative_lookahead() -> None:
    """Measured as a crash loop on this file's first start: Alloy compiles
    stage regexes with Go's RE2, which has no negative lookahead at all, so
    ``^(?!aizzak$).*`` is not a slow pattern -- it is a config the collector
    refuses to load. The project filter is a label matcher instead."""
    config = _text(_ALLOY)
    executable = "\n".join(line for line in config.split("\n") if not line.strip().startswith("//"))
    assert "(?!" not in executable, (
        "deploy/alloy/config.alloy: RE2 rejects negative lookahead, and the failure is a "
        "collector that will not start rather than a rule that matches badly"
    )


def test_the_high_cardinality_ids_are_metadata_and_never_labels() -> None:
    """0.2's rule («كلُّ عَلَمٍ منخفضُ التعدّد»), applied to the log store for
    the same reason: ``correlation_id`` has one distinct value PER REQUEST, and
    a label with that cardinality mints a Loki stream per request and takes
    the store down long before the platform does."""
    config = _text(_ALLOY)
    metadata_block = config.split("stage.structured_metadata")[1]
    for field in LOG_ID_FIELDS:
        assert field in metadata_block, f"{field!r} must be structured metadata"

    for label_block in config.split("stage.labels")[1:]:
        body = label_block.split("}")[0]
        for field in LOG_ID_FIELDS:
            assert field not in body, (
                f"deploy/alloy/config.alloy: {field!r} appears in a stage.labels block. "
                "A per-request label is an unbounded index key -- structured metadata is "
                "the Loki 3.x feature for exactly this."
            )


def test_the_store_allows_structured_metadata_at_all() -> None:
    """The collector sending structured metadata to a Loki that rejects it is
    a 400 per push and an empty store. Both halves, or neither works."""
    loki = yaml.safe_load(_text(_LOKI))
    assert loki["limits_config"]["allow_structured_metadata"] is True
    schema = loki["schema_config"]["configs"][-1]
    assert schema["schema"] == "v13" and schema["store"] == "tsdb", (
        "structured metadata needs TSDB + schema v13; an older schema accepts the config "
        "and drops the metadata"
    )


def test_retention_is_both_configured_and_enabled() -> None:
    """``retention_period`` without ``compactor.retention_enabled`` is a
    promise nothing keeps: the setting parses, the volume grows forever, and
    the misconfiguration looks exactly like a working one."""
    loki = yaml.safe_load(_text(_LOKI))
    assert loki["limits_config"]["retention_period"]
    assert loki["compactor"]["retention_enabled"] is True


def test_the_collector_persists_its_read_positions() -> None:
    """Without a persisted cursor every restart re-ships each container's
    whole retained history, and the store fills with duplicates of lines it
    already holds."""
    alloy = _compose()["services"]["alloy"]
    storage = [arg for arg in alloy["command"] if arg.startswith("--storage.path=")]
    assert storage, f"{_COMPOSE.name}: alloy must be given a --storage.path"
    path = storage[0].split("=", 1)[1]
    assert any(mount.endswith(f":{path}") for mount in alloy["volumes"]), (
        f"{_COMPOSE.name}: alloy's --storage.path ({path}) is not backed by a volume"
    )


def test_the_dashboard_query_is_the_acceptance_criterion() -> None:
    """«استعلامٌ واحد يجمع كلّ أسطر طلبٍ فاشلٍ عبر الحافّة والتطبيق والعامل
    بـcorrelation_id واحد» -- shipped as a provisioned panel rather than a
    query somebody has to remember at 3am."""
    dashboard = json.loads(
        _text(_REPO_ROOT / "deploy" / "grafana" / "dashboards" / "aizzak-logs.json")
    )
    variables = {v["name"] for v in dashboard["templating"]["list"]}
    assert "correlation_id" in variables

    exprs = [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
    joined = [e for e in exprs if "correlation_id" in e and "$correlation_id" in e]
    assert joined, (
        "aizzak-logs.json: no panel filters on the correlation_id variable -- the "
        "acceptance criterion of 0.6 is that ONE query gathers a request's lines"
    )
    # The query must span the whole stack, not one service: the criterion names
    # the edge AND the app AND the worker.
    assert any(re.search(r'\{stack="aizzak"\}', e) for e in joined), (
        "aizzak-logs.json: the join query must select the whole stack; a per-service "
        "selector cannot answer a criterion about three services at once"
    )
