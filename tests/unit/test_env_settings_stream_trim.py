"""Unit tests for the two capacity 5.5 env knobs (``STREAM_TRIM_INTERVAL_S`` ·
``STREAM_TRIM_MARGIN_S``, ``infrastructure/config/env_settings.py``).

The interval is this step's ``م-8`` switch -- ``0`` must reach the contract as
``0`` so the relay builds no trimmer at all -- and the margin must never be
negative, since a negative margin would move the cut ABOVE the slowest
reader. ``env_file`` is neutralised for the reason
``test_env_settings_stream_maxlen.py`` gives.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.infrastructure.config import env_settings
from app.infrastructure.config.env_settings import load_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(env_settings._EnvSettings.model_config, "env_file", None)
    monkeypatch.delenv("STREAM_TRIM_INTERVAL_S", raising=False)
    monkeypatch.delenv("STREAM_TRIM_MARGIN_S", raising=False)


def test_the_defaults_trim_every_minute_and_keep_ten_minutes_of_history() -> None:
    events = load_settings().events
    assert events.stream_trim_interval_s == 60.0
    assert events.stream_trim_margin_s == 600.0


def test_zero_interval_is_the_off_switch_and_arrives_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STREAM_TRIM_INTERVAL_S", "0")
    assert load_settings().events.stream_trim_interval_s == 0.0


def test_values_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_TRIM_INTERVAL_S", "15")
    monkeypatch.setenv("STREAM_TRIM_MARGIN_S", "0")
    events = load_settings().events
    assert (events.stream_trim_interval_s, events.stream_trim_margin_s) == (15.0, 0.0)


@pytest.mark.parametrize("name", ["STREAM_TRIM_INTERVAL_S", "STREAM_TRIM_MARGIN_S"])
def test_a_negative_value_is_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "-1")
    with pytest.raises(ValidationError):
        load_settings()
