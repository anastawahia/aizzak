"""Unit tests for the unified worker dispatcher (5.1-ج · ``workers/main.py``).

Hermetic: ``_RUNNERS`` entries are monkeypatched with fake coroutines so
these tests never actually build a worker (which would otherwise hit the
honest-failure ``AppError`` every real ``build_<name>_worker_from_env``
raises today, ``test_workers_bootstrap.py``'s own tests for that).
"""

from __future__ import annotations

import pytest

from app.workers import main as workers_main


@pytest.fixture(autouse=True)
def _clear_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from "no WORKER env var, no extra argv" -- the
    process's own real environment must never leak into these assertions."""
    monkeypatch.delenv("WORKER", raising=False)
    monkeypatch.setattr(workers_main.sys, "argv", ["main.py"])


# --------------------------------------------------------------------------- #
# _select_worker                                                              #
# --------------------------------------------------------------------------- #
def test_select_worker_prefers_the_env_var_over_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER", "media")
    monkeypatch.setattr(workers_main.sys, "argv", ["main.py", "memory"])

    assert workers_main._select_worker() == "media"


def test_select_worker_falls_back_to_argv_when_no_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workers_main.sys, "argv", ["main.py", "memory"])

    assert workers_main._select_worker() == "memory"


def test_select_worker_raises_systemexit_naming_valid_choices_when_neither_is_set() -> None:
    with pytest.raises(SystemExit) as exc_info:
        workers_main._select_worker()

    message = str(exc_info.value)
    assert "knowledge" in message
    assert "media" in message
    assert "memory" in message
    assert "outbox_relay" in message


# --------------------------------------------------------------------------- #
# run() -- dispatch                                                           #
# --------------------------------------------------------------------------- #
async def test_run_dispatches_to_the_env_selected_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"knowledge": False}

    async def fake_knowledge_run() -> None:
        called["knowledge"] = True

    monkeypatch.setenv("WORKER", "knowledge")
    monkeypatch.setitem(workers_main._RUNNERS, "knowledge", fake_knowledge_run)

    await workers_main.run()

    assert called["knowledge"] is True


async def test_run_dispatches_to_outbox_relay_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``outbox_relay`` is a selectable name too (the module docstring) even
    though D-26 normally runs it via its own dedicated command."""
    called = {"relay": False}

    async def fake_relay_run() -> None:
        called["relay"] = True

    monkeypatch.setenv("WORKER", "outbox_relay")
    monkeypatch.setitem(workers_main._RUNNERS, "outbox_relay", fake_relay_run)

    await workers_main.run()

    assert called["relay"] is True


async def test_run_raises_systemexit_for_an_unknown_worker_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER", "not-a-real-worker")

    with pytest.raises(SystemExit) as exc_info:
        await workers_main.run()

    message = str(exc_info.value)
    assert "not-a-real-worker" in message
    assert "knowledge" in message  # still lists every valid choice
