"""pytest-asyncio is the one async test runner; anyio's pytest plugin stays off.

docs/integration-ci-fix-plan.md. With both plugins active, the async fixtures
of a test carrying the anyio marker were claimed by whichever plugin loaded
first -- an order pytest does not guarantee, and one that differed between CI
and this machine. On CI anyio won: a fixture opened its Redis or Postgres
connection in anyio's event loop while the test ran in pytest-asyncio's, and
the integration job failed 46 tests (``Event loop is closed`` at teardown,
``attached to a different loop`` in the body) that passed on every local run.

``-p no:anyio`` in ``addopts`` removes the second runner; ``--strict-markers``
then rejects the marker at collection, so it cannot come back unnoticed.
"""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _ini_options() -> dict[str, Any]:
    with _PYPROJECT.open("rb") as handle:
        options = tomllib.load(handle)["tool"]["pytest"]["ini_options"]
    assert isinstance(options, dict)
    return options


def test_pyproject_keeps_auto_mode_and_disables_the_anyio_plugin() -> None:
    options = _ini_options()
    assert options.get("asyncio_mode") == "auto"
    assert "-p no:anyio" in " ".join(str(options.get("addopts", "")).split()), (
        "pyproject.toml's addopts must carry `-p no:anyio`; without it anyio's "
        "pytest plugin can claim async fixtures in a different event loop "
        "than the test's, depending on plugin load order"
    )


def test_only_pytest_asyncio_is_loaded(pytestconfig: pytest.Config) -> None:
    plugins = pytestconfig.pluginmanager
    assert plugins.has_plugin("asyncio")
    assert not plugins.has_plugin("anyio")


@pytest.fixture
async def fixture_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    yield asyncio.get_running_loop()


async def test_an_async_fixture_runs_in_its_tests_event_loop(
    fixture_loop: asyncio.AbstractEventLoop,
) -> None:
    assert asyncio.get_running_loop() is fixture_loop
