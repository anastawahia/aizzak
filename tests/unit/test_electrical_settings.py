"""``ElectricalSettings`` and its environment mapping (BT27, PLT-21): the defaults, the bounds,
the validators, and the one reader of the environment."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.framework.settings.settings import ElectricalSettings, Settings
from app.infrastructure.config import env_settings
from app.infrastructure.config.env_settings import load_settings

_KEYS = (
    "ELECTRICAL_ENABLED",
    "ELE_PARSE_URL",
    "ELE_PARSE_TIMEOUT_S",
    "ELE_STAGE_DIR",
    "ELE_STAGE_CHUNK_BYTES",
    "ELE_INSPECT_BATCH_PAGES",
    "ELE_MAX_PAGES",
    "ELE_QUEUE_PROTECTION_LIMIT",
    "ELE_PROGRESS_MIN_INTERVAL_S",
    "ELE_JOB_LEASE_S",
    "ELECTRICAL_WORKER_ROLES",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read ONLY what each test sets: no ``.env``, no inherited variable."""
    monkeypatch.setitem(env_settings._EnvSettings.model_config, "env_file", None)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


# --- the contract --------------------------------------------------------------------
def test_the_defaults_are_the_documented_ones_and_the_feature_is_off() -> None:
    s = ElectricalSettings()
    assert s.enabled is False
    assert s.ele_parse_url == "http://ele-parse:8090"
    assert s.ele_parse_timeout_s == 330.0
    assert s.stage_dir == "/var/lib/aizzak/ele-stage"
    assert s.stage_chunk_bytes == 8_388_608  # 8 MiB
    assert s.inspect_batch_pages == 200
    assert s.max_pages == 5_000  # FR-ING-02 "at least 5,000"
    assert s.queue_protection_limit == 1_000
    assert s.progress_min_interval_s == 2.0
    assert s.job_lease_s == 600
    assert s.worker_roles == ("document",)


def test_the_root_settings_carry_it_and_it_is_immutable() -> None:
    assert Settings().electrical == ElectricalSettings()
    frozen = ElectricalSettings(enabled=True)
    with pytest.raises(ValidationError):
        frozen.__setattr__("enabled", False)
    with pytest.raises(ValidationError):
        ElectricalSettings(**{"unknown": 1})


def test_the_range_ceiling_of_ele_parse_is_below_the_client_timeout() -> None:
    # ele-parse's own default range ceiling is 300 s (LLD 3.3.10): the service must time out
    # first, so the client's patience has to be longer
    assert ElectricalSettings().ele_parse_timeout_s > 300


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ele_parse_timeout_s", 0),
        ("ele_parse_timeout_s", -1.0),
        ("stage_chunk_bytes", 65_535),
        ("stage_chunk_bytes", 67_108_865),
        ("inspect_batch_pages", 0),
        ("inspect_batch_pages", 1_001),
        ("max_pages", 0),
        ("queue_protection_limit", 0),
        ("progress_min_interval_s", -0.1),
        ("job_lease_s", 59),
    ],
)
def test_a_value_outside_its_bounds_is_refused(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ElectricalSettings(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stage_chunk_bytes", 65_536),
        ("stage_chunk_bytes", 67_108_864),
        ("inspect_batch_pages", 1),
        ("inspect_batch_pages", 1_000),
        ("progress_min_interval_s", 0),
        ("job_lease_s", 60),
    ],
)
def test_the_bounds_are_inclusive(field: str, value: Any) -> None:
    ElectricalSettings(**{field: value})


@pytest.mark.parametrize("url", ["ele-parse:8090", "ftp://ele-parse", "", "http://ele-parse:8090/"])
def test_the_service_url_must_be_http_without_a_trailing_slash(url: str) -> None:
    with pytest.raises(ValidationError):
        ElectricalSettings(ele_parse_url=url)


def test_an_https_url_is_accepted() -> None:
    assert ElectricalSettings(ele_parse_url="https://ele-parse.internal").ele_parse_url


@pytest.mark.parametrize("path", ["", "/", "relative/stage", "ele-stage"])
def test_the_stage_directory_must_be_an_absolute_non_root_path(path: str) -> None:
    with pytest.raises(ValidationError):
        ElectricalSettings(stage_dir=path)


@pytest.mark.parametrize("roles", [(), ("pages",), ("document", "ocr"), ("document", "document")])
def test_only_the_roles_inc_1_knows_are_accepted_once_each(roles: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        ElectricalSettings(worker_roles=roles)


# --- the environment -----------------------------------------------------------------
def test_an_empty_environment_gives_the_defaults() -> None:
    assert load_settings().electrical == ElectricalSettings()


def test_every_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ELECTRICAL_ENABLED": "true",
        "ELE_PARSE_URL": "http://parse.internal:9000",
        "ELE_PARSE_TIMEOUT_S": "120.5",
        "ELE_STAGE_DIR": "/data/stage",
        "ELE_STAGE_CHUNK_BYTES": "1048576",
        "ELE_INSPECT_BATCH_PAGES": "50",
        "ELE_MAX_PAGES": "9000",
        "ELE_QUEUE_PROTECTION_LIMIT": "10",
        "ELE_PROGRESS_MIN_INTERVAL_S": "0.5",
        "ELE_JOB_LEASE_S": "900",
        "ELECTRICAL_WORKER_ROLES": "document",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    s = load_settings().electrical

    assert s == ElectricalSettings(
        enabled=True,
        ele_parse_url="http://parse.internal:9000",
        ele_parse_timeout_s=120.5,
        stage_dir="/data/stage",
        stage_chunk_bytes=1_048_576,
        inspect_batch_pages=50,
        max_pages=9_000,
        queue_protection_limit=10,
        progress_min_interval_s=0.5,
        job_lease_s=900,
        worker_roles=("document",),
    )


def test_the_worker_roles_are_a_comma_list_and_blanks_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELECTRICAL_WORKER_ROLES", " document , ")
    assert load_settings().electrical.worker_roles == ("document",)


def test_an_unknown_role_in_the_environment_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELECTRICAL_WORKER_ROLES", "document,pages")
    with pytest.raises(ValidationError):
        load_settings()


def test_a_bad_number_in_the_environment_stops_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELE_INSPECT_BATCH_PAGES", "5000")
    with pytest.raises(ValidationError):
        load_settings()


def test_the_flag_is_off_unless_the_environment_turns_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_settings().electrical.enabled is False
    monkeypatch.setenv("ELECTRICAL_ENABLED", "false")
    assert load_settings().electrical.enabled is False
    monkeypatch.setenv("ELECTRICAL_ENABLED", "1")
    assert load_settings().electrical.enabled is True
