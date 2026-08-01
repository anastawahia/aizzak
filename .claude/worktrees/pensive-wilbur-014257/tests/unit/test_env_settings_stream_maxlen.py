"""Unit tests for the ``STREAM_MAXLEN`` env mapping (7.3,
``infrastructure/config/env_settings.py``).

The env layer models the cap as a plain ``int`` with ``0`` meaning "off",
while the ``Settings`` contract models it as ``int | None`` -- so there is a
real translation between the two, and a translation with a falsy edge case is
exactly the kind that reads as obviously correct and ships inverted. Redis
rejects ``MAXLEN 0``, so getting this backwards would not fail quietly at the
boundary; it would fail at the first published event, inside the relay.

``env_file`` is neutralised for every test here: this repository HAS a real
(gitignored) ``.env`` in the working directory, so without that these
assertions would silently depend on whatever a developer set locally.
"""

from __future__ import annotations

import pytest

from app.infrastructure.config import env_settings
from app.infrastructure.config.env_settings import load_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read ONLY what each test sets: no ``.env``, no inherited variable."""
    monkeypatch.setitem(env_settings._EnvSettings.model_config, "env_file", None)
    monkeypatch.delenv("STREAM_MAXLEN", raising=False)


def test_zero_means_no_trimming_and_reaches_the_contract_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``0`` is the documented "off" switch. It must arrive as ``None``: the
    adapter branches on absence, and ``MAXLEN 0`` is not a command Redis
    accepts."""
    monkeypatch.setenv("STREAM_MAXLEN", "0")

    assert load_settings().events.stream_maxlen is None


def test_a_positive_cap_is_passed_through_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_MAXLEN", "250000")

    assert load_settings().events.stream_maxlen == 250_000


def test_the_default_is_a_cap_not_unbounded_growth() -> None:
    """An unset variable must NOT mean "grow forever". The point of 7.3 is
    that unbounded is the wrong default, so the safe value has to be the one
    an operator gets by doing nothing."""
    assert load_settings().events.stream_maxlen == 100_000
