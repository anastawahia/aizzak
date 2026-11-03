"""Static check of the WireTwin ``electrical`` Compose profile (INC-1 S9, O9, INC1-AC-9).

Reads ``docker-compose.yml`` as YAML and asserts the DEC-04 isolation of ``ele-parse`` and the
shape of ``worker-electrical``. Nothing starts: the real container behaviour (no route out, a
read-only root, a killed heavy PDF) is the opt-in ``test_ele_parse_containers.py`` (O10).

Why the checks are this literal: ``ele-parse`` runs PyMuPDF on files a stranger drew, so the
only defence that does not depend on PyMuPDF being bug-free is that the box has nothing in it and
no door out. Each assertion below removes one thing a compromised parser could use.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _ROOT / ".env.example"
_ENV_SETTINGS = _ROOT / "src" / "app" / "infrastructure" / "config" / "env_settings.py"

_STAGE_MOUNT = "/var/lib/aizzak/ele-stage"
# Anything that names a credential or a data store. The parser needs none of them (AC-9).
_FORBIDDEN_ENV = re.compile(r"SECRET|PASSWORD|TOKEN|VAULT|DATABASE|REDIS|MINIO", re.IGNORECASE)
# The one legitimate match: a tuning knob counted in tokens of TEXT, not an auth token.
_ALLOWED_ENV = frozenset({"ELE_PARSE_TOKEN_SAMPLE"})


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    loaded = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def parser(compose: dict[str, Any]) -> dict[str, Any]:
    spec = compose["services"]["ele-parse"]
    assert isinstance(spec, dict)
    return spec


@pytest.fixture(scope="module")
def worker(compose: dict[str, Any]) -> dict[str, Any]:
    spec = compose["services"]["worker-electrical"]
    assert isinstance(spec, dict)
    return spec


def _names(networks: Any) -> list[str]:
    """Network names whether Compose wrote them as a list or a mapping."""
    return sorted(networks) if networks else []


# --------------------------------------------------------------------- the profile ----


def test_both_services_exist_only_behind_the_electrical_profile(
    parser: dict[str, Any], worker: dict[str, Any]
) -> None:
    assert parser["profiles"] == ["electrical"]
    assert worker["profiles"] == ["electrical"]


def test_no_other_service_is_in_the_electrical_profile(compose: dict[str, Any]) -> None:
    members = sorted(
        name
        for name, spec in compose["services"].items()
        if "electrical" in (spec.get("profiles") or ())
    )
    assert members == ["ele-parse", "worker-electrical"]


# --------------------------------------------------------------- ele-parse isolation ----


def test_ele_parse_is_on_the_internal_network_alone(
    compose: dict[str, Any], parser: dict[str, Any]
) -> None:
    assert _names(parser["networks"]) == ["ele-parse"], "no `default`, no second network"
    assert compose["networks"]["ele-parse"] == {"internal": True}
    assert "network_mode" not in parser
    assert "ports" not in parser, "nothing is published"


def test_only_the_worker_shares_the_ele_parse_network(compose: dict[str, Any]) -> None:
    members = sorted(
        name
        for name, spec in compose["services"].items()
        if "ele-parse" in _names(spec.get("networks"))
    )
    assert members == ["ele-parse", "worker-electrical"]
    assert "default" in _names(compose["services"]["worker-electrical"]["networks"])


def test_app_is_not_on_the_ele_parse_network(compose: dict[str, Any]) -> None:
    assert "ele-parse" not in _names(compose["services"]["app"].get("networks"))


def test_ele_parse_environment_carries_no_secret_dsn_or_vault_variable(
    parser: dict[str, Any],
) -> None:
    env = parser["environment"]
    offenders = sorted(k for k in env if _FORBIDDEN_ENV.search(k) and k not in _ALLOWED_ENV)
    assert offenders == []
    # The values too: a harmless-looking key must not carry an interpolated credential.
    leaking = sorted(
        k for k, v in env.items() if _FORBIDDEN_ENV.search(str(v)) and k not in _ALLOWED_ENV
    )
    assert leaking == []
    assert all(k.startswith("ELE_PARSE_") for k in env), sorted(env)
    for forbidden in ("env_file", "secrets", "configs", "depends_on", "extra_hosts"):
        assert forbidden not in parser, forbidden


def test_ele_parse_has_a_read_only_root_a_non_root_user_and_no_capabilities(
    parser: dict[str, Any],
) -> None:
    assert parser["read_only"] is True
    uid, _, gid = str(parser["user"]).partition(":")
    assert uid.isdigit() and int(uid) != 0 and gid.isdigit() and int(gid) != 0
    assert parser["cap_drop"] == ["ALL"]
    assert "cap_add" not in parser
    assert "no-new-privileges:true" in parser["security_opt"]
    assert not parser.get("privileged")
    assert parser["tmpfs"] == ["/tmp:size=64m"], "scratch is a small tmpfs, not a volume"


def test_ele_parse_mounts_the_stage_volume_read_only_and_nothing_else(
    parser: dict[str, Any],
) -> None:
    assert parser["volumes"] == [f"ele-stage:{_STAGE_MOUNT}:ro"]


def test_ele_parse_has_cpu_memory_and_process_limits(parser: dict[str, Any]) -> None:
    limits = parser["deploy"]["resources"]["limits"]
    assert re.fullmatch(r"\$\{ELE_PARSE_CPUS:-[0-9.]+\}", str(limits["cpus"]))
    assert re.fullmatch(r"\$\{ELE_PARSE_MEM:-[0-9]+[mg]\}", str(limits["memory"]))
    assert isinstance(limits["pids"], int) and 0 < limits["pids"] <= 128
    # The service-level key is not used next to `deploy.resources.limits` (Compose 5 refuses it).
    assert "pids_limit" not in parser


def test_ele_parse_has_a_healthcheck_and_the_child_limits_are_named(
    parser: dict[str, Any],
) -> None:
    assert "healthcheck" in parser
    env = parser["environment"]
    for key in (
        "ELE_PARSE_STAGE_ROOT",
        "ELE_PARSE_PAGE_TIMEOUT_S",
        "ELE_PARSE_RANGE_TIMEOUT_S",
        "ELE_PARSE_MEMORY_MB",
        "ELE_PARSE_MAX_CHILDREN",
    ):
        assert key in env, key
    assert env["ELE_PARSE_STAGE_ROOT"] == _STAGE_MOUNT


def test_ele_parse_image_is_its_own_not_the_app_image(parser: dict[str, Any]) -> None:
    assert parser["image"].startswith("${ELE_PARSE_IMAGE_REPO:-aizzak/ele-parse}:")


# --------------------------------------------------------------------- the worker ----


def test_the_worker_runs_the_electrical_loop_with_the_document_role(
    worker: dict[str, Any],
) -> None:
    env = worker["environment"]
    assert env["WORKER"] == "electrical"
    assert env["ELECTRICAL_WORKER_ROLES"] == "document"
    assert env["ELE_PARSE_URL"] == "http://ele-parse:8090"
    assert env["ELE_STAGE_DIR"] == _STAGE_MOUNT
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.ops.healthcheck",
        "electrical",
    ]


def test_the_worker_writes_the_stage_volume_and_the_parser_only_reads_it(
    worker: dict[str, Any], parser: dict[str, Any]
) -> None:
    assert worker["volumes"] == [f"ele-stage:{_STAGE_MOUNT}"]
    assert parser["volumes"] == [f"ele-stage:{_STAGE_MOUNT}:ro"]


def test_electrical_concurrency_is_one_and_the_worker_has_no_replicas(
    worker: dict[str, Any],
) -> None:
    """C-3: ele-parse has no isolation BETWEEN tenants (one uid, one stage volume holding every
    workspace's files). The only thing keeping a compromised parser from reading another
    tenant's file is that one inspection runs at a time on one worker. Raising either is a
    security decision, not a capacity one (docs/design/08-local-runbook.md 4.29)."""
    assert worker["environment"]["WORKER_CONCURRENCY"] == "${WORKER_CONCURRENCY_ELECTRICAL:-1}"
    assert "replicas" not in worker.get("deploy", {})
    assert "scale" not in worker
    example = _ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.strip().startswith("WORKER_CONCURRENCY_ELECTRICAL="):
            assert line.split("=", 1)[1].split("#")[0].strip() in ("", "1")


def test_the_stage_volume_is_a_named_volume(compose: dict[str, Any]) -> None:
    assert "ele-stage" in compose["volumes"]


def test_the_worker_has_limits(worker: dict[str, Any]) -> None:
    limits = worker["deploy"]["resources"]["limits"]
    assert "WORKER_ELECTRICAL_CPUS" in str(limits["cpus"])
    assert "WORKER_ELECTRICAL_MEM" in str(limits["memory"])


# ------------------------------------------------------------- the feature flag ----


def test_app_carries_the_feature_flag_off_by_default(compose: dict[str, Any]) -> None:
    env = compose["services"]["app"]["environment"]
    assert env["ELECTRICAL_ENABLED"] == "${ELECTRICAL_ENABLED:-false}"


def test_every_electrical_setting_key_is_named_in_env_example() -> None:
    """The eleven keys `env_settings.py` reads, the five `ele-parse` keys, and the size keys
    (the last as commented overrides). Names only: no value here may be a credential."""
    source = _ENV_SETTINGS.read_text(encoding="utf-8")
    read = set(re.findall(r'alias="(ELE[A-Z_]*)"', source))
    assert len(read) == 11, sorted(read)
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    assigned = {m.group(1) for ln in lines if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", ln))}
    commented = {m.group(1) for ln in lines if (m := re.match(r"^#([A-Z][A-Z0-9_]*)=", ln))}
    assert read <= assigned, sorted(read - assigned)
    parse_keys = {
        "ELE_PARSE_PAGE_TIMEOUT_S",
        "ELE_PARSE_RANGE_TIMEOUT_S",
        "ELE_PARSE_MEMORY_MB",
        "ELE_PARSE_MAX_CHILDREN",
        "ELE_PARSE_TOKEN_SAMPLE",
    }
    assert parse_keys <= assigned, sorted(parse_keys - assigned)
    assert {
        "ELE_PARSE_CPUS",
        "ELE_PARSE_MEM",
        "WORKER_ELECTRICAL_CPUS",
        "WORKER_ELECTRICAL_MEM",
    } <= commented
    for key in sorted(read | parse_keys):
        value = next(ln.partition("=")[2] for ln in lines if ln.startswith(f"{key}="))
        assert not _FORBIDDEN_ENV.search(value), key


def test_every_tunable_the_settings_read_reaches_the_containers(
    compose: dict[str, Any], worker: dict[str, Any]
) -> None:
    """A key in `.env.example` that Compose never passes on is a knob wired to nothing."""
    tunables = {
        "ELE_PARSE_TIMEOUT_S",
        "ELE_STAGE_CHUNK_BYTES",
        "ELE_INSPECT_BATCH_PAGES",
        "ELE_MAX_PAGES",
        "ELE_QUEUE_PROTECTION_LIMIT",
        "ELE_PROGRESS_MIN_INTERVAL_S",
        "ELE_JOB_LEASE_S",
    }
    assert tunables <= set(worker["environment"])
    assert tunables <= set(compose["services"]["app"]["environment"])
    # ...and into no other service: their config is not this step's to change.
    for name in ("worker-memory", "worker-knowledge", "worker-media", "outbox-relay"):
        assert not tunables & set(compose["services"][name]["environment"]), name
